r"""게시물 한 개의 캐러셀 슬라이드를 만든다 (AI 없음, Pillow).

기본 구성 (프로필 `feed.slides` 로 순서·종류를 바꿀 수 있다):
  1장 표지: 코디컷 + 제목 (tools.overlay)
  상품 장: 상품 한 장씩, 누끼(또는 원본 사진)·쇼핑몰·상품명·가격·코멘트
  마지막 장: 코디 전체 목록과 저장·팔로우 문구
모양은 프로필 `feed` 와 `visual` 을 따른다. 상품 정보는 재료 파일(FeedSpec)에 적은 값이 우선이고,
product 로 지정하면 data/<계정>/products.json 의 값을 기본으로 쓴다.

사용법:
  python -m tools.feed --spec samples/women/feed_leather.yaml --out out/2026-09-27/slides
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from pydantic import ValidationError

from core.models import FeedItem, FeedSpec
from core.profile import REPO_ROOT, Feed, Profile, account_dir, load_profile
from core.store import Store
from tools import overlay
from tools.compose import load_layout
from tools.overlay import load_font
from tools.photoroom import trim_alpha

MAX_SLIDES = 10  # 인스타 캐러셀 한도
BASE = overlay.BASE_WIDTH


@dataclass
class Card:
    """슬라이드에 그릴 상품 한 개 (재료 파일 + 상품 창고를 합친 값)."""

    category: str
    name: str
    shop: str | None
    price: int | None
    comment: str
    image: Image.Image | None  # 누끼면 RGBA, 사진이면 RGB
    is_cutout: bool


# ---------- 상품 정보 ----------

_BRACKETS = re.compile(r"\[[^\]]*\]|【[^】]*】|\([^)]*\)|<[^>]*>")
_CODE = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9-]{5,}$")  # SPJLG49W06, mjk467 같은 품번


def drawable(text: str) -> str:
    """폰트에 없는 이모지 등을 뺀다."""
    return "".join(
        ch
        for ch in text
        if ord(ch) < 0x2000 or 0xAC00 <= ord(ch) <= 0xD7A3 or 0x3000 <= ord(ch) <= 0x318F
    )


def clean_name(raw: str) -> str:
    """쇼핑몰 상품명에서 광고 문구·품번·검색용 단어를 뺀 짧은 이름."""
    s = drawable(raw)
    s = _BRACKETS.sub(" ", s)
    s = re.split(r"\s[-/|#]\s?|#", s)[0]  # " - 3COL ...", "/ 크로스백", "#세미부츠컷" 뒤는 검색용
    s = s.replace("_", " ")
    words = [w for w in s.split() if not _CODE.match(w) and w.lower() not in {"made", "made."}]
    return " ".join(words).strip() or drawable(raw).strip()


def price_text(price: int) -> str:
    return f"{price:,}원"


def fetch_photo(image_url: str, dest: Path) -> Path:
    """상품 창고의 대표 사진을 받는다 (tools.scout 로만 접근)."""
    from tools import scout

    if not dest.exists():
        with scout.client() as c:
            data = scout.fetch_bytes(c, scout.normalize_image_url(image_url))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return dest


def build_cards(
    spec: FeedSpec,
    feed: Feed,
    root: Path = REPO_ROOT,
    store: Store | None = None,
    photo_dir: Path | None = None,
) -> list[Card]:
    """photo_dir 를 주면 누끼·사진이 없는 상품은 상품 창고의 첫 사진을 받아 쓴다."""
    products = {}
    if any(it.product for it in spec.items):
        products = {p.id: p for p in (store or Store(spec.account)).products()}
    cards = []
    for it in spec.items:
        base = products.get(it.product) if it.product else None
        if it.product and base is None:
            raise ValueError(f"상품 창고에 없는 상품입니다: {it.product}")
        category = it.category or (base.category if base else None)
        name = it.name or (clean_name(base.name) if base else None)
        if not category or not name:
            raise ValueError(f"category 와 name 이 필요합니다: {it.model_dump(exclude_none=True)}")
        if not (it.cutout or it.photo) and base and photo_dir:
            dest = photo_dir / f"{base.id.split(':')[-1]}.jpg"
            it = it.model_copy(update={"photo": str(fetch_photo(str(base.image_urls[0]), dest))})
        image, is_cutout = _item_image(it, feed, root)
        if image is None:
            raise ValueError(f"{name}: cutout 이나 photo 가 필요합니다 (상품 창고 사진은 --photos)")
        cards.append(
            Card(
                category=category,
                name=drawable(name),
                shop=it.shop or (base.shop if base else None),
                price=it.price if it.price is not None else (base.price if base else None),
                comment=drawable(it.comment),
                image=image,
                is_cutout=is_cutout,
            )
        )
    return cards


def _item_image(it: FeedItem, feed: Feed, root: Path) -> tuple[Image.Image | None, bool]:
    order = [("cutout", it.cutout), ("photo", it.photo)]
    if feed.product_image == "photo":
        order.reverse()
    for kind, path in order:
        if path:
            img = Image.open(root / path)
            if kind == "cutout":
                return trim_alpha(img.convert("RGBA")), True
            return img.convert("RGB"), False
    return None, False


# ---------- 그리기 도구 ----------


class Painter:
    def __init__(self, profile: Profile, size: tuple[int, int]):
        self.p = profile
        self.f = profile.feed
        self.v = profile.visual
        self.w, self.h = size
        self.k = self.w / BASE
        self.m = round(self.w * self.f.margin_x)

    def px(self, n: float) -> int:
        return round(n * self.k)

    def bold(self, size: int):
        return load_font(self.v.font, self.px(size))

    def body(self, size: int):
        return load_font(self.f.body_font, self.px(size))

    def background(self) -> Image.Image:
        if self.f.background_image:
            path = account_dir(self.p.id) / self.f.background_image
            return ImageOps.fit(Image.open(path).convert("RGB"), (self.w, self.h), Image.LANCZOS)
        return Image.new("RGB", (self.w, self.h), self.f.background)

    def accent_bar(self, draw: ImageDraw.ImageDraw, top: float, bottom: float) -> None:
        """표지 제목과 같은 왼쪽 세로선."""
        if not self.v.accent_color:
            return
        x1 = self.m - self.px(self.v.accent_gap)
        x0 = x1 - max(1, self.px(self.v.accent_width))
        draw.rectangle((x0, round(top), x1 - 1, round(bottom)), fill=self.v.accent_color)

    def header(self, draw: ImageDraw.ImageDraw, left: list[tuple[str, object, str]]) -> None:
        """위쪽 한 줄: 왼쪽에 (글자, 폰트, 색) 들, 오른쪽에 계정 이름."""
        y = self.h * 0.075
        x = self.m
        for text, font, color in left:
            draw.text((x, y), text, font=font, fill=color, anchor="ls")
            x += font.getlength(text) + self.px(16)
        if self.v.logo_text:
            font = self.body(self.v.logo_font_size)
            draw.text(
                (self.w - self.m, y),
                self.v.logo_text,
                font=font,
                fill=self.f.sub_color,
                anchor="rs",
            )


def wrap(text: str, font, width: float, max_lines: int) -> list[str]:
    """단어 단위로 줄을 나눈다. 한 단어가 너무 길면 글자 단위로 자르고, 넘치면 … 로 끝낸다."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if font.getlength(cand) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = word
        while font.getlength(cur) > width:
            cut = len(cur)
            while cut > 1 and font.getlength(cur[:cut]) > width:
                cut -= 1
            lines.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and font.getlength(last + "…") > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def drop_shadow(img: Image.Image, blur: float, opacity: float, offset: tuple[int, int]):
    """누끼 모양 그대로의 흐린 그림자 레이어 (img 보다 blur*3 씩 큰 RGBA)와 상품 위치."""
    pad = round(blur * 3)
    size = (img.width + 2 * pad, img.height + 2 * pad)
    alpha = Image.new("L", size, 0)
    alpha.paste(
        img.getchannel("A").point(lambda a: round(a * opacity)), (pad + offset[0], pad + offset[1])
    )
    alpha = alpha.filter(ImageFilter.GaussianBlur(blur))
    shade = Image.new("RGBA", size, (0, 0, 0, 0))
    shade.putalpha(alpha)
    return shade, (pad, pad)


def rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def place_image(
    canvas: Image.Image,
    card: Card,
    box: tuple[int, int, int, int],
    k: float,
    shadow: bool = True,
    fill: float = 1.0,
) -> None:
    """box 안 가운데에 상품을 비율 그대로 놓는다. fill 은 box 대비 최대 크기."""
    if card.image is None:
        return
    x0, y0, x1, y1 = box
    bw, bh = round((x1 - x0) * fill), round((y1 - y0) * fill)
    img = ImageOps.contain(card.image, (bw, bh), Image.LANCZOS)
    if not card.is_cutout:
        img = rounded(img, round(18 * k))
    x = x0 + (x1 - x0 - img.width) // 2
    y = y0 + (y1 - y0 - img.height) // 2
    if shadow:
        blur, opacity = (16 * k, 0.28) if card.is_cutout else (22 * k, 0.18)
        shade, (px, py) = drop_shadow(img, blur, opacity, (round(8 * k), round(14 * k)))
        canvas.alpha_composite(shade, (x - px, y - py))
    canvas.alpha_composite(img, (x, y))


def fill_ratio(card: Card, layout: dict | None = None) -> float:
    if not card.is_cutout:
        return 1.0
    return (layout or load_layout()).get("feed_fill", {}).get(card.category, 1.0)


# ---------- 슬라이드 ----------


def cover_slide(spec: FeedSpec, profile: Profile, size: tuple[int, int], root: Path) -> Image.Image:
    img = ImageOps.fit(Image.open(root / spec.cover).convert("RGB"), size, Image.LANCZOS)
    if spec.cover_has_title:
        return img
    return overlay.apply(img, spec.title_lines, profile.visual)


def product_slide(card: Card, index: int, profile: Profile, size: tuple[int, int]) -> Image.Image:
    pt = Painter(profile, size)
    f = pt.f
    canvas = pt.background().convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    width = pt.w - 2 * pt.m

    left = []
    if f.show_index:
        left.append((f"{index:02d}", pt.bold(f.index_size), pt.v.accent_color or f.text_color))
    left.append((card.category, pt.body(f.index_size), f.sub_color))
    pt.header(draw, left)

    # 아래 글자 묶음의 높이를 먼저 재고, 남은 위쪽을 상품 사진 자리로 쓴다
    shop_font, name_font = pt.body(f.shop_size), pt.bold(f.name_size)
    price_font, comment_font = pt.bold(f.price_size), pt.body(f.comment_size)
    name_lines = wrap(card.name, name_font, width, f.name_max_lines)
    comment_lines = (
        wrap(card.comment, comment_font, width, f.comment_max_lines) if card.comment else []
    )
    name_step = name_font.size * 1.28
    comment_step = comment_font.size * 1.5
    show_shop = f.show_shop and card.shop
    show_price = f.show_price and card.price is not None
    rows: list[tuple[str, float, object, str]] = []  # (글자, 기준선까지 간격, 폰트, 색)
    if show_shop:
        rows.append((card.shop, shop_font.size, shop_font, f.sub_color))
    for i, line in enumerate(name_lines):
        gap = name_font.size * (1.25 if show_shop and i == 0 else 1.0) if i == 0 else name_step
        rows.append((line, gap, name_font, f.text_color))
    if show_price:
        rows.append((price_text(card.price), price_font.size * 1.55, price_font, f.text_color))
    for i, line in enumerate(comment_lines):
        rows.append(
            (
                line,
                comment_font.size * 2.0 if i == 0 else comment_step,
                comment_font,
                f.comment_color,
            )
        )
    block = sum(g for _, g, _, _ in rows) + comment_font.size * 0.3
    # 넘겨 볼 때 글자 시작 위치가 같도록 고정하고, 글자가 많을 때만 위로 올린다
    bottom = pt.h * (1 - 0.075)
    y = min(pt.h * f.text_top, bottom - block)

    top_img, bottom_img = pt.h * 0.115, y - pt.px(56)
    box = (pt.m, round(top_img), pt.w - pt.m, round(bottom_img))
    place_image(canvas, card, box, pt.k, fill=fill_ratio(card))

    name_top = name_bottom = None
    for text, gap, font, color in rows:
        y += gap
        draw.text((pt.m, y), text, font=font, fill=color, anchor="ls")
        if font is name_font:
            name_top = y - font.size * 0.8 if name_top is None else name_top
            name_bottom = y + font.size * 0.14
    if name_top is not None:
        pt.accent_bar(draw, name_top, name_bottom)
    return canvas.convert("RGB")


def bookmark_icon(draw: ImageDraw.ImageDraw, x: float, baseline: float, h: float, color: str):
    """저장 아이콘 (책갈피 모양)."""
    w = h * 0.72
    top = baseline - h * 0.92
    pts = [(x, top), (x + w, top), (x + w, top + h), (x + w / 2, top + h * 0.72), (x, top + h)]
    draw.polygon(pts, outline=color, width=max(2, round(h * 0.1)))


def summary_slide(cards: list[Card], spec: FeedSpec, profile: Profile, size: tuple[int, int]):
    pt = Painter(profile, size)
    f, v = pt.f, pt.v
    canvas = pt.background().convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    width = pt.w - 2 * pt.m

    pt.header(
        draw,
        [
            ("LOOK", pt.bold(f.index_size), v.accent_color or f.text_color),
            (f"{len(cards)} ITEMS", pt.body(f.index_size), f.sub_color),
        ],
    )

    head_font = pt.bold(f.heading_size)
    y = pt.h * 0.075 + head_font.size * 1.9
    draw.text((pt.m, y), drawable(f.heading), font=head_font, fill=f.text_color, anchor="ls")
    pt.accent_bar(draw, y - head_font.size * 0.8, y + head_font.size * 0.14)
    sub_font = pt.body(f.shop_size)
    for line in wrap(drawable(" ".join(spec.title_lines)), sub_font, width, 2):
        y += sub_font.size * 1.55
        draw.text((pt.m, y), line, font=sub_font, fill=f.sub_color, anchor="ls")

    # 맨 아래 문구
    cta_font = pt.body(f.cta_size)
    cta = [drawable(s.replace("{logo}", v.logo_text)) for s in f.cta_lines]
    cta_step = cta_font.size * 1.6
    cta_top = pt.h * (1 - 0.075) - cta_step * max(0, len(cta) - 1)

    # 상품 목록
    list_top = y + pt.px(52)
    list_bottom = cta_top - cta_font.size - pt.px(64)
    if f.show_total:
        list_bottom -= pt.px(64)
    row_h = min(pt.px(150), (list_bottom - list_top) / max(1, len(cards)))
    thumb = round(row_h - pt.px(22))
    name_font, meta_font = pt.bold(f.row_name_size), pt.body(f.row_meta_size)
    price_font = pt.bold(f.row_name_size - 2)
    line_color = _mix(f.background, f.sub_color, 0.35)
    tile = _mix(f.background, "#FFFFFF", 0.55)
    for i, card in enumerate(cards):
        top = list_top + row_h * i
        ty = round(top + (row_h - thumb) / 2)
        box = Image.new("RGBA", (thumb, thumb), tile)
        canvas.alpha_composite(rounded(box, pt.px(14)), (pt.m, ty))
        pad = pt.px(10)
        place_image(
            canvas,
            card,
            (pt.m + pad, ty + pad, pt.m + thumb - pad, ty + thumb - pad),
            pt.k,
            shadow=False,
        )
        tx = pt.m + thumb + pt.px(28)
        price = price_text(card.price) if f.show_price and card.price is not None else ""
        price_w = price_font.getlength(price) + pt.px(24) if price else 0
        name = wrap(card.name, name_font, pt.w - pt.m - tx - price_w, 1)[0]
        mid = top + row_h / 2
        meta = card.category + (f" · {card.shop}" if f.show_shop and card.shop else "")
        draw.text((tx, mid - pt.px(6)), meta, font=meta_font, fill=f.sub_color, anchor="ls")
        draw.text(
            (tx, mid + name_font.size * 0.95), name, font=name_font, fill=f.text_color, anchor="ls"
        )
        if price:
            draw.text(
                (pt.w - pt.m, mid + name_font.size * 0.95),
                price,
                font=price_font,
                fill=f.text_color,
                anchor="rs",
            )
        if i < len(cards) - 1:
            ly = round(top + row_h)
            draw.line((tx, ly, pt.w - pt.m, ly), fill=line_color, width=max(1, pt.px(1.5)))

    if f.show_total:
        total = sum(c.price or 0 for c in cards)
        ty = list_top + row_h * len(cards) + pt.px(64)
        draw.line(
            (pt.m, ty - pt.px(46), pt.w - pt.m, ty - pt.px(46)),
            fill=f.text_color,
            width=max(1, pt.px(2)),
        )
        draw.text((pt.m, ty), "합계", font=meta_font, fill=f.sub_color, anchor="ls")
        draw.text(
            (pt.w - pt.m, ty), price_text(total), font=price_font, fill=f.text_color, anchor="rs"
        )

    for i, line in enumerate(cta):
        by = cta_top + cta_step * i
        tw = cta_font.getlength(line)
        icon_w = cta_font.size * 0.72 + pt.px(16) if i == 0 else 0
        x = (pt.w - tw - icon_w) / 2
        color = f.text_color if i == 0 else f.sub_color
        if i == 0:
            bookmark_icon(draw, x, by, cta_font.size * 0.95, v.accent_color or f.text_color)
        draw.text((x + icon_w, by), line, font=cta_font, fill=color, anchor="ls")
    return canvas.convert("RGB")


def _mix(a: str, b: str, t: float) -> tuple[int, int, int]:
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return tuple(round(x + (y - x) * t) for x, y in zip(ca, cb, strict=True))


# ---------- 전체 ----------


def canvas_size(profile: Profile) -> tuple[int, int]:
    return (1080, 1350) if profile.visual.feed_ratio == "4:5" else (1080, 1080)


def render(
    spec: FeedSpec,
    out: Path,
    root: Path = REPO_ROOT,
    profile: Profile | None = None,
    store: Store | None = None,
    photo_dir: Path | None = None,
) -> list[Path]:
    """슬라이드를 out/01_cover.jpg, 02_아우터.jpg … 로 저장하고 순서대로 경로를 돌려준다."""
    profile = profile or load_profile(spec.account)
    size = canvas_size(profile)
    cards = build_cards(spec, profile.feed, root, store, photo_dir)
    kinds = [k for k in profile.feed.slides if k != "cover" or spec.cover]
    count = sum(len(cards) if k == "products" else 1 for k in kinds)
    if count > MAX_SLIDES:
        raise ValueError(f"슬라이드가 {count}장입니다 (최대 {MAX_SLIDES}장). 상품 수를 줄이세요")
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    def save(img: Image.Image, name: str) -> None:
        path = out / f"{len(paths) + 1:02d}_{name}.jpg"
        img.save(path, "JPEG", quality=93)
        paths.append(path)

    for kind in kinds:
        if kind == "cover":
            save(cover_slide(spec, profile, size, root), "cover")
        elif kind == "products":
            for i, card in enumerate(cards, 1):
                save(product_slide(card, i, profile, size), card.category)
        else:
            save(summary_slide(cards, spec, profile, size), "summary")
    return paths


def load_spec(path: Path) -> FeedSpec:
    return FeedSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="캐러셀 슬라이드 만들기")
    ap.add_argument("--spec", required=True, type=Path, help="재료 파일 (FeedSpec YAML)")
    ap.add_argument("--out", required=True, type=Path, help="슬라이드를 저장할 폴더")
    ap.add_argument("--product-image", choices=["cutout", "photo"], help="프로필 값 대신 이것으로")
    ap.add_argument("--photos", type=Path, help="누끼·사진이 없는 상품의 사진을 받아 둘 폴더")
    args = ap.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        profile = load_profile(spec.account)
        if args.product_image:
            profile.feed.product_image = args.product_image
        paths = render(spec, args.out, profile=profile, photo_dir=args.photos)
    except (ValueError, FileNotFoundError, ValidationError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
