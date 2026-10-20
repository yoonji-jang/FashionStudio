r"""코디컷에 제목과 계정 이름을 합성한다 (AI 없음, Pillow).

이미지 아래쪽을 살짝 어둡게 깔고, 왼쪽에 포인트 색 세로선을 둔 흰 제목 두 줄과
그 아래 계정 이름을 얹는다.
글자는 폰트로 그리므로 오타 없이 매번 같은 모양이다. 모양은 프로필 `visual` 을 따른다.

사용법:
  python -m tools.overlay --account women --image look.jpg \
      --title "가을 필수템 야상" "고급스럽게 입는 법" --out cover.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from core.profile import REPO_ROOT, Visual, load_profile

FONTS_DIR = REPO_ROOT / "assets" / "fonts"
BASE_WIDTH = 1080  # 글자 크기 설정의 기준 폭. 다른 크기 이미지는 비율대로 키우거나 줄인다
MIN_FONT_RATIO = 0.6  # 긴 제목도 설정 크기의 60% 아래로는 줄이지 않는다


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"폰트가 없습니다: {path} (assets/fonts/README.md 참고)")
    return ImageFont.truetype(str(path), size)


def gradient(size: tuple[int, int], height: float, opacity: float) -> Image.Image:
    """아래로 갈수록 진해지는 검은 그라데이션 (RGBA). 위쪽 경계가 보이지 않게 부드럽게 시작한다."""
    w, h = size
    top = round(h * (1 - height))
    span = max(1, h - top)
    alpha = Image.new("L", (1, h), 0)
    for y in range(top, h):
        t = (y - top) / span
        alpha.putpixel((0, y), round(255 * opacity * t * t * (3 - 2 * t)))  # smoothstep
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.putalpha(alpha.resize((w, h)))
    return layer


def fit_font(lines: list[str], font_name: str, size: int, max_width: int) -> ImageFont.FreeTypeFont:
    """가장 긴 줄이 max_width 안에 들어오도록 글자 크기를 줄인다."""
    min_size = max(1, round(size * MIN_FONT_RATIO))
    while True:
        font = load_font(font_name, size)
        widest = max(font.getlength(line) for line in lines)
        if widest <= max_width or size <= min_size:
            if widest > max_width:
                raise ValueError(f"제목이 너무 깁니다 ({widest:.0f}px > {max_width}px): {lines}")
            return font
        size -= 2


def text_layer(
    size: tuple[int, int],
    items: list[tuple[tuple[float, float], str, ImageFont.FreeTypeFont, str]],
    shadow: float,
    blur: float,
) -> Image.Image:
    """글자와 은은한 그림자를 그린 RGBA 레이어."""
    text = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(text)
    for xy, s, font, color in items:
        draw.text(xy, s, font=font, fill=color, anchor="ls")
    if shadow <= 0:
        return text
    mask = text.getchannel("A").filter(ImageFilter.GaussianBlur(blur))
    shade = Image.new("RGBA", size, (0, 0, 0, 0))
    shade.putalpha(mask.point(lambda a: round(a * shadow)))
    return Image.alpha_composite(shade, text)


def _check_lines(title_lines: list[str], visual: Visual) -> list[str]:
    title_lines = [s.strip() for s in title_lines if s.strip()]
    if not title_lines:
        raise ValueError("제목이 비어 있습니다")
    if len(title_lines) > visual.title_max_lines:
        raise ValueError(f"제목은 {visual.title_max_lines}줄까지입니다: {title_lines}")
    return title_lines


def apply(
    image: Image.Image,
    title_lines: list[str],
    visual: Visual,
    label: str = "",
    label_size: int = 26,
) -> Image.Image:
    """image 에 그라데이션, 포인트 선, 제목, 계정 이름을 합성한 같은 크기의 RGB 이미지.
    label 이 있으면 제목 첫 줄 위에 작게 (여러 벌 게시물 표지의 LOOK 1, 크기는 폭 1080px 기준)."""
    lines = _check_lines(title_lines, visual)
    base = image.convert("RGBA")
    w, h = base.size
    k = w / BASE_WIDTH
    shade = gradient(base.size, visual.gradient_height, visual.gradient_opacity)
    base = Image.alpha_composite(base, shade)

    margin = round(w * visual.title_margin_x)
    width = w - 2 * margin
    font = fit_font(lines, visual.font, round(visual.title_font_size * k), width)
    step = font.size * visual.title_line_spacing
    last_baseline = h * (1 - visual.title_bottom)
    first_baseline = last_baseline - step * (len(lines) - 1)
    items = []
    for i, line in enumerate(lines):
        x = margin
        if visual.title_align == "center":
            x += (width - font.getlength(line)) / 2
        items.append(((x, first_baseline + step * i), line, font, visual.title_color))
    if label:
        label_font = load_font(visual.font, round(label_size * k))
        label_y = first_baseline - font.size * 1.05 - label_font.size * 0.3
        items.append(((margin, label_y), label, label_font, visual.title_color))

    if visual.logo_text:
        logo_font = load_font(visual.logo_font or visual.font, round(visual.logo_font_size * k))
        if visual.logo_align == "center":
            x = (w - logo_font.getlength(visual.logo_text)) / 2
        else:
            x = margin
        y = h * (1 - visual.logo_bottom)
        items.append(((x, y), visual.logo_text, logo_font, visual.logo_color))

    out = Image.alpha_composite(base, text_layer(base.size, items, visual.text_shadow, blur=8 * k))

    if visual.accent_color and visual.title_align == "left":
        # 제목 왼쪽 세로선: 첫 줄 글자 윗부분부터 마지막 줄 기준선 조금 아래까지
        x1 = margin - round(visual.accent_gap * k)
        x0 = x1 - max(1, round(visual.accent_width * k))
        top = first_baseline - font.size * 0.8
        bottom = last_baseline + font.size * 0.14
        box = (x0, round(top), x1 - 1, round(bottom))
        ImageDraw.Draw(out).rectangle(box, fill=visual.accent_color)
    return out.convert("RGB")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="코디컷에 제목·계정 이름 합성")
    parser.add_argument("--account", required=True, help="accounts/<id>")
    parser.add_argument("--image", required=True, type=Path, help="코디컷 이미지")
    parser.add_argument("--title", required=True, nargs="+", help="제목 줄들 (보통 2줄)")
    parser.add_argument("--out", required=True, type=Path, help="결과 JPEG 경로")
    args = parser.parse_args(argv)

    visual = load_profile(args.account).visual
    try:
        result = apply(Image.open(args.image), args.title, visual)
    except (ValueError, FileNotFoundError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.save(args.out, "JPEG", quality=95)
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
