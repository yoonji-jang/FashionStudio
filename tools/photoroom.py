r"""Photoroom 이미지 API 도구.

- cutout: 상품 사진 1장 → 배경 없는 상품 누끼(PNG)
    옷(아우터·상의·하의·원피스)은 Flat Lay 로 모델이 입은 사진을 바닥에 펼친 모양으로 바꾸고,
    가방·신발·소품은 글로 지정한 물건만 잘라낸다(Text-Guided Segmentation).
- backdrop: 프로필 visual.backdrop 문구로 배경 사진 한 장 생성
- refine: 누끼를 실제 상품 사진처럼 다듬음 (Edit With AI). 가려지거나 잘린 부분을 채우고
    다른 물건(옆 가방 끈 등)을 지운다. 신발은 깔창까지 보이는 상세컷처럼 만든다.
- style: 두 상품을 입은 것처럼 합친다 (자켓 속 셔츠, 바지에 끼운 벨트). Edit With AI + 참고 이미지
- finish: 코드로 배치한 누끼 합성본에 AI 그림자 또는 AI 배경을 입힘 (상품 픽셀은 그대로)
- scene: 코드 합성본 전체를 AI가 한 번 더 다듬어 실제 촬영 사진처럼 만듦

사용법:
  python -m tools.photoroom cutout --image in.jpg --category 아우터 --desc "레더자켓" \
      --out a.png
  python -m tools.photoroom backdrop --account women --out backdrop.jpg

API 키는 환경 변수 PHOTOROOM_API_KEY. PHOTOROOM_SANDBOX=1 이면 무료 샌드박스(워터마크)로 호출한다.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
from pathlib import Path
from string import Template
from typing import Literal, Protocol

import httpx
import yaml
from PIL import Image, ImageOps

from core.profile import REPO_ROOT, load_profile

PROMPTS_DIR = REPO_ROOT / "prompts"
REFINE_HINTS_PATH = PROMPTS_DIR / "refine_hints.yaml"
CONFIG_PATH = REPO_ROOT / "config" / "models.yaml"

FLATLAY_CATEGORIES = {"아우터", "상의", "하의", "원피스"}
SEGMENT_NOUN = {"가방": "bag", "신발": "shoe", "소품": "accessory"}

CutoutMethod = Literal["flatlay", "segment"]


class QuotaError(RuntimeError):
    """402: 크레딧·요금제 한도 초과. 더 돌려도 소용없다."""


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["image"]


def render_prompt(name: str, prompts_dir: Path = PROMPTS_DIR, **values: str) -> str:
    """prompts/<name>.md 를 읽어 맨 앞 HTML 주석을 빼고 $변수를 채운다."""
    text = (prompts_dir / f"{name}.md").read_text(encoding="utf-8")
    text = re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.S)
    return " ".join(Template(text).substitute(values).split())


def default_method(category: str) -> CutoutMethod:
    return "flatlay" if category in FLATLAY_CATEGORIES else "segment"


def has_transparency(img: Image.Image) -> bool:
    if img.mode != "RGBA":
        return False
    lo, _ = img.getchannel("A").getextrema()
    return lo < 250


def trim_alpha(img: Image.Image) -> Image.Image:
    """투명한 가장자리를 잘라 상품만 남긴다.
    샌드박스 워터마크가 투명 영역에 옅게 깔리므로 반 이상 불투명한 부분만 상품으로 본다."""
    img = img.convert("RGBA")
    bbox = img.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox()
    return img.crop(bbox) if bbox else img


def crop_fraction(src: Path, box: tuple[float, float, float, float], dest: Path) -> Path:
    """사진 일부만 쓰고 싶을 때 (여러 색이 한 사진에 있는 경우). box 는 0~1 비율."""
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    w, h = img.size
    img.crop((round(box[0] * w), round(box[1] * h), round(box[2] * w), round(box[3] * h))).save(
        dest
    )
    return dest


# ---------------------------------------------------------------- 클라이언트


class EditClient(Protocol):
    def edit(self, fields: dict[str, str], files: dict[str, Path | bytes]) -> bytes: ...


class PhotoroomClient:
    def __init__(
        self,
        api_key: str | None = None,
        sandbox: bool | None = None,
        timeout: float = 180.0,
        transport: httpx.BaseTransport | None = None,
        config: dict | None = None,
    ):
        key = api_key or os.environ.get("PHOTOROOM_API_KEY", "")
        if not key:
            raise RuntimeError("PHOTOROOM_API_KEY 가 없습니다 (GitHub Secrets 또는 환경 변수)")
        if sandbox is None:
            sandbox = os.environ.get("PHOTOROOM_SANDBOX", "") not in ("", "0", "false")
        self.api_key = f"sandbox_{key}" if sandbox and not key.startswith("sandbox_") else key
        self.config = config or load_config()
        self.http = httpx.Client(timeout=timeout, transport=transport)
        self.calls = 0

    def edit(
        self, fields: dict[str, str], files: dict[str, Path | bytes], attempts: int = 3
    ) -> bytes:
        headers = {"x-api-key": self.api_key}
        if fields.get("shadow.mode") == "ai.auto-with-overrides":
            headers["pr-ai-shadows-model-version"] = self.config["shadow_model_version"]
        # 파일이 없어도(배경 생성) multipart 로 보내야 하므로 글자 필드도 파일 목록에 넣는다
        payload: list[tuple[str, tuple]] = [(k, (None, v)) for k, v in fields.items()]
        payload += [
            (k, (Path(v).name, Path(v).read_bytes()) if isinstance(v, Path) else ("image.png", v))
            for k, v in files.items()
        ]
        for i in range(attempts):
            resp = self.http.post(self.config["endpoint"], files=payload, headers=headers)
            self.calls += 1
            if resp.status_code in (429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(5 * 2**i)
                continue
            if resp.status_code == 402:
                raise QuotaError(f"Photoroom 402 (크레딧·요금제 확인): {resp.text[:800]}")
            if resp.status_code >= 400:
                raise RuntimeError(f"Photoroom {resp.status_code}: {resp.text[:800]}")
            return resp.content
        raise RuntimeError("unreachable")


class FakeClient:
    """키 없이 흐름을 확인하는 가짜 클라이언트. 흰 배경을 투명하게 바꾸는 정도만 흉내 낸다."""

    def __init__(self):
        self.calls = 0

    def edit(self, fields: dict[str, str], files: dict[str, Path | bytes]) -> bytes:
        self.calls += 1
        out = io.BytesIO()
        if "imageFromPrompt.prompt" in fields or "editWithAI.mode" in fields:
            if "imageFile" not in files:
                Image.new("RGB", (768, 1024), (205, 200, 194)).save(out, format="PNG")
                return out.getvalue()
            src = files["imageFile"]
            img = Image.open(src if isinstance(src, Path) else io.BytesIO(src)).convert("RGB")
            img.save(out, format="PNG")  # AI 편집: 불투명한 그림을 돌려준다
            return out.getvalue()
        src = files["imageFile"]
        img = Image.open(src if isinstance(src, Path) else io.BytesIO(src)).convert("RGBA")
        if (
            "background.color" not in fields
            and "background.imageFile" not in fields
            and ("background.prompt" not in fields)
        ):
            gray = img.convert("L")
            img.putalpha(gray.point(lambda p: 0 if p > 235 else 255))
        else:
            bg = Image.new("RGBA", img.size, (205, 200, 194, 255))
            img = Image.alpha_composite(bg, img)
        img.save(out, format="PNG")
        return out.getvalue()


# ---------------------------------------------------------------- 작업


def cutout(
    client: EditClient,
    image: Path,
    category: str,
    desc: str,
    out: Path,
    method: CutoutMethod | None = None,
    segment: str | None = None,
    config: dict | None = None,
    negative: str | None = None,
) -> Path:
    """상품 누끼를 만든다. 결과는 투명 배경 PNG, 가장자리는 잘라 둔다."""
    config = config or load_config()
    method = method or default_method(category)
    fields: dict[str, str] = {"export.format": "png"}
    if method == "flatlay":
        fields |= {
            "flatLay.mode": "ai.auto",
            "flatLay.prompt": render_prompt("flatlay", desc=desc),
            "flatLay.size": config["flat_lay_size"],
        }
    else:
        fields |= {
            "segmentation.prompt": segment or SEGMENT_NOUN.get(category, "product"),
            "segmentation.negativePrompt": ", ".join(
                x for x in ("person, hand, leg, skin, text", negative) if x
            ),
        }
    img = Image.open(io.BytesIO(client.edit(fields, {"imageFile": image})))
    if not has_transparency(img.convert("RGBA")):
        # Flat Lay 결과에 배경이 남아 있으면 한 번 더 배경만 지운다
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        img = remove_background(client, buf.getvalue())
    out.parent.mkdir(parents=True, exist_ok=True)
    trim_alpha(img).save(out)
    return out


def backdrop(client: EditClient, description: str, out: Path, config: dict | None = None) -> Path:
    """배경 사진 한 장을 만든다. 계정마다 한 번 만들어 두고 재사용한다."""
    config = config or load_config()
    fields = {
        "imageFromPrompt.prompt": render_prompt("backdrop", backdrop=description),
        "imageFromPrompt.size": config["backdrop_size"],
        "removeBackground": "false",
        "export.format": "png",
    }
    data = client.edit(fields, {})
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.open(io.BytesIO(data)).convert("RGB").save(out, quality=92)
    return out


def on_white(path: Path, margin: float = 0.06) -> bytes:
    """누끼를 흰 정사각형 가운데에 얹는다. AI 편집은 1K 정사각형으로 나오므로 미리 맞춘다."""
    item = Image.open(path).convert("RGBA")
    side = round(max(item.size) * (1 + 2 * margin))
    base = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    base.alpha_composite(item, ((side - item.width) // 2, (side - item.height) // 2))
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def remove_background(client: EditClient, data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(client.edit({"export.format": "png"}, {"imageFile": data})))


def edit_with_ai(
    client: EditClient,
    image: bytes,
    prompt: str,
    references: dict[str, bytes] | None = None,
    extra: dict[str, str] | None = None,
) -> bytes:
    """Edit With AI: 글로 지시해 사진을 고친다. references 는 참고 이미지 (최대 4장)."""
    fields = {
        "removeBackground": "false",
        "editWithAI.mode": "ai.auto",
        "editWithAI.prompt": prompt,
        "export.format": "png",
    } | (extra or {})
    files: dict[str, Path | bytes] = {"imageFile": image}
    for name, data in (references or {}).items():
        files[f"editWithAI.additionalImages.{name}.imageFile"] = data
    return client.edit(fields, files)


def refine_hint(slot: str, path: Path = REFINE_HINTS_PATH) -> str:
    """slot 은 layout 자리 이름 또는 카테고리 (예: 목걸이, 신발)."""
    hints = yaml.safe_load(path.read_text(encoding="utf-8"))
    return hints.get(slot, hints["default"])


def refine(client: EditClient, cut: Path, slot: str, desc: str, out: Path) -> Path:
    """누끼 → 온전한 상품 사진 → 다시 누끼. 가려진 부분은 채우고 다른 물건은 지운다."""
    prompt = render_prompt("refine", desc=desc, hint=refine_hint(slot))
    data = edit_with_ai(client, on_white(cut), prompt)
    out.parent.mkdir(parents=True, exist_ok=True)
    trim_alpha(remove_background(client, data)).save(out)
    return out


StyleMode = Literal["direct", "open", "over", "peek"]


def style(
    client: EditClient,
    base: Path,
    base_desc: str,
    add: Path,
    add_desc: str,
    how: str,
    out: Path,
    mode: StyleMode = "direct",
) -> Path:
    """base 상품에 add 상품을 입힌 모양으로 합친다 (예: 자켓 속 셔츠, 바지에 끼운 벨트).

    direct: base 사진에 add 를 참고 이미지로 넣어 입힌다. 자켓+셔츠는 셔츠를 자켓 위에 붙인 것처럼
            나오기도 한다 (run-9).
    open:   base(아우터)만 먼저 연 뒤 입힌다. 연 사진은 <out>_opened.png 로 남긴다.
            run-11 에서는 자켓이 사라지고 셔츠만 남았다.
    over:   add(셔츠) 사진을 본 이미지로 두고 base(자켓)를 참고 이미지로 걸친다.
    peek:   base(가디건) 사진만 보내고 add 는 글로만 설명해, 깃·소매 끝·밑단처럼 보이는 부분만
            그리게 한다. 셔츠 사진을 주면 셔츠 몸판·소매를 통째로 붙였다 (run-23, 24).
            무늬 없는 기본 이너에만 쓴다 (사진을 안 보내므로 디테일은 설명대로 나온다).
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    values = {"base_desc": base_desc, "add_desc": add_desc, "how": how}
    if mode == "peek":
        data = edit_with_ai(client, on_white(base), render_prompt("style_peek", **values))
    elif mode == "over":
        data = edit_with_ai(
            client, on_white(add), render_prompt("style_over", **values), {"outer": on_white(base)}
        )
    else:
        image = on_white(base)
        if mode == "open":
            image = edit_with_ai(client, image, render_prompt("open_outer", desc=base_desc))
            Image.open(io.BytesIO(image)).save(out.with_name(out.stem + "_opened.png"))
        data = edit_with_ai(
            client, image, render_prompt("style", **values), {"worn": on_white(add)}
        )
    trim_alpha(remove_background(client, data)).save(out)
    return out


def scene(
    client: EditClient,
    composed: Path,
    out: Path,
    size: tuple[int, int],
    backdrop_text: str,
) -> Path:
    """코드 합성본 전체를 실제 촬영한 사진처럼 다듬는다 (그림자·천의 자연스러움).

    AI 편집은 1K 정사각형으로 나와 세로 사진을 보내면 가장자리가 잘린다 (run-13 팔찌).
    그래서 좌우를 배경색으로 채운 정사각형을 보내고, 돌아온 사진에서 원래 비율만큼 가운데를 자른다.
    """
    img = Image.open(composed).convert("RGB")
    w, h = img.size
    side = max(w, h)
    # 빈 옆자리는 배경 가장자리를 거울처럼 이어 붙여 채운다 (단색이면 띠가 남음, run-14)
    square = ImageOps.pad(img, (side, side), color=(0, 0, 0))
    x0, y0 = (side - w) // 2, (side - h) // 2
    if x0:
        square.paste(ImageOps.mirror(img.crop((0, 0, x0, h))), (0, y0))
        square.paste(ImageOps.mirror(img.crop((w - (side - w - x0), 0, w, h))), (x0 + w, y0))
    buf = io.BytesIO()
    square.save(buf, format="PNG")
    data = edit_with_ai(client, buf.getvalue(), render_prompt("scene", backdrop=backdrop_text))
    res = Image.open(io.BytesIO(data)).convert("RGB")
    scale = res.width / side
    cw, ch = round(w * scale), round(h * scale)
    left, top = (res.width - cw) // 2, (res.height - ch) // 2
    res = res.crop((left, top, left + cw, top + ch)).resize(size, Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    res.save(out, quality=92)
    return out


FinishMode = Literal["ai_shadow", "ai_background"]


def finish(
    client: EditClient,
    layered: Path,
    out: Path,
    mode: FinishMode,
    size: tuple[int, int],
    backdrop_image: Path | None = None,
    backdrop_text: str = "",
) -> Path:
    """누끼를 배치한 투명 합성본(layered)에 그림자·배경을 입힌다. 상품 위치는 그대로 둔다."""
    fields = {
        "keepExistingAlphaChannel": "auto",
        "referenceBox": "originalImage",
        "outputSize": f"{size[0]}x{size[1]}",
        "export.format": "png",
    }
    files: dict[str, Path | bytes] = {"imageFile": layered}
    if mode == "ai_shadow":
        if backdrop_image is None:
            raise ValueError("ai_shadow 는 배경 사진이 필요합니다")
        fields |= {"shadow.mode": "ai.auto-with-overrides", "shadow.subjectPoseOverride": "flatlay"}
        files["background.imageFile"] = backdrop_image
    else:
        fields["background.prompt"] = render_prompt("background", backdrop=backdrop_text)
    img = Image.open(io.BytesIO(client.edit(fields, files))).convert("RGBA")
    if has_transparency(img) and backdrop_image is not None:
        # 배경이 비어서 오면 (그림자만 입힌 경우) 배경 사진 위에 직접 얹는다
        base = ImageOps.fit(Image.open(backdrop_image).convert("RGBA"), img.size, Image.LANCZOS)
        img = Image.alpha_composite(base, img)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, quality=92)
    return out


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("cutout")
    p.add_argument("--image", type=Path, required=True)
    p.add_argument("--category", required=True)
    p.add_argument("--desc", required=True)
    p.add_argument("--method", choices=["flatlay", "segment"])
    p.add_argument("--segment", help="segment 방식에서 남길 물건 (영어 단수, 예: bag)")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("backdrop")
    p.add_argument("--account", required=True)
    p.add_argument("--out", type=Path, required=True)
    for p in sub.choices.values():
        p.add_argument("--fake", action="store_true", help="API 없이 흐름만 확인")
    args = ap.parse_args(argv)

    client: EditClient = FakeClient() if args.fake else PhotoroomClient()
    if args.cmd == "cutout":
        print(
            cutout(
                client, args.image, args.category, args.desc, args.out, args.method, args.segment
            )
        )
    else:
        text = load_profile(args.account).visual.backdrop
        print(backdrop(client, text, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
