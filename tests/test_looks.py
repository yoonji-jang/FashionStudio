"""코디 여러 벌 게시물 (20단계, profile looks)."""

import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from PIL import Image

from core.models import Candidate, CoverScore, DailyPlan, Manifest, PickRecord, Review
from core.profile import load_profile
from core.store import Store
from tests.test_pick import rec, tg_of, upd, write_candidates
from tests.test_plan import fake_photos, product  # noqa: F401 - fixture
from tests.test_publish import CHAT, FakeApis
from tools import ai_label, feedback, looks, pick, plan, publish, review, share

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
    assert "옷은 한 벌에만" in text
    assert "3번 벌 캡션 한 줄(point)이 없습니다" in text
    assert "최대 2벌" in text


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
    assert review.main(["finish", str(out)]) == 0


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

    # 얼굴 검출 도구가 얼굴을 찾은 후보는 점수와 상관없이 clean 0
    d = out2 / "looks" / "1"
    monkeypatch.setattr(faces, "find_faces", lambda img: [(0, 0, 10, 10)])
    text = review.prep(d).read_text()
    assert "모델컷" in text and "c1 [얼굴]" in text
    r, _ = review.apply(d, review.Scores.model_validate({"covers": [score("c1", 10)]}))
    assert r.candidates[0].score.clean == 0 and "얼굴 검출" in r.candidates[0].score.notes
