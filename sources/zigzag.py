"""지그재그 상품 소스.

- 검색: 검색 페이지(zigzag.kr/search)를 브라우저로 열어 페이지가 받아 오는 검색 결과를 읽는다.
  카드마다 상품 번호, 이름, 쇼핑몰, 가격, 평점, 리뷰 수, 대표 사진, 판매 가능 여부가 있다.
- 상세: 상품 페이지(zigzag.kr/catalog/products/<번호>) HTML 에 들어 있는 페이지 데이터
  (__NEXT_DATA__)에서 판매 상태와 사진 목록을 읽는다. 평점은 상세에 없어 검색 카드 값을 쓴다.
쇼핑몰 연락처 같은 판매자 개인정보는 읽지도 저장하지도 않는다.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from core.models import Category, Product, SaleStatus
from sources.base import ProductSource, SearchFilters, name_excluded, price_ok
from tools import scout

PRODUCT_URL = "https://zigzag.kr/catalog/products/{id}"
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_SOLD_OUT_WORDS = ("SOLD_OUT", "SOLDOUT", "CLOSED", "STOP", "SUSPEND", "DELETED")


# ---------- 검색 카드 ----------


def iter_cards(obj: Any):
    """검색 결과 JSON 어디에 있든 상품 카드(catalog_product_id 가 있는 항목)를 찾아낸다."""
    if isinstance(obj, dict):
        if obj.get("catalog_product_id"):
            yield obj
            return
        for v in obj.values():
            yield from iter_cards(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_cards(v)


def parse_count(value: Any) -> int | None:
    """리뷰 수 표시값을 숫자로. 예: 931, "1,234", "1.2천", "3만+", "999+"."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return int(value)
    text = str(value).replace(",", "").replace("+", "").strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(천|만)?", text)
    if not m:
        return None
    num = float(m.group(1)) * {"천": 1_000, "만": 10_000, None: 1}[m.group(2)]
    return int(num)


def parse_rating(value: Any) -> float | None:
    """평점 5점 만점으로. 100점 만점 값이 오면 5점으로 바꾼다. 없거나 0이면 None."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if 5 < v <= 100:
        v /= 20
    return round(v, 2) if 0 < v <= 5 else None


def _price(card: dict) -> int | None:
    for key in ("final_price", "max_price"):
        v = card.get(key)
        if isinstance(v, int | float) and v > 0:
            return int(v)
    return None


def card_to_product(card: dict, category: Category, query: str, today: date) -> Product | None:
    pid = str(card["catalog_product_id"])
    image = card.get("image_url") or card.get("jpeg_image_url") or card.get("webp_image_url")
    image = scout.normalize_image_url(image) if image else image
    price = _price(card)
    name = card.get("title") or card.get("name")
    if not (image and price and name) or scout.is_placeholder(image):
        return None
    score = parse_rating(card.get("review_score"))
    return Product(
        id=f"zigzag:{pid}",
        source="zigzag",
        url=PRODUCT_URL.format(id=pid),
        shop=card.get("shop_name") or "",
        name=name,
        price=price,
        category=category,
        image_urls=[image],
        rating=score,
        review_count=parse_count(card.get("display_review_count", card.get("review_count"))),
        status="sold_out" if is_sold_out(card.get("sellable_status")) else "on_sale",
        query=query,
        collected_at=today,
    )


def is_sold_out(status: Any) -> bool:
    return bool(status) and any(w in str(status).upper() for w in _SOLD_OUT_WORDS)


def passes(p: Product, filters: SearchFilters) -> bool:
    if filters.exclude_sold_out and p.status != "on_sale":
        return False
    if (p.rating or 0) < filters.min_rating:
        return False
    if not price_ok(p.price, filters.min_price, filters.max_price):
        return False
    if name_excluded(p.name, filters.exclude_words):
        return False
    return (p.review_count or 0) >= filters.min_reviews


def cards_to_products(
    responses: list[dict], category: Category, query: str, filters: SearchFilters, today: date
) -> list[Product]:
    out: dict[str, Product] = {}
    for card in (c for r in responses for c in iter_cards(r)):
        p = card_to_product(card, category, query, today)
        if p and p.id not in out and passes(p, filters):
            out[p.id] = p
    return list(out.values())[: filters.max_results]


# ---------- 상품 페이지 ----------


@dataclass
class PageInfo:
    status: SaleStatus
    image_urls: list[str]
    style_tags: list[str]
    name: str | None = None
    shop: str | None = None
    price: int | None = None


_PRICE_KEYS = ("final_price", "discount_price", "sales_price", "price")


def page_price(product: dict) -> int | None:
    """상품 정보에서 판매가를 찾는다. 가격 묶음(product_price 등) 안의 final_price 를 먼저 본다."""
    found: dict[str, int] = {}

    def walk(obj: Any, depth: int) -> None:
        if depth > 3 or not isinstance(obj, dict):
            return
        for k, v in obj.items():
            if k in _PRICE_KEYS and isinstance(v, int | float) and v > 0:
                found.setdefault(k, int(v))
            elif isinstance(v, dict) and "price" in k:
                walk(v, depth + 1)

    walk(product, 0)
    return next((found[k] for k in _PRICE_KEYS if k in found), None)


def _page_data(page_html: str) -> dict | None:
    m = _NEXT_DATA.search(page_html)
    if not m:
        return None
    try:
        queries = json.loads(m.group(1))["props"]["pageProps"]["dehydratedState"]["queries"]
    except (ValueError, KeyError, TypeError):
        return None
    for q in queries:
        data = (q.get("state") or {}).get("data") or {}
        if isinstance(data, dict) and isinstance(data.get("product"), dict):
            return data
    return None


def page_status(product: dict) -> SaleStatus:
    sales = str(product.get("sales_status") or "")
    if is_sold_out(sales):
        return "sold_out"
    if (
        sales == "ON_SALE"
        and product.get("display_status", "VISIBLE") == "VISIBLE"
        and product.get("is_purchasable", True)
    ):
        return "on_sale"
    return "hidden"


def parse_page(status_code: int, page_html: str) -> PageInfo:
    """상품 페이지 HTML → 판매 상태·사진 목록. 사라졌거나 읽을 수 없으면 hidden."""
    data = _page_data(page_html) if status_code == 200 else None
    if not data:
        return PageInfo(status="hidden", image_urls=[], style_tags=[])
    product = data["product"]
    images = []
    for im in product.get("product_image_list") or []:
        url = im.get("origin_url") or im.get("url")
        url = scout.normalize_image_url(url) if url else url
        if not url or scout.is_placeholder(url) or url.split("?")[0].lower().endswith(".gif"):
            continue
        if url not in images:
            images.append(url)
    trait = ((data.get("shop") or {}).get("trait") or {}).get("style") or ""
    return PageInfo(
        status=page_status(product),
        image_urls=images,
        style_tags=[s.strip() for s in trait.split(",") if s.strip()],
        name=product.get("name"),
        shop=(data.get("shop") or {}).get("name"),
        price=page_price(product),
    )


_IMG_SRC = re.compile(r"""<img[^>]+?(?:data-src|ec-data-src|src)\s*=\s*["']([^"']+)["']""", re.I)
_SIZE_WORDS = re.compile(
    r"총장|기장|어깨|가슴|소매|암홀|허리|엉덩이|힙|밑위|허벅지|밑단|화장|단면|실측"
)


def description_images(page_html: str) -> list[str]:
    """상세 본문(설명 HTML)의 이미지 주소, 위에서부터. 실측표가 이미지로 들어 있는 경우가 많다.
    본문 원본은 돌려주지 않는다 (판매자 정보가 섞일 수 있음). gif 는 뺀다."""
    data = _page_data(page_html)
    desc = str(((data or {}).get("product") or {}).get("description") or "")
    urls: list[str] = []
    for src in _IMG_SRC.findall(desc):
        src = html.unescape(src.strip())
        if src.startswith("//"):
            src = "https:" + src
        if not src.startswith("http") or src.split("?")[0].lower().endswith(".gif"):
            continue
        if src not in urls:
            urls.append(src)
    return urls


def size_lines(page_html: str, limit: int = 30) -> list[str]:
    """상세 본문 글 가운데 실측 줄만 (총장·어깨 같은 말과 숫자가 함께 있는 줄). 다른 글은 버린다."""
    data = _page_data(page_html)
    desc = str(((data or {}).get("product") or {}).get("description") or "")
    text = html.unescape(re.sub(r"<br\s*/?>|</(?:p|div|li|tr)>", "\n", desc, flags=re.I))
    text = re.sub(r"<[^>]+>", " ", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if _SIZE_WORDS.search(line) and re.search(r"\d", line) and len(line) <= 160:
            lines.append(line)
    return lines[:limit]


class Source(ProductSource):
    name = "zigzag"

    def __init__(self, today: date | None = None):
        self.today = today or date.today()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = scout.client()
        return self._client

    def search(
        self, queries: list[str], category: Category, filters: SearchFilters
    ) -> dict[str, list[Product]]:
        raw = scout.search_responses(queries, min_items=filters.max_results * 2)
        return {
            q: cards_to_products(raw.get(q, []), category, q, filters, self.today) for q in queries
        }

    def detail(self, product: Product) -> Product:
        code, text = scout.fetch_page(self.client, str(product.url))
        info = parse_page(code, text)
        update: dict[str, Any] = {"status": info.status, "checked_at": self.today}
        if info.image_urls:
            update["image_urls"] = info.image_urls
        if info.style_tags:
            update["style_tags"] = info.style_tags
        if info.shop and not product.shop:
            update["shop"] = info.shop
        return Product.model_validate({**product.model_dump(), **update})

    def thumb_url(self, image_url: str, width: int = 240) -> str:
        sep = "&" if "?" in image_url else "?"
        return f"{image_url}{sep}width={width}&quality=80&format=jpeg"
