"""원본 상품 사진과 생성 결과를 한 장에 나란히 붙인 비교표를 만든다.

위 줄: 원본(참고) 사진들, 아래: 생성 결과들.

사용법:
  python -m tools.compare --refs a.png b.png --results r1.png r2.png --out sheet.jpg \
      --title "A: free layout"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (250, 250, 248)
INK = (40, 40, 40)
PAD = 24


def _font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size)


def _fit(path: Path, box_w: int, box_h: int) -> Image.Image:
    img = Image.open(path).convert("RGB")
    img.thumbnail((box_w, box_h), Image.LANCZOS)
    return img


def contact_sheet(
    refs: list[tuple[str, Path]],
    results: list[tuple[str, Path]],
    out: Path,
    title: str = "",
    ref_size: int = 260,
    result_h: int = 720,
    per_row: int = 5,
) -> Path:
    """refs·results 는 (라벨, 경로) 목록. 라벨은 영문으로 (기본 글꼴에 한글이 없음)."""
    result_w = result_h * 4 // 5
    label_h = 34
    title_h = 56 if title else 0
    ref_row_w = len(refs) * (ref_size + PAD)
    res_row_w = min(len(results), per_row) * (result_w + PAD)
    width = max(ref_row_w, res_row_w, 600) + PAD
    res_rows = (len(results) + per_row - 1) // per_row
    height = (
        PAD
        + title_h
        + (ref_size + label_h + PAD if refs else 0)
        + res_rows * (result_h + label_h + PAD)
    )

    sheet = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(sheet)
    y = PAD
    if title:
        draw.text((PAD, y), title, fill=INK, font=_font(36))
        y += title_h

    small = _font(22)
    if refs:
        for i, (label, path) in enumerate(refs):
            x = PAD + i * (ref_size + PAD)
            img = _fit(path, ref_size, ref_size)
            sheet.paste(img, (x + (ref_size - img.width) // 2, y + (ref_size - img.height) // 2))
            draw.rectangle([x, y, x + ref_size, y + ref_size], outline=(210, 210, 210))
            draw.text((x, y + ref_size + 6), label, fill=INK, font=small)
        y += ref_size + label_h + PAD
        draw.line([PAD, y - PAD // 2, width - PAD, y - PAD // 2], fill=(200, 200, 200), width=2)

    for i, (label, path) in enumerate(results):
        row, col = divmod(i, per_row)
        x = PAD + col * (result_w + PAD)
        yy = y + row * (result_h + label_h + PAD)
        img = _fit(path, result_w, result_h)
        sheet.paste(img, (x + (result_w - img.width) // 2, yy))
        draw.text((x, yy + result_h + 6), label, fill=INK, font=small)

    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--refs", type=Path, nargs="*", default=[])
    ap.add_argument("--results", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--title", default="")
    args = ap.parse_args(argv)
    refs = [(p.stem, p) for p in args.refs]
    results = [(p.stem, p) for p in args.results]
    print(contact_sheet(refs, results, args.out, args.title))
    return 0


if __name__ == "__main__":
    sys.exit(main())
