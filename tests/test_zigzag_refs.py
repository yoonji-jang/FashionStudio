from datetime import date

from tests.test_refs import ref
from tools import zigzag_refs as zr

DAY = date(2026, 11, 16)


def test_parse_terms():
    assert zr.parse_terms("레드 니트; 벨벳=벨벳 원피스, 벨벳 스커트\n퍼=퍼 자켓;;") == [
        ("레드 니트", ["레드 니트"]),
        ("벨벳", ["벨벳 원피스", "벨벳 스커트"]),
        ("퍼", ["퍼 자켓"]),
    ]


def test_pick_cards_sorts_by_reviews_and_skips_sold_out_and_seen():
    cards = [
        {"catalog_product_id": 1, "display_review_count": "12"},
        {"catalog_product_id": 2, "display_review_count": "1.2천"},
        {"catalog_product_id": 3, "display_review_count": "999+", "sellable_status": "SOLD_OUT"},
        {"catalog_product_id": 4, "review_count": 300},
        {"catalog_product_id": 1, "display_review_count": "12"},
        {"catalog_product_id": 5},
    ]
    got = zr.pick_cards([{"items": cards}], 3, seen={"4"})
    assert [c["catalog_product_id"] for c in got] == [2, 1, 5]


def _index():
    rows = []
    red = "레드 니트"
    for i, (style, shop) in enumerate(
        [(red, "A"), (red, "A"), (red, "B"), (red, "A"), ("벨벳", "A")], 1
    ):
        rows.append(
            {
                "style": style,
                "shop": shop,
                "product": f"zigzag:{i}",
                "images": [f"https://cf.zigzag.kr/{i}_1.jpg", f"https://cf.zigzag.kr/{i}_2.jpg"],
            }
        )
    return rows


def _item(photo, name="n"):
    return {
        "name": name,
        "photo": photo,
        "tags": ["저녁 데이트"],
        "note": "",
        "slots": [
            {"category": "상의", "color": "레드", "desc": "레드 니트", "query": "니트"},
            {"category": "하의", "color": "블랙", "desc": "블랙 스커트", "query": "스커트"},
        ],
    }


def test_add_checks_caps_and_fills_source_tags():
    existing = [ref(n=830)]
    items = [
        _item("https://cf.zigzag.kr/1_2.jpg"),
        _item("https://cf.zigzag.kr/1_1.jpg"),  # 같은 상품
        _item("https://example.com/x.jpg"),  # 확인표에 없음
        _item("https://cf.zigzag.kr/2_1.jpg"),
        _item("https://cf.zigzag.kr/3_1.jpg"),
        _item("https://cf.zigzag.kr/4_1.jpg"),  # 레드 니트 3벌 넘음
    ]
    refs, added, skipped = zr.add(items, _index(), [], existing, "크리스마스 데이트룩", 3, 2, DAY)
    assert [r.n for r in refs] == [830, 831, 832, 833]
    new = refs[1]
    assert new.source == zr.SOURCE and new.added == DAY
    assert new.tags == ["크리스마스 데이트룩", "레드 니트", "저녁 데이트"]
    assert [a["product"] for a in added] == ["zigzag:1", "zigzag:2", "zigzag:3"]
    assert len(skipped) == 3

    # 다음 차례: 쇼핑몰 A 는 이미 2벌 (per_shop 2)
    refs2, added2, skipped2 = zr.add(
        [_item("https://cf.zigzag.kr/5_1.jpg")], _index(), added, refs, "", 3, 2, DAY
    )
    assert len(refs2) == len(refs) and len(added2) == 3
    assert "쇼핑몰" in skipped2[0]


def test_add_cli_writes_numbers(tmp_path, monkeypatch):
    import json

    from core import store as store_mod

    monkeypatch.setattr(store_mod, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data" / "women").mkdir(parents=True)
    out = tmp_path / "zz"
    out.mkdir()
    (out / "index.json").write_text(json.dumps(_index()), "utf-8")
    new = tmp_path / "new.json"
    new.write_text(json.dumps([_item("https://cf.zigzag.kr/5_2.jpg")]), "utf-8")
    monkeypatch.setattr(zr, "Store", lambda acct: store_mod.Store(acct, tmp_path / "data"))
    assert zr.main(["add", "--account", "women", "--new", str(new), "--out", str(out)]) == 0
    assert json.loads((out / "added.json").read_text())[0]["n"] == 101
