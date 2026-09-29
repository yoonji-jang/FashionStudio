r"""상품 누끼를 배치해 코디컷을 만든다 (AI 없음, Pillow).

상품 픽셀은 누끼 그대로 쓰므로 색·모양·디테일이 원본과 같다.
배치는 config/layout.yaml 을 따른다.

사용법:
  python -m tools.compose --cutout 아우터=a.png --cutout 하의=b.png ... \
      --backdrop backdrop.jpg --out look.jpg [--layered-out layered.png]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from PIL import Image, ImageChops, ImageFilter, ImageOps

from core.profile import REPO_ROOT
from tools.photoroom import trim_alpha

LAYOUT_PATH = REPO_ROOT / "config" / "layout.yaml"


def load_layout(path: Path = LAYOUT_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def slot_rules(categories: list[str], layout: dict) -> dict[str, dict]:
    rules = dict(layout["slots"])
    if "아우터" not in categories:
        rules |= layout.get("no_outer", {})
    elif "상의" in categories:
        # 상의를 아우터에 입히지 못해 따로 놓을 때: 아우터 뒤에 가려지지 않게 옆에 둔다
        rules |= layout.get("outer_and_top", {})
    return rules


def real_length(category: str, text: str, layout: dict) -> float | None:
    """상품 이름·설명에 든 말(롱, 크롭 …)로 실제 기장(cm)을 정한다. 맞는 말이 없으면 None."""
    low = text.lower()
    for word, cm in (layout.get("lengths", {}).get(category) or {}).items():
        if str(word).lower() in low:
            return float(cm)
    return None


def fit_real_size(item: Image.Image, rule: dict, layout: dict) -> Image.Image:
    """실제 치수(cm)에 맞춰 크기를 정한다. 폭이 max_width 를 넘으면 더 줄인다."""
    target = rule["cm"] * layout["px_per_cm"]
    side = item.height if rule.get("measure", "height") == "height" else max(item.size)
    scale = target / side
    max_w = rule.get("max_width", 0.95) * layout["canvas"][0]
    scale = min(scale, max_w / item.width)
    size = (max(1, round(item.width * scale)), max(1, round(item.height * scale)))
    return item.resize(size, Image.LANCZOS)


def _overlaps(canvas: Image.Image, item: Image.Image, pos: tuple[int, int]) -> bool:
    sheet = Image.new("L", canvas.size, 0)
    sheet.paste(item.getchannel("A").point(lambda a: 255 if a > 128 else 0), pos)
    body = canvas.getchannel("A").point(lambda a: 255 if a > 128 else 0)
    return ImageChops.multiply(body, sheet).getbbox() is not None


def layer(cutouts: list[tuple[str, Path]], layout: dict) -> Image.Image:
    """투명 캔버스 위에 누끼를 실제 비율로 z 순서대로 놓는다.
    avoid_overlap 자리(가방·신발·주얼리)는 먼저 놓인 옷과 겹치면 바깥쪽으로 밀고, 여백까지 밀어도
    겹치면 조금씩 줄인다."""
    w, h = layout["canvas"]
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rules = slot_rules([c for c, _ in cutouts], layout)
    missing = [c for c, _ in cutouts if c not in rules]
    if missing:
        raise ValueError(f"layout.yaml 에 자리가 없는 카테고리: {missing}")
    m = layout.get("margin", 0.02) * w
    for category, path in sorted(cutouts, key=lambda cp: rules[cp[0]].get("z", 0)):
        rule = rules[category]
        item = fit_real_size(trim_alpha(Image.open(path)), rule, layout)
        if rule.get("rotate"):
            item = item.rotate(rule["rotate"], expand=True, resample=Image.BICUBIC)
        cx0, cy0 = rule["center"][0] * w, rule["center"][1] * h
        away = -1 if cx0 < w / 2 else 1  # 캔버스 가운데에서 멀어지는 방향
        for _ in range(60):
            # 가로로 긴 상품(가방 등)이 캔버스 밖으로 잘리지 않게 여백 안으로 밀어 넣는다
            cx = min(max(cx0, m + item.width / 2), w - m - item.width / 2)
            cy = min(max(cy0, m + item.height / 2), h - m - item.height / 2)
            pos = (round(cx - item.width / 2), round(cy - item.height / 2))
            if not rule.get("avoid_overlap") or not _overlaps(canvas, item, pos):
                break
            at_edge = (
                (cx - item.width / 2 <= m + 1) if away < 0 else (cx + item.width / 2 >= w - m - 1)
            )
            if at_edge:
                size = (max(1, round(item.width * 0.95)), max(1, round(item.height * 0.95)))
                item = item.resize(size, Image.LANCZOS)
            else:
                cx0 = cx + away * 8
        # 캔버스 밖으로 조금 나가도 되도록 (paste 는 음수 좌표 허용) 한 겹 만들어 겹친다
        sheet = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sheet.paste(item, pos)
        canvas.alpha_composite(sheet)
    return canvas


def fill_backdrop(path: Path, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(Image.open(path).convert("RGB"), size, Image.LANCZOS)


def with_code_shadow(layered: Image.Image, backdrop: Image.Image, shadow: dict) -> Image.Image:
    """누끼 모양 그대로 흐린 그림자를 깔고 그 위에 상품을 올린다."""
    alpha = layered.getchannel("A")
    shade = Image.new("L", layered.size, 0)
    shade.paste(alpha.point(lambda a: round(a * shadow["opacity"])), tuple(shadow["offset"]))
    shade = shade.filter(ImageFilter.GaussianBlur(shadow["blur"]))
    out = backdrop.convert("RGBA")
    out.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", layered.size, 0).split(), shade)))
    out.alpha_composite(layered)
    return out.convert("RGB")


def compose(
    cutouts: list[tuple[str, Path]],
    backdrop: Path,
    out: Path,
    layered_out: Path | None = None,
    layout: dict | None = None,
) -> Path:
    layout = layout or load_layout()
    layered = layer(cutouts, layout)
    if layered_out:
        layered_out.parent.mkdir(parents=True, exist_ok=True)
        layered.save(layered_out)
    result = with_code_shadow(layered, fill_backdrop(backdrop, layered.size), layout["shadow"])
    out.parent.mkdir(parents=True, exist_ok=True)
    result.save(out, quality=92)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--cutout", action="append", required=True, help="카테고리=경로")
    ap.add_argument("--backdrop", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--layered-out", type=Path)
    args = ap.parse_args(argv)
    cutouts = [(c, Path(p)) for c, p in (s.split("=", 1) for s in args.cutout)]
    print(compose(cutouts, args.backdrop, args.out, args.layered_out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
