"""Gemini 이미지 생성 도구.

- prep: 상품 사진을 흰 배경 정사각형으로 정리 (코드만, AI 없음)
- extract: 상품 사진 1장 → 사람·글자 없는 단독 정리컷 (Gemini)
- compose: 정리컷 여러 장 → 플랫레이 코디컷 한 장 (Gemini)

사용법:
  python -m tools.gemini_image prep --image in.jpg --out out.png
  python -m tools.gemini_image extract --image in.png --category 아우터 --desc "브라운 레더자켓" \
      --out out.png
  python -m tools.gemini_image compose --plan plan.json --account women --out look.png

compose 의 plan.json: {"items": [{"category": "아우터", "desc": "...", "image": "path.png"}, ...]}
API 키는 환경 변수 GEMINI_API_KEY 에서 읽는다.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from string import Template
from typing import Protocol

import httpx
import yaml
from PIL import Image, ImageChops, ImageOps
from pydantic import BaseModel, Field

from core.models import Category
from core.profile import REPO_ROOT, load_profile

PROMPTS_DIR = REPO_ROOT / "prompts"
MODELS_PATH = REPO_ROOT / "config" / "models.yaml"
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

CATEGORY_EN: dict[str, str] = {
    "아우터": "outerwear",
    "상의": "top",
    "하의": "bottoms",
    "원피스": "dress",
    "가방": "bag",
    "신발": "shoes",
    "소품": "accessory",
}


class NoImageError(RuntimeError):
    """모델이 이미지를 돌려주지 않음 (안전 필터, 텍스트만 응답 등)."""


class QuotaError(RuntimeError):
    """재시도 후에도 429. 결제 미등록·한도 초과라 더 돌려도 소용없다."""


# ---------------------------------------------------------------- 설정·지시문


def resolve_model(name: str, path: Path = MODELS_PATH) -> str:
    """config/models.yaml 의 키(default, retry, test)면 ID로 바꾸고, 아니면 그대로 쓴다."""
    models = yaml.safe_load(path.read_text(encoding="utf-8"))["image"]
    return models.get(name, name)


def render_prompt(name: str, prompts_dir: Path = PROMPTS_DIR, **values: str) -> str:
    """prompts/<name>.md 를 읽어 맨 앞 HTML 주석을 빼고 $변수를 채운다."""
    text = (prompts_dir / f"{name}.md").read_text(encoding="utf-8")
    text = re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.S)
    return Template(text).substitute(values).strip() + "\n"


def layout_lines(categories: list[str], prompts_dir: Path = PROMPTS_DIR) -> str:
    rules = yaml.safe_load((prompts_dir / "layout.yaml").read_text(encoding="utf-8"))
    seen: list[str] = []
    for c in categories:
        if c in rules and c not in seen:
            seen.append(c)
    return "\n".join(f"- Place {rules[c]}." for c in seen)


# ---------------------------------------------------------------- 전처리


def prep_image(src: Path, dest: Path, size: int = 1024, margin: float = 0.06) -> Path:
    """투명·여백을 정리하고 흰 배경 정사각형으로 맞춘다. 상품 모양은 건드리지 않는다."""
    img = ImageOps.exif_transpose(Image.open(src))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, "white")
        img = Image.alpha_composite(bg, img)
    img = img.convert("RGB")

    # 가장자리의 흰색(에 가까운) 여백을 잘라낸다
    diff = ImageChops.difference(img, Image.new("RGB", img.size, "white")).convert("L")
    bbox = diff.point(lambda p: 255 if p > 12 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)

    inner = round(size * (1 - 2 * margin))
    img.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), "white")
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest


# ---------------------------------------------------------------- Gemini 호출

Part = str | Path


def _image_part(path: Path) -> dict:
    img = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {
        "inlineData": {"mimeType": "image/png", "data": base64.b64encode(buf.getvalue()).decode()}
    }


def build_request(parts: list[Part], aspect_ratio: str, image_size: str) -> dict:
    return {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": p} if isinstance(p, str) else _image_part(p) for p in parts],
            }
        ],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio, "imageSize": image_size},
        },
    }


def parse_image(body: dict) -> bytes:
    for cand in body.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            data = part.get("inlineData") or part.get("inline_data")
            if data and data.get("data"):
                return base64.b64decode(data["data"])
    reasons = [c.get("finishReason") for c in body.get("candidates", [])]
    texts = [
        p.get("text", "")
        for c in body.get("candidates", [])
        for p in (c.get("content") or {}).get("parts", [])
        if p.get("text")
    ]
    block = (body.get("promptFeedback") or {}).get("blockReason")
    raise NoImageError(f"이미지 없음: finish={reasons} block={block} text={' '.join(texts)[:200]}")


class ImageClient(Protocol):
    model: str

    def generate(self, parts: list[Part], aspect_ratio: str, image_size: str) -> bytes: ...


class GeminiClient:
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        timeout: float = 180.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY 가 없습니다 (.env 또는 GitHub Secrets)")
        self.http = httpx.Client(timeout=timeout, transport=transport)

    def generate(
        self, parts: list[Part], aspect_ratio: str, image_size: str, attempts: int = 4
    ) -> bytes:
        body = build_request(parts, aspect_ratio, image_size)
        url = API_URL.format(model=self.model)
        for i in range(attempts):
            resp = self.http.post(url, json=body, headers={"x-goog-api-key": self.api_key})
            if resp.status_code in (429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(2 ** (i + 1) * 5)
                continue
            if resp.status_code == 429:
                raise QuotaError(f"Gemini 429 ({self.model}): {resp.text[:1500]}")
            if resp.status_code >= 400:
                raise RuntimeError(f"Gemini {resp.status_code} ({self.model}): {resp.text[:1500]}")
            return parse_image(resp.json())
        raise RuntimeError("unreachable")


class FakeClient:
    """키 없이 흐름을 확인할 때 쓰는 가짜 클라이언트. 입력 이미지를 이어 붙여 돌려준다."""

    def __init__(self, model: str = "fake"):
        self.model = model

    def generate(self, parts: list[Part], aspect_ratio: str, image_size: str) -> bytes:
        w, h = (int(x) for x in aspect_ratio.split(":"))
        canvas = Image.new("RGB", (w * 100, h * 100), (214, 210, 204))
        images = [Image.open(p).convert("RGB") for p in parts if isinstance(p, Path)]
        cell = canvas.width // 3
        for i, img in enumerate(images):
            img.thumbnail((cell, cell))
            canvas.paste(img, ((i % 3) * cell, (i // 3) * cell))
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()


def save_image(data: bytes, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.open(io.BytesIO(data)).convert("RGB").save(dest, quality=92)  # jpg면 품질 적용
    return dest


# ---------------------------------------------------------------- extract / compose


class ComposeItem(BaseModel):
    category: Category
    desc: str
    image: Path


class ComposePlan(BaseModel):
    items: list[ComposeItem] = Field(min_length=1, max_length=10)


def extract(
    client: ImageClient, image: Path, category: str, desc: str, out: Path, image_size: str = "1K"
) -> Path:
    prompt = render_prompt("extract", category_en=CATEGORY_EN[category], desc=desc)
    return save_image(client.generate([image, prompt], "1:1", image_size), out)


def compose_parts(items: list[ComposeItem], backdrop: str, layout: bool) -> list[Part]:
    """상품마다 '이름표 → 사진' 순서로 붙이고, 마지막에 지시문을 둔다."""
    parts: list[Part] = []
    listing = []
    for i, item in enumerate(items, 1):
        label = f"Photo {i}: {CATEGORY_EN[item.category]} — {item.desc}"
        parts += [label, item.image]
        listing.append(f"- {label}")
    parts.append(
        render_prompt(
            "compose",
            items="\n".join(listing),
            layout=layout_lines([it.category for it in items]) if layout else "",
            backdrop=backdrop,
        )
    )
    return parts


def compose(
    client: ImageClient,
    items: list[ComposeItem],
    backdrop: str,
    out: Path,
    ratio: str = "4:5",
    layout: bool = True,
    image_size: str = "2K",
) -> Path:
    parts = compose_parts(items, backdrop, layout)
    return save_image(client.generate(parts, ratio, image_size), out)


# ---------------------------------------------------------------- CLI


def make_client(model: str, fake: bool = False) -> ImageClient:
    return FakeClient() if fake else GeminiClient(resolve_model(model))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prep")
    p.add_argument("--image", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)

    for name in ("extract", "compose"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--model", default="default", help="models.yaml 키 또는 모델 ID")
        p.add_argument("--fake", action="store_true", help="API 없이 가짜 이미지로 흐름만 확인")
        if name == "extract":
            p.add_argument("--image", type=Path, required=True)
            p.add_argument("--category", choices=list(CATEGORY_EN), required=True)
            p.add_argument("--desc", required=True)
        else:
            p.add_argument("--plan", type=Path, required=True)
            p.add_argument("--account", required=True)
            p.add_argument("--ratio", help="생략하면 프로필 visual.feed_ratio")
            p.add_argument("--no-layout", action="store_true", help="카테고리별 배치 지시 빼기")

    args = ap.parse_args(argv)
    if args.cmd == "prep":
        print(prep_image(args.image, args.out))
        return 0

    client = make_client(args.model, args.fake)
    if args.cmd == "extract":
        print(extract(client, args.image, args.category, args.desc, args.out))
    else:
        profile = load_profile(args.account)
        plan = ComposePlan.model_validate(json.loads(args.plan.read_text(encoding="utf-8")))
        out = compose(
            client,
            plan.items,
            profile.visual.backdrop,
            args.out,
            ratio=args.ratio or profile.visual.feed_ratio,
            layout=not args.no_layout,
        )
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
