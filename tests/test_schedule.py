import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

import core.profile
from core.profile import load_profile
from tools import schedule

REF = date(2026, 9, 28)
KST = ZoneInfo("Asia/Seoul")

# 시간 계산은 고정 시간표(conftest FIXED_SCHEDULE)로 본다. 실제 시각은 test_women_profile_is_hourly
pytestmark = pytest.mark.usefixtures("fixed_times")


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_women_slots_become_utc_crons():
    p = load_profile("women")
    crons = {(s.task, s.local()): s.cron("Asia/Seoul", REF) for s in schedule.slots(p)}
    assert crons[("candidates", "10:17")] == "17 1 * * *"
    assert crons[("pick", "15:17")] == "17 6 * * *"  # 마감
    assert crons[("publish", "20:03")] == "3 11 * * *"
    # 월,목 04:17 KST 는 UTC 로 전날(일,수) 19:17
    assert crons[("collect", "월,목 04:17")] == "17 19 * * 0,3"
    assert crons[("themes", "일 18:17")] == "17 9 * * 0"


def test_pick_and_publish_slots_mark_first_and_final():
    p = load_profile("women")
    picks = [s for s in schedule.slots(p) if s.task == "pick"]
    assert picks[0].local() == "10:47" and picks[0].first and not picks[0].final
    assert picks[-1].local() == p.schedule.pick_deadline and picks[-1].final
    pubs = [s for s in schedule.slots(p) if s.task == "publish"]
    assert len(pubs) == p.schedule.publish_retries + 1
    assert pubs[0].first and not pubs[0].final and pubs[-1].final


def test_pick_disabled_has_no_pick_slots():
    p = load_profile("women")
    p.pick.enabled = False
    assert not [s for s in schedule.slots(p) if s.task == "pick"]
    p.schedule.enabled = False
    assert schedule.slots(p) == []


def due(*args) -> list[schedule.Task]:
    """공유리워드(links, share)·피드백(fix) 작업은 모든 작업에 붙어서 빼고 본다
    (test_share, test_feedback)."""
    return [t for t in schedule.due(*args) if t.task not in ("links", "share", "fix")]


def test_due_uses_fired_cron_even_when_actions_is_late():
    # 10:17 KST 후보 cron 이 40분 늦게 깨어나도 그날 게시물
    tasks = due("17 1 * * *", utc("2026-09-28T01:57:00"), ["women"])
    assert [(t.task, t.date) for t in tasks] == [("candidates", "2026-09-28")]
    assert tasks[0].minutes_left == 5 * 60 - 40
    # 마감 칸
    [t] = due("17 6 * * *", utc("2026-09-28T06:25:00"), ["women"])
    assert t.task == "pick" and t.final and t.date == "2026-09-28" and t.minutes_left == 0
    # 게시 마지막 확인
    [t] = due("3 13 * * *", utc("2026-09-28T13:10:00"), ["women"])
    assert t.task == "publish" and t.final and not t.first
    # 주간 주제: 일요일 저녁 → 다음 날부터
    [t] = due("17 9 * * 0", utc("2026-09-27T09:30:00"), ["women"])
    assert t.task == "themes" and t.date == "2026-09-28"
    assert due("0 0 * * *", utc("2026-09-28T00:00:00"), ["women"]) == []


def test_workflow_crons_match_profiles(monkeypatch):
    """profile.yaml schedule 을 바꾸고 sync 를 안 하면 실패한다 (실제 시간표로 본다)."""
    import core.profile

    monkeypatch.setattr(schedule, "load_profile", core.profile.load_profile)
    assert not schedule.sync(check_only=True), "python -m tools.schedule sync 를 돌리세요"


def test_profile_rejects_deadline_after_publish(tmp_path):
    import shutil

    import yaml

    shutil.copytree("accounts/women", tmp_path / "women")
    path = tmp_path / "women" / "profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    sch = data["schedule"]
    sch.update(candidates_at="10:00", pick_deadline="19:30", publish_at="20:00", ok_deadline=None)
    sch["ok_remind_at"] = None
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="1시간"):
        load_profile("women", root=tmp_path)
    sch["pick_deadline"] = "09:00"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="순서"):
        load_profile("women", root=tmp_path)
    # 승인 마감은 고르기 마감과 게시 사이 (게시가 다음 날 아침이면 그 전 새벽)
    sch.update(candidates_at="09:00", pick_deadline="18:00", publish_at="07:00")
    sch["ok_deadline"] = "08:00"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="ok_deadline"):
        load_profile("women", root=tmp_path)
    sch.update(ok_deadline="05:00", ok_remind_at="06:00")
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="ok_remind_at"):
        load_profile("women", root=tmp_path)
    sch["ok_remind_at"] = "22:00"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    # fixed_times 가 바꾼 load_profile 말고 원래 것으로 읽는다
    assert core.profile.load_profile("women", root=tmp_path).schedule.ok_deadline == "05:00"


def test_execute_dispatches_workflows(tmp_path, monkeypatch):
    from tools import pick

    gh = schedule.DryRun()
    T = schedule.Task
    tasks = [
        T("women", "candidates", "2026-09-28", True, True, 300),
        T("women", "publish", "2026-09-28", False, False, 0),
        T("women", "collect", "2026-09-28", True, True, 0),
    ]
    schedule.execute(tasks, tmp_path, gh)
    assert gh.sent[0] == (
        "daily.yml",
        {"account": "women", "date": "2026-09-28", "mode": "candidates", "sandbox": False},
    )
    assert gh.sent[1][1]["notify_wait"] is False and gh.sent[1][1]["scheduled"] is True
    assert gh.sent[2] == ("collect.yml", {"account": "women", "commit": True})

    act = pick.Action("render", "2026-09-28-run3", 2)
    monkeypatch.setattr(pick, "check", lambda *a, **k: act)
    gh = schedule.DryRun()
    schedule.execute([T("women", "pick", "2026-09-28", False, False, 100)], tmp_path, gh)
    assert gh.sent[0][1]["mode"] == "render" and gh.sent[0][1]["pick"] == "2026-09-28-run3/2"

    # 후보 한 벌 고치기 (/fix 1 …) 는 mode=edit 로, 고칠 점을 JSON 으로 넘긴다
    fix = {"update_id": 7, "looks": [1], "text": "이너를 나시로"}
    act = pick.Action("edit", "2026-09-28-run3", fixes=[fix])
    gh = schedule.DryRun()
    schedule.execute([T("women", "pick", "2026-09-28", False, False, 100)], tmp_path, gh)
    sent = gh.sent[0][1]
    assert sent["mode"] == "edit"
    assert json.loads(sent["pick"]) == {"run": "2026-09-28-run3", "fixes": [fix]}


def test_claude_steps_accept_bot_dispatch():
    """schedule 이 띄운 실행은 실행자가 봇이라 allowed_bots 가 없으면 Claude 단계가 멈춘다."""
    from pathlib import Path

    import yaml

    for path in sorted(Path(".github/workflows").glob("*.yml")):
        jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
        for job in jobs.values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("anthropics/claude-code-action"):
                    bots = step["with"].get("allowed_bots", "")
                    assert "github-actions" in bots, f"{path.name}: {step.get('name')}"


# ---------- auto: 밀린 칸 따라잡기, 한 번만 실행 (12단계) ----------


def auto(ticks, now, results=None):
    tasks, missed = schedule.due_auto(ticks, results, utc(now), ["women"])
    main = [t for t in tasks if t.task in ("candidates", "pick", "publish")]
    return main, missed


def test_auto_runs_slot_once_and_skips_done(tmp_path):
    ticks = schedule.Ticks(tmp_path, utc("2026-09-28T01:10:00"))
    # 10:18 KST: 후보 칸
    tasks, missed = auto(ticks, "2026-09-28T01:18:00")
    assert [(t.task, t.date) for t in tasks] == [("candidates", "2026-09-28")]
    assert tasks[0].keys == ["women candidates 2026-09-28 10:17"] and tasks[0].late == 1
    assert missed == []  # 기록 시작 전 칸(어제 게시 등)은 알리지 않음
    schedule.execute(tasks, tmp_path, schedule.DryRun(), ticks=ticks)
    ticks.save(utc("2026-09-28T01:18:00"))
    # GitHub 예약이 늦게 같은 칸을 또 깨워도 하지 않는다
    again = schedule.Ticks(tmp_path)
    assert auto(again, "2026-09-28T01:40:00") == ([], [])


def test_auto_catches_up_missed_slots_with_latest_one(tmp_path):
    ticks = schedule.Ticks(tmp_path, utc("2026-09-28T00:00:00"))
    # 12:30 KST 까지 아무것도 안 돌았음: 후보 + 고르기(가장 최근 12:17 칸 하나)
    tasks, missed = auto(ticks, "2026-09-28T03:30:00")
    assert [t.task for t in tasks] == ["candidates", "pick"]
    cand, pick = tasks
    assert cand.late == 133
    assert pick.first and not pick.final
    assert pick.keys == [f"women pick 2026-09-28 {h}" for h in ("10:47", "11:17", "11:47", "12:17")]
    assert pick.minutes_left == 167


def test_auto_skips_stale_candidates_and_reports_once(tmp_path):
    ticks = schedule.Ticks(tmp_path, utc("2026-09-28T00:00:00"))
    # 14:50 KST: 고를 시간이 30분도 안 남아 후보는 건너뛰고 알림, 고르기는 계속
    tasks, missed = auto(ticks, "2026-09-28T05:50:00")
    assert [t.task for t in tasks] == ["pick"]
    assert [(t.task, t.at) for t in missed] == [("candidates", "10:17")]
    # 다음 칸에서는 다시 알리지 않는다
    _, missed = auto(ticks, "2026-09-28T06:17:00")
    assert missed == []


def test_auto_publish_retries_group_and_final_alert(tmp_path):
    ticks = schedule.Ticks(tmp_path, utc("2026-09-28T00:00:00"))
    for k in ("candidates 2026-09-28 10:17", "pick 2026-09-28 15:17"):
        ticks.mark([f"women {k}"], "done", utc("2026-09-28T06:17:00"))
    # 21:10 KST: 20:03, 20:33, 21:03 칸이 모두 밀림 → 게시 한 번 (첫 칸 표시 이어받음)
    tasks, missed = auto(ticks, "2026-09-28T12:10:00")
    [pub] = [t for t in tasks if t.task == "publish"]
    assert pub.first and not pub.final and len(pub.keys) == 3
    assert not [t for t in missed if t.task == "publish"]
    # 22:40 KST 까지 하나도 못 돌면 마지막 칸을 놓친 것으로 알림
    ticks2 = schedule.Ticks(tmp_path / "x", utc("2026-09-28T00:00:00"))
    _, missed = auto(ticks2, "2026-09-28T13:40:00")
    finals = [t for t in missed if t.task == "publish" and t.final]
    assert len(finals) == 1


def test_auto_skips_candidates_already_sent(tmp_path, monkeypatch):
    from tools import pick

    monkeypatch.setattr(pick, "latest", lambda *a: (tmp_path, None))
    ticks = schedule.Ticks(tmp_path, utc("2026-09-28T00:00:00"))
    tasks, _ = auto(ticks, "2026-09-28T03:25:00", tmp_path)
    assert [t.task for t in tasks] == ["pick"]
    assert "women candidates 2026-09-28 10:17" in ticks


class FakeTG:
    chat_id = "1"

    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str, **kw) -> int:
        self.sent.append(text)
        return 0

    def updates(self) -> list[dict]:
        return []


def test_auto_late_and_missed_alerts(tmp_path, monkeypatch):
    from tools import pick

    monkeypatch.setattr(pick, "themes_again", lambda *a: None)
    tg = FakeTG()
    ticks = schedule.Ticks(tmp_path, utc("2026-09-28T00:00:00"))
    tasks, missed = auto(ticks, "2026-09-28T02:30:00")  # 11:30 KST, 73분 늦음
    schedule.report_missed(missed, tg)
    schedule.execute(tasks, tmp_path, schedule.DryRun(), tg, ticks=ticks)
    assert any("코디 후보 보내기 자동 실행이 73분 늦게" in s for s in tg.sent)
    assert "women candidates 2026-09-28 10:17" in ticks
    tg = FakeTG()
    ticks = schedule.Ticks(tmp_path / "y", utc("2026-09-28T00:00:00"))
    _, missed = auto(ticks, "2026-09-28T05:50:00")
    schedule.report_missed(missed, tg)
    assert tg.sent and "코디 후보 보내기(10:17) 자동 실행을 놓쳤어요" in tg.sent[0]


def test_ticks_file_is_pruned(tmp_path):
    ticks = schedule.Ticks(tmp_path, utc("2026-09-01T00:00:00"))
    ticks.mark(
        ["women pick 2026-09-01 10:47", "women pick 2026-09-28 10:47"],
        "done",
        utc("2026-09-28T02:00:00"),
    )
    ticks.save(utc("2026-09-28T02:00:00"))
    kept = schedule.Ticks(tmp_path)
    assert "women pick 2026-09-28 10:47" in kept and "women pick 2026-09-01 10:47" not in kept
    assert kept.since == utc("2026-09-01T00:00:00")


def test_external_jobs_cover_every_slot():
    lines = schedule.external_jobs(["women"])
    assert "Asia/Seoul  매일  10~14시  17,47분" in lines
    assert "Asia/Seoul  매일  20~21시  03,33분" in lines
    # 일 18:17 주제는 매일 18:17 칸과 같아서 따로 만들지 않음
    assert not any(line.split()[1] == "일" and "17분" in line for line in lines)


def test_schedule_event_runs_auto():
    import yaml

    wf = yaml.safe_load(open(".github/workflows/schedule.yml", encoding="utf-8"))
    env = wf["jobs"]["tick"]["env"]["CRON"]
    assert "'auto'" in env and "github.event_name == 'schedule'" in env


def test_backup_cron_keeps_only_key_slots():
    p = load_profile("women")
    p.schedule.github_cron = "backup"
    lines = schedule.cron_lines([(p, sl) for sl in schedule.slots(p)], REF)
    kinds = sorted(d.split()[1] + " " + d.split()[-1] for ds in lines.values() for d in ds)
    assert "candidates 10:17" in kinds and "pick 10:47" in kinds and "pick 15:17" in kinds
    assert "publish 20:03" in kinds and "publish 22:03" in kinds
    assert "pick 12:17" not in kinds and "publish 21:03" not in kinds
    assert not [k for k in kinds if k.startswith("links")]


def test_minimal_cron_keeps_only_first_publish():
    p = load_profile("women")
    p.schedule.github_cron = "minimal"
    lines = schedule.cron_lines([(p, sl) for sl in schedule.slots(p)], REF)
    kinds = [d.split()[1] + " " + d.split()[-1] for ds in lines.values() for d in ds]
    assert kinds == ["publish 20:03"]


def test_women_profile_is_hourly(monkeypatch):
    """women 은 외부 스케줄러가 매시 정각에 깨운다 (cron-job.org Every 1 hour)."""
    import core.profile

    monkeypatch.setattr(schedule, "load_profile", core.profile.load_profile)
    lines = schedule.external_jobs(["women"])
    assert all(line.split()[-1] == "00분" for line in lines), lines


def test_women_overnight_schedule_dates(monkeypatch, tmp_path):
    """09시 후보 → 18시 고르기 마감 → 05시 승인 마감 → 다음 날 07시 게시 (2026-10-01 부터)."""
    monkeypatch.setattr(schedule, "load_profile", core.profile.load_profile)
    p = core.profile.load_profile("women")
    p.schedule.enabled = True  # 프로필에서 잠시 꺼도 시간 계산은 시험
    monkeypatch.setattr(schedule, "load_profile", lambda *a, **k: p)
    got = {}
    for sl in schedule.slots(p):
        got.setdefault(sl.task, []).append(sl.local())
    assert got["links"][0] == "10:00" and got["links"][-1] == "05:00"
    assert got["fix"][-1] == "04:00" and got["remind"] == ["22:00", "05:00"]
    assert got["publish"] == ["07:00", "08:00"] and got["themes"] == ["토 18:00"]
    ticks = schedule.Ticks(tmp_path, datetime(2026, 9, 30, 0, 0, tzinfo=UTC))
    kst = lambda d, h: datetime(2026, 10, d, h, 0, tzinfo=KST)  # noqa: E731
    # 자정 넘어서도 어제 후보의 게시 날짜 (10/1)
    for when, want in [(kst(1, 3), "2026-10-01"), (kst(1, 10), "2026-10-02")]:
        tasks, _ = schedule.due_auto(ticks, None, when.astimezone(UTC))
        assert {t.date for t in tasks if t.task in ("links", "fix")} == {want}
        for t in tasks:
            ticks.mark(t.keys, "done", when)
    # 게시 07:00 은 그날 날짜, 주제(토)는 월요일(일요일 후보의 게시 날짜)부터
    [pub] = [
        t
        for t in schedule.due_auto(ticks, None, kst(2, 7).astimezone(UTC))[0]
        if t.task == "publish"
    ]
    assert pub.date == "2026-10-02"
    sat = datetime(2026, 10, 3, 18, 0, tzinfo=KST).astimezone(UTC)
    [th] = [t for t in schedule.due_auto(ticks, None, sat)[0] if t.task == "themes"]
    assert th.date == "2026-10-05"


def _post_days(monkeypatch):
    """women 시간표에 16단계 요일별 게시물(월수금 펼친컷, 화목토 모델컷, 일 쉼)을 켠다."""
    days = {"월": "flatlay", "화": "model", "수": "flatlay", "목": "model", "금": "flatlay"}
    days["토"] = "model"
    load = schedule.load_profile

    def with_days(account, *a, **k):
        p = load(account, *a, **k)
        p.schedule = p.schedule.model_copy(update={"post_days": days})
        return p

    monkeypatch.setattr(schedule, "load_profile", with_days)
    return with_days("women")


def _published(results, run: str, account: str = "women"):
    import json

    d = results / run
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"account": account}), encoding="utf-8")
    (d / "published.json").write_text("{}", encoding="utf-8")


def test_post_kind_by_weekday(monkeypatch):
    p = _post_days(monkeypatch)
    kinds = [p.schedule.post_kind(date(2026, 10, 5 + i)) for i in range(7)]  # 월~일
    assert kinds == ["flatlay", "model", "flatlay", "model", "flatlay", "model", None]
    # post_days_from 전 날짜는 요일과 상관없이 펼친 코디컷 (이미 준비된 게시물)
    start = p.schedule.model_copy(update={"post_days_from": date(2026, 10, 12)})
    assert start.post_kind(date(2026, 10, 11)) == "flatlay"
    assert start.post_kind(date(2026, 10, 13)) == "model"
    assert (
        load_profile("women")
        .schedule.model_copy(update={"post_days": None})
        .post_kind(date(2026, 10, 11))
        == "flatlay"
    )


def test_model_day_dispatches_model_render_from_yesterday(tmp_path, monkeypatch):
    """화요일 게시(모델컷): 월요일 게시한 결과로 daily(mode=model). 고르기는 하지 않는다."""
    _post_days(monkeypatch)
    _published(tmp_path, "2026-10-05-run90")
    _published(tmp_path, "2026-10-05-run88")
    (tmp_path / "2026-10-05-run91").mkdir()  # 게시 안 한 실행은 쓰지 않음
    T = schedule.Task
    gh = schedule.DryRun()
    tasks = [
        T("women", "candidates", "2026-10-06", True, True, 300),
        T("women", "pick", "2026-10-06", False, True, 0),
        T("women", "publish", "2026-10-06", True, False, 0),
    ]
    schedule.execute(tasks, tmp_path, gh)
    assert gh.sent[0] == (
        "daily.yml",
        {
            "account": "women",
            "date": "2026-10-06",
            "mode": "model",
            "pick": "2026-10-05-run90",
            "sandbox": False,
        },
    )
    assert [w for w, _ in gh.sent] == ["daily.yml", "publish.yml"]  # 고르기 없음, 게시는 함


def test_model_day_without_yesterday_post_rests(tmp_path, monkeypatch):
    _post_days(monkeypatch)
    sent = []

    class TG:
        chat_id = "1"

        def send(self, text, **kw):
            sent.append(text)

    gh = schedule.DryRun()
    T = schedule.Task
    schedule.execute([T("women", "candidates", "2026-10-06", True, True, 300)], tmp_path, gh, TG())
    assert gh.sent == [] and "쉬어요" in sent[0]


def test_rest_day_skips_candidates_pick_and_publish(tmp_path, monkeypatch):
    _post_days(monkeypatch)
    ticks = schedule.Ticks(tmp_path)
    T = schedule.Task
    tasks = [
        T("women", "candidates", "2026-10-11", True, True, 300, keys=["women candidates x"]),
        T("women", "pick", "2026-10-11", True, True, 300),
        T("women", "publish", "2026-10-11", True, True, 0),
    ]
    gh = schedule.DryRun()
    schedule.execute(tasks, tmp_path, gh, ticks=ticks)
    assert gh.sent == [] and "women candidates x" in ticks


def test_limit_resets_parses_claude_message():
    at = datetime(2026, 10, 7, 8, 20, tzinfo=UTC)
    msg = "You've hit your session limit · resets 9am (UTC)"
    assert schedule.limit_resets(msg, at) == datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    late = datetime(2026, 10, 7, 9, 30, tzinfo=UTC)  # 지난 시각이면 다음 날
    assert schedule.limit_resets(msg, late) == datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    seoul = schedule.limit_resets("limit · resets 3:30pm (Asia/Seoul)", at)
    assert seoul == datetime(2026, 10, 7, 6, 30, tzinfo=UTC) + schedule.timedelta(days=1)
    weekly = schedule.limit_resets("weekly limit · resets Oct 9, 5am (UTC)", at)
    assert weekly == datetime(2026, 10, 9, 5, 0, tzinfo=UTC)
    assert schedule.limit_resets("모르는 말", at) == at + schedule.LIMIT_FALLBACK


def test_retry_limited_redispatches_once_after_reset(tmp_path, monkeypatch):
    import json

    class Posts:
        def posts(self):
            return []

    monkeypatch.setattr(schedule, "Store", lambda account: Posts())

    class GH:
        def __init__(self):
            self.calls = []

        def dispatch(self, workflow, inputs):
            self.calls.append((workflow, inputs))

    class TG:
        def __init__(self):
            self.texts = []

        def send(self, text, **kw):
            self.texts.append(text)
            return 1

    at = datetime(2026, 10, 7, 8, 20, tzinfo=UTC)
    (tmp_path / "limits").mkdir()

    def stopped(name, workflow, inputs, key, run="", ago=0):
        rec = {
            "workflow": workflow,
            "inputs": inputs,
            "key": key,
            "run": run,
            "message": "You've hit your session limit · resets 9am (UTC)",
            "at": int(at.timestamp()) - ago,
        }
        path = tmp_path / "limits" / f"{name}.json"
        path.write_text(json.dumps(rec))
        return path

    render = {"account": "women", "date": "2026-10-08", "mode": "render", "pick": "r160/chosen"}
    daily = stopped(
        "daily.yml-1", "daily.yml", render, "daily:women:2026-10-08", "2026-10-08-run166"
    )
    themes = stopped("themes.yml-2", "themes.yml", {"account": "women"}, "themes:women")
    older = stopped("themes.yml-1", "themes.yml", {"account": "women"}, "themes:women", ago=60)
    (tmp_path / "2026-10-07-run151").mkdir()  # 같은 날짜를 나중에 다시 만든 daily 는 건너뜀
    redone = stopped(
        "daily.yml-0",
        "daily.yml",
        {**render, "date": "2026-10-07"},
        "daily:women:2026-10-07",
        "2026-10-07-run150",
    )
    gh, tg = GH(), TG()

    # 한도가 풀리기 전에는 기다린다
    assert schedule.retry_limited(tmp_path, gh, tg, at + schedule.timedelta(minutes=20)) == []
    assert gh.calls == []
    assert json.loads(older.read_text())["skipped"] == "같은 일의 더 나중 기록이 있음"
    assert json.loads(redone.read_text())["skipped"] == "더 나중 실행이 있음"

    after = datetime(2026, 10, 7, 9, 10, tzinfo=UTC)
    assert schedule.retry_limited(tmp_path, gh, tg, after, dry_run=True)[0].startswith("(시험)")
    assert gh.calls == []
    schedule.retry_limited(tmp_path, gh, tg, after)
    assert sorted(gh.calls) == [("daily.yml", render), ("themes.yml", {"account": "women"})]
    assert any("게시물 만들기" in t for t in tg.texts) and any("주제 정하기" in t for t in tg.texts)
    assert json.loads(daily.read_text())["retried"] and json.loads(themes.read_text())["retried"]
    schedule.retry_limited(tmp_path, gh, tg, after)  # 한 번만
    assert len(gh.calls) == 2


def test_claude_workflows_record_usage_limit():
    from pathlib import Path

    for wf in ("daily.yml", "themes.yml", "feedback-rules.yml"):
        text = (Path(".github/workflows") / wf).read_text(encoding="utf-8")
        assert ".github/scripts/limit.sh" in text and "LIMITED" in text, wf
