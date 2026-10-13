from datetime import date

from core.models import RefLook
from tests.test_plan import product
from tools import refs

DAY = date(2026, 10, 19)


def ref(n=101, added=DAY, photo="https://woonan.co.kr/a.jpg", **kw):
    slots = kw.pop("slots", None) or [
        {
            "category": "상의",
            "color": "차콜",
            "desc": "차콜 모크넥 베스트",
            "query": "모크넥 니트베스트",
        },
        {"category": "하의", "color": "블랙", "desc": "블랙 미디스커트", "query": "미디 스커트"},
        {"category": "가방", "color": "블랙", "desc": "블랙 토트", "query": "토트백"},
    ]
    return RefLook(
        n=n, name="t", source="쇼핑몰 코디 (우난)", photo=photo, added=added, slots=slots
    )


def test_match_needs_kind_and_color_for_required_slots():
    vest = product(1, "상의", colors=["챠콜", "아이보리"])
    vest = vest.model_copy(update={"name": "울 모크넥 니트 베스트"})
    skirt_navy = product(2, "하의", colors=["네이비"]).model_copy(update={"name": "미디 스커트"})
    [r] = refs.match([ref()], [vest, skirt_navy])
    assert r.matches == {"0": ["zigzag:1"]}  # 차콜 = 챠콜, 띄어쓰기 달라도 맞음
    assert not r.ready  # 블랙 스커트가 없음 (가방은 없어도 됨)
    assert refs.missing_queries([r]) == {"하의": ["블랙 미디 스커트", "블랙 스커트"]}

    skirt = product(3, "하의", colors=["블랙"]).model_copy(update={"name": "A라인 미디 스커트"})
    [r] = refs.match([ref()], [vest, skirt])
    assert r.ready and refs.missing_queries([r]) == {}


def test_check_reports_problems_and_drops_old():
    old = ref(n=102, added=date(2026, 9, 1))
    bad = ref(n=101, photo=None)
    kept, errors = refs.check([ref(), bad, old], keep_weeks=4, today=DAY)
    assert [r.n for r in kept] == [101, 101]
    assert any("번호가 겹침" in e for e in errors) and any("사진 주소" in e for e in errors)


def test_crawl_visits_menus_then_product_pages(tmp_path, monkeypatch):
    from tools import scout

    home = "https://woonan.co.kr/"
    pages = {
        home: [
            ("로그인", home + "member/login.html"),
            ("BEST 30", home + "category/best/42/"),
            ("OUTER", home + "category/outer/24/"),
            ("공지", home + "board/notice/1/"),
        ],
        home + "category/best/42/": [
            ("a", home + "product/coat/101/category/42/"),
            ("a", home + "product/coat/101/"),
            ("b", home + "product/knit/102/"),
        ],
        home + "category/outer/24/": [("c", home + "product/jacket/103/category/24/")],
    }
    monkeypatch.setattr(
        scout, "page_links", lambda url, sites, limit=200, scrolls=0: pages.get(url, [])
    )
    got = {}
    monkeypatch.setattr(
        scout, "ref_sheets", lambda urls, sites, out, n: got.setdefault("urls", urls) and []
    )
    scout.crawl(home, ["woonan.co.kr"], tmp_path, menus=5, products=10)
    # 로그인·공지는 건너뛰고, 메뉴를 번갈아, 같은 상품(101)은 한 번만
    assert got["urls"] == [
        home + "product/coat/101/category/42/",
        home + "product/jacket/103/category/24/",
        home + "product/knit/102/",
    ]


def test_match_trusts_products_found_by_the_slot_query():
    vest = product(5, "상의").model_copy(
        update={"name": "터틀넥 니트 조끼", "query": "차콜 모크넥 니트베스트"}
    )
    skirt = product(6, "하의", colors=["블랙"]).model_copy(update={"name": "미디 스커트"})
    [r] = refs.match([ref()], [vest, skirt])
    assert r.ready and r.matches["0"] == ["zigzag:5"]


def test_merge_keeps_remote_matches_and_adds_new():
    remote = refs.match([ref(n=101)], [])
    merged = refs.merge(remote, [ref(n=101), ref(n=130)])
    assert [r.n for r in merged] == [101, 130] and merged[0] is remote[0]


def test_zigzag_pages_rotates_categories_and_shops():
    ps = [
        product(1, "아우터", shop="A"),
        product(2, "아우터", shop="A"),
        product(3, "아우터", shop="B"),
        product(4, "상의", shop="A"),
        product(5, "가방", shop="C"),
    ]
    used = ref(photo=str(ps[2].image_urls[1]))  # 3번 상품 사진은 이미 참고로 씀
    picked = refs.zigzag_pages(ps, [used], count=3)
    assert [p.id for p in picked] == ["zigzag:1", "zigzag:4", "zigzag:2"]


def test_add_numbers_new_refs_after_existing(tmp_path, monkeypatch):
    import json

    from core.store import Store

    store = Store("women", root=tmp_path)
    store.save_refs([ref(n=131)])
    monkeypatch.setattr(refs, "Store", lambda acc: store)
    item = ref().model_dump(mode="json", exclude={"n", "matches", "ready"})
    new = tmp_path / "new.json"
    new.write_text(json.dumps([item, item]), encoding="utf-8")
    assert refs.main(["add", "--account", "women", "--new", str(new)]) == 0
    assert [r.n for r in store.refs()] == [131, 132, 133]
