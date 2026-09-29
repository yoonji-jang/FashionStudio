import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from PIL import Image

from core.models import DailyPlan, Manifest, PostRecord, Product
from core.profile import load_profile
from core.store import Store
from tools import feed, plan

DAY = date(2026, 9, 27)
CATS = ["아우터", "상의", "하의", "가방", "신발", "소품", "소품"]


def product(i: int, category: str, shop: str | None = None, **kw) -> Product:
    return Product(
        id=f"zigzag:{i}",
        source="zigzag",
        url=f"https://zigzag.kr/catalog/products/{i}",
        shop=shop or f"샵{i}",
        name=f"[MADE] 상품 {i}",
        price=10000 * i,
        category=category,
        image_urls=[f"https://example.com/{i}_{k}.jpg" for k in range(3)],
        rating=4.8,
        review_count=100,
        **kw,
    )


@pytest.fixture
def store(tmp_path):
    s = Store("women", root=tmp_path / "data")
    s.save_products([product(i, c) for i, c in enumerate(CATS, 1)] + [product(99, "상의")])
    return s


@pytest.fixture(autouse=True)
def fake_photos(monkeypatch):
    """상품 사진을 받는 대신 색 사진을 만든다 (네트워크 없음)."""

    def fetch(url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            Image.new("RGB", (400, 500), (120, 90, 60)).save(dest)
        return dest

    monkeypatch.setattr(feed, "fetch_photo", fetch)


def daily(**kw) -> DailyPlan:
    items = []
    for i, cat in enumerate(CATS, 1):
        it = {"product": f"zigzag:{i}", "desc": f"상품 {i} (item {i})", "name": f"상품 {i}"}
        if cat == "소품":
            it["slot"] = "목걸이" if i == 6 else "귀걸이"
        items.append(it)
    items[1].update(wear_with="아우터", wear_modes=["peek"])
    data = {
        "account": "women",
        "date": DAY,
        "theme": {
            "date": DAY,
            "type": "아이템",
            "title_hint": "초가을 트렌치",
            "mood": "베이지",
            "slots": [{"category": "아우터", "desc": "베이지 트렌치"}],
        },
        "items": items,
        "reason": "톤 맞춤",
        "text": {
            "title_lines": ["아침저녁 쌀쌀할 땐", "트렌치코트 하나로"],
            "points": ["첫째 포인트", "둘째 포인트", "셋째 포인트"],
            "hashtags": ["#트렌치코트", "#가을코디", "#초가을코디", "#셔츠코디", "#주말코디"],
        },
    }
    data.update(kw)
    return DailyPlan.model_validate(data)


def test_sample_plan_passes_check():
    p = plan.load_plan(Path("samples/women/plan_2026-09-27.yaml"))
    errors, _ = plan.check(p, load_profile("women"), Store("women"))
    assert errors == []


def test_check_passes(store):
    errors, warns = plan.check(daily(), load_profile("women"), store)
    assert errors == [] and warns == []


def test_check_catches_rule_breaks(store):
    profile = load_profile("women")
    profile.monetize = "none"
    store.save_posts([PostRecord(date=date(2026, 9, 20), title="t", products=["zigzag:3"])])
    p = daily()
    p.items[0].product = "zigzag:404"  # 창고에 없음
    p.items[4].photo = 7  # 없는 사진
    p.items[5].comment = "무조건 사세요"  # 금지 표현
    p.text.title_lines = ["제목에 이모지 🍂", "둘째 줄입니다요"]
    p.text.points.append("넷째")
    p.text.hashtags = ["#광고", "띄어 쓴 태그"]
    errors, _ = plan.check(p, profile, store)
    text = "\n".join(errors)
    for expected in [
        "상품 창고에 없습니다",
        "재사용 금지 기간",
        "7 번 사진이 없습니다",
        "금지 표현 '무조건'",
        "이모지",
        "코디 포인트는 3~3줄",
        "해시태그 형식",
        "monetize: none 인데 #광고",
        "wear_with 아우터 상품이 코디에 없습니다",
    ]:
        assert expected in text, expected


def test_check_counts_shops_and_required(store):
    profile = load_profile("women")
    products = store.products()
    for p in products[:3]:
        p.shop = "같은샵"
    store.save_products(products)
    p = daily()
    p.items = [it for it in p.items if it.product != "zigzag:4"]  # 가방 빼기
    errors, _ = plan.check(p, profile, store)
    assert any("같은샵" in e for e in errors)
    assert any("필수 카테고리 가방" in e for e in errors)


def test_candidates_skip_used_and_sold_out(store):
    profile = load_profile("women")
    products = store.products()
    products[1].status = "sold_out"
    store.save_products(products)
    store.save_posts([PostRecord(date=date(2026, 9, 25), title="t", products=["zigzag:99"])])
    found = plan.candidates(profile, store, DAY, category="상의")
    assert found == []
    assert "원문" in plan.candidate_line(products[0])


def test_caption_manual_link_adds_ad_notice(store):
    profile = load_profile("women")
    profile.monetize = "none"
    p = daily()
    body = plan.caption(p, profile, store)
    assert body.startswith("첫째 포인트")
    assert profile.copy_rules.ai_notice in body
    assert "샵1 · 상품 1" in body
    tags = plan.final_hashtags(p, profile)
    assert "#광고" not in tags and tags[-1] == profile.copy_rules.base_hashtags[-1]

    profile.monetize = "manual_link"
    body = plan.caption(p, profile, store)
    assert body.splitlines()[0] == profile.copy_rules.ad_notice
    assert plan.final_hashtags(p, profile)[0] == "#광고"


def test_caption_uses_comment_cta_when_enabled(store):
    profile = load_profile("women")
    link = profile.copy_rules.link_line
    profile.comments.enabled = False
    assert link in plan.caption(daily(), profile, store)
    profile.comments.enabled = True
    body = plan.caption(daily(), profile, store)
    assert link not in body and profile.comments.cta in body
    profile.comments.cta = "댓글로 '{keyword}'라고 남겨 주세요"
    profile.comments.keywords = ["정보"]
    assert "댓글로 '정보'라고" in plan.caption(daily(), profile, store)
    assert body.rstrip().endswith(profile.copy_rules.ai_notice)


def test_context_mentions_stock_and_theme(store):
    profile = load_profile("women")
    store.save_themes([daily().theme])
    text = plan.context(profile, store, DAY, with_weather=False)
    assert "일요일" in text and "초가을" in text
    assert "초가을 트렌치" in text
    assert "| 상의 | 2 | 2 | 0 |" in text


def test_save_themes_merges_by_date(store):
    profile = load_profile("women")
    t = daily().theme
    plan.save_themes(profile, store, [t])
    plan.save_themes(profile, store, [t.model_copy(update={"title_hint": "바뀐 주제"})])
    assert [x.title_hint for x in store.themes()] == ["바뀐 주제"]
    with pytest.raises(ValueError):
        plan.save_themes(profile, store, [t.model_copy(update={"title_hint": "정치·사회 이슈"})])


def test_photo_sheet(store, tmp_path):
    p = daily()
    photos = plan.fetch_candidates(p, load_profile("women"), store, tmp_path / "photos")
    assert [r["index"] for r in photos["zigzag:1"]] == [0, 1, 2]
    out = plan.photo_sheet(p, photos, tmp_path / "sheet.jpg")
    assert Image.open(out).width > 1000


def test_build_without_outfit(store, tmp_path):
    p = daily()
    p.items[0].photo = 2
    m = plan.build(p, tmp_path / "out", load_profile("women"), store, outfit="none")
    names = [Path(x).name for x in m.images]
    assert names[0] == "01_아우터.jpg" and names[-1] == "08_summary.jpg"
    assert (tmp_path / "out" / "photos" / "1_2.jpg").exists()
    saved = Manifest.model_validate_json((tmp_path / "out" / "manifest.json").read_text())
    assert saved.hashtags == m.hashtags and saved.needs_human
    assert "#트렌치코트" in (tmp_path / "out" / "caption.txt").read_text()
    assert (tmp_path / "out" / "preview.jpg").exists()


def test_build_with_fake_outfit(store, tmp_path):
    m = plan.build(daily(), tmp_path / "out", load_profile("women"), store, fake=True)
    assert Path(m.images[0]).name == "01_cover.jpg"
    samples = yaml.safe_load((tmp_path / "out" / "outfit" / "samples.yaml").read_text())
    shirt = samples["items"][1]
    assert shirt["wear_with"] == "아우터" and shirt["wear_modes"] == ["peek"]
    assert samples["items"][5]["slot"] == "목걸이"


def test_outfit_samples_wears_top_and_sets_length(store, tmp_path):
    p = daily()
    p.items[0].desc = "차콜 롱코트 (charcoal long coat)"
    p.items[1].wear_with = None
    p.items[1].wear_modes = None
    data = plan.outfit_samples(p, store, tmp_path)
    coat, top = data["items"][0], data["items"][1]
    assert coat["cm"] == 110  # 이름의 "롱"으로 실제 기장
    assert top["wear_with"] == "아우터" and top["wear_modes"] == ["over"]
    assert "open" in top["wear_how"]
    assert "cm" not in data["items"][2]  # 맞는 말이 없으면 기본값
    p.items[0].cm = 95
    assert plan.outfit_samples(p, store, tmp_path)["items"][0]["cm"] == 95  # 기획 값이 먼저
    p.items[1].wear_modes = ["beside"]
    top = plan.outfit_samples(p, store, tmp_path)["items"][1]
    assert not any(k.startswith("wear_") for k in top)  # 입히지 않고 옆에 둠


def test_build_refuses_plan_with_errors(store, tmp_path):
    p = daily(text=None)
    with pytest.raises(ValueError, match="text"):
        plan.build(p, tmp_path / "out", load_profile("women"), store, outfit="none")


def test_cli_check_exit_code(tmp_path, monkeypatch, capsys):
    path = tmp_path / "plan.yaml"
    data = json.loads(daily().model_dump_json())
    data["items"][0]["product"] = "zigzag:404"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert plan.main(["check", str(path)]) == 1
    assert "오류" in capsys.readouterr().out
