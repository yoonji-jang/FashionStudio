"""코디 여러 벌 게시물 (20단계, profile looks)."""

import json
import shutil
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml
from PIL import Image

from core.models import (
    Candidate,
    CoverScore,
    DailyPlan,
    Manifest,
    PickRecord,
    PreviewRecord,
    Review,
)
from core.profile import load_profile
from core.store import Store
from tests.test_pick import rec, tg_of, upd, write_candidates
from tests.test_plan import fake_photos, product  # noqa: F401 - fixture
from tests.test_publish import CHAT, FakeApis
from tools import ai_label, cover_look, feedback, looks, notify, pick, plan, publish, review, share

pytestmark = pytest.mark.looks
DAY = date(2026, 10, 2)
CLOTHES = ["아우터", "상의", "하의"]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """8벌을 만들 수 있는 창고: 옷은 벌마다 다르고 가방·신발은 두 벌씩 같이 쓴다."""
    s = Store("women", root=tmp_path / "data")
    for mod in (review, plan, looks):
        monkeypatch.setattr(mod, "Store", lambda account: s)
    items = []
    for n in range(1, 9):
        for k, cat in enumerate(CLOTHES):
            items.append(product(n * 10 + k, cat))
    for k in range(4):
        items.append(product(200 + k, "가방"))
        items.append(product(300 + k, "신발"))
    s.save_products(items)
    return s


def look(n: int, **kw) -> dict:
    ids = [n * 10 + k for k in range(3)] + [200 + (n - 1) // 2, 300 + (n - 1) // 2]
    data = {
        "items": [
            {"product": f"zigzag:{i}", "desc": f"상품 {i} (item {i})", "name": f"상품 {i}"}
            for i in ids
        ],
        "reason": f"{n}번 벌은 톤온톤으로. 둘째 문장",
        "point": f"{n}번 코디 한 줄 설명",
    }
    return data | kw


def looks_plan(count: int = 6, **kw) -> DailyPlan:
    data = {
        "account": "women",
        "date": DAY,
        "theme": {
            "date": DAY,
            "type": "상황",
            "title_hint": "가을 출근룩 6가지",
            "mood": "차분",
            "slots": [{"category": "아우터", "desc": "가을 아우터"}],
        },
        "looks": [look(n) for n in range(1, count + 1)],
        "reason": "같은 주제로 색과 아우터를 다르게",
        "text": {
            "title_lines": ["출근룩 고민될 때", "가을 코디 모음"],
            "intro": "출근룩 고민될 때 하나씩 골라 입어 보세요 🍂",
            "hashtags": ["#출근룩", "#가을코디", "#오피스룩", "#직장인코디", "#데일리코디"],
        },
    }
    return DailyPlan.model_validate(data | kw)


def test_plan_needs_items_or_looks():
    with pytest.raises(ValueError):
        DailyPlan.model_validate(looks_plan().model_dump() | {"items": looks_plan().looks[0].items})
    p = looks_plan()
    assert len(p.all_items()) == 6 * 3 + 3 + 3  # 가방·신발은 두 벌씩 같이 써서 한 번만
    sub = p.look_plan(1)
    assert sub.items[0].product == "zigzag:20" and not sub.looks
    assert sub.reason.startswith("2번 벌")


def test_check_looks_rules(store):
    profile = load_profile("women")
    errors, _ = plan.check(looks_plan(), profile, store)
    assert errors == []
    # 후보(draft)는 예비 벌까지 8벌, 최종은 6벌까지
    assert plan.check(looks_plan(8, text=None), profile, store, draft=True)[0] == []
    assert any("5~6개" in e for e in plan.check(looks_plan(8), profile, store)[0])
    p = looks_plan()
    p.looks[1].items[0].product = "zigzag:10"  # 1번 벌 아우터를 2번 벌에도
    p.looks[2].point = ""
    p.looks[3].items[3].product = "zigzag:200"  # 가방을 세 벌에
    text = "\n".join(plan.check(p, profile, store)[0])
    assert "겹치는 옷은" not in text  # women 은 옷 2개까지 겹쳐도 됨
    p.looks[2].items[0].product = "zigzag:10"  # 같은 아우터를 세 벌에
    assert "최대 2벌" in "\n".join(plan.check(p, profile, store)[0])
    p.looks[2].items[0].product = looks_plan().looks[2].items[0].product
    strict = profile.model_copy(deep=True)
    strict.looks.max_shared_clothes = 0
    assert "겹치는 옷은 0개까지" in "\n".join(plan.check(p, strict, store)[0])
    assert "3번 벌 캡션 한 줄(point)이 없습니다" in text
    assert "최대 2벌" in text


def test_same_kind_warning(store):
    # 2026-10-06: 여섯 벌 모두 스웨이드 자켓이 되지 않게 (profile looks.max_same_kind 3)
    profile = load_profile("women")
    p = looks_plan()
    products = {x.id: x for x in store.products()}
    for n in range(1, 5):
        pid = f"zigzag:{n * 10}"
        products[pid] = products[pid].model_copy(update={"name": f"스웨이드 자켓 {n}"})
    warns = plan.same_kind_warnings(p, profile, products)
    assert warns == [
        "아우터 '스웨이드' 이(가) 4벌에 들어 있습니다 (최대 3벌, 벌마다 다른 아이템으로)"
    ]
    # 2026-10-13: 아이템 주제 날은 여섯 벌 모두 그 아이템이어도 된다
    p.theme.type = "아이템"
    assert plan.same_kind_warnings(p, profile, products) == []


def test_accessory_warning(store):
    # 2026-10-06 "잡화가 하나도 없네": 소품 있는 벌이 네 벌보다 적으면 경고
    profile = load_profile("women")
    p = looks_plan()
    products = {x.id: x for x in store.products()}
    assert plan.accessory_warnings(p, profile, products) == [
        "소품(주얼리·양말·모자 …)이 있는 벌이 0벌입니다 (4벌 이상, styling.md 6번)"
    ]
    acc = product(500, "소품")
    products[acc.id] = acc
    for look in p.looks[:4]:
        look.items.append(look.items[0].model_copy(update={"product": acc.id}))
    assert plan.accessory_warnings(p, profile, products) == []


def test_type_warning(store):
    # 2026-10-07 "전부 셔츠고 너무 무난한 기본템": 셔츠 상의가 든 벌은 두 벌까지
    profile = load_profile("women")
    assert profile.looks.type_limits.get("셔츠") == 2
    p = looks_plan()
    products = {x.id: x for x in store.products()}
    assert plan.type_warnings(p, profile, products) == []
    for look in p.looks[:3]:
        top = next(
            products[it.product] for it in look.items if products[it.product].category == "상의"
        )
        products[top.id] = top.model_copy(update={"name": top.name + " 오버핏 셔츠"})
    assert plan.type_warnings(p, profile, products) == [
        "'셔츠' 상의가 3벌에 들어 있습니다 (최대 2벌, 맨투맨·헨리넥·니트 등으로 섞기)"
    ]
    # 티셔츠는 셔츠로 세지 않는다 (profile looks.type_ignore)
    for look in p.looks[:3]:
        top = next(
            products[it.product] for it in look.items if products[it.product].category == "상의"
        )
        products[top.id] = top.model_copy(update={"name": top.name.replace("셔츠", "티셔츠")})
    assert plan.type_warnings(p, profile, products) == []


def test_single_plan_rejected_when_looks_enabled(store):
    from tests.test_plan import daily

    errors, _ = plan.check(daily(), load_profile("women"), store)
    assert any("looks(여러 벌)로 기획해야" in e for e in errors)


def test_caption_has_line_per_look(store):
    body = plan.caption(looks_plan(), load_profile("women"), store)
    assert "LOOK 1  1번 코디 한 줄 설명" in body and "LOOK 6" in body
    assert "샵10" not in body  # 상품 목록은 장마다 있고 링크는 댓글 DM


def build(tmp_path, store, p=None):
    out = tmp_path / "out"
    out.mkdir()
    p = p or looks_plan()
    (out / "plan.yaml").write_text(
        yaml.safe_dump(p.model_dump(mode="json", exclude_none=True), allow_unicode=True)
    )
    return out, plan.build(p, out, load_profile("women"), store, fake=True)


def test_build_makes_look_folders_and_slides(tmp_path, store):
    out, m = build(tmp_path, store)
    names = [Path(x).name for x in m.images]
    assert names == [
        "01_cover.jpg",
        *[f"{i + 2:02d}_look{i + 1}.jpg" for i in range(6)],
        "08_comment.jpg",
    ]
    for n in range(1, 7):
        d = out / "looks" / str(n)
        assert (d / "plan.yaml").exists() and (d / "outfit" / "engine.txt").exists()
        assert len(list((d / "outfit" / "results").glob("G_*.jpg"))) == 1  # looks.candidates
        assert (d / "review" / "review.json").exists()
    assert m.plan.looks[0] == ["zigzag:10", "zigzag:11", "zigzag:12", "zigzag:200", "zigzag:300"]
    assert m.needs_human and m.review.status == "not_reviewed"
    # AI 표시는 표지와 벌 장에만
    kinds = {Path(x).name: ai_label.read(out / "slides" / Path(x).name) for x in m.images}
    assert kinds["02_look1.jpg"] and kinds["01_cover.jpg"] and not kinds["08_comment.jpg"]
    # 릴스용 9:16 판은 표지·벌 장만 (reels.fill)
    tall = sorted(p.name for p in (out / "slides" / "tall").glob("*.jpg"))
    assert tall == names[:-1]
    assert Image.open(out / "slides" / "tall" / "02_look1.jpg").size == (1080, 1920)
    assert "LOOK 1" in (out / "caption.txt").read_text()
    assert "코디 6벌" in (out / "report.md").read_text()


def test_look_slide_text_does_not_fail_on_long_names(tmp_path, store):
    p = looks_plan()
    p.looks[0].items[0].name = "아주 긴 이름의 울 블렌드 더블 브레스티드 롱 코트"
    out, m = build(tmp_path, store, p)
    img = Image.open(out / "slides" / "02_look1.jpg")
    assert img.size == (1080, 1350)


def score(cid: str, total: int) -> dict:
    vals = {"color": 2, "shape": 2, "complete": 2, "clean": 2, "layout": 2}
    if total < 10:
        vals["shape"] = 0
    return {"id": cid, **vals}


def test_review_per_look_then_finish(tmp_path, store):
    out, _ = build(tmp_path, store)
    assert review.main(["prep", str(out)]) == 0  # 게시물 폴더면 모든 벌 폴더를 prep
    assert (out / "looks" / "3" / "review" / "input.md").exists()
    for n in range(1, 7):
        d = out / "looks" / str(n)
        total = 8 if n == 2 else 10
        r, code = review.apply(d, review.Scores.model_validate({"covers": [score("c1", total)]}))
        if n == 2:
            assert code == review.EXIT_RETRY
            new = review.more(d, fake=True)
            assert len(new) == 1 and new[0].id == "c2"  # 떨어진 벌만 한 장 더
            r, code = review.apply(d, review.Scores.model_validate({"covers": [score("c2", 10)]}))
        assert code == 0 and r.chosen
    m = looks.finish(out)
    assert m.review.passed and not m.needs_human and m.review_score == 10
    feed = yaml.safe_load((out / "feed.yaml").read_text())
    assert feed["looks"][1]["image"].endswith("looks/2/outfit_r2/results/G_1.jpg")
    # profile looks.cover_collage 가 꺼져 있으면 한 벌 표지 (cover_look 이 없으면 1번 벌)
    assert feed["cover_collage"] is False
    assert feed["cover"].endswith("looks/1/outfit/results/G_1.jpg")
    assert review.main(["finish", str(out)]) == 0
    data = yaml.safe_load((out / "plan.yaml").read_text())
    # 이 게시물만 콜라주
    (out / "plan.yaml").write_text(yaml.safe_dump(data | {"cover_collage": True}))
    looks.finish(out)
    assert yaml.safe_load((out / "feed.yaml").read_text())["cover_collage"] is True
    # daily-feed 가 고른 표지 벌 (2026-10-12)
    (out / "plan.yaml").write_text(yaml.safe_dump(data | {"cover_look": 2}))
    looks.finish(out)
    feed = yaml.safe_load((out / "feed.yaml").read_text())
    assert feed["cover_collage"] is False
    assert feed["cover"].endswith("looks/2/outfit_r2/results/G_1.jpg")
    # 벌 장은 그대로 LOOK 1부터 (표지 벌도 다시 보여 줌)
    names = [Path(p).name for p in notify.load_manifest(out).images]
    assert names[:3] == ["01_cover.jpg", "02_look1.jpg", "03_look2.jpg"]


def test_pick_sends_look_sheets(tmp_path, store):
    profile = load_profile("women")
    out = tmp_path / "out"
    write_candidates(out, [looks_plan(8, text=None)])
    api = FakeApis()
    record = pick.send(out, "2026-10-02-run5", tg_of(api), profile, store)
    assert record.count == 8 and record.looks == 6 and record.looks_min == 5
    assert len(list((out / "picks" / "sheets").glob("*.jpg"))) == 8
    text = pick.looks_text(pick.load_candidates(out)[0], profile, DAY, 1)
    assert "7. 7번 벌은 톤온톤으로 (예비)" in text
    assert "4번 빼고" in text and "1~6번으로 만들어요" in text


def test_decide_looks():
    profile = load_profile("women")
    r = rec(count=8, looks=6, looks_min=5)

    def choice(*texts, final=False):
        ups = [upd(i, t) for i, t in enumerate(texts, 1)]
        d, note, _ = pick.decide(ups, r, CHAT, profile, final, 200, 5)
        return (d.choice if d else None), note

    assert choice() == (None, "")
    assert choice(final=True)[0] == [1, 2, 3, 4, 5, 6]  # 마감까지 그대로면 앞 6벌
    assert choice("/pick")[0] == [1, 2, 3, 4, 5, 6]
    assert choice("4번 빼고")[0] == [1, 2, 3, 5, 6, 7]  # 예비 7번이 채움
    assert choice("/drop 2 3")[0] == [1, 4, 5, 6, 7, 8]
    assert choice("1 3 5 7 8")[0] == [1, 3, 5, 7, 8]  # 5벌도 됨
    assert choice("34678")[0] == [3, 4, 6, 7, 8]  # 붙여 쳐도 됨
    assert choice("3,4,6,7,8")[0] == [3, 4, 6, 7, 8]
    assert choice("3, 4, 6, 7, 8번")[0] == [3, 4, 6, 7, 8]
    assert "5~6벌을 골라" in choice("2번")[1]  # 한 벌만은 안 됨
    assert "9번 벌은 없어요" in choice("1 2 3 4 9")[1]
    assert choice("다시")[0] == "again"


def test_check_writes_chosen_plan(tmp_path, store, monkeypatch):
    profile = load_profile("women")
    results = tmp_path / "results"
    run = results / "2026-10-02-run5"
    write_candidates(run, [looks_plan(8, text=None)])
    record = PickRecord(
        account="women",
        date=DAY,
        run=run.name,
        count=8,
        looks=6,
        looks_min=5,
        sent_at=1000,
        message_ids=[11],
    )
    (run / "picks" / "pick.json").write_text(record.model_dump_json())

    class Tg:
        chat_id = CHAT

        def __init__(self):
            self.sent = []

        def updates(self):
            return [upd(1, "4번 빼고")]

        def send(self, text, **kw):
            self.sent.append(text)
            return 1

    tg = Tg()
    act = pick.check(profile, DAY, results, tg, False, 200)
    assert act.action == "render" and act.choice == "chosen"
    chosen = plan.load_plan(run / "picks" / "chosen.yaml")
    assert [lk.reason[:2] for lk in chosen.looks] == ["1번", "2번", "3번", "5번", "6번", "7번"]
    assert "1, 2, 3, 5, 6, 7번 벌로" in tg.sent[-1]


def test_feedback_maps_look_slides_and_keeps_covers(tmp_path, store):
    out, m = build(tmp_path, store)
    slides = feedback.slide_map(m)
    assert slides[2] == {"no": 3, "file": "03_look2.jpg", "kind": "look", "look": 2}
    assert feedback.label({"slide": 3, "kind": "look", "look": 2}) == "3장 2번 코디"
    # /fix 준비: 벌마다 지난 코디컷을 cover 로 둬서 고칠 벌만 다시 그린다
    prev = tmp_path / "prev"
    prev.mkdir()
    (prev / "run1").symlink_to(out)
    new = tmp_path / "new"
    data = feedback.prepare({"run": "run1", "fixes": []}, prev, new)
    fixed = plan.load_plan(new / "plan.yaml")
    assert all(lk.cover and lk.cover.endswith("results/G_1.jpg") for lk in fixed.looks)
    assert (new / "looks" / "1" / "review" / "review.json").exists()
    assert data["slides"][1]["kind"] == "look"


def test_links_are_grouped_by_look(tmp_path, store):
    _, m = build(tmp_path, store)
    m = Manifest.model_validate_json(m.model_dump_json())
    links = publish.post_links(m)
    assert links[0].look == 1 and links[5].look == 2
    assert links[3].product == "zigzag:200" and links[3].look == 1  # 두 벌에 쓴 가방은 처음 벌
    text = share.request_text("head", links, ask_reply=False)
    assert "[LOOK 1]" in text and "\n\n[LOOK 2]" in text
    assert json.loads(links[0].model_dump_json())["look"] == 1


def test_fix_redraws_only_uncovered_look(tmp_path, store):
    out, _ = build(tmp_path, store)
    for n in range(1, 7):
        review.apply(
            out / "looks" / str(n), review.Scores.model_validate({"covers": [score("c1", 10)]})
        )
    # 1번 벌은 2회차 후보까지 있었음 (고르지 않은 후보 파일은 /fix 폴더로 옮겨지지 않음)
    r = review.load_review(out / "looks" / "1")
    extra = r.candidates[0].model_copy(update={"id": "c2", "file": "outfit_r2/results/G_1.jpg"})
    review.save_review(
        out / "looks" / "1", r.model_copy(update={"candidates": [*r.candidates, extra]})
    )
    prev = tmp_path / "prev"
    prev.mkdir()
    (prev / "run1").symlink_to(out)
    new = tmp_path / "new"
    feedback.prepare({"run": "run1", "fixes": []}, prev, new)
    p = plan.load_plan(new / "plan.yaml")
    p.looks[2].cover = None  # 3번 벌만 다시 그림
    p = p.model_copy(
        update={
            "looks": [
                lk.model_copy(update={"cover": str(Path(lk.cover).resolve()) if lk.cover else None})
                for lk in p.looks
            ]
        }
    )
    m = plan.build(p, new, load_profile("women"), store, fake=True)
    r3 = review.load_review(new / "looks" / "3")
    assert r3.status == "not_reviewed" and r3.candidates[0].score is None
    r1 = review.load_review(new / "looks" / "1")
    assert r1.candidates[0].score is not None  # 다시 그리지 않은 벌은 점수 그대로
    assert r1.status == "passed" and [c.id for c in r1.candidates] == ["c1"]  # 검수 결과도 그대로
    review.prep(new / "looks" / "1")  # 옮기지 않은 후보 때문에 멈추지 않음
    assert m.review.status == "not_reviewed"


def test_pick_prefers_candidate_that_follows_fix():
    prof = load_profile("women")
    r = Review(
        candidates=[
            Candidate(id="c1", file="a.jpg", score=CoverScore(**score("c1", 9), feedback_ok=False)),
            Candidate(id="c2", file="b.jpg", score=CoverScore(**score("c2", 7), feedback_ok=True)),
        ]
    )
    assert review.pick(r, prof).id == "c2"
    r.candidates[1].score.feedback_ok = None  # 요청이 없으면 점수대로
    r.candidates[0].score.feedback_ok = None
    assert review.pick(r, prof).id == "c1"


def test_title_must_not_count_looks(store):
    prof = load_profile("women")
    assert prof.looks.title_count is False
    p = looks_plan()
    for title, bad in [
        (["출근룩 고민될 때", "가을 코디 여섯 벌"], True),
        (["출근룩 고민될 때", "6가지 룩 모음"], True),
        (["일교차 큰 날 출근룩부터", "약속룩까지 가을 코디 모음"], False),
    ]:
        q = p.model_copy(update={"text": p.text.model_copy(update={"title_lines": title})})
        errs = plan.looks_problems(q, prof, {pr.id: pr for pr in store.products()}, False)
        assert any("벌 수" in e for e in errs) is bad, title


def test_model_post_redraws_yesterday_looks_as_model_shots(tmp_path, store, monkeypatch):
    """16단계: 전날 게시한 벌을 같은 상품으로 모델컷 기획으로. 빠진 상품은 창고에 다시 넣는다."""
    from core.models import PostRecord
    from tools import faces, model_post

    out, m = build(tmp_path, store)
    (out / "published.json").write_text("{}")
    results = tmp_path / "results"
    results.mkdir()
    out.rename(results / f"{DAY}-run90")
    monkeypatch.setattr(model_post, "Store", lambda account: store)
    ids = [it.product for look in looks_plan().looks for it in look.items]
    store.save_posts([PostRecord(date=DAY, title="어제", theme="가을 출근룩 6가지", products=ids)])
    store.save_products([p for p in store.products() if p.id != "zigzag:10"])  # 수집 때 정리됨

    nxt = date(2026, 10, 3)
    assert model_post.source_run(results, "women", nxt) == results / f"{DAY}-run90"
    assert model_post.source_run(results, "women", date(2026, 10, 4)) is None
    out2 = tmp_path / "out2"
    p = model_post.prepare(f"{DAY}-run90", results, nxt, out2)
    assert "zigzag:10" in {x.id for x in store.products()}
    profile = load_profile("women")
    assert p.shot == "model" and p.source_run == f"{DAY}-run90" and p.date == nxt
    assert p.text is None and all(not lk.point and not lk.cover for lk in p.looks)
    assert [lk.items for lk in p.looks] == [lk.items for lk in looks_plan().looks]
    poses = [lk.pose for lk in p.looks]
    assert len(set(poses[:4])) == min(4, len(profile.model_shot.poses))
    assert (out2 / "before" / "caption.txt").exists()

    p = p.model_copy(update={"text": looks_plan().text})
    p.looks = [lk.model_copy(update={"point": f"{n}번 입은 모습"}) for n, lk in enumerate(p.looks)]
    errors, _ = plan.check(p, profile, store)
    assert errors == []  # 어제 쓴 상품·같은 주제여도 모델컷은 괜찮음
    errors, _ = plan.check(p.model_copy(update={"shot": "flatlay"}), profile, store)
    assert any("재사용 금지" in e for e in errors)

    (out2 / "plan.yaml").write_text(yaml.safe_dump(p.model_dump(mode="json", exclude_none=True)))
    plan.build(p, out2, profile, store, fake=True)
    prompt = (out2 / "looks" / "1" / "outfit" / "prompt.txt").read_text()
    assert "wearing this exact outfit" in prompt and poses[0].split(";")[0] in prompt
    assert "flat lay" not in prompt and "$" not in prompt

    # 얼굴 검출 도구가 찾은 자리는 잘라 두고 검수가 판단한다 (폰에 가린 얼굴도 잡으므로)
    d = out2 / "looks" / "1"
    monkeypatch.setattr(faces, "find_faces", lambda img: [(20, 20, 60, 70)])
    text = review.prep(d).read_text()
    assert "모델컷" in text and "c1 [얼굴 확인]" in text and "review/face_c1_1.jpg" in text
    assert (d / "review" / "face_c1_1.jpg").exists()
    r, _ = review.apply(d, review.Scores.model_validate({"covers": [score("c1", 10)]}))
    assert r.candidates[0].score.clean == 2


def test_fix_cover_swapped_to_other_image_drops_old_review(tmp_path, store):
    """/fix 로 벌 cover 를 다른 사진으로 바꾸면 지난 검수의 후보(c1)를 남기지 않는다
    (2026-10-07 10/8 LOOK 4)."""
    out, _ = build(tmp_path, store)
    for n in range(1, 7):
        review.apply(
            out / "looks" / str(n), review.Scores.model_validate({"covers": [score("c1", 10)]})
        )
    prev = tmp_path / "prev"
    prev.mkdir()
    (prev / "run1").symlink_to(out)
    new = tmp_path / "new"
    feedback.prepare({"run": "run1", "fixes": []}, prev, new)
    other = tmp_path / "other.jpg"
    other.write_bytes((out / "looks" / "2" / "outfit" / "results" / "G_1.jpg").read_bytes())
    p = plan.load_plan(new / "plan.yaml")
    looks_ = [lk.model_copy(update={"cover": str(Path(lk.cover).resolve())}) for lk in p.looks]
    looks_[3] = looks_[3].model_copy(update={"cover": str(other)})
    plan.build(p.model_copy(update={"looks": looks_}), new, load_profile("women"), store, fake=True)
    assert looks.chosen_image(new / "looks" / "4").resolve() == other.resolve()
    assert review.load_review(new / "looks" / "4").status == "not_reviewed"
    assert review.load_review(new / "looks" / "1").status == "passed"


def test_unity_warnings():
    # 2026-10-07 "상의 하의가 따로 논다", "나그랑·줄무늬가 아우터와 안 어울린다"
    from core.models import PlanItem, Product

    profile = load_profile("women")
    base = dict(
        source="zigzag",
        url="https://zigzag.kr/catalog/products/1",
        shop="s",
        rating=4.8,
        image_urls=["https://img.example/a.jpg"],
        price=39000,
    )
    prods = {
        f"zigzag:{i}": Product(id=f"zigzag:{i}", name=n, category=c, **base)
        for i, (n, c) in enumerate(
            [
                ("벌룬 맨투맨", "상의"),
                ("나그랑 스트라이프 티", "상의"),
                ("새틴 롱스커트", "하의"),
                ("레더 토트백", "가방"),
                ("스웨이드 롱부츠", "신발"),
                ("레더 블루종", "아우터"),
            ]
        )
    }

    def item(i, color, **kw):
        return PlanItem(product=f"zigzag:{i}", color=color, desc="x (x)", name="x", **kw)

    busy = [
        item(0, "네이비"),
        item(1, "아이보리", wear_with="상의"),
        item(2, "아이보리"),
        item(3, "블랙"),
        item(4, "다크브라운"),
    ]
    warns = plan.unity_warnings("1번 벌: ", busy, prods, profile)
    assert any("색 계열이 4개" in w for w in warns)
    assert any("재질 분위기" in w for w in warns)
    assert any("맨 바깥 상의" in w for w in warns)
    calm = [item(1, "그레이"), item(2, "그레이"), item(3, "블랙"), item(4, "블랙")]
    assert [w for w in plan.unity_warnings("", calm, prods, profile) if "재질" not in w] == []
    # 래글런 위에 아우터
    outer = [item(5, "블랙"), item(1, "그레이")]
    assert any("맨 바깥" in w for w in plan.unity_warnings("", outer, prods, profile))
    # 이너는 베이직 단품만: 세트·장식 있는 나시는 이너로 쓰지 않는다
    prods["zigzag:9"] = Product(
        id="zigzag:9", name="[set]홀터넥 나시 세트 니트 가디건", category="상의", **base
    )
    prods["zigzag:10"] = Product(id="zigzag:10", name="베이직 골지 나시", category="상의", **base)
    inner = [item(0, "그레이"), item(9, "블랙", wear_with="상의"), item(3, "블랙")]
    assert any("이너는 베이직 단품" in w for w in plan.unity_warnings("", inner, prods, profile))
    plain = [item(0, "그레이"), item(10, "블랙", wear_with="상의"), item(3, "블랙")]
    assert not any("이너는" in w for w in plan.unity_warnings("", plain, prods, profile))


def test_find_edits_reads_look_number_or_replied_sheet():
    r = rec(count=8, looks=6, looks_min=5, message_ids=[21, 22, 30], sheets={21: 1, 22: 2})
    ups = [
        upd(1, "/fix 1 이너를 나그랑티가 아닌 나시로 변경"),
        upd(2, "/fix 1,3 더 밝은 색으로"),
        upd(3, "/fix 2번 신발을 로퍼로"),
        upd(4, "/fix 가방을 숄더백으로", to=22),  # 2번 벌 시트에 답장
        upd(5, "/fix 이너를 나시로"),  # 번호 없음
        upd(6, "/fix 1"),  # 내용 없음
        upd(7, "/fix 1 이너를 나시로", to=999),  # 봇의 다른 메시지에 답장
    ]
    found = pick.find_edits(ups, r, CHAT)
    assert [(e["update_id"], e["looks"], e["text"]) for e in found] == [
        (1, [1], "이너를 나그랑티가 아닌 나시로 변경"),
        (2, [1, 3], "더 밝은 색으로"),
        (3, [2], "신발을 로퍼로"),
        (4, [2], "가방을 숄더백으로"),
        (5, [], "이너를 나시로"),
    ]


class EditTg:
    chat_id = CHAT

    def __init__(self, texts):
        self.ups = [upd(i, t, at=1100 + i) for i, t in enumerate(texts, 1)]
        self.sent = []

    def updates(self):
        return self.ups

    def send(self, text, **kw):
        self.sent.append(text)
        return 100 + len(self.sent)


def sent_candidates(results, store):
    run = results / "2026-10-02-run5"
    write_candidates(run, [looks_plan(8, text=None)])
    record = rec(run=run.name, count=8, looks=6, looks_min=5, message_ids=[21, 30], sheets={21: 1})
    (run / "picks" / "pick.json").write_text(record.model_dump_json())
    return run


def test_candidate_fix_edits_one_look_then_pick_continues(tmp_path, store):
    profile = load_profile("women")
    results = tmp_path / "results"
    run = sent_candidates(results, store)
    # 번호를 먼저 보냈어도 /fix 가 있으면 고친 뒤에 반영한다
    tg = EditTg(["1 3 5 7 8", "/fix 1 이너를 나그랑티가 아닌 나시로"])
    act = pick.check(profile, DAY, results, tg, False, 200, datetime.fromtimestamp(2000))
    assert act.action == "edit" and act.run == run.name
    assert act.fixes == [{"update_id": 2, "looks": [1], "text": "이너를 나그랑티가 아닌 나시로"}]
    assert "1번 벌을 고치고 있어요" in tg.sent[-1]
    assert not (run / "picks" / "picked.json").exists()
    # 고치는 중에는 고르기를 미룬다
    wait = pick.check(profile, DAY, results, tg, False, 140, datetime.fromtimestamp(5000))
    assert wait.action == "wait" and "고치는 중" in wait.detail

    # daily mode=edit: 지난 후보를 가져와 Claude 가 1번 벌을 고친다 (2번은 실수로 바꿈)
    out = tmp_path / "out"
    store.save_products([*store.products(), product(500, "상의")])
    pick.prepare_edit({"run": run.name, "fixes": act.fixes}, results, out)
    data = yaml.safe_load((out / "picks" / "p1.yaml").read_text())
    data["looks"][0]["items"][1] = {
        "product": "zigzag:500",
        "desc": "블랙 골지 나시 (black ribbed tank)",
        "name": "골지 나시",
    }
    data["looks"][0]["reason"] = "블랙 나시로 산뜻하게. 메모"
    data["looks"][1]["reason"] = "바꾸면 안 되는 벌"
    (out / "picks" / "p1.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
    api = FakeApis()
    new = pick.send_edit(out, "2026-10-02-run6", tg_of(api), profile, store)
    edited = pick.load_candidates(out)[0]
    assert edited.looks[0].items[1].product == "zigzag:500"
    assert edited.looks[1].reason.startswith("2번 벌")  # 적히지 않은 벌은 되돌림
    assert new.sent_at == 1000 and new.edits == [2] and new.run == "2026-10-02-run6"
    assert len(new.message_ids) == 4 and len(new.sheets) == 2  # 고친 1번 시트만 더 보냄
    text = api.texts[-1]
    assert "1번: 이너를 나그랑티가 아닌 나시로" in text and "1. 블랙 나시로 산뜻하게" in text

    # 고친 후보가 결과 폴더에 올라가면 고치기 전에 보낸 번호로 고친 벌을 만든다
    shutil.copytree(out, results / "2026-10-02-run6")
    act = pick.check(profile, DAY, results, tg, False, 80, datetime.fromtimestamp(6000))
    assert act.action == "render" and act.run == "2026-10-02-run6"
    chosen = plan.load_plan(results / "2026-10-02-run6" / "picks" / "chosen.yaml")
    assert chosen.looks[0].items[1].product == "zigzag:500"
    assert [lk.reason[:2] for lk in chosen.looks[1:]] == ["3번", "5번", "7번", "8번"]


def test_candidate_fix_refused_without_number_or_near_deadline(tmp_path, store):
    profile = load_profile("women")
    results = tmp_path / "results"
    run = sent_candidates(results, store)
    tg = EditTg(["/fix 이너를 나시로", "/fix 9 신발 바꿔"])
    act = pick.check(profile, DAY, results, tg, False, 200, datetime.fromtimestamp(2000))
    assert act.action == "wait"
    assert "벌 번호를 같이" in tg.sent[0] and "9번 벌은 없어요" in tg.sent[0]
    n = len(tg.sent)
    pick.check(profile, DAY, results, tg, False, 200, datetime.fromtimestamp(3000))
    assert len(tg.sent) == n  # 한 번만 알린다
    # 마감이 가까우면 고치지 않고 고르기만 받는다
    tg.ups.append(upd(9, "/fix 1 이너를 나시로", at=1200))
    act = pick.check(profile, DAY, results, tg, True, 0, datetime.fromtimestamp(4000))
    assert act.action == "render" and "고칠 시간이 없어요" in tg.sent[n]
    assert not (run / "picks" / pick.EDITS).exists()


def test_notify_reads_cover_choice():
    r = PreviewRecord(account="women", date=DAY, run="r", sent_at=1000, message_ids=[11])
    assert notify.parse_command("/ok 3", r) == "ok"
    assert notify.parse_command("/ok 표지 3", r) == "ok"
    assert notify.parse_command("/ok women 10-02 표지3", r) == "ok"
    assert notify.parse_command("/ok 아니 잠깐", r) is None
    assert notify.parse_command("/skip 3", r) is None
    ups = [upd(1, "/ok"), upd(2, "/cover 4"), upd(3, "표지 5번"), upd(4, "/ok women 10-03 2")]
    assert notify.find_decision(ups, r, CHAT) == "ok"
    assert notify.find_cover(ups, r, CHAT) == (3, 5)  # 다른 날짜 글은 뺌
    assert notify.find_cover([upd(1, "/ok 2"), upd(2, "/ok")], r, CHAT) == (1, 2)
    assert notify.find_cover([upd(1, "/ok")], r, CHAT) is None
    assert notify.find_cover([upd(1, "/cover 3", at=900)], r, CHAT) is None  # 미리보기 전


def test_cover_look_change_from_telegram(tmp_path, store):
    out, _ = build(tmp_path, store)
    profile = load_profile("women")
    record = PreviewRecord(account="women", date=DAY, run="r", sent_at=1000, message_ids=[11])
    (out / "telegram.json").write_text(record.model_dump_json())
    assert cover_look.current(out, profile) == 1
    # 가짜 코디컷은 벌마다 같아서 3번 벌만 색을 바꿔 둔다
    img3 = looks.chosen_image(looks.look_dir(out, 3))
    Image.new("RGB", Image.open(img3).size, (40, 90, 160)).save(img3)
    before = (out / "slides" / "01_cover.jpg").read_bytes()
    tall_before = (out / "slides" / "tall" / "01_cover.jpg").read_bytes()
    text = notify.control_text(notify.load_manifest(out), profile, False, cover=1)
    assert "표지: LOOK 1" in text and "/ok 2" in text and "/cover 2" in text

    api = FakeApis(replies=[(1100, "/ok 3", None)])
    line = cover_look.check(out, tg_of(api))
    assert "LOOK 3" in line
    assert cover_look.current(out, profile) == 3
    assert yaml.safe_load((out / "feed.yaml").read_text())["cover"].endswith(
        "looks/3/outfit/results/G_1.jpg"
    )
    assert (out / "slides" / "01_cover.jpg").read_bytes() != before
    assert (out / "slides" / "tall" / "01_cover.jpg").read_bytes() != tall_before
    assert ai_label.read(out / "slides" / "01_cover.jpg")
    assert any(m.endswith("/sendPhoto") for _, m in api.calls)
    assert notify.find_decision(tg_of(api).updates(), record, CHAT) == "ok"
    # 같은 글은 한 번만
    n = len(api.calls)
    assert cover_look.check(out, tg_of(api)) == ""
    assert not any(m.endswith("/sendPhoto") for _, m in api.calls[n:])
    # /fix 로 다시 만들어도 고른 표지 벌을 그대로 쓴다 (plan.yaml)
    looks.finish(out)
    assert yaml.safe_load((out / "feed.yaml").read_text())["cover"].endswith(
        "looks/3/outfit/results/G_1.jpg"
    )
    # 없는 벌 번호는 알리고 그대로
    api2 = FakeApis(replies=[(1100, "/ok 3", None), (1200, "/cover 9", None)])
    assert "실패" in cover_look.check(out, tg_of(api2))
    assert any("LOOK 9" in t for t in api2.texts)
    assert cover_look.current(out, profile) == 3


def test_cover_with_fix_in_one_message():
    r = PreviewRecord(account="women", date=DAY, run="r", sent_at=1000, message_ids=[11])
    text = "/fix 2 신발 더 크게\n/cover 3"
    assert notify.find_cover([upd(1, text)], r, CHAT) == (1, 3)
    slides = [{"slide": i, "kind": "look", "look": i - 1} for i in range(1, 9)]
    fixes = feedback.find_fixes([upd(1, text)], r, CHAT, slides)
    assert len(fixes) == 1 and fixes[0]["text"] == "신발 더 크게"
    fixes = feedback.find_fixes([upd(1, "/cover 3\n/fix 2 신발 더 크게")], r, CHAT, slides)
    assert len(fixes) == 1 and fixes[0]["text"] == "신발 더 크게"
    assert notify.find_cover([upd(1, "/cover 3\n/fix 2 신발")], r, CHAT) == (1, 3)


def test_cover_carries_to_fixed_preview(tmp_path, store):
    """/fix 로 다시 만든 미리보기도 그 전에 고른 표지 벌을 쓴다 (같이 보내도)."""
    a, _ = build(tmp_path, store)
    rec_a = PreviewRecord(account="women", date=DAY, run="a", sent_at=1000, message_ids=[11])
    (a / "telegram.json").write_text(rec_a.model_dump_json())
    # 고친 결과 b: 표지 고르기를 지난 결과에 반영하기 전에 /fix 가 기획을 가져감
    b = tmp_path / "b"
    shutil.copytree(a, b)
    (b / "fix.json").write_text(json.dumps({"run": "out", "cover_look": None, "fixes": []}))
    rec_b = rec_a.model_copy(update={"run": "b", "sent_at": 2000, "message_ids": [21]})
    (b / "telegram.json").write_text(rec_b.model_dump_json())
    api = FakeApis(replies=[(1100, "/fix 2 신발 더 크게\n/cover 3", None)])
    assert "LOOK 3" in cover_look.check(a, tg_of(api))
    assert "LOOK 3" in cover_look.check(b, tg_of(api))  # 고친 미리보기 뒤 글이 아니어도
    assert cover_look.current(b, load_profile("women")) == 3
    # 이어받은 표지는 다시 알리지 않는다
    c = tmp_path / "c"
    shutil.copytree(b, c)
    (c / "cover.json").unlink()
    (c / "fix.json").write_text(json.dumps({"run": "b", "cover_look": 3, "fixes": []}))
    n = len(api.calls)
    assert "그대로" in cover_look.check(c, tg_of(api))
    assert not any("send" in m for _, m in api.calls[n:])
    # /fix 가 표지를 5번으로 바꿨으면 /fix 를 따른다
    data = yaml.safe_load((c / "plan.yaml").read_text()) | {"cover_look": 5}
    (c / "plan.yaml").write_text(yaml.safe_dump(data))
    (c / "cover.json").unlink()
    assert "그대로" in cover_look.check(c, tg_of(api))
    assert cover_look.current(c, load_profile("women")) == 5


def test_fault_fails_review_whatever_the_score():
    # 2026-10-12 사용자: 짧은 바지·벌어진 신발을 검수가 찾고도 1점만 깎아 통과시킴
    prof = load_profile("women")
    bad = CoverScore(**score("c1", 10), faults=["hem_short"])
    ok = CoverScore(**score("c2", 10))
    assert not review.passes(bad, prof) and review.passes(ok, prof)
    r = Review(
        candidates=[
            Candidate(id="c1", file="a.jpg", score=bad),
            Candidate(id="c2", file="b.jpg", score=ok),
        ]
    )
    assert review.pick(r, prof).id == "c2"
    r.candidates[1].score = CoverScore(**score("c2", 10), faults=["shoes_not_neat", "tuck_messy"])
    assert review.pick(r, prof).id == "c1"  # 둘 다 불합격이면 결함이 적은 쪽
