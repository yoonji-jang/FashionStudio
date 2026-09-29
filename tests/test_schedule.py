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
    """공유리워드 확인(share)은 모든 작업에 붙어서 빼고 본다 (test_share)."""
    return [t for t in schedule.due(*args) if t.task != "share"]


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
