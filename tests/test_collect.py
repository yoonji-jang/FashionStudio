"""4단계 상품 수집: 지그재그 해석, 사진 고르기, 창고 합치기 (네트워크 없이)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from core.models import Product
from core.store import Store
from sources import SearchFilters, zigzag
from tools import collect, photo_pick, scout

TODAY = date(2026, 9, 26)


def card(pid: int, **kw) -> dict:
    base = {
        "type": "PRODUCT_CARD",
        "catalog_product_id": str(pid),
        "title": f"상품 {pid}",
        "shop_name": "샵",
        "final_price": 42400,
        "max_price": 71900,
        "review_score": 4.8,
        "display_review_count": "931",
        "image_url": f"https://cf.product-image.s.zigzag.kr/original/{pid}.jpeg",
        "sellable_status": "ON_SALE",
        "is_ad": False,
    }
    return {**base, **kw}


def search_response(cards: list[dict]) -> dict:
    return {"data": {"search_result": {"total_count": 99, "ui_item_list": cards}}}


def page_html(status="ON_SALE", display="VISIBLE", purchasable=True, images=None) -> str:
    images = images or [
        {"image_type": "SUB", "url": "https://img.example/a.jpeg"},
        {"image_type": "MAIN", "url": "https://img.example/main.gif"},
        {"image_type": "SUB", "url": "https://img.example/b.jpeg"},
    ]
    data = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": ["getPdpBaseInfo", "1"],
                            "state": {
                                "data": {
                                    "shop": {
                                        "name": "모어라운드",
                                        "trait": {"style": "캐주얼,심플베이직"},
                                    },
                                    "product": {
                                        "id": "1",
                                        "name": "레더 자켓",
                                        "sales_status": status,
                                        "display_status": display,
                                        "is_purchasable": purchasable,
                                        "product_image_list": images,
                                    },
                                }
                            },
                        }
                    ]
                }
            }
        }
    }
    script = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    return f"<html>{script}</html>"


# ---------- 지그재그 해석 ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (931, 931),
        ("1,234", 1234),
        ("1.2천", 1200),
        ("3만+", 30000),
        ("999+", 999),
        ("", None),
        ("x", None),
    ],
)
def test_parse_count(raw, expected):
    assert zigzag.parse_count(raw) == expected


def test_parse_rating():
    assert zigzag.parse_rating(4.9) == 4.9
    assert zigzag.parse_rating("4.3") == 4.3
    assert zigzag.parse_rating(96) == 4.8
    assert zigzag.parse_rating(0) is None
    assert zigzag.parse_rating(None) is None


def test_cards_filtered_by_rating_reviews_and_sold_out():
    resp = search_response(
        [
            card(1),
            card(2, review_score=4.2),
            card(3, display_review_count="5"),
            card(4, sellable_status="SOLD_OUT"),
            {"nested": [card(5)]},
            card(1),  # 중복
            card(6, image_url="https://cf.image-farm.s.zigzag.kr/original/cms/banner.png"),
        ]
    )
    filters = SearchFilters(min_rating=4.5, min_reviews=20, max_results=10)
    products = zigzag.cards_to_products([resp], "아우터", "레더자켓", filters, TODAY)
    assert [p.id for p in products] == ["zigzag:1", "zigzag:5"]
    p = products[0]
    assert p.url.path == "/catalog/products/1"
    assert (p.price, p.rating, p.review_count, p.query) == (42400, 4.8, 931, "레더자켓")
    assert p.category == "아우터" and p.collected_at == TODAY


def test_parse_page_on_sale_skips_gif_and_reads_style():
    info = zigzag.parse_page(200, page_html())
    assert info.status == "on_sale"
    assert info.image_urls == ["https://img.example/a.jpeg", "https://img.example/b.jpeg"]
    assert info.style_tags == ["캐주얼", "심플베이직"]


@pytest.mark.parametrize(
    "kwargs,code,expected",
    [
        ({"status": "SOLD_OUT"}, 200, "sold_out"),
        ({"display": "HIDDEN"}, 200, "hidden"),
        ({"purchasable": False}, 200, "hidden"),
        ({"status": "SUSPENDED"}, 200, "sold_out"),
        ({}, 404, "hidden"),
    ],
)
def test_parse_page_not_on_sale(kwargs, code, expected):
    assert zigzag.parse_page(code, page_html(**kwargs)).status == expected


def test_parse_page_without_data_is_hidden():
    assert zigzag.parse_page(200, "<html>점검 중</html>").status == "hidden"


def test_thumb_url():
    src = zigzag.Source()
    assert src.thumb_url("https://x/a.jpeg").endswith("a.jpeg?width=240&quality=80&format=jpeg")
    assert "?w=1&width=240" in src.thumb_url("https://x/a.jpeg?w=1")


# ---------- 사진 고르기 ----------


def _flat_shot() -> Image.Image:
    img = Image.new("RGB", (300, 360), (245, 245, 245))
    ImageDraw.Draw(img).rectangle((80, 60, 220, 300), fill=(20, 20, 20))
    return img


def _model_shot() -> Image.Image:
    img = Image.new("RGB", (300, 360), (120, 150, 90))  # 야외 배경
    d = ImageDraw.Draw(img)
    for x in range(0, 300, 20):
        d.line((x, 0, x + 40, 360), fill=(90, 110, 160), width=6)  # 복잡한 배경
    d.ellipse((110, 20, 190, 110), fill=(224, 172, 140))  # 얼굴(피부색)
    d.rectangle((90, 110, 210, 330), fill=(20, 20, 20))
    return img


def _multi_shot() -> Image.Image:
    img = Image.new("RGB", (300, 360), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle((20, 40, 130, 200), fill=(20, 20, 20))
    d.rectangle((170, 40, 280, 200), fill=(150, 30, 40))
    d.rectangle((90, 240, 210, 340), fill=(30, 40, 120))
    return img


def test_photo_scores():
    flat = photo_pick.score_image(_flat_shot())
    model = photo_pick.score_image(_model_shot())
    multi = photo_pick.score_image(_multi_shot())
    assert flat.flat and flat.parts == 1
    assert not model.flat and model.skin > flat.skin
    assert not multi.flat and multi.parts == 3
    ranked = photo_pick.rank({"model": model, "multi": multi, "flat": flat})
    assert ranked[0] == "flat"


# ---------- 창고 합치기 ----------


def _jpeg(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


class FakeSource(zigzag.Source):
    """검색·상세를 가짜로. pages: 상품 번호 → 판매 상태."""

    def __init__(self, cards: dict[str, list[dict]], pages: dict[str, str]):
        super().__init__(today=TODAY)
        self.cards, self.pages, self.detailed = cards, pages, []

    def search(self, queries, category, filters):
        return {
            q: zigzag.cards_to_products(
                [search_response(self.cards.get(q, []))], category, q, filters, self.today
            )
            for q in queries
        }

    def detail(self, product):
        pid = product.id.split(":")[1]
        self.detailed.append(pid)
        info = zigzag.parse_page(
            200,
            page_html(
                status=self.pages.get(pid, "ON_SALE"),
                images=[
                    {"url": f"https://img.example/{pid}_model.jpeg"},
                    {"url": f"https://img.example/{pid}_flat.jpeg"},
                ],
            ),
        )
        return Product.model_validate(
            {
                **product.model_dump(),
                "status": info.status,
                "checked_at": TODAY,
                "image_urls": info.image_urls,
            }
        )


def fake_fetch(url: str) -> bytes:
    return _jpeg(_flat_shot() if "_flat" in url else _model_shot())


def _stored(pid: int, checked: date, **kw) -> Product:
    p = zigzag.card_to_product(card(pid), "아우터", "레더자켓", TODAY - timedelta(days=10))
    return Product.model_validate({**p.model_dump(), "checked_at": checked, **kw})


@pytest.fixture(autouse=True)
def no_price_rules(monkeypatch):
    """가짜 카드 가격(42,400원)이 계정의 실제 가격대에 걸리지 않게 가격대·제외어를 끈다."""
    original = collect.load_profile

    def load(account, *args, **kwargs):
        profile = original(account, *args, **kwargs)
        profile.product_source.price_range = {}
        profile.product_source.exclude_words = []
        return profile

    monkeypatch.setattr(collect, "load_profile", load)


def test_collect_adds_drops_and_orders_photos(tmp_path: Path):
    store = Store("women", root=tmp_path)
    store.save_products(
        [
            _stored(10, TODAY - timedelta(days=5)),  # 오래전 확인 → 재확인, 판매 종료 → 빠짐
            _stored(11, TODAY - timedelta(days=5)),  # 재확인, 판매 중 → 남음
            _stored(12, TODAY, last_used=TODAY),  # 오늘 확인 → 재확인 안 함
        ]
    )
    source = FakeSource(
        cards={"레더자켓": [card(1), card(2), card(3, review_score=3.9)]},
        pages={"2": "SOLD_OUT", "10": "SOLD_OUT"},
    )
    report = collect.collect(
        "women",
        plan={"아우터": ["레더자켓"]},
        source=source,
        store=store,
        fetch=fake_fetch,
        today=TODAY,
    )
    ids = {p.id: p for p in store.products()}
    assert set(ids) == {"zigzag:1", "zigzag:11", "zigzag:12"}
    assert [p.id for p in report.added] == ["zigzag:1"]
    assert [(p.id, s) for p, s in report.dropped] == [("zigzag:10", "sold_out")]
    assert "12" not in source.detailed and "3" not in source.detailed
    new = ids["zigzag:1"]
    assert str(new.image_urls[0]).endswith("1_flat.jpeg") and new.has_flat_shot
    assert ids["zigzag:12"].last_used == TODAY
    assert report.searched == {"레더자켓": 2}

    out = tmp_path / "report"
    collect.write_report(report, out, store)
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "새로 모은 상품: 1개" in text and "품절" in text
    assert (out / "sheet.jpg").exists() and (out / "products.json").exists()


def test_collect_trims_to_max_per_category(tmp_path: Path, monkeypatch):
    store = Store("women", root=tmp_path)
    cards = [card(i, display_review_count=str(20 + i)) for i in range(1, 6)]
    source = FakeSource(cards={"q": cards}, pages={})
    profile = collect.load_profile("women")
    profile.product_source.max_per_category = 3
    monkeypatch.setattr(collect, "load_profile", lambda _: profile)
    report = collect.collect(
        "women", plan={"아우터": ["q"]}, source=source, store=store, fetch=fake_fetch, today=TODAY
    )
    kept = [p.id for p in store.products()]
    assert kept == ["zigzag:5", "zigzag:4", "zigzag:3"]  # 리뷰 많은 순
    assert report.trimmed == 2 and len(report.added) == 3


# ---------- 접근 도구 ----------


def test_normalize_image_url():
    s3 = "https://s3.ap-northeast-2.amazonaws.com/product-image.s.zigzag.kr/original/d/a.jpeg"
    assert scout.normalize_image_url(s3) == "https://cf.product-image.s.zigzag.kr/original/d/a.jpeg"
    assert scout.normalize_image_url("https://slowand.com/a.jpg") == "https://slowand.com/a.jpg"


class _Resp:
    def __init__(self, code: int):
        self.status_code, self.content, self.text = code, b"", ""


class _Client:
    def __init__(self, codes: list[int]):
        self.codes, self.calls = codes, 0

    def get(self, url):
        self.calls += 1
        return _Resp(self.codes.pop(0))


def test_get_retries_then_distinguishes_page_and_image():
    waits: list[float] = []
    page = _Client([403, 429, 200])
    assert scout._get(page, "https://zigzag.kr/catalog/products/1", waits.append).status_code == 200
    with pytest.raises(scout.Blocked):
        scout._get(_Client([403, 403, 403]), "https://zigzag.kr/x", waits.append)
    img = _Client([403, 403])
    with pytest.raises(scout.ImageUnavailable):
        scout._get(img, "https://cf.product-image.s.zigzag.kr/a.jpeg", waits.append)
    assert img.calls == 2  # 사진은 한 번만 다시 시도


def test_image_failure_does_not_stop_collect(tmp_path: Path):
    store = Store("women", root=tmp_path)
    source = FakeSource(cards={"q": [card(1)]}, pages={})

    def flaky(url: str) -> bytes:
        if "_model" in url:
            raise scout.ImageUnavailable(url)
        return fake_fetch(url)

    report = collect.collect(
        "women", plan={"아우터": ["q"]}, source=source, store=store, fetch=flaky, today=TODAY
    )
    assert report.stopped is None and [p.id for p in report.added] == ["zigzag:1"]
    assert str(store.products()[0].image_urls[0]).endswith("1_flat.jpeg")


def test_model_on_plain_background_is_not_flat():
    """단색 배경이라도 사람이 테두리에 걸쳐 잘리면(다리·몸통) 단독컷이 아니다 (collect-2 실측)."""
    img = Image.new("RGB", (300, 360), (240, 240, 238))
    ImageDraw.Draw(img).rectangle((100, 0, 200, 360), fill=(30, 30, 30))  # 위아래로 잘린 사람
    s = photo_pick.score_image(img)
    assert s.skin < 0.012 and s.parts == 1 and not s.flat


def test_cards_filtered_by_price_and_hype_words():
    resp = search_response(
        [
            card(1),
            card(2, final_price=9900),
            card(3, title="[10만장돌파] 베이직 니트"),
            card(4, title="[BEST] 스웨이드 자켓"),
        ]
    )
    filters = SearchFilters(min_price=20000, max_price=100000, exclude_words=["만장", "best"])
    products = zigzag.cards_to_products([resp], "아우터", "레더자켓", filters, TODAY)
    assert [p.id for p in products] == ["zigzag:1"]


def test_quality_caps_reviews_and_prefers_point_items():
    from core.profile import load_profile
    from tools.collect import fits, quality

    profile = load_profile("women")
    cap = profile.product_source.review_cap
    base = dict(
        source="zigzag",
        url="https://zigzag.kr/catalog/products/1",
        shop="s",
        category="상의",
        rating=4.8,
        image_urls=["https://img.example/a.jpg"],
    )
    seller = Product(
        id="zigzag:1", name="베이직 라운드 니트", price=39000, review_count=cap * 50, **base
    )
    point = Product(
        id="zigzag:2", name="모헤어 스트라이프 니트", price=39000, review_count=cap, **base
    )
    assert quality(point, profile) > quality(seller, profile)
    assert quality(seller) > quality(point)  # 프로필 없이는 예전처럼 리뷰 수 순
    plain = seller.model_copy(update={"name": "라운드 니트", "review_count": cap})
    basic = seller.model_copy(update={"review_count": cap})
    assert quality(basic, profile) < quality(plain, profile)  # 이름에 '베이직' → 뒤로
    # 기본템 감점은 상의·아우터에만: 무지 하의는 바탕이라 그대로 (2026-10-07)
    skirt = basic.model_copy(update={"category": "하의", "name": "베이직 미니 스커트"})
    plain_skirt = skirt.model_copy(update={"name": "미니 스커트"})
    assert quality(skirt, profile) == quality(plain_skirt, profile)
    cheap = Product(id="zigzag:3", name="모헤어 니트", price=9000, review_count=100, **base)
    assert fits(point, profile.product_source) and not fits(cheap, profile.product_source)


def test_collect_keeps_products_for_ref_looks(tmp_path: Path, monkeypatch):
    """참고 코디 칸의 검색어로 모은 상품은 창고 한도와 상관없이 남긴다 (2026-10-13)."""
    from core.models import RefLook

    store = Store("women", root=tmp_path)
    slots = [
        {"category": "아우터", "color": "블랙", "desc": "블랙 자켓", "query": "q"},
        {"category": "하의", "color": "블랙", "desc": "블랙 스커트", "query": "스커트"},
    ]
    store.save_refs([RefLook(n=101, name="t", source="s", added=TODAY, slots=slots)])
    cards = [card(i, display_review_count=str(20 + i)) for i in range(1, 6)]
    source = FakeSource(cards={"블랙 q": cards}, pages={})
    profile = collect.load_profile("women")
    profile.product_source.max_per_category = 3
    monkeypatch.setattr(collect, "load_profile", lambda _: profile)
    collect.collect(
        "women",
        plan={"아우터": ["블랙 q"]},
        source=source,
        store=store,
        fetch=fake_fetch,
        today=TODAY,
    )
    assert len(store.products()) == 5


def test_collect_keeps_products_matched_to_ref_looks(tmp_path: Path, monkeypatch):
    """참고 코디 칸에 이미 맞춰진 상품은 다른 검색어로 모였어도 남긴다 (2026-10-13)."""
    from core.models import RefLook

    store = Store("women", root=tmp_path)
    slots = [
        {"category": "아우터", "color": "블랙", "desc": "블랙 자켓", "query": "q"},
        {"category": "하의", "color": "블랙", "desc": "블랙 스커트", "query": "스커트"},
    ]
    cards = [card(i, display_review_count=str(20 + i)) for i in range(1, 6)]
    source = FakeSource(cards={"다른 검색어": cards}, pages={})
    profile = collect.load_profile("women")
    profile.product_source.max_per_category = 3
    monkeypatch.setattr(collect, "load_profile", lambda _: profile)
    run = dict(source=source, store=store, fetch=fake_fetch, today=TODAY)
    collect.collect("women", plan={"아우터": ["다른 검색어"]}, **run)
    kept = {p.id for p in store.products()}
    dropped = sorted({f"zigzag:{i}" for i in range(1, 6)} - kept)[0]
    ref = RefLook(n=101, name="t", source="s", added=TODAY, slots=slots, matches={"0": [dropped]})
    store.save_refs([ref])
    collect.collect("women", plan={"아우터": ["다른 검색어"]}, **run)
    assert dropped in {p.id for p in store.products()}


def test_cheap_floor_is_lower_percentile():
    from sources.base import cheap_floor

    prices = [10000 * i for i in range(1, 21)]  # 1만 ~ 20만
    assert cheap_floor(prices, 15) == 40000  # 하위 15%(3개)보다 비싼 첫 가격
    assert cheap_floor(prices, 0) == 0 and cheap_floor(prices[:5], 15) == 0  # 끔, 너무 적음


def test_search_drops_cheapest_15_percent_of_category(monkeypatch):
    cards = {
        "니트": [card(i, final_price=10000 * i) for i in range(1, 11)],
        "가디건": [card(i, final_price=10000 * i) for i in range(5, 21)],  # 5~10 은 겹침
    }
    monkeypatch.setattr(
        scout,
        "search_responses",
        lambda queries, min_items=0: {q: [search_response(cards[q])] for q in queries},
    )
    source = zigzag.Source(today=TODAY)
    found = source.search(["니트", "가디건"], "상의", SearchFilters(cheap_percent=15))
    # 두 검색어 결과 20개(겹친 상품은 한 번) 중 하위 15% = 1~3만원은 뺀다
    assert source.floors == {"상의": 40000}
    assert min(p.price for p in found["니트"]) == 40000


def test_collect_drops_stored_products_below_cheap_floor(tmp_path: Path):
    store = Store("women", root=tmp_path)
    store.save_products([_stored(1, TODAY, price=15000), _stored(2, TODAY, price=80000)])

    class Floored(FakeSource):
        def search(self, queries, category, filters):
            self.floors[category] = 30000
            return super().search(queries, category, filters)

    source = Floored(cards={"q": []}, pages={})
    report = collect.collect(
        "women", plan={"아우터": ["q"]}, source=source, store=store, fetch=fake_fetch, today=TODAY
    )
    assert [p.id for p in store.products()] == ["zigzag:2"]
    assert report.unfit == 1 and report.floors == {"아우터": 30000}
