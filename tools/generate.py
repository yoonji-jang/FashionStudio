"""생성형 이미지 AI 비교 실험 (11단계). 상품 사진 여러 장을 넣어 장면 한 장을 새로 그리게 한다.
하루 코디컷은 tools.outfit_gen 이 이 파일의 부품(참고판, API 호출)을 쓴다.

누끼를 오려 붙이는 지금 방식(tools.poc, Photoroom)과 달리, 모델이 코디 전체를 한 번에 그려서
빛·각도·천의 주름이 자연스럽다. 대신 상품이 원본과 달라질 수 있어 비교표로 사람이 확인한다.

장면(shot): flatlay(펼친 코디컷), flatlay_guide(우리 코디컷을 구도 가이드로), person(얼굴 없는
사람컷), mannequin(마네킹컷). 프롬프트는 prompts/gen_<shot>.md, 모델은 config/models.yaml generate.

사용법:
  python -m tools.generate --spec experiments/gen_compare.yaml --out out/gen/run-1
  python -m tools.generate ... --models nano_banana_pro --shots flatlay
  python -m tools.generate ... --fake        # API 없이 흐름만 확인

상품마다 누끼 1장 + 쇼핑몰 사진 몇 장(experiments 의 photos 번호, 같은 색만)을 한 장의 참고판으로
이어 붙여 보낸다. 사진 수가 늘어도 보내는 이미지는 상품당 1장이라 모델 입력 한도에 걸리지 않는다.
쇼핑몰 사진은 tools.scout 로 받고, 모델 얼굴은 잘라 낸다.

결과 (out 폴더): inputs/ 보낸 상품 참고판, <모델>/<장면>_<회차>.jpg, sheet.jpg 비교표,
report.md 요약(모델별 성공 수·시간·비용), run.json 실행 기록.
API 키는 환경 변수 OPENROUTER_API_KEY.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Protocol

import httpx
import yaml
from PIL import Image, ImageDraw, ImageFont

from core.profile import REPO_ROOT
from tools.photoroom import render_prompt

CONFIG_PATH = REPO_ROOT / "config" / "models.yaml"
FONT_PATH = REPO_ROOT / "assets" / "fonts" / "Pretendard-Medium.otf"
SHOTS = ("flatlay", "flatlay_guide", "person", "mannequin", "model")
# 장면별 지시문 (기본 prompts/gen_<장면>.md). model 은 16단계 모델컷 게시물과 같은 지시문을 쓴다
PROMPTS = {"model": "outfit_model"}
SHOT_KO = {
    "flatlay": "펼친 코디컷",
    "flatlay_guide": "가이드 배치",
    "person": "사람컷",
    "mannequin": "마네킹컷",
    "model": "모델컷",
}
CATEGORY_EN = {
    "아우터": "outer",
    "상의": "top",
    "하의": "bottoms",
    "원피스": "dress",
    "가방": "bag",
    "신발": "shoes",
    "소품": "accessory",
}
CANVAS = (1080, 1350)
# 쇼핑몰 원본 사진은 결과 폴더(브랜치에 올라감)에 두지 않는다
CACHE_DIR = Path(tempfile.gettempdir()) / "fashionstudio_gen"


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["generate"]


def read_ref(ref: str) -> bytes:
    """'브랜치:경로' 는 git 에서, 아니면 저장소 기준 파일에서 읽는다."""
    path = REPO_ROOT / ref
    if ":" not in ref or path.exists():
        return path.read_bytes()
    branch, _, rel = ref.partition(":")
    for rev in (f"origin/{branch}", branch):
        res = subprocess.run(
            ["git", "show", f"{rev}:{rel}"], cwd=REPO_ROOT, capture_output=True, check=False
        )
        if res.returncode == 0:
            return res.stdout
    raise FileNotFoundError(f"{ref} 를 찾지 못했어요 (git fetch origin {branch} 필요)")


def prepare_input(data: bytes, max_px: int) -> bytes:
    """투명 누끼는 흰 바탕에 올리고, 긴 변을 max_px 로 줄여 JPEG 로 만든다."""
    im = Image.open(io.BytesIO(data))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bbox = im.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox()
        if bbox:
            im = im.crop(bbox)
        pad = max(im.size) // 12
        bg = Image.new("RGB", (im.width + 2 * pad, im.height + 2 * pad), "white")
        bg.paste(im, (pad, pad), im)
        im = bg
    im = im.convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def product_photos(
    product_id: str,
    indexes: list[int],
    account: str,
    cache: Path,
    extra: dict | None = None,
) -> list[bytes]:
    """상품 창고의 쇼핑몰 사진 중 고른 번호만 받아, 모델 얼굴을 잘라 낸 JPEG 로 돌려준다.
    창고에서 빠진 상품은 extra(지난 게시물 manifest 의 상품, products_from)에서 찾는다."""
    from core.store import Store
    from tools import faces, feed

    found = {p.id: p for p in Store(account).products()} | (extra or {})
    product = found[product_id]
    out = []
    for i in indexes:
        dest = cache / f"{product_id.split(':')[-1]}_{i}.jpg"
        path = feed.fetch_photo(str(product.image_urls[i]), dest)
        img, ok = faces.hide_faces(Image.open(path).convert("RGB"))
        if not ok:
            print(f"{product_id} #{i}: 얼굴을 잘라 낼 수 없어 뺌", file=sys.stderr)
            continue
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        out.append(buf.getvalue())
    if not out and indexes:
        # 고른 사진이 모두 얼굴을 못 잘라 내면 첫 사진 원본을 쓴다 (10-14 사용자: 얼굴 사진 괜찮음)
        print(f"{product_id} #{indexes[0]}: 얼굴 없는 사진이 없어 원본을 씀", file=sys.stderr)
        buf = io.BytesIO()
        img = Image.open(cache / f"{product_id.split(':')[-1]}_{indexes[0]}.jpg")
        img.convert("RGB").save(buf, "JPEG", quality=92)
        out.append(buf.getvalue())
    return out


def reference_panel(images: list[bytes], max_px: int) -> bytes:
    """같은 상품 사진 여러 장을 흰 바탕에 가로로 이어 붙인 참고판 (긴 변 max_px)."""
    ims = [Image.open(io.BytesIO(prepare_input(b, max_px))).convert("RGB") for b in images]
    h = min(im.height for im in ims)
    ims = [im.resize((round(im.width * h / im.height), h)) for im in ims]
    gap = h // 30
    panel = Image.new("RGB", (sum(im.width for im in ims) + gap * (len(ims) - 1), h), "white")
    x = 0
    for im in ims:
        panel.paste(im, (x, 0))
        x += im.width + gap
    panel.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    panel.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def item_lines(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        size = f" Real size: {it['size']}." if it.get("size") else ""
        lines.append(
            f"Image {i}: the {CATEGORY_EN.get(it['category'], it['category'])}, {it['desc']}.{size}"
        )
    return "\n".join(lines)


def build_prompt(shot: str, spec: dict, repeat: int = 1) -> str:
    """repeat 회차 지시문. 장면 값이 목록이면 회차마다 하나씩 돌아가며 쓴다 (포즈 등)."""
    extra = spec.get("shots", {}).get(shot, {}) or {}
    values = {}
    for k, v in extra.items():
        if k == "repeats":
            continue
        values[k] = str(v[(repeat - 1) % len(v)] if isinstance(v, list) else v)
    return render_prompt(
        PROMPTS.get(shot, f"gen_{shot}"),
        items=item_lines(spec["items"]),
        styling=spec.get("styling", ""),
        **values,
    )


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def build_payload(model: dict, prompt: str, images: list[bytes], cfg: dict) -> dict:
    content = [{"type": "text", "text": prompt}]
    content += [{"type": "image_url", "image_url": {"url": data_url(b)}} for b in images]
    return {
        "model": model["id"],
        "messages": [{"role": "user", "content": content}],
        "modalities": model.get("modalities", ["image", "text"]),
        "image_config": {"aspect_ratio": cfg["aspect_ratio"], "image_size": cfg["image_size"]},
        "usage": {"include": True},
    }


def parse_response(body: dict) -> tuple[bytes, float | None]:
    """응답에서 첫 이미지와 비용(달러, 있으면)을 꺼낸다."""
    if "error" in body:
        raise RuntimeError(f"API 오류: {body['error']}")
    msg = body["choices"][0]["message"]
    for img in msg.get("images") or []:
        url = img.get("image_url", {}).get("url", "")
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1]), body.get("usage", {}).get("cost")
    text = (msg.get("content") or "")[:200]
    raise RuntimeError(f"이미지가 없는 응답: {text!r}")


def fit_canvas(data: bytes, size: tuple[int, int] = CANVAS) -> Image.Image:
    """4:5 가 아닌 결과는 가운데를 잘라 1080x1350 으로 맞춘다."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    tw, th = size
    scale = max(tw / im.width, th / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    left, top = (im.width - tw) // 2, (im.height - th) // 2
    return im.crop((left, top, left + tw, top + th))


class QuotaError(RuntimeError):
    """402: OpenRouter 크레딧 부족. 더 돌려도 소용없다."""


class Client(Protocol):
    def generate(
        self, model: dict, prompt: str, images: list[bytes]
    ) -> tuple[bytes, float | None]: ...


class OpenRouterClient:
    def __init__(self, cfg: dict, key: str):
        self.cfg = cfg
        self.http = httpx.Client(
            timeout=cfg.get("timeout_sec", 300), headers={"Authorization": f"Bearer {key}"}
        )

    def generate(self, model: dict, prompt: str, images: list[bytes]) -> tuple[bytes, float | None]:
        payload = build_payload(model, prompt, images, self.cfg)
        res = self.http.post(self.cfg["endpoint"], json=payload)
        if res.status_code == 402:
            raise QuotaError(f"OpenRouter 크레딧이 부족합니다: {res.text[:300]}")
        if res.status_code >= 400:
            raise RuntimeError(f"HTTP {res.status_code}: {res.text[:300]}")
        return parse_response(res.json())


class FakeClient:
    """API 없이 흐름만 확인: 입력 사진을 이어 붙인 그림을 돌려준다."""

    def generate(self, model: dict, prompt: str, images: list[bytes]) -> tuple[bytes, float | None]:
        ims = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
        out = Image.new("RGB", (1080, 1350), "#d8d4ce")
        for i, im in enumerate(ims):
            im.thumbnail((340, 340))
            out.paste(im, (20 + (i % 3) * 350, 20 + (i // 3) * 350))
        buf = io.BytesIO()
        out.save(buf, "PNG")
        return buf.getvalue(), 0.0


def make_sheet(rows: list[tuple[str, list[tuple[str, Path | None]]]], out: Path) -> None:
    """장면마다 한 줄, 모델마다 한 칸인 비교표."""
    cw, ch, head, label = 360, 450, 44, 200
    ncol = max(len(cells) for _, cells in rows)
    sheet = Image.new("RGB", (label + ncol * (cw + 10), len(rows) * (ch + head + 10)), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(FONT_PATH), 22) if FONT_PATH.exists() else None
    for r, (row_name, cells) in enumerate(rows):
        y = r * (ch + head + 10)
        draw.text((10, y + head + ch // 2), row_name, fill="black", font=font)
        for c, (col_name, path) in enumerate(cells):
            x = label + c * (cw + 10)
            draw.text((x, y + 8), col_name, fill="black", font=font)
            if path and path.exists():
                im = Image.open(path).convert("RGB")
                im.thumbnail((cw, ch))
                sheet.paste(im, (x, y + head))
            else:
                draw.rectangle((x, y + head, x + cw, y + head + ch), outline="#bbb")
                draw.text((x + 20, y + head + ch // 2), "실패", fill="#b00", font=font)
    sheet.save(out, "JPEG", quality=88)


def manifest_products(ref: str) -> dict:
    """지난 게시물 manifest.json ('브랜치:경로') 의 상품들. 창고에서 빠진 상품의 사진 주소."""
    from core.models import Manifest

    m = Manifest.model_validate_json(read_ref(ref))
    return {p.id: p for p in m.plan.items}


def run(
    spec_path: Path,
    out: Path,
    client: Client,
    cfg: dict,
    models: list[str] | None = None,
    shots: list[str] | None = None,
) -> dict:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    # 화면비 시험 (예: 9:16 릴스 화각). spec 의 aspect_ratio·canvas 가 config 를 덮는다
    if spec.get("aspect_ratio"):
        cfg = {**cfg, "aspect_ratio": spec["aspect_ratio"]}
        if hasattr(client, "cfg"):
            client.cfg = cfg
    canvas = tuple(spec.get("canvas") or CANVAS)
    out.mkdir(parents=True, exist_ok=True)
    (out / "inputs").mkdir(exist_ok=True)
    max_px = cfg.get("input_max_px", 1024)

    inputs = []
    account = spec.get("account", "women")
    extra = manifest_products(spec["products_from"]) if spec.get("products_from") else None
    for i, it in enumerate(spec["items"], 1):
        shots_in = [read_ref(it["image"])] if it.get("image") else []
        if it.get("photos"):
            shots_in += product_photos(it["product"], it["photos"], account, CACHE_DIR, extra)
        jpeg = reference_panel(shots_in, cfg.get("panel_max_px", 1536))
        (out / "inputs" / f"{i:02d}_{CATEGORY_EN.get(it['category'], 'item')}.jpg").write_bytes(
            jpeg
        )
        inputs.append(jpeg)
    guide = prepare_input(read_ref(spec["guide"]), max_px) if spec.get("guide") else None
    compare = out / "inputs" / "current.jpg"
    if spec.get("compare_with"):
        compare.write_bytes(read_ref(spec["compare_with"]))

    model_keys = models or list(cfg["models"])
    shot_keys = [s for s in (shots or SHOTS) if s in spec.get("shots", {})]
    record: dict = {
        "spec": str(spec_path),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": [],
    }
    for shot in shot_keys:
        images = inputs + ([guide] if shot == "flatlay_guide" and guide else [])
        repeats = int(spec["shots"][shot].get("repeats", 1))
        for key in model_keys:
            model = cfg["models"][key]
            (out / key).mkdir(exist_ok=True)
            for n in range(1, repeats + 1):
                prompt = (
                    (REPO_ROOT / spec["prompt_text"]).read_text(encoding="utf-8")
                    if spec.get("prompt_text")  # 하루 코디컷 지시문을 그대로 고쳐 쓴 시험
                    else build_prompt(shot, spec, n)
                )
                (out / f"prompt_{shot}_{n}.txt").write_text(prompt, encoding="utf-8")
                t0 = time.time()
                entry = {"model": key, "shot": shot, "repeat": n}
                try:
                    data, cost = client.generate(model, prompt, images)
                    path = out / key / f"{shot}_{n}.jpg"
                    fit_canvas(data, canvas).save(path, "JPEG", quality=92)
                    entry.update(ok=True, cost=cost, file=str(path.relative_to(out)))
                except Exception as e:  # 한 모델이 실패해도 나머지는 계속
                    entry.update(ok=False, error=str(e)[:300])
                entry["sec"] = round(time.time() - t0, 1)
                print(json.dumps(entry, ensure_ascii=False), flush=True)
                record["results"].append(entry)

    rows = []
    for shot in shot_keys:
        # 펼친 코디컷 줄 맨 앞에는 지금 방식(누끼 배치) 결과를 둔다
        cells = [("지금 방식", compare)] if shot.startswith("flatlay") and compare.exists() else []
        for key in model_keys:
            cells.append((cfg["models"][key]["name"].split(" (")[0], out / key / f"{shot}_1.jpg"))
        rows.append((SHOT_KO[shot], cells))
    if rows:
        make_sheet(rows, out / "sheet.jpg")
    record["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (out / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), "utf-8")
    (out / "report.md").write_text(report(spec, record, cfg, model_keys), encoding="utf-8")
    return record


def report(spec: dict, record: dict, cfg: dict, model_keys: list[str]) -> str:
    lines = [
        f"# 생성형 이미지 AI 비교: {spec.get('title', '')}",
        "",
        "| 모델 | 성공 | 평균 시간(초) | 비용($) |",
        "|---|---|---|---|",
    ]
    total = 0.0
    for key in model_keys:
        rs = [r for r in record["results"] if r["model"] == key]
        ok = [r for r in rs if r["ok"]]
        cost = sum(r.get("cost") or 0 for r in ok)
        total += cost
        avg = sum(r["sec"] for r in rs) / len(rs) if rs else 0
        lines.append(
            f"| {cfg['models'][key]['name']} | {len(ok)}/{len(rs)} | {avg:.0f} | {cost:.3f} |"
        )
    lines += ["", f"총 비용 약 ${total:.2f} (OpenRouter 가 알려 준 값)", "", "## 실패", ""]
    fails = [r for r in record["results"] if not r["ok"]]
    lines += [f"- {r['model']} {r['shot']}_{r['repeat']}: {r['error']}" for r in fails] or ["없음"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--spec", type=Path, default=REPO_ROOT / "experiments" / "gen_compare.yaml")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--models", nargs="*")
    p.add_argument("--shots", nargs="*", choices=SHOTS)
    p.add_argument("--fake", action="store_true")
    a = p.parse_args(argv)
    cfg = load_config()
    if a.fake:
        client: Client = FakeClient()
    else:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            print("OPENROUTER_API_KEY 가 없어요 (GitHub Secrets 에 등록)", file=sys.stderr)
            return 2
        client = OpenRouterClient(cfg, key)
    record = run(a.spec, a.out, client, cfg, a.models, a.shots)
    return 0 if any(r["ok"] for r in record["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
