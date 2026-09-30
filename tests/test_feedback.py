import json
from datetime import UTC, date, datetime

import pytest
import yaml

from core.models import CoverScore, FeedbackEntry, PostRecord
from core.profile import load_profile
from tests.test_publish import CHAT, DAY, FakeApis, clients, record, results, sent  # noqa: F401
from tests.test_review import built, fake_photos, store  # noqa: F401 - pytest fixture
from tools import feedback, notify, plan, publish, review, schedule

RUN = "2026-09-27-run5"
# 2026-09-27 게시 마지막 확인(22:00) - 40분 = 21:20 KST 보다 앞
EARLY = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)  # 18:00 KST
LATE = datetime(2026, 9, 27, 12, 30, tzinfo=UTC)  # 21:30 KST


def slides_of(results):  # noqa: F811
    return feedback.slide_map(notify.load_manifest(results / RUN))


def test_slide_map_follows_carousel_order(results):  # noqa: F811
    slides = slides_of(results)
    assert slides[0]["kind"] == "cover"
    # profile feed.slides 순서: cover, products, summary, comment(댓글 안내 장)
    assert [s["kind"] for s in slides if s["kind"] != "product"] == ["cover", "summary", "comment"]
    products = [s for s in slides if s["kind"] == "product"]
    m = notify.load_manifest(results / RUN)
    assert [s["product"] for s in products] == [p.id for p in m.plan.items]
    assert products[0]["no"] == 2 and products[0]["category"] == m.plan.items[0].category


def test_target_reads_slide_caption_and_replies(results):  # noqa: F811
    slides = slides_of(results)
    n = len(slides)
    rec = record(message_ids=list(range(100, 100 + n + 2)))
    fix = feedback.target(["1", "신발", "더", "크게"], None, rec, slides)
    assert fix["slide"] == 1 and fix["kind"] == "cover" and fix["text"] == "신발 더 크게"
    fix = feedback.target(["3장", "사진이", "어두워요"], None, rec, slides)
    assert fix["slide"] == 3 and fix["kind"] == "product" and fix["product"]
    fix = feedback.target(["캡션", "더", "짧게"], None, rec, slides)
    assert fix == {"slide": None, "kind": "caption", "text": "더 짧게"}
    # 사진에 답장하면 그 장, 캡션 메시지에 답장하면 캡션
    fix = feedback.target(["색이", "달라요"], 101, rec, slides)
    assert fix["slide"] == 2 and fix["text"] == "색이 달라요"
    assert feedback.target(["짧게"], 100 + n, rec, slides)["kind"] == "caption"
    # 없는 장 번호는 전체 피드백으로
    fix = feedback.target(["99", "밝게"], None, rec, slides)
    assert fix["kind"] == "all" and fix["text"] == "99 밝게"
    assert feedback.label({"slide": 3, "kind": "product", "category": "하의"}) == "3장 하의"
    assert feedback.label({"slide": None, "kind": "caption"}) == "캡션"


def update(uid: int, at: int, text: str, reply_to: int | None = None, chat: str = CHAT):
    msg = {"message_id": 500 + uid, "date": at, "chat": {"id": int(chat)}, "text": text}
    if reply_to:
        msg["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": uid, "message": msg}


def test_find_fixes_filters(results):  # noqa: F811
    slides = slides_of(results)
    rec = record(message_ids=[11, 12])
    upd = [
        update(1, 900, "/fix 1 전에 보낸 것"),  # 미리보기 전
        update(2, 1100, "/fix 1 남의 채팅", chat="9"),
        update(3, 1200, "/fix 2 women 09-27 가방 사진 밝게"),
        update(4, 1300, "/fix"),  # 내용 없음
        update(5, 1400, "/fix 다른 미리보기", reply_to=77),
        update(6, 1500, "/fix women 09-28 다른 날"),
        update(7, 1600, "/ok"),
    ]
    found = feedback.find_fixes(upd, rec, CHAT, slides)
    assert [(f["update_id"], f["slide"], f["text"]) for f in found] == [(3, 2, "가방 사진 밝게")]


def test_check_rerenders_once_then_busy_and_limit(results, store):  # noqa: F811
    api = FakeApis()
    rec = sent(api, results / RUN)
    tg, _, _ = clients(api)
    api.replies = [
        (rec.sent_at + 5, "/fix 1 신발 더 크게", None),
        (rec.sent_at + 6, "/fix 캡션 더 짧게", None),
    ]
    fix = feedback.check("women", DAY, results, tg, EARLY, store=store)
    assert fix["run"] == RUN and fix["round"] == 1
    assert [f["kind"] for f in fix["fixes"]] == ["cover", "caption"]
    assert fix["fixes"][0] == {"slide": 1, "kind": "cover", "text": "신발 더 크게"}
    entries = store.feedback()
    assert [(e.result, e.round, e.kind) for e in entries] == [
        ("rerender", 1, "cover"),
        ("rerender", 1, "caption"),
    ]
    assert "다시 만들어" in api.texts[-1] and "1장 표지: 신발 더 크게" in api.texts[-1]
    # 같은 글은 두 번 처리하지 않는다
    assert feedback.check("women", DAY, results, tg, EARLY, store=store) is None
    # 고친 미리보기가 오기 전에 온 /fix 는 기록만
    api.replies.append((rec.sent_at + 50, "/fix 3 이름 짧게", None))
    assert feedback.check("women", DAY, results, tg, EARLY, store=store) is None
    assert store.feedback()[-1].result == "busy" and "다시 만드는 중" in api.texts[-1]
    # 하루 횟수(max_fixes 2)를 넘으면 기록만
    store.add_feedback([entry(update_id=90, round=2, run="2026-09-27-run6")])
    api.replies.append((rec.sent_at + 60, "/fix 2 한 번 더", None))
    later = datetime.fromtimestamp(rec.sent_at + 3 * 3600, UTC)  # busy 기준(120분)이 지난 뒤
    assert feedback.check("women", DAY, results, tg, later, store=store) is None
    assert store.feedback()[-1].result == "limit" and "/ok women 09-27" in api.texts[-1]


def entry(**kw) -> FeedbackEntry:
    base = dict(account="women", date=DAY, run=RUN, text="x", at=0, update_id=0)
    return FeedbackEntry(**(base | {"result": "rerender"} | kw))


def test_check_late_and_after_post(results, store):  # noqa: F811
    api = FakeApis()
    rec = sent(api, results / RUN)
    tg, _, _ = clients(api)
    api.replies = [(rec.sent_at + 5, "/fix 1 가방 오른쪽 위로", None)]
    assert feedback.check("women", DAY, results, tg, LATE, store=store) is None
    assert store.feedback()[-1].result == "late"
    store.save_posts([PostRecord(date=DAY, title="t")])
    api.replies.append((rec.sent_at + 6, "/fix 캡션 짧게", None))
    assert feedback.check("women", DAY, results, tg, EARLY, store=store) is None
    assert store.feedback()[-1].result == "after_post" and "이미 게시" in api.texts[-1]


def test_store_feedback_jsonl_keeps_korean(store):  # noqa: F811
    store.add_feedback([entry(text="신발 더 크게", slide=1, kind="cover")])
    store.add_feedback([entry(text="캡션 짧게", kind="caption", update_id=2)])
    raw = (store.dir / "feedback.jsonl").read_text(encoding="utf-8")
    assert len(raw.splitlines()) == 2 and "신발 더 크게" in raw
    assert [e.text for e in store.feedback()] == ["신발 더 크게", "캡션 짧게"]
    lines = feedback.recent_lines(store, DAY, 14, {"cover"})
    assert lines == ["- 09-27 1장 표지: 신발 더 크게"]
    assert "표지 1" in feedback.summary(store, DAY, 30)


def test_prepare_keeps_reviewed_cover(results, store, tmp_path, monkeypatch):  # noqa: F811
    prev = results / RUN
    r = review.load_review(prev)
    r.chosen = "c2"
    r.candidates[1].score = CoverScore(id="c2", color=2, shape=2, complete=2, clean=2, layout=1)
    review.save_review(prev, r)
    root = tmp_path / "repo"  # 저장소 기준 경로(plan.cover)를 시험 폴더로
    monkeypatch.setattr(plan, "REPO_ROOT", root)
    out = root / "out" / str(DAY)
    pick = {"run": RUN, "round": 1, "fixes": [{"slide": 3, "kind": "product", "text": "짧게"}]}
    data = feedback.prepare(pick, results, out)
    assert data["cover"] == f"out/{DAY}/{r.candidates[1].file}"
    assert (root / data["cover"]).exists()
    p = plan.load_plan(out / "plan.yaml")
    assert p.cover == data["cover"]
    fix = json.loads((out / "fix.json").read_text())
    assert fix["fixes"][0]["text"] == "짧게" and fix["slides"][0]["kind"] == "cover"
    assert (out / "before" / "slides" / "01_cover.jpg").exists()
    assert (out / "before" / "caption.txt").exists()
    # 표지를 그대로 두고 다시 만들면 지난 점수가 남아 표지는 다시 검수하지 않는다
    plan.build(p, out, load_profile("women"), store, fake=True)
    again = review.load_review(out)
    assert [c.id for c in again.candidates] == ["c1", "c2"] and again.chosen == "c2"
    assert again.candidates[1].score.total == 9
    assert not list(out.glob("outfit*/gen.json"))  # 이미지 AI 를 부르지 않음


def test_preview_shows_fix_round_changes_and_hint(results):  # noqa: F811
    run = results / RUN
    profile = load_profile("women")
    m = notify.load_manifest(run)
    text = notify.control_text(m, profile, False)
    assert "/fix 1 신발 더 크게" in text and "남은 횟수 2번" in text
    fix = {"round": 1, "fixes": [{"slide": 1, "kind": "cover", "text": "신발 크게"}]}
    text = notify.control_text(m, profile, False, fix)
    assert "고친 점 (1/2회)" in text and "- 1장 표지: 신발 크게" in text
    assert "남은 횟수 1번" in text
    text = notify.control_text(m, profile, False, fix | {"round": 2}, ["1장 표지: 신발을 키움"])
    assert "- 1장 표지: 신발을 키움" in text and "/fix 1" not in text
    assert "/fix" not in notify.control_text(m, profile, True)  # 시험 미리보기

    (run / "fix.json").write_text(json.dumps(fix), encoding="utf-8")
    (run / "fix_result.yaml").write_text(
        yaml.safe_dump({"changes": ["1장 표지: 신발을 실제 크기로"]}, allow_unicode=True)
    )
    api = FakeApis()
    tg, _, _ = clients(api)
    notify.preview(run, RUN, tg)
    assert "고친 점 (1/2회)" in api.texts[-1] and "신발을 실제 크기로" in api.texts[-1]


def test_publish_waits_for_fixed_preview_unless_ok_after(results, store):  # noqa: F811
    api = FakeApis()
    rec = sent(api, results / RUN)
    tg, ig, host = clients(api)
    api.replies = [
        (rec.sent_at + 5, "/ok", None),
        (rec.sent_at + 6, "/fix 1 신발 더 크게", None),
    ]
    store.add_feedback([entry(update_id=2, at=rec.sent_at + 6)])  # schedule 이 다시 만들기로 기록
    code = publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host)
    assert code == publish.WAITING and "새 미리보기를 기다립니다" in api.texts[-1]
    # /fix 뒤에 /ok 를 보내면 지금 미리보기로 게시한다
    api.replies.append((rec.sent_at + 7, "/ok", None))
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert store.posts()[-1].date == DAY


def test_publish_ignores_fix_that_was_only_recorded(results, store):  # noqa: F811
    api = FakeApis()
    rec = sent(api, results / RUN)
    tg, ig, host = clients(api)
    api.replies = [(rec.sent_at + 5, "/ok", None), (rec.sent_at + 6, "/fix 1 다음엔 밝게", None)]
    store.add_feedback([entry(update_id=2, result="limit")])
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0


def test_schedule_checks_fix_between_deadline_and_publish(monkeypatch, tmp_path):
    profile = load_profile("women")
    fix = [sl for sl in schedule.slots(profile) if sl.task == "fix"]
    # 외부 스케줄러가 매시 정각에 깨우는 시각과 같다 (새로 깨울 시각이 늘지 않음)
    assert [sl.local() for sl in fix] == ["16:00", "17:00", "18:00", "19:00"]
    assert not any(schedule.backup_slot(sl) for sl in fix)
    # 다른 작업으로 깨어날 때도 같이 본다
    tasks = schedule.due("0 1 * * *", datetime(2026, 9, 28, 1, 0, tzinfo=UTC))
    assert [t.task for t in tasks if t.task == "fix"] == ["fix"]

    calls = []

    def fake_check(account, day, res, tg, now, dry_run):
        calls.append((account, day))
        return {
            "run": "2026-09-28-run9",
            "round": 1,
            "fixes": [{"kind": "caption", "text": "짧게"}],
        }

    monkeypatch.setattr(feedback, "check", fake_check)
    gh = schedule.DryRun()
    t = schedule.Task("women", "fix", "2026-09-28", True, True, 0)
    schedule.execute([t], tmp_path, gh, object(), datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
    assert calls == [("women", date(2026, 9, 28))]
    wf, inputs = gh.sent[0]
    assert wf == "daily.yml" and inputs["mode"] == "fix" and inputs["date"] == "2026-09-28"
    assert json.loads(inputs["pick"])["run"] == "2026-09-28-run9"


def test_context_shows_recent_feedback(store):  # noqa: F811
    store.add_feedback([entry(text="바지가 짧아요", slide=1, kind="cover", date=date(2026, 9, 26))])
    text = plan.context(load_profile("women"), store, DAY, with_weather=False)
    assert "사람 피드백" in text and "09-26 1장 표지: 바지가 짧아요" in text


@pytest.mark.parametrize("text", ["/fix 1 women 09-27 가방", "/FIX@bot 1 가방"])
def test_parse_fix_command_keeps_args(text):
    cmd = notify.parse(text, "women", DAY)
    assert cmd.name == "fix" and cmd.args == ["1", "가방"]


def test_pending_rules_only_new_feedback(store):  # noqa: F811
    assert feedback.pending_entries(store) == []
    store.add_feedback([entry(text="신발 더 크게", slide=1, kind="cover", update_id=3)])
    store.add_feedback([entry(text="캡션 짧게", kind="caption", update_id=7)])
    report = feedback.pending_report(store, DAY)
    assert "새 피드백 2개" in report and "1장 표지: 신발 더 크게" in report
    feedback.mark_rules(store, 3)
    assert [e.text for e in feedback.pending_entries(store)] == ["캡션 짧게"]
    feedback.mark_rules(store, 1)  # 되돌아가지 않는다
    assert feedback.rules_state(store) == 3
    feedback.mark_rules(store, 7)
    assert feedback.pending_entries(store) == []


def test_pending_cli_exit_code(store, tmp_path, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(feedback, "Store", lambda account: store)
    assert feedback.main(["pending", "--account", store.account]) == feedback.NO_NEW
    store.add_feedback([entry(text="캡션 짧게", kind="caption", update_id=9)])
    out = tmp_path / "rules.md"
    assert feedback.main(["pending", "--account", store.account, "--out", str(out)]) == 0
    assert "UPTO=9" in capsys.readouterr().out and "캡션 짧게" in out.read_text(encoding="utf-8")
    assert feedback.main(["mark", "--account", store.account, "--upto", "9"]) == 0
    assert feedback.main(["pending", "--account", store.account]) == feedback.NO_NEW


def test_schedule_rules_once_a_day(tmp_path):
    profile = load_profile("women")
    rules = [sl for sl in schedule.slots(profile) if sl.task == "rules"]
    assert [sl.local() for sl in rules] == [profile.feedback.rules_at]
    gh = schedule.DryRun()
    t = schedule.Task("women", "rules", "2026-09-28", True, True, 0)
    schedule.execute([t], tmp_path, gh, object(), datetime(2026, 9, 28, 23, 0, tzinfo=UTC))
    assert gh.sent == [("feedback-rules.yml", {"account": "women"})]
