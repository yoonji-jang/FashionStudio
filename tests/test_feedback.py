import json
from datetime import UTC, date, datetime

import httpx
import pytest
import yaml

from core.models import CoverScore, FeedbackEntry, PickRecord, PostRecord
from core.profile import load_profile
from tests.test_publish import CHAT, DAY, FakeApis, clients, record, results, sent  # noqa: F401
from tests.test_review import built, fake_photos, store  # noqa: F401 - pytest fixture
from tools import feedback, notify, plan, publish, review, schedule

pytestmark = pytest.mark.usefixtures("fixed_times")

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


def test_find_fixes_splits_several_slide_numbers(results):  # noqa: F811
    slides = slides_of(results)
    rec = record(message_ids=[11, 12])
    upd = [update(1, 1200, "/fix 2 3 이너를 베이직으로"), update(2, 1300, "/fix 2,4번 밝게")]
    found = feedback.find_fixes(upd, rec, CHAT, slides)
    assert [(f["slide"], f["text"]) for f in found] == [
        (2, "이너를 베이직으로"),
        (3, "이너를 베이직으로"),
        (2, "밝게"),
        (4, "밝게"),
    ]


def photo_update(uid: int, at: int, fid: str, caption: str = "", group: str = ""):
    u = update(uid, at, "")
    m = u["message"]
    del m["text"]
    m["photo"] = [{"file_id": fid, "width": 10, "height": 10}]
    if caption:
        m["caption"] = caption
    if group:
        m["media_group_id"] = group
    return u


def test_find_fixes_reads_caption_and_attached_photos(results):  # noqa: F811
    """사진에 설명으로 단 /fix 와 그 앨범·앞뒤 3분 안 사진을 참고 사진으로 (2026-10-22 사용자)."""
    slides = slides_of(results)
    rec = record(message_ids=[11, 12])
    text = (
        "/fix 코끝을 두번째 사진처럼 오똑하게. 코디 후보 3번도 추가해줘.\n"
        "/fix 2 가디건 단추는 열고 이너 나시가 보이게\n/fix 3 벨트 제거"
    )
    upd = [
        photo_update(1, 1200, "A", caption=text, group="g"),
        photo_update(2, 1200, "B", group="g"),
        photo_update(3, 1300, "C"),  # 3분 안에 따로 보낸 사진
        photo_update(4, 1600, "D"),  # 3분 넘게 지난 사진
        photo_update(5, 1250, "E", caption="/cover 2"),  # 명령을 단 사진은 빼고
    ]
    found = feedback.find_fixes(upd, rec, CHAT, slides)
    assert [(f["slide"], f["kind"]) for f in found][0] == (None, "all")
    assert [f["slide"] for f in found[1:]] == [2, 3]
    assert all(f["photos"] == ["A", "B", "C"] for f in found)
    assert "코디 후보 3번도 추가" in found[0]["text"]
    # 사진 없는 /fix 는 photos 가 없다
    plain = feedback.find_fixes([update(9, 1200, "/fix 2 밝게")], rec, CHAT, slides)
    assert "photos" not in plain[0]


def test_save_fix_photos_downloads_and_drops_failed(tmp_path):
    class Tg:
        def download(self, fid, dest):
            if fid == "X":
                raise RuntimeError("없음")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(fid.encode())
            return dest

    fixes = [
        {"update_id": 7, "photos": ["A", "X"]},
        {"update_id": 7, "photos": ["A"]},
        {"update_id": 8, "photos": ["X"]},
    ]
    feedback.save_fix_photos(fixes, tmp_path, "women", DAY, Tg(), False)
    want = f"flow/women/{DAY}/fix/7_1.jpg"
    assert fixes[0]["photos"] == [want] and fixes[1]["photos"] == [want]
    assert "photos" not in fixes[2] and (tmp_path / want).read_bytes() == b"A"


def test_check_leaves_old_preview_after_new_candidates(results, store):  # noqa: F811
    api = FakeApis()
    rec = sent(api, results / RUN)
    tg, _, _ = clients(api)
    # 미리보기 뒤에 같은 날 후보를 새로 보냈다 (다시 받은 후보)
    picks = results / "2026-09-27-run7" / "picks"
    picks.mkdir(parents=True)
    cand = PickRecord(
        account="women", date=DAY, run="2026-09-27-run7", count=8, sent_at=rec.sent_at + 100
    )
    (picks / "pick.json").write_text(cand.model_dump_json())
    api.replies = [
        (rec.sent_at + 5, "/fix 1 신발 더 크게", None),  # 미리보기에 한 말
        (rec.sent_at + 200, "/fix 5 이너나시 제거해줘", None),  # 새 후보에 한 말
    ]
    fix = feedback.check("women", DAY, results, tg, EARLY, store=store)
    assert [f["text"] for f in fix["fixes"]] == ["신발 더 크게"]
    assert [(e.result, e.run) for e in store.feedback()] == [
        ("candidates", "2026-09-27-run7"),
        ("rerender", RUN),
    ]
    assert any("새 후보를 보낸 뒤" in t for t in api.texts)
    # 새 후보에 한 말만 오면 다시 그리지 않는다
    api.replies.append((rec.sent_at + 300, "/fix 2 이너 베이직으로", None))
    assert feedback.check("women", DAY, results, tg, EARLY, store=store) is None
    assert store.feedback()[-1].result == "candidates"


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


def test_prepare_copies_fix_photos_and_candidates(results, tmp_path, monkeypatch):  # noqa: F811
    prev = results / RUN
    (results / "photo" / "fix").mkdir(parents=True)
    (results / "photo" / "fix" / "7_1.jpg").write_bytes(b"jpg")
    cand = results / "2026-09-26-run4" / "picks"
    cand.mkdir(parents=True)
    (cand / "p1.yaml").write_text("looks: []\n", encoding="utf-8")
    (prev / "run.json").write_text(json.dumps({"pick": "2026-09-26-run4/chosen"}), "utf-8")
    root = tmp_path / "repo"
    monkeypatch.setattr(plan, "REPO_ROOT", root)
    out = root / "out" / str(DAY)
    fixes = [{"kind": "all", "text": "키 크게", "photos": ["photo/fix/7_1.jpg", "photo/없음.jpg"]}]
    data = feedback.prepare({"run": RUN, "round": 1, "fixes": fixes}, results, out)
    assert data["fixes"][0]["photos"] == [f"out/{DAY}/fix_photos/7_1.jpg"]
    assert (out / "fix_photos" / "7_1.jpg").read_bytes() == b"jpg"
    assert data["candidates"] == f"out/{DAY}/candidates.yaml"
    assert (out / "candidates.yaml").exists()
    assert fixes[0]["photos"][0] == "photo/fix/7_1.jpg"  # 받은 pick 은 그대로


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


def test_schedule_checks_fix_after_candidates_until_publish(monkeypatch, tmp_path):
    profile = load_profile("women")
    fix = [sl for sl in schedule.slots(profile) if sl.task == "fix"]
    # 고르자마자 미리보기가 오므로 후보(10:17) 뒤 매 간격, 게시(20:03) 전까지
    assert [sl.local() for sl in fix] == [f"{h}:17" for h in range(11, 20)]
    assert not any(schedule.backup_slot(sl) for sl in fix)
    # 다른 작업(후보 10:17)으로 깨어날 때도 같이 본다
    tasks = schedule.due("17 1 * * *", datetime(2026, 9, 28, 1, 17, tzinfo=UTC))
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


def test_check_reads_new_fix_when_more_than_100_messages(results, store):  # noqa: F811
    # 텔레그램은 offset 없이 오래된 100개만 준다. 그 뒤에 온 /fix 도 읽어야 한다
    api = FakeApis()
    rec = sent(api, results / RUN)
    tg, _, _ = clients(api)
    chatter = [(rec.sent_at - 100 + i, "2", None) for i in range(100)]
    multi = "/fix 1 단추가 3개\n/fix 5 가방 정보가 다름"
    api.replies = chatter + [(rec.sent_at + 5, multi, None)]
    fix = feedback.check("women", DAY, results, tg, EARLY, store=store)
    # 한 메시지의 /fix 두 줄은 장마다 따로 읽는다
    assert fix is not None
    assert [(f["slide"], f["text"]) for f in fix["fixes"]] == [
        (1, "단추가 3개"),
        (5, "가방 정보가 다름"),
    ]
    assert [e.slide for e in store.feedback()] == [1, 5]


def test_updates_log_confirms_only_committed_and_keeps_new(tmp_path):
    # 기록이 있으면 기록 끝 다음부터 받아 텔레그램에서 확인 처리하고, 기록과 합쳐 돌려준다
    api = FakeApis()
    http = httpx.Client(transport=httpx.MockTransport(api))
    log = tmp_path / "telegram_updates.json"
    now = int(datetime.now(UTC).timestamp())
    api.replies = [(now - 60, "/ok", None), (now - 30, "2", None)]
    tg = notify.Telegram("T", CHAT, http, log=log, save_log=True)
    assert [u["update_id"] for u in tg.updates()] == [1, 2]
    assert api.offsets == [0]  # 기록이 없으면 offset 없이
    saved = log.read_text(encoding="utf-8")
    assert "보낸이" not in saved and "sender" not in saved  # 이름·아이디는 남기지 않음
    assert [u["message"]["text"] for u in json.loads(saved)] == ["/ok", "2"]
    # 같은 실행에서는 시작 때 기록(없음) 기준이라 새 글을 확인 처리하지 않는다
    api.replies.append((now - 10, "/fix 1 단추", None))
    assert [u["update_id"] for u in tg.updates()] == [1, 2, 3]
    assert api.offsets == [0, 0]
    # 다음 실행: 커밋된 기록 끝(3) 다음부터. 텔레그램이 옛 글을 지워도 기록에서 읽는다
    api.replies[0] = (now - 60, "(지워짐)", None)
    tg2 = notify.Telegram("T", CHAT, http, log=log, save_log=False)
    got = tg2.updates()
    assert api.offsets[-1] == 4
    assert [u["message"]["text"] for u in got] == ["/ok", "2", "/fix 1 단추"]
    # 다른 채팅 글과 오래된 글은 기록하지 않는다
    other = {"update_id": 9, "message": {"date": now, "chat": {"id": 1}, "text": "x"}}
    old = {"update_id": 8, "message": {"date": now - 4 * 86400, "chat": {"id": int(CHAT)}}}
    notify.save_updates_log(log, [old, other], CHAT, now)
    assert json.loads(log.read_text(encoding="utf-8")) == []
