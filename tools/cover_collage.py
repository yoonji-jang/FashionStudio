r"""여러 벌 게시물 표지: 벌 전부를 한 배경 위에 모은 콜라주 (AI 없음, 2026-10-07 사용자).

벌마다 고른 코디컷에서 바닥을 걷어 옷만 오려 내고(OpenCV grabCut), 결이 고운 단색 배경 위에
위 줄·아래 줄로 놓는다. 두 줄 사이 가운데에 박스 없는 흰 제목과 포인트 세로선, 왼쪽 아래 계정 이름.
출처·상품 정보는 넣지 않는다 (사용자). 모양은 profile `looks.cover_collage` 와 `visual`.

사용법 (시안 확인용):
  python -m tools.cover_collage --account women --title "첫 줄" "둘째 줄" \
      --out out/cover.jpg looks/1.jpg looks/2.jpg ...
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from core.profile import Profile, load_profile
from tools import overlay
from tools.overlay import load_font

WORK = 720  # 오려 내기는 이 폭으로 줄여서 계산하고 마스크만 원래 크기로 늘린다
MIN_AREA = 0.04  # 옷 부분이 이보다 작으면 오려 내기 실패로 본다


def _lab(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)


def _floor(lab: np.ndarray) -> np.ndarray:
    """바닥 색 판: 테두리에서 시작해 바닥 같은 픽셀로 2차 곡면을 맞춘다 (옷에 끌려가지 않게)."""
    h, w = lab.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xx, yy = xx / w, yy / h
    a = np.stack([np.ones_like(xx), xx, yy, xx * xx, yy * yy, xx * yy], -1).reshape(-1, 6)
    floor = np.zeros((h, w), bool)
    b = max(4, round(w * 0.04))
    floor[:b], floor[-b:], floor[:, :b], floor[:, -b:] = True, True, True, True
    floor = floor.reshape(-1)
    px = lab.reshape(-1, 3)
    fit = px
    for _ in range(4):
        coef, *_ = np.linalg.lstsq(a[floor][::7], px[floor][::7], rcond=None)
        fit = a @ coef
        d = px - fit
        dist = np.sqrt((d[:, 0] * 0.5) ** 2 + (d[:, 1] * 1.6) ** 2 + (d[:, 2] * 1.6) ** 2)
        floor = dist < 9
    return fit.reshape(h, w, 3)


def cutout(path: Path) -> Image.Image:
    """코디컷에서 옷만 남긴 RGBA (옷 범위로 자름). 바닥이 고른 펼친 코디컷을 전제로 한다.
    바닥과 색이 다른 곳·윤곽선을 옷 후보로 grabCut 을 돌리고, 테두리에 닿는 조각(창빛)과
    가장자리에 남은 바닥·그림자 조각을 걷어 낸다."""
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"코디컷을 열 수 없습니다: {path}")
    h0, w0 = img.shape[:2]
    small = cv2.resize(img, (WORK, round(h0 * WORK / w0)), interpolation=cv2.INTER_AREA)
    lab = _lab(small)
    d = lab - _floor(lab)
    # 밝기 차이(그림자)는 덜, 색 차이는 더 본다
    dist = np.sqrt((d[..., 0] * 0.6) ** 2 + (d[..., 1] * 1.6) ** 2 + (d[..., 2] * 1.6) ** 2)
    chroma = np.sqrt(d[..., 1] ** 2 + d[..., 2] ** 2)
    shadow = (d[..., 0] < 0) & (d[..., 0] > -45) & (chroma < 4.5)
    edges = cv2.dilate(cv2.Canny(cv2.GaussianBlur(small, (3, 3), 0), 40, 110), np.ones((3, 3)))
    fg = (((dist > 16) | (edges > 0)) & ~shadow).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    mask = np.full(fg.shape, cv2.GC_PR_BGD, np.uint8)
    mask[fg > 0] = cv2.GC_PR_FGD
    strong = cv2.erode((dist > 30).astype(np.uint8), np.ones((5, 5), np.uint8))
    mask[strong > 0] = cv2.GC_FGD
    mask[cv2.dilate(fg, np.ones((25, 25), np.uint8)) == 0] = cv2.GC_BGD
    mask[shadow & (strong == 0)] = cv2.GC_BGD
    fg_px = np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD)).mean()
    if fg_px < MIN_AREA or fg_px > 0.95:
        raise ValueError(f"옷을 오려 내지 못했습니다 (바닥과 옷을 가르지 못함): {path}")
    bgm, fgm = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(small, mask, None, bgm, fgm, 5, cv2.GC_INIT_WITH_MASK)
    m = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)

    # 작은 조각과 테두리에 닿는 조각(창으로 든 빛 등)을 빼고 구멍을 메운다
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m)
    keep = np.zeros_like(m)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        touches = x <= 1 or y <= 1 or x + w >= m.shape[1] - 1 or y + h >= m.shape[0] - 1
        if area > m.size * 0.0012 and not touches:
            keep[labels == i] = 255
    contours, _ = cv2.findContours(keep, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(keep)
    cv2.drawContours(filled, contours, -1, 255, -1)
    # 가방 손잡이 안쪽처럼 바닥색이 확실한 구멍은 다시 뚫는다
    hole = ((filled > 0) & (dist < 7) & (keep == 0)).astype(np.uint8)
    filled[cv2.morphologyEx(hole, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)) > 0] = 0
    # 가장자리에 남은 바닥·그림자 조각 (어두운 배경에서 회색 테두리로 보임)
    rim = (filled > 0) & (cv2.erode(filled, np.ones((17, 17), np.uint8)) == 0)
    filled[rim & ((dist < 13) | (shadow & (dist < 22)))] = 0
    filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    filled = cv2.erode(filled, np.ones((2, 2), np.uint8))
    if (filled > 0).mean() < MIN_AREA:
        raise ValueError(f"옷을 오려 내지 못했습니다: {path}")

    alpha = cv2.GaussianBlur(
        cv2.resize(filled, (w0, h0), interpolation=cv2.INTER_LINEAR), (3, 3), 0
    )
    rgba = cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)
    rgba[..., 3] = alpha
    out = Image.fromarray(rgba)
    return out.crop(out.getbbox())


def _rgb(color: str) -> np.ndarray:
    return np.array([int(color[i : i + 2], 16) for i in (1, 3, 5)], np.float32)


def backdrop(size: tuple[int, int], top: str, bottom: str, texture: float) -> Image.Image:
    """위에서 아래로 살짝 어두워지는 단색 + 고운 결 (콘크리트·종이 느낌). 매번 같은 결."""
    w, h = size
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    img = _rgb(top) * (1 - t) + _rgb(bottom) * t
    img = np.repeat(img, w, axis=1)
    if texture:
        rng = np.random.default_rng(3)
        n = rng.normal(0, 1, (h // 3, w // 3)).astype(np.float32)
        grain = Image.fromarray(((n * 32 + 128).clip(0, 255)).astype(np.uint8))
        grain = grain.resize((w, h), Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(1))
        img = img + (np.asarray(grain, np.float32)[..., None] - 128) / 32 * texture
    return Image.fromarray(img.clip(0, 255).astype(np.uint8)).convert("RGBA")


def place(
    canvas: Image.Image, cut: Image.Image, cx: float, cy: float, h: int, w_max: int, shadow: float
) -> None:
    """cut 을 높이 h(폭은 w_max 까지)로 줄여 가운데 (cx, cy) 에 놓고 아래로 은은한 그림자."""
    k = min(h / cut.height, w_max / cut.width)
    size = (max(1, round(cut.width * k)), max(1, round(cut.height * k)))
    c = cut.resize(size, Image.Resampling.LANCZOS)
    x, y = round(cx - size[0] / 2), round(cy - size[1] / 2)
    if shadow:
        sh = Image.new("RGBA", size, (30, 26, 22, 0))
        a = c.getchannel("A").point(lambda v: round(v * shadow))
        sh.putalpha(a.filter(ImageFilter.GaussianBlur(max(1, h // 50))))
        canvas.alpha_composite(sh, (x + max(1, h // 90), y + max(1, h // 50)))
    canvas.alpha_composite(c, (x, y))


def row_counts(n: int) -> tuple[int, int]:
    """위 줄·아래 줄 벌 수 (6 → 3·3, 5 → 3·2)."""
    top = math.ceil(n / 2)
    return top, n - top


def compose(
    cuts: list[Image.Image],
    title_lines: list[str],
    profile: Profile,
    size: tuple[int, int] = (1080, 1350),
    top: float = 0.0,
) -> Image.Image:
    """콜라주 표지 RGB. size 가 4:5 보다 길면(릴스 9:16) 배경만 늘리고 벌·글자는 위에서
    top 픽셀 아래의 4:5 자리에 둔다 (4:5 장을 릴스에 올렸을 때와 같은 화면 자리)."""
    cc = profile.looks.cover_collage
    v = profile.visual
    w, h = size
    fh = w * 5 / 4  # 벌·글자를 두는 4:5 자리
    k = w / overlay.BASE_WIDTH
    canvas = backdrop(size, cc.top_color, cc.bottom_color, cc.texture)

    look_h = round(fh * cc.look_height)
    w_max = round(w * cc.max_width)
    counts = row_counts(len(cuts))
    i = 0
    for row, count in zip(cc.rows, counts, strict=True):
        for j in range(count):
            cx = w * (j + 0.5) / count
            place(canvas, cuts[i], cx, top + fh * row, look_h, w_max, cc.shadow)
            i += 1

    lines = [s.strip() for s in title_lines if s.strip()]
    margin = round(w * v.title_margin_x)
    font = overlay.fit_font(lines, v.font, round(cc.title_size * k), w - 2 * margin)
    step = font.size * cc.title_line_spacing
    first = top + fh * cc.title_top
    items = [((margin, first + step * n), s, font, cc.title_color) for n, s in enumerate(lines)]
    if v.logo_text:
        logo = load_font(v.logo_font or v.font, round(cc.logo_size * k))
        items.append(((margin, top + fh * (1 - cc.logo_bottom)), v.logo_text, logo, cc.logo_color))
    layer = overlay.text_layer(size, items, cc.title_shadow, blur=8 * k)
    canvas.alpha_composite(layer)
    if v.accent_color:
        x1 = margin - round(v.accent_gap * k)
        x0 = x1 - max(1, round(v.accent_width * k))
        last = first + step * (len(lines) - 1)
        box = (x0, round(first - font.size * 0.8), x1 - 1, round(last + font.size * 0.14))
        ImageDraw.Draw(canvas).rectangle(box, fill=v.accent_color)
    return canvas.convert("RGB")


def render(
    images: list[Path],
    title_lines: list[str],
    profile: Profile,
    size: tuple[int, int] = (1080, 1350),
    top: float = 0.0,
) -> Image.Image:
    return compose([cutout(p) for p in images], title_lines, profile, size, top)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="여러 벌 콜라주 표지 (시안 확인용)")
    ap.add_argument("--account", required=True)
    ap.add_argument("--title", required=True, nargs="+", help="제목 줄들")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("images", nargs="+", type=Path, help="벌마다 고른 코디컷")
    args = ap.parse_args(argv)
    try:
        img = render(args.images, args.title, load_profile(args.account))
    except (ValueError, FileNotFoundError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    img.save(args.out, "JPEG", quality=93)
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
