import io
import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from PIL import Image

from core.models import DailyPlan, Manifest, PlanItem, PostRecord, Product
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
        price=60000 + 100 * i,  # 계정 가격대(21단계) 안
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
            "intro": "도입 한 줄이에요 🍂",
            "points": ["첫째 포인트", "둘째 포인트", "셋째 포인트"],
            "hashtags": ["#트렌치코트", "#가을코디", "#초가을코디", "#셔츠코디", "#주말코디"],
        },
    }
    data.update(kw)
    return DailyPlan.model_validate(data)


def test_sample_plan_passes_check(tmp_path):
    p = plan.load_plan(Path("samples/women/plan_2026-09-27.yaml"))
    # 샘플 상품은 21단계 수집 기준(가격대·판매량 제목)으로 실제 창고에서 빠졌으니 형식만 본다
    cats = ["아우터", "상의", "하의", "가방", "신발", "소품", "소품"]
    photos = [f"https://example.com/{k}.jpg" for k in range(6)]
    s = Store("women", root=tmp_path / "data")
    s.save_products(
        [
            product(int(it.product.split(":")[1]), c).model_copy(update={"image_urls": photos})
            for it, c in zip(p.items, cats, strict=True)
        ]
    )
    errors, _ = plan.check(p, load_profile("women"), s)
    assert errors == []


def test_check_passes(store):
    errors, warns = plan.check(daily(), load_profile("women"), store)
    assert errors == []
    assert warns == [
        "포인트 아이템(profile style.point_words)이 없습니다"
    ]  # 테스트 상품 이름이 평범
    # 이름에 포인트 말(21단계)이 있는 상품이 하나라도 있으면 경고가 없다
    point = store.products()[0].model_copy(update={"name": "[MADE] 스웨이드 상품 1"})
    store.save_products([point] + store.products()[1:])
    assert plan.check(daily(), load_profile("women"), store) == ([], [])


def test_busy_warnings():
    # 2026-10-06 "꾸역꾸역 레이어드해서 답답하다": 네 겹, 무늬 둘, 소품 셋이면 경고
    profile = load_profile("women")
    names = ["레더 자켓", "니트 베스트", "스트라이프 셔츠", "나시", "체크 스커트", "숄더백", "로퍼"]
    cats = ["아우터", "상의", "상의", "상의", "하의", "가방", "신발", "소품", "소품", "소품"]
    items = [
        product(i, c).model_copy(update={"name": n})
        for i, (c, n) in enumerate(zip(cats, names, strict=False), 1)
    ]
    warns = plan.busy_warnings("1번 벌: ", cats, items, profile)
    assert [w.split(" (")[0] for w in warns] == [
        "1번 벌: 아우터·상의가 4겹입니다",
        "1번 벌: 무늬 있는 상품이 2개입니다",
        "1번 벌: 소품이 3개입니다",
    ]
    # 세 겹(안에 얇은 이너)까지는 괜찮다
    assert plan.busy_warnings("", cats[:3], items[:2] + items[3:4], profile) == []


def test_inner_warning_for_deep_neck():
    # 딥 브이넥 상의 한 장이면 안에 티·나시를 받쳐 입히라고 경고
    profile = load_profile("women")
    deep = product(1, "상의").model_copy(update={"name": "딥 브이넥 니트"})
    tank = product(2, "상의").model_copy(update={"name": "골지 나시"})
    warns = plan.busy_warnings("", ["상의", "하의"], [deep, product(3, "하의")], profile)
    assert [w.split(" (")[0] for w in warns] == [
        "목선이 파인 상의라 안에 얇은 이너(티·나시)를 받쳐 입히세요"
    ]
    assert plan.busy_warnings("", ["상의", "상의", "하의"], [deep, tank], profile) == []


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


def test_check_title_banned_weekday(store):
    p = daily()
    p.text.title_lines = ["비 오는 수요일 출근길엔", "트렌치 끈 묶어 입기"]
    errors, _ = plan.check(p, load_profile("women"), store)
    assert any("쓰지 않는 말 '수요일'" in e for e in errors)


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


def test_check_intro_and_emoji_rules(store):
    profile = load_profile("women")
    p = daily()
    p.text.intro = None
    p.text.points = ["첫째 🍂", "둘째 🖤🤍", "셋째 ☕"]
    p.items[0].comment = "가볍게 걸쳐요 🍂"
    errors, _ = plan.check(p, profile, store)
    text = "\n".join(errors)
    for expected in [
        "도입 한 줄",
        "이모지는 3개까지",
        "한 줄에 이모지는 1개까지",
        "이모지를 쓰지 않습니다",
    ]:
        assert expected in text, expected

    profile.copy_rules.intro = False
    p = daily()
    p.text.intro = None
    errors, _ = plan.check(p, profile, store)
    assert errors == []
    assert plan.caption(p, profile, store).startswith("첫째 포인트")


def test_caption_manual_link_adds_ad_notice(store):
    profile = load_profile("women")
    profile.monetize = "none"
    p = daily()
    body = plan.caption(p, profile, store)
    assert body.startswith("도입 한 줄이에요 🍂\n\n첫째 포인트")
    assert profile.copy_rules.ai_notice in body
    assert "샵1 · 상품 1" in body
    tags = plan.final_hashtags(p, profile)
    assert "#광고" not in tags and tags[-1] == profile.copy_rules.base_hashtags[-1]

    profile.monetize = "manual_link"
    body = plan.caption(p, profile, store)
    assert body.splitlines()[0] == profile.copy_rules.ad_notice
    assert body.splitlines()[2] == p.text.intro
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
    assert names[0] == "01_아우터.jpg" and names[-2:] == ["08_summary.jpg", "09_comment.jpg"]
    assert (tmp_path / "out" / "photos" / "1_2.jpg").exists()
    saved = Manifest.model_validate_json((tmp_path / "out" / "manifest.json").read_text())
    assert saved.hashtags == m.hashtags and saved.needs_human
    assert "#트렌치코트" in (tmp_path / "out" / "caption.txt").read_text()
    assert (tmp_path / "out" / "preview.jpg").exists()


def test_build_with_fake_outfit(store, tmp_path):
    m = plan.build(
        daily(), tmp_path / "out", load_profile("women"), store, outfit="photoroom", fake=True
    )
    assert Path(m.images[0]).name == "01_cover.jpg"
    samples = yaml.safe_load((tmp_path / "out" / "outfit" / "samples.yaml").read_text())
    shirt = samples["items"][1]
    assert shirt["wear_with"] == "아우터" and shirt["wear_modes"] == ["peek"]
    assert samples["items"][5]["slot"] == "목걸이"


def test_build_with_generated_outfit(store, tmp_path):
    """기본(profile outfit_image.engine: generate): 쇼핑몰 사진 참고판으로 생성형 코디컷 후보."""
    out = tmp_path / "out"
    profile = load_profile("women")
    m = plan.build(daily(), out, profile, store, fake=True)
    assert Path(m.images[0]).name == "01_cover.jpg"
    assert (out / "outfit" / "engine.txt").read_text() == "generate"
    assert len(list((out / "outfit" / "results").glob("G_*.jpg"))) == profile.review.candidates
    assert len(list((out / "outfit" / "originals").glob("*.jpg"))) == len(CATS)
    prompt = (out / "outfit" / "prompt.txt").read_text()
    assert "The first sheet: the outer, item 1." in prompt
    assert "exactly one pair (two earrings)" in prompt  # 귀걸이 개수
    assert "almost closed" in prompt  # 아우터 기본 입히기 (profile outer_styling)
    assert "$" not in prompt
    assert "코디컷 생성 비용" in (out / "report.md").read_text()
    assert not (out / "outfit" / "samples.yaml").exists()  # Photoroom 흐름을 타지 않음


def test_rebuild_with_cover_keeps_generation_cost(store, tmp_path, monkeypatch):
    """검수 뒤 cover 를 정해 다시 빌드해도 report.md 에 생성 비용이 남는다."""
    out = tmp_path / "out"
    profile = load_profile("women")
    plan.build(daily(), out, profile, store, fake=True)
    monkeypatch.setattr(plan, "REPO_ROOT", tmp_path)
    p = daily(cover="out/outfit/results/G_1.jpg")
    plan.build(p, out, profile, store, fake=True)
    assert "코디컷 생성 비용" in (out / "report.md").read_text()


def test_generated_outfit_prompt_parts(store):
    from tools import outfit_gen

    p = daily(styling="The jacket is buttoned.")
    products = {x.id: x for x in store.products()}
    p.items[0].ref_photos = [2, 0, 2]
    p.items[1].photo = 1
    assert outfit_gen.ref_indexes(p.items[0]) == [2, 0]
    assert outfit_gen.ref_indexes(p.items[1]) == [0, 1]  # 없으면 cutout_photo + photo
    assert outfit_gen.english("차콜 롱코트 (charcoal long coat)") == "charcoal long coat"
    nested = "블랙 부츠 (black ankle boots, one pair (two boots))"
    assert outfit_gen.english(nested) == "black ankle boots, one pair (two boots)"
    profile = load_profile("women")
    assert outfit_gen.styling_text(p, products, profile) == "The jacket is buttoned."
    p.items[0].size = "about 50 cm long"
    cfg = outfit_gen.generate.load_config()
    assert "Real size: about 50 cm long." in outfit_gen.item_lines(p, products, cfg)


def test_layered_tops_get_layer_styling(store):
    """상의 레이어드 (21단계): wear_with: 상의 인 셔츠를 다른 상의(니트) 안에 입힌다."""
    from tools import outfit_gen

    p = daily()
    p.items[1].wear_with = None
    p.items[1].wear_modes = None
    p.items.insert(
        2, PlanItem(product="zigzag:99", desc="셔츠 (shirt)", name="셔츠", wear_with="상의")
    )
    products = {x.id: x for x in store.products()}
    text = outfit_gen.styling_text(p, products, load_profile("women"))
    assert "The top in the third sheet is layered under the top in the second sheet" in text
    p.items[2].wear_with = None
    assert "layered under" not in outfit_gen.styling_text(p, products, load_profile("women"))


def test_prompt_parts_only_for_items_in_the_look(store):
    """코디컷 지시문 조각 (2026-10-12): 그 벌의 하의·신발·소품에 맞는 조각만 붙는다."""
    from tools import outfit_gen

    p = daily()
    products = {x.id: x for x in store.products()}
    p.items[2].desc = "블랙 와이드 슬랙스 (black wide slacks)"
    p.items[4].desc = "블랙 로퍼 (black loafers)"
    p.items[5].desc = "실버 목걸이 (silver necklace)"
    names = [t.split(".")[0] for t in outfit_gen.prompt_parts(p, products, "")]
    text = " ".join(outfit_gen.prompt_parts(p, products, ""))
    assert "The trousers keep" in text and "seen from directly above" in text
    assert "skirt" not in text and "boots lie" not in text and "vest" not in text
    assert "Jewelry and watches" in text and len(names) == 3

    p.items[2].desc = "차콜 플리츠 롱스커트 (charcoal pleated long skirt)"
    p.items[4].desc = "블랙 롱부츠 (black long boots)"
    text = " ".join(outfit_gen.prompt_parts(p, products, "draped over the shoulders"))
    assert "The skirt keeps" in text and "The trousers" not in text
    assert "The boots lie on their side" in text and "seen from directly above" not in text
    assert "knit draped over the shoulders" in text


def test_outfit_prompt_stays_short(store):
    """지시문이 길면 이미지 AI 가 지시를 놓친다 (2026-10-12, 2,500단어였음).
    본문과 모든 조각을 한꺼번에 합쳐도(실제 한 벌은 일부만) 1,000단어를 넘지 않고,
    고정 구도가 들어가며 $ 자리가 남지 않는다."""
    from tools import outfit_gen
    from tools.photoroom import render_prompt

    p = daily()
    products = {x.id: x for x in store.products()}
    prof = load_profile("women")
    prompt = outfit_gen.build_prompt(p, products, prof, {})
    assert "$" not in prompt and "lower right beside the bottoms" in prompt
    rules = yaml.safe_load((outfit_gen.PARTS_DIR / "parts.yaml").read_text(encoding="utf-8"))
    every_part = " ".join(render_prompt(n, outfit_gen.PARTS_DIR) for n in rules)
    base = render_prompt(
        prof.outfit_image.prompt,
        items="",
        styling="",
        parts=every_part,
        layout=prof.looks.composition,
        scene=prof.outfit_image.scene,
    )
    assert len(base.split()) <= 1000


def test_check_ref_photos_index(store):
    p = daily()
    p.items[0].ref_photos = [0, 5]
    errors, _ = plan.check(p, load_profile("women"), store)
    assert any("ref_photos" in e for e in errors)


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


def test_fix_edits_previous_outfit_image(store, tmp_path, monkeypatch):
    """/fix 고쳐 그리기 (2026-10-13): edit_from 이 있으면 지난 코디컷을 첫 그림으로 보내고
    고칠 점만 적은 지시문을 쓴다. 상품 참고판 번호는 2번부터."""
    from tools import outfit_gen

    base = tmp_path / "prev.jpg"
    Image.new("RGB", (40, 50), "#888888").save(base)
    monkeypatch.setattr(outfit_gen, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(outfit_gen, "reference_panels", lambda *a, **k: [b"p1", b"p2"])

    class Recorder:
        def generate(self, model, prompt, images):
            self.prompt, self.images = prompt, images
            buf = io.BytesIO()
            Image.new("RGB", (1080, 1350)).save(buf, "PNG")
            return buf.getvalue(), 0.0

    rec = Recorder()
    p = daily(edit_from="prev.jpg", edit_request="Pair the shoes neatly.")
    made = outfit_gen.make_candidates(
        p, store, tmp_path / "w", load_profile("women"), 1, client=rec
    )
    assert len(made) == 1
    assert rec.images == [base.read_bytes(), b"p1", b"p2"]
    assert "Pair the shoes neatly." in rec.prompt and "$" not in rec.prompt
    assert "The second sheet: the" in rec.prompt and "The first sheet: the" not in rec.prompt

    p = daily()
    outfit_gen.make_candidates(p, store, tmp_path / "w2", load_profile("women"), 1, client=rec)
    assert rec.images == [b"p1", b"p2"] and "Pair the shoes" not in rec.prompt


def test_check_catches_missing_edit_from(store):
    errors, _ = plan.check(daily(edit_from="out/없는/G_1.jpg"), load_profile("women"), store)
    assert any("edit_from 파일이 없습니다" in e for e in errors)


def test_worn_ref_photo_goes_last(store, tmp_path, monkeypatch):
    """기준 코디 사진(ref_photo)이 있으면 마지막 그림으로 보내 입는 모양을 따르게 한다 (2026-10-14).
    받지 못하면 없이 그린다."""
    from tools import outfit_gen, scout

    photo = tmp_path / "ref.jpg"
    Image.new("RGB", (60, 90), "#777777").save(photo)
    monkeypatch.setattr(outfit_gen, "reference_panels", lambda *a, **k: [b"p1"])
    monkeypatch.setattr(scout, "ref_photo", lambda url, sites, dest: photo)
    monkeypatch.setattr(outfit_gen.faces, "hide_faces", lambda img: (img, True))

    class Recorder:
        def generate(self, model, prompt, images):
            self.prompt, self.images = prompt, images
            buf = io.BytesIO()
            Image.new("RGB", (1080, 1350)).save(buf, "PNG")
            return buf.getvalue(), 0.0

    rec = Recorder()
    p = daily(ref_photo="https://woonan.co.kr/a.jpg")
    outfit_gen.make_candidates(p, store, tmp_path / "w", load_profile("women"), 1, client=rec)
    assert len(rec.images) == 2 and rec.images[0] == b"p1"
    assert "The last attached image" in rec.prompt
    assert (tmp_path / "w/originals/worn_ref.jpg").exists()

    def fail(*a):
        raise scout.ImageUnavailable("x")

    monkeypatch.setattr(scout, "ref_photo", fail)
    outfit_gen.make_candidates(p, store, tmp_path / "w2", load_profile("women"), 1, client=rec)
    assert rec.images == [b"p1"] and "The last attached image" not in rec.prompt


def test_scarf_gets_scarf_size_even_in_necklace_slot(store):
    from tools import outfit_gen

    p = daily()
    products = {x.id: x for x in store.products()}
    it = p.items[5].model_copy(update={"desc": "burgundy knit scarf", "slot": "목걸이"})
    cfg = {"default_sizes": {"목걸이": "one necklace", "스카프": "one scarf"}}
    assert outfit_gen.item_size(it, products[it.product], cfg) == "one scarf"
