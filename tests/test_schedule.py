from datetime import UTC, date, datetime

import pytest

from core.profile import load_profile
from tools import schedule

REF = date(2026, 9, 28)


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


def test_workflow_crons_match_profiles():
    """profile.yaml schedule 을 바꾸고 sync 를 안 하면 실패한다."""
    assert not schedule.sync(check_only=True), "python -m tools.schedule sync 를 돌리세요"


def test_profile_rejects_deadline_after_publish(tmp_path):
    import shutil

    import yaml

    shutil.copytree("accounts/women", tmp_path / "women")
    path = tmp_path / "women" / "profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["schedule"]["pick_deadline"] = "19:30"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="1시간"):
        load_profile("women", root=tmp_path)
    data["schedule"]["pick_deadline"] = "09:00"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="순서"):
        load_profile("women", root=tmp_path)


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
    # 고르기(10~15시)와 /fix 확인(15:47~19:47)이 30분 간격으로 이어진다
    assert "Asia/Seoul  매일  10~19시  17,47분" in lines
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
