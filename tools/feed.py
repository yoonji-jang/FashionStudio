r"""게시물 한 개의 캐러셀 슬라이드를 만든다 (AI 없음, Pillow).

기본 구성 (프로필 `feed.slides` 로 순서·종류를 바꿀 수 있다):
  1장 표지: 코디컷 + 제목 (tools.overlay)
  상품 장: 상품 한 장씩, 누끼(또는 원본 사진)·쇼핑몰·상품명·가격·코멘트
  요약 장: 코디 전체 목록과 저장·팔로우 문구
  댓글 안내 장(comment): 상품 정보는 댓글을 남기면 DM으로 받는다는 안내
  벌 장(looks, 20단계): 여러 벌 게시물에서 벌마다 코디컷 + 아래쪽에 상품 정보 몇 줄 (profile looks)
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

import httpx
import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from pydantic import ValidationError

from core.models import FeedItem, FeedLook, FeedSpec, Product
from core.profile import REPO_ROOT, Feed, Profile, account_dir, load_profile
from core.store import Store
from tools import ai_label, overlay
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
    source: str | None = None  # 사진을 가져온 곳 (예: 지그재그)


def photo_credit(card: Card, feed: Feed) -> str:
    """원본 사진 아래 출처 표기. 누끼든 사진이든 쇼핑몰 사진에서 왔으므로 둘 다 붙인다."""
    if not feed.photo_credit or not (card.source or card.shop):
        return ""
    text = feed.photo_credit.format(source=card.source or "", shop=card.shop or "")
    return drawable(" ".join(text.split()).strip(" ·"))


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


def pick_photo(product: Product, dest_dir: Path, feed: Feed) -> Path:
    """상품 창고 사진 중 모델 얼굴이 없는 사진을 앞에서부터 고른다 (창고 사진은 누끼 좋은 순).
    모든 사진에 얼굴이 있으면 얼굴 아래로 잘라 낸 첫 사진을 쓴다."""
    from tools import faces, scout

    pid = product.id.split(":")[-1]
    fallback = None
    tried = 0
    # 사진 목록이 다른 서버에 있어 못 받는 경우가 있어 마지막에 페이지 대표 사진(og:image)도 본다
    candidates = [str(u) for u in product.image_urls] + [None]
    for i, url in enumerate(candidates):
        if tried >= feed.photo_candidates:
            break
        try:
            if url is None:
                path = dest_dir / f"{pid}_og.jpg"
                if not path.exists():
                    scout.fetch_product_image(str(product.url), path)
            else:
                path = fetch_photo(url, dest_dir / f"{pid}_{i}.jpg")
        except (scout.ImageUnavailable, OSError, RuntimeError, httpx.HTTPError):
            # 사진 주소에 판매자 정보가 들어 있을 수 있어 주소는 기록하지 않는다
            print(f"{product.id}: {i}번 사진을 받지 못해 건너뜀", file=sys.stderr)
            continue
        tried += 1
        if not feed.hide_faces:
            return path
        img = Image.open(path).convert("RGB")
        found = faces.find_faces(img)
        if not found:
            return path
        if fallback is None:
            cropped = faces.crop_below_faces(img, found)
            if cropped is not None:
                fallback = dest_dir / f"{pid}_{i}_noface.jpg"
                cropped.save(fallback, quality=95)
    if fallback is None:
        raise ValueError(f"{product.id}: 모델 얼굴 없이 쓸 수 있는 사진이 없습니다")
    return fallback


def product_from_page(url: str, category: str) -> Product:
    """창고에 없는 상품의 공개 상품 페이지를 읽어 Product 로 만든다 (tools.scout 로만 접근).
    판매자 연락처 같은 정보는 읽지 않고 이름·스토어·가격·사진만 쓴다."""
    from sources import zigzag
    from tools import scout

    with scout.client() as c:
        code, text = scout.fetch_page(c, url)
    info = zigzag.parse_page(code, text)
    if not info.image_urls or not info.name:
        raise ValueError(f"상품 페이지를 읽지 못했습니다 (판매 종료?): {url}")
    pid = url.rstrip("/").split("/")[-1]
    return Product(
        id=f"zigzag:{pid}",
        source="zigzag",
        url=url,
        shop=info.shop or "",
        name=info.name,
        price=info.price or 0,
        category=category,
        image_urls=info.image_urls,
        status=info.status,
    )


def build_cards(
    spec: FeedSpec,
    feed: Feed,
    root: Path = REPO_ROOT,
    store: Store | None = None,
    photo_dir: Path | None = None,
) -> list[Card]:
    """photo_dir 를 주면 사진이 필요한데 없는 상품은 상품 창고 사진을 받아 쓴다 (pick_photo)."""
    products = {}
    if any(it.product for it in spec.items):
        products = {p.id: p for p in (store or Store(spec.account)).products()}
    cards = []
    for it in spec.items:
        base = products.get(it.product) if it.product else None
        if it.product and base is None:
            raise ValueError(f"상품 창고에 없는 상품입니다: {it.product}")
        if base is None and it.url and photo_dir:
            base = product_from_page(str(it.url), it.category or "소품")
        category = it.category or (base.category if base else None)
        name = it.name or (clean_name(base.name) if base else None)
        if not category or not name:
            raise ValueError(f"category 와 name 이 필요합니다: {it.model_dump(exclude_none=True)}")
        wants_photo = not it.photo and (feed.product_image == "photo" or not it.cutout)
        if wants_photo and base and photo_dir:
            it = it.model_copy(update={"photo": str(pick_photo(base, photo_dir, feed))})
        image, is_cutout = _item_image(it, feed, root)
        if image is None:
            raise ValueError(f"{name}: cutout 이나 photo 가 필요합니다 (상품 창고 사진은 --photos)")
        cards.append(
            Card(
                category=category,
                name=drawable(name),
                shop=it.shop or (base.shop if base else None),
                price=it.price
                if it.price is not None
                else ((base.price or None) if base else None),
                comment=drawable(it.comment),
                image=image,
                is_cutout=is_cutout,
                source=feed.source_names.get(it.source or (base.source if base else ""), None)
                or it.source
                or (base.source if base else None),
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
            img = img.convert("RGB")
            if feed.hide_faces:
                from tools import faces

                img, ok = faces.hide_faces(img)
                if not ok:
                    print(f"경고: 얼굴을 잘라낼 수 없는 사진입니다: {path}", file=sys.stderr)
            return img, False
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
) -> tuple[int, int, int, int] | None:
    """box 안 가운데에 상품을 비율 그대로 놓고, 놓인 자리를 돌려준다. fill 은 box 대비 최대 크기."""
    if card.image is None:
        return None
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
    return (x, y, x + img.width, y + img.height)


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


@dataclass
class LookLine:
    """벌 장에 적는 상품 한 줄."""

    category: str
    name: str
    shop: str | None
    price: int | None


def look_lines(
    look: FeedLook, account: str, store: Store | None = None, products: dict | None = None
) -> list[LookLine]:
    """벌 장 글자 재료. 사진은 받지 않는다 (상품 창고 값 + 재료 파일에 적은 값)."""
    if products is None and any(it.product for it in look.items):
        products = {p.id: p for p in (store or Store(account)).products()}
    rows = []
    for it in look.items:
        base = (products or {}).get(it.product) if it.product else None
        if it.product and base is None:
            raise ValueError(f"상품 창고에 없는 상품입니다: {it.product}")
        category = it.category or (base.category if base else "")
        name = it.name or (clean_name(base.name) if base else "")
        if not category or not name:
            raise ValueError(f"category 와 name 이 필요합니다: {it.model_dump(exclude_none=True)}")
        price = it.price if it.price is not None else (base.price or None) if base else None
        rows.append(
            LookLine(
                category=drawable(category),
                name=drawable(name),
                shop=it.shop or (base.shop if base else None),
                price=price,
            )
        )
    return rows


def look_slide(
    image: Path,
    lines: list[LookLine],
    number: int,
    profile: Profile,
    size: tuple[int, int],
) -> Image.Image:
    """벌 장: 코디컷 위 왼쪽 아래(표지 제목 자리, 코디컷 지시문이 비워 둠)에 상품 정보 몇 줄."""
    lk, v = profile.looks, profile.visual
    img = ImageOps.fit(Image.open(image).convert("RGB"), size, Image.LANCZOS).convert("RGBA")
    w, h = size
    k = w / BASE
    img = Image.alpha_composite(
        img, overlay.gradient(size, lk.gradient_height, lk.gradient_opacity)
    )
    margin = round(w * profile.feed.margin_x)
    width = round(w * lk.text_width) - margin  # 코디컷 지시문이 비워 둔 왼쪽 아래 폭 안에
    label_font = load_font(v.font, round(lk.label_size * k))
    name_font = load_font(v.font, round(lk.line_size * k))
    cat_font = load_font(profile.feed.body_font, round(lk.line_size * k))
    shop_font = load_font(profile.feed.body_font, round(lk.shop_size * k))
    step = name_font.size * lk.line_spacing
    last = h * (1 - lk.bottom)
    first = last - step * (len(lines) - 1)
    items: list = []
    for i, ln in enumerate(lines):
        y = first + step * i
        # 카테고리(보조 색) · 이름(굵게) · 스토어(작게, 자리가 있을 때). 넘치면 이름을 줄인다
        x = margin
        cat = f"{ln.category}  "
        items.append(((x, y), cat, cat_font, lk.sub_color))
        x += cat_font.getlength(cat)
        name = wrap(ln.name, name_font, margin + width - x, 1)[0]
        items.append(((x, y), name, name_font, lk.text_color))
        x += name_font.getlength(name)
        extra = [ln.shop] if lk.show_shop and ln.shop else []
        if lk.show_price and ln.price:
            extra.append(price_text(ln.price))
        tail = f"  {drawable(' · '.join(extra))}" if extra else ""
        if tail and x + shop_font.getlength(tail) <= margin + width:
            items.append(((x, y), tail, shop_font, lk.sub_color))
    label_y = None
    if lk.label:
        label_y = first - name_font.size * 1.25 - label_font.size * 0.4
        items.append(((margin, label_y), lk.label.format(n=number), label_font, lk.text_color))
    out = Image.alpha_composite(img, overlay.text_layer(size, items, v.text_shadow, blur=8 * k))
    if v.accent_color and label_y is not None:
        x1 = margin - round(v.accent_gap * k)
        x0 = x1 - max(1, round(v.accent_width * k))
        box = (x0, round(label_y - label_font.size * 0.8), x1 - 1, round(label_y + 2 * k))
        ImageDraw.Draw(out).rectangle(box, fill=v.accent_color)
    return out.convert("RGB")


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
    placed = place_image(canvas, card, box, pt.k, fill=fill_ratio(card))
    credit = photo_credit(card, f)
    if credit and placed:
        # 사진 오른쪽 아래 바로 밑에 작게
        draw.text(
            (placed[2], placed[3] + pt.px(f.credit_size * 1.6)),
            credit,
            font=pt.body(f.credit_size),
            fill=f.sub_color,
            anchor="rs",
        )

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
    summary_credit = drawable(f.summary_credit) if f.summary_credit else ""
    if summary_credit:
        list_bottom -= pt.px(f.credit_size * 2.2)
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

    if summary_credit:
        cy = list_top + row_h * len(cards) + pt.px(f.credit_size * 1.9)
        if f.show_total:
            cy += pt.px(64)
        draw.text(
            (pt.w - pt.m, cy),
            summary_credit,
            font=pt.body(f.credit_size),
            fill=f.sub_color,
            anchor="rs",
        )

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


def comment_icon(draw: ImageDraw.ImageDraw, cx: float, cy: float, h: float, color: str):
    """말풍선 아이콘 (둥근 풍선 + 왼쪽 아래 꼬리)."""
    w = h * 1.15
    lw = max(2, round(h * 0.075))
    box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h * 0.32)
    draw.rounded_rectangle(box, radius=h * 0.34, outline=color, width=lw)
    tail = [(cx - w * 0.2, box[3] - lw), (cx - w * 0.3, cy + h / 2), (cx - w * 0.02, box[3] - lw)]
    draw.polygon(tail, fill=color)
    r = h * 0.07
    for dx in (-w * 0.22, 0, w * 0.22):
        y = (box[1] + box[3]) / 2
        draw.ellipse((cx + dx - r, y - r, cx + dx + r, y + r), fill=color)


def comment_slide(profile: Profile, size: tuple[int, int]) -> Image.Image:
    """상품 정보는 댓글로 받으라는 안내 장. 글자는 모두 가운데 정렬."""
    pt = Painter(profile, size)
    f, v = pt.f, pt.v
    canvas = pt.background().convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    accent = v.accent_color or f.text_color
    pt.header(draw, [("INFO", pt.bold(f.index_size), accent)])

    def fill(s: str) -> str:
        return drawable(s.replace("{logo}", v.logo_text))

    title = [fill(s) for s in f.comment_title]
    sub = [fill(s) for s in f.comment_sub]
    title_font, sub_font = pt.bold(f.comment_title_size), pt.body(f.comment_sub_size)
    icon_h = pt.px(110)
    title_step, sub_step = title_font.size * 1.35, sub_font.size * 1.6
    box_h = pt.px(96) if f.comment_box else 0
    block = (
        icon_h
        + pt.px(70)
        + title_step * len(title)
        + pt.px(34)
        + sub_step * len(sub)
        + (pt.px(70) + box_h if box_h else 0)
    )
    y = (pt.h - block) / 2
    comment_icon(draw, pt.w / 2, y + icon_h / 2, icon_h, accent)
    y += icon_h + pt.px(70)
    for line in title:
        y += title_font.size
        draw.text((pt.w / 2, y), line, font=title_font, fill=f.text_color, anchor="ms")
        y += title_step - title_font.size
    y += pt.px(34)
    for line in sub:
        y += sub_font.size
        draw.text((pt.w / 2, y), line, font=sub_font, fill=f.sub_color, anchor="ms")
        y += sub_step - sub_font.size
    if box_h:
        # 인스타 댓글 입력창처럼 보이는 둥근 칸: 왼쪽에 안내 글자, 오른쪽에 "게시"
        y += pt.px(70)
        bw = pt.w - 2 * pt.m
        x0 = (pt.w - bw) / 2
        tile = Image.new("RGBA", (round(bw), box_h), _mix(f.background, "#FFFFFF", 0.7))
        canvas.alpha_composite(rounded(tile, box_h // 2), (round(x0), round(y)))
        font = pt.body(f.comment_sub_size - 2)
        mid = y + box_h / 2
        draw.text(
            (x0 + pt.px(40), mid), fill(f.comment_box), font=font, fill=f.sub_color, anchor="lm"
        )
        draw.text(
            (x0 + bw - pt.px(40), mid),
            "게시",
            font=pt.bold(f.comment_sub_size - 2),
            fill=accent,
            anchor="rm",
        )
    return canvas.convert("RGB")


def _mix(a: str, b: str, t: float) -> tuple[int, int, int]:
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return tuple(round(x + (y - x) * t) for x, y in zip(ca, cb, strict=True))


# ---------- 전체 ----------


def canvas_size(profile: Profile) -> tuple[int, int]:
    return (1080, 1350) if profile.visual.feed_ratio == "4:5" else (1080, 1080)


def save_slide(img: Image.Image, path: Path, profile: Profile) -> None:
    """슬라이드 JPEG 저장. 프로필 ai_label 이 켜진 종류면 AI 합성 표시를 파일에 넣는다."""
    label = ai_label.save_options(profile.ai_label, ai_label.slide_kind(path))
    img.save(path, "JPEG", quality=93, **label)


def render(
    spec: FeedSpec,
    out: Path,
    root: Path = REPO_ROOT,
    profile: Profile | None = None,
    store: Store | None = None,
    photo_dir: Path | None = None,
) -> list[Path]:
    """슬라이드를 out/01_cover.jpg, 02_아우터.jpg (벌 장은 02_look1.jpg) … 로 저장하고
    순서대로 경로를 돌려준다."""
    profile = profile or load_profile(spec.account)
    size = canvas_size(profile)
    kinds = [k for k in profile.feed.slides if k != "cover" or spec.cover]
    # 상품 장·요약 장이 없으면 상품 사진을 받지 않는다 (여러 벌 게시물)
    needs_cards = "products" in kinds or "summary" in kinds
    cards = build_cards(spec, profile.feed, root, store, photo_dir) if needs_cards else []
    looks = [lk for lk in spec.looks if lk.image]
    shown = list(enumerate(looks, 1))
    if spec.cover and "cover" in kinds and not profile.looks.repeat_cover_look:
        shown = shown[1:]  # 1번 벌은 표지로만
    counts = {"products": len(cards), "looks": len(shown)}
    count = sum(counts.get(k, 1) for k in kinds)
    if count > MAX_SLIDES:
        raise ValueError(f"슬라이드가 {count}장입니다 (최대 {MAX_SLIDES}장). 상품 수를 줄이세요")
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    def save(img: Image.Image, name: str) -> None:
        path = out / f"{len(paths) + 1:02d}_{name}.jpg"
        save_slide(img, path, profile)
        paths.append(path)

    for kind in kinds:
        if kind == "cover":
            save(cover_slide(spec, profile, size, root), "cover")
        elif kind == "products":
            for i, card in enumerate(cards, 1):
                save(product_slide(card, i, profile, size), card.category)
        elif kind == "looks":
            products = {p.id: p for p in (store or Store(spec.account)).products()}
            for n, look in shown:
                lines = look_lines(look, spec.account, products=products)
                save(look_slide(root / look.image, lines, n, profile, size), f"look{n}")
        elif kind == "comment":
            save(comment_slide(profile, size), "comment")
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
