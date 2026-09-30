from pathlib import Path

import pytest
import yaml
from PIL import Image

from core.models import Manifest, Review
from core.profile import load_profile
from core.store import Store
from tests.test_plan import CATS, daily, product
from tools import plan, review


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = Store("women", root=tmp_path / "data")
    s.save_products([product(i, c) for i, c in enumerate(CATS, 1)])
    # review 는 계정 이름으로 창고를 열기 때문에 같은 임시 창고를 쓰게 한다
    monkeypatch.setattr(review, "Store", lambda account: s)
    monkeypatch.setattr(plan, "Store", lambda account: s)
    # 후보 수는 계정 설정과 무관하게 2장으로 시험한다
    monkeypatch.setattr(review, "load_profile", two_candidates)
    return s


def two_candidates(account: str = "women"):
    profile = load_profile(account)
    profile.review.candidates = 2
    return profile


@pytest.fixture(autouse=True)
def fake_photos(monkeypatch):
    from tools import feed

    def fetch(url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            Image.new("RGB", (400, 500), (120, 90, 60)).save(dest)
        return dest

    monkeypatch.setattr(feed, "fetch_photo", fetch)


def built(store, tmp_path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    p = daily()
    (out / "plan.yaml").write_text(
        yaml.safe_dump(p.model_dump(mode="json"), allow_unicode=True), encoding="utf-8"
    )
    plan.build(p, out, two_candidates(), store, fake=True)
    return out


def score(cid: str, **kw) -> dict:
    base = {"id": cid, "color": 2, "shape": 2, "complete": 2, "clean": 2, "layout": 2}
    return base | kw


def test_build_makes_candidates_and_prep_sheets(store, tmp_path):
    out = built(store, tmp_path)
    r = review.load_review(out)
    assert [c.id for c in r.candidates] == ["c1", "c2"]  # profile review.candidates
    assert r.chosen == "c1" and r.status == "not_reviewed"
    m = Manifest.model_validate_json((out / "manifest.json").read_text())
    assert m.needs_human and m.review_score == 0 and m.review.candidates

    text = review.prep(out).read_text()
    assert "c1" in text and "c2" in text and "화이트" not in text  # 고른 색이 없으면 -
    assert "기장 68cm" in text and "긴 쪽 52cm" in text  # 크기 비율 검수용 실제 크기
    assert (out / "review" / "covers.jpg").exists()
    assert (out / "review" / "slides.jpg").exists()


def test_apply_picks_best_and_rerenders_cover(store, tmp_path):
    out = built(store, tmp_path)
    review.prep(out)
    before = (out / "slides" / "01_cover.jpg").read_bytes()
    # c2 를 코디컷 원본과 다른 색으로 바꿔 표지가 실제로 바뀌는지 본다
    c2 = out / review.load_review(out).candidates[1].file
    Image.new("RGB", Image.open(c2).size, (20, 120, 200)).save(c2)
    scores = review.Scores(
        covers=[score("c1", clean=0, notes="셔츠 소매가 하얀 덩어리"), score("c2", shape=1)]
    )
    r, code = review.apply(out, scores)
    assert code == 0 and r.status == "passed" and r.chosen == "c2"
    assert (out / "slides" / "01_cover.jpg").read_bytes() != before
    m = Manifest.model_validate_json((out / "manifest.json").read_text())
    assert m.review_score == 9 and not m.needs_human and m.review.chosen == "c2"
    assert "c1: 8/10" in (out / "report.md").read_text()  # 0점 항목이 있어 8점이어도 탈락


def test_apply_asks_retry_then_fails_after_max_rounds(store, tmp_path):
    out = built(store, tmp_path)
    bad = review.Scores(covers=[score("c1", complete=0, color=1), score("c2", color=1, shape=0)])
    r, code = review.apply(out, bad)
    assert code == review.EXIT_RETRY and r.status == "retry" and not r.passed

    new = review.more(out, fake=True)
    assert [c.id for c in new] == ["c3", "c4"] and all(c.round == 2 for c in new)
    with pytest.raises(ValueError, match="c3"):
        review.apply(out, review.Scores())  # 새 후보 점수가 없음

    r, code = review.apply(out, review.Scores(covers=[score("c3", clean=0), score("c4", color=0)]))
    assert code == review.EXIT_HUMAN and r.status == "failed"
    assert r.chosen == "c3"  # 그래도 가장 나은 후보를 표지로 둔다
    m = Manifest.model_validate_json((out / "manifest.json").read_text())
    assert m.needs_human and m.review_score == 8
    with pytest.raises(OverflowError):
        review.more(out, fake=True)


def test_slide_issues_need_human(store, tmp_path):
    out = built(store, tmp_path)
    scores = review.Scores(
        covers=[score("c1"), score("c2")],
        slides=[{"slide": "03_상의.jpg", "kind": "색", "note": "블루 옵션 사진"}],
    )
    r, code = review.apply(out, scores)
    assert code == review.EXIT_HUMAN and r.chosen == "c1" and r.status == "failed"
    assert r.slide_issues[0].by == "claude"


def test_code_checks_find_cut_text(store, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    p = daily()
    p.items[0].comment = "아주 긴 코멘트 " * 20
    (out / "plan.yaml").write_text(
        yaml.safe_dump(p.model_dump(mode="json"), allow_unicode=True), encoding="utf-8"
    )
    plan.build(p, out, load_profile("women"), store, outfit="none")
    review.prep(out)
    r = review.load_review(out)
    assert r.candidates == []
    assert any(s.kind == "글자" and s.slide.startswith("01_") for s in r.slide_issues)
    r, code = review.apply(out, review.Scores())
    assert code == review.EXIT_HUMAN


def test_rebuild_with_chosen_cover_keeps_scores(store, tmp_path):
    out = built(store, tmp_path)
    review.apply(out, review.Scores(covers=[score("c1", color=1), score("c2")]))
    chosen = out / review.load_review(out).candidates[1].file
    p = daily(cover=str(chosen))
    plan.build(p, out, load_profile("women"), store)
    r = Review.model_validate_json((out / "review" / "review.json").read_text())
    assert r.chosen == "c2" and r.candidates[0].score.total == 9 and r.status == "not_reviewed"
    r, code = review.apply(out, review.Scores())
    assert code == 0 and r.chosen == "c2"


def test_failed_outfit_needs_human(store, tmp_path):
    """코디컷을 만들려다 한 장도 못 만들면 표지 없이 자동 게시되지 않게 사람 확인으로 둔다."""
    out = built(store, tmp_path)
    review.start(out, [])  # 후보가 하나도 없는 상태
    r, code = review.apply(out, review.Scores())
    assert code == review.EXIT_HUMAN and not r.passed
