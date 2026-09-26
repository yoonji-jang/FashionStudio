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
from PIL import Image, ImageFilter, ImageOps

from core.profile import REPO_ROOT

LAYOUT_PATH = REPO_ROOT / "config" / "layout.yaml"


def load_layout(path: Path = LAYOUT_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def slot_rules(categories: list[str], layout: dict) -> dict[str, dict]:
    rules = dict(layout["slots"])
    if "아우터" not in categories:
        rules |= layout.get("no_outer", {})
    return rules


def layer(cutouts: list[tuple[str, Path]], layout: dict) -> Image.Image:
    """투명 캔버스 위에 누끼를 z 순서대로 놓는다."""
    w, h = layout["canvas"]
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rules = slot_rules([c for c, _ in cutouts], layout)
    missing = [c for c, _ in cutouts if c not in rules]
    if missing:
        raise ValueError(f"layout.yaml 에 자리가 없는 카테고리: {missing}")
    for category, path in sorted(cutouts, key=lambda cp: rules[cp[0]].get("z", 0)):
        rule = rules[category]
        item = Image.open(path).convert("RGBA")
        if rule.get("rotate"):
            item = item.rotate(rule["rotate"], expand=True, resample=Image.BICUBIC)
        box = (round(rule["size"][0] * w), round(rule["size"][1] * h))
        item = ImageOps.contain(item, box, Image.LANCZOS)
        cx, cy = rule["center"][0] * w, rule["center"][1] * h
        canvas.alpha_composite(item, (round(cx - item.width / 2), round(cy - item.height / 2)))
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
