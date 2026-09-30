r"""매일 자동 실행 시간표 (9단계). Claude 는 부르지 않는다.

계정마다 profile.yaml `schedule` 에 적은 시각을 GitHub Actions cron(UTC) 줄로 바꾸고,
schedule 워크플로가 깨어나면 할 작업을 찾아 돌린다.
GitHub 예약은 자주 빠지거나 몇 시간 늦어서(12단계), 외부 스케줄러가 정시에 cron=auto 로 깨운다.
auto 는 "지금까지 해야 했는데 아직 안 한 칸"을 찾는다: 밀린 칸은 따라잡고(같은 작업은 최근 칸
하나로 한 번), 늦게 해도 소용없는 칸(last_chance 지남)은 건너뛰고 알린다. 한 칸은
daily-results 의 ticks.json 에 기록해 외부 스케줄러와 GitHub 예약이 둘 다 깨워도 한 번만 한다.

작업:
  candidates  코디 후보 보내기 (daily 워크플로, mode=candidates. pick 을 끄면 mode=full)
  pick        고른 번호 확인 (tools.pick). 고르면 daily(mode=render), "다시"면 새 후보,
              마감이면 profile pick.on_timeout
  publish     게시 (publish 워크플로). 승인이 늦으면 publish_retry_every 마다 다시
  themes      다음 주 주제 (themes 워크플로)
  collect     지그재그 상품 수집 (collect 워크플로, 창고 커밋)
  token       인스타 토큰 연장 (token 워크플로)
  comments    댓글 확인 → DM 코디 링크 (tools.comments, 이 job 안에서 바로 돈다).
              profile comments.check_at 시각에 따로 깨어나고, with_other_ticks 면
              다른 작업으로 깨어날 때도 같이 한다 (Actions 시간을 거의 더 쓰지 않음)
  links       미리보기에 /ok 가 오면 상품별 지그재그 링크 보내기
              (tools.share, manual_link 나 link_page.send_links).
              고르기 마감~게시 사이 schedule.ok_check_every 마다, 다른 작업 때도 같이
  share       공유리워드 링크 답장 반영, 상품 페이지 다시 올림 (tools.share, manual_link).
              따로 깨어나지 않고 다른 작업으로 깨어날 때마다 같이 한다
  fix         미리보기에 온 /fix 피드백 확인 (15단계, tools.feedback). 다시 만들 것이면
              daily(mode=fix). 고르기 마감~게시 사이 profile feedback.check_every 마다,
              다른 작업 때도 같이
  rules       새 /fix 피드백을 규칙에 반영하는 PR (feedback-rules 워크플로, profile
              feedback.rules_at 에 하루 한 번. 새 피드백이 없으면 워크플로가 건너뜀)

사용법:
  python -m tools.schedule list                    계정별 시간표와 cron 줄
  python -m tools.schedule sync [--check]          schedule.yml 의 cron 줄을 프로필에 맞춤
  python -m tools.schedule external                외부 스케줄러(cron-job.org)에 만들 작업 시각
  python -m tools.schedule due --cron auto --results <폴더>   지금 할 작업 (JSON)
  python -m tools.schedule due --cron "17 1 * * *" 그 cron 줄에 해당하는 작업 (JSON)
  python -m tools.schedule run --cron "17 1 * * *" --results <daily-results 폴더> [--dry-run]
      작업을 실행한다 (워크플로 실행 요청, 고른 번호 확인).
      환경 변수 GITHUB_TOKEN, GITHUB_REPOSITORY,
      GITHUB_REF_NAME, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, IG_SECRETS(댓글 DM, Secrets JSON),
      MEDIA_REPO_TOKEN(공유리워드 링크를 반영한 상품 페이지)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from core.profile import REPO_ROOT, WEEKDAYS, Profile, load_index, load_profile, minutes
from core.store import Store
from tools import share

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "schedule.yml"
BEGIN = "    # >>> tools.schedule sync"
END = "    # <<< tools.schedule sync"


@dataclass(frozen=True)
class Slot:
    """시간표의 한 칸. base 시각에서 offset 분 뒤에 돈다."""

    account: str
    task: str
    base: str  # 기준 시각 HH:MM (계정 시간대)
    offset: int = 0  # 기준 시각에서 몇 분 뒤
    days: tuple[int, ...] | None = None  # 주간 작업의 요일 (월=0), None 이면 매일
    first: bool = True  # 같은 작업의 첫 칸
    final: bool = True  # 같은 작업의 마지막 칸 (고르기 마감, 게시 마지막 확인)

    def local(self) -> str:
        t = (minutes(self.base) + self.offset) % 1440
        hhmm = f"{t // 60:02d}:{t % 60:02d}"
        if self.days is None:
            return hhmm
        return ",".join(WEEKDAYS[d] for d in self.days) + " " + hhmm

    def cron(self, tz: str, ref: date) -> str:
        """UTC cron 줄. 서머타임이 있는 시간대는 ref 날짜의 시차로 계산한다."""
        start = datetime.combine(ref, _hhmm(self.base), ZoneInfo(tz)) + timedelta(
            minutes=self.offset
        )
        utc = start.astimezone(UTC)
        if self.days is None:
            return f"{utc.minute} {utc.hour} * * *"
        shift = (utc.date() - ref).days
        # cron 요일은 일=0, 월=1 … (파이썬 월=0)
        dows = sorted({(d + 1 + shift) % 7 for d in self.days})
        return f"{utc.minute} {utc.hour} * * {','.join(map(str, dows))}"


def _hhmm(text: str) -> time:
    h, m = map(int, text.split(":"))
    return time(h, m)


def _weekly(text: str) -> tuple[tuple[int, ...], str]:
    days, hhmm = text.split(" ")
    return tuple(WEEKDAYS.index(d) for d in days.split(",")), hhmm


def slots(profile: Profile) -> list[Slot]:
    s, a = profile.schedule, profile.id
    if not s.enabled:
        return []
    out = [Slot(a, "candidates", s.candidates_at)]
    if profile.pick.enabled:
        span = (minutes(s.pick_deadline) - minutes(s.candidates_at)) % 1440
        offsets = list(range(s.pick_check_every, span, s.pick_check_every)) + [span]
        out += [
            Slot(a, "pick", s.candidates_at, off, first=i == 0, final=off == span)
            for i, off in enumerate(offsets)
        ]
    n = s.publish_retries
    out += [
        Slot(a, "publish", s.publish_at, i * s.publish_retry_every, first=i == 0, final=i == n)
        for i in range(n + 1)
    ]
    for task, value in (
        ("themes", s.themes_at),
        ("collect", s.collect_at),
        ("token", s.token_refresh_at),
    ):
        if value:
            days, hhmm = _weekly(value)
            out.append(Slot(a, task, hhmm, days=days))
    if share.sends_list(profile) and s.ok_check_every:
        # 고르기 마감(고르기를 끄면 후보 시각) 뒤 게시 전까지 /ok 확인 → 상품 링크 (tools.share)
        start = s.pick_deadline if profile.pick.enabled else s.candidates_at
        span = (minutes(s.publish_at) - minutes(start)) % 1440
        out += [
            Slot(a, "links", start, off) for off in range(s.ok_check_every, span, s.ok_check_every)
        ]
    fb = profile.feedback
    if fb.enabled and fb.check_every:
        # 고르기 마감(고르기를 끄면 후보 시각) 뒤 게시 전까지 /fix 확인 (게시 재시도 때는 같이 봄)
        start = s.pick_deadline if profile.pick.enabled else s.candidates_at
        span = (minutes(s.publish_at) - minutes(start)) % 1440
        out += [Slot(a, "fix", start, off) for off in range(fb.check_every, span, fb.check_every)]
    if fb.enabled and fb.rules_at:
        out.append(Slot(a, "rules", fb.rules_at))  # 피드백을 규칙에 반영하는 PR (하루 한 번)
    if profile.comments.enabled and profile.comments.mode == "builtin":
        out += [Slot(a, "comments", hhmm) for hhmm in profile.comments.check_at]
    return out


def all_slots(accounts: list[str] | None = None) -> list[tuple[Profile, Slot]]:
    result = []
    for account in accounts or load_index().accounts:
        profile = load_profile(account)
        result += [(profile, sl) for sl in slots(profile)]
    return result


def backup_slot(sl: Slot) -> bool:
    """github_cron: backup 에서도 GitHub 예약 줄을 두는 칸. 나머지는 auto 가 따라잡는다."""
    if sl.task in ("pick", "publish"):
        return sl.first or sl.final
    return sl.task not in ("links", "comments", "fix")


def cron_lines(items: list[tuple[Profile, Slot]], ref: date) -> dict[str, list[str]]:
    """cron 줄 → 설명 목록 (같은 줄에 여러 작업이 겹칠 수 있다)."""
    lines: dict[str, list[str]] = {}
    for profile, sl in items:
        if profile.schedule.github_cron == "backup" and not backup_slot(sl):
            continue
        lines.setdefault(sl.cron(profile.schedule.timezone, ref), []).append(
            f"{sl.account} {sl.task} {sl.local()}"
        )
    return lines


def _cron_key(cron: str) -> tuple[int, int]:
    m, h = cron.split()[:2]
    return int(h), int(m)


def render_block(lines: dict[str, list[str]]) -> str:
    rows = [BEGIN + " (직접 고치지 말고 profile.yaml schedule 을 바꾼 뒤 sync)"]
    for cron in sorted(lines, key=_cron_key):
        rows.append(f'    - cron: "{cron}"  # {", ".join(lines[cron])}')
    rows.append(END)
    return "\n".join(rows)


def sync(path: Path = WORKFLOW, ref: date | None = None, check_only: bool = False) -> bool:
    """워크플로의 cron 줄을 프로필 시간표에 맞춘다. 바뀌었으면(또는 check_only 에서 다르면) True."""
    text = path.read_text(encoding="utf-8")
    start, end = text.index(BEGIN), text.index(END) + len(END)
    block = render_block(cron_lines(all_slots(), ref or date.today()))
    if text[start:end] == block:
        return False
    if not check_only:
        path.write_text(text[:start] + block + text[end:], encoding="utf-8")
    return True


# ---------- 깨어났을 때 ----------


@dataclass
class Task:
    account: str
    task: str
    date: str  # 대상 날짜 (candidates·pick·publish: 게시 날짜, themes: 첫 주제 날짜)
    first: bool
    final: bool
    minutes_left: int  # pick: 마감까지 남은 분 (지났으면 0)
    keys: list[str] = field(default_factory=list)  # 이 작업으로 끝나는 칸 (ticks.json)
    late: int = 0  # auto: 가장 이른 칸보다 몇 분 늦게 도는지
    at: str = ""  # 놓친 칸의 원래 시각 HH:MM (알림용)


def slot_date(profile: Profile, sl: Slot, now: datetime, early: int = 10) -> tuple[date, datetime]:
    """이 칸이 원래 돌았어야 할 (기준 날짜, 시각). Actions 가 늦게 깨워도 앞 날짜로 가지 않는다.
    early: 칸 시각보다 이만큼(분) 일찍 깨어나도 그 칸으로 본다."""
    tz = ZoneInfo(profile.schedule.timezone)
    local = now.astimezone(tz)
    for back in range(0, 8):
        d = local.date() - timedelta(days=back)
        at = datetime.combine(d, _hhmm(sl.base), tz) + timedelta(minutes=sl.offset)
        if sl.days is not None and at.weekday() not in sl.days:
            continue
        if at <= local + timedelta(minutes=early):
            return d, at
    raise ValueError(f"{sl.account} {sl.task}: 기준 날짜를 찾지 못했습니다")


def post_date(profile: Profile, base: date) -> date:
    """후보를 보낸 날(base)의 게시 날짜. 게시 시각이 후보 시각보다 앞이면 다음 날 게시."""
    s = profile.schedule
    return base + timedelta(days=0 if minutes(s.candidates_at) < minutes(s.publish_at) else 1)


def _task(profile: Profile, sl: Slot, base: date, now: datetime) -> Task:
    """칸(sl)을 기준 날짜(base)의 작업으로 바꾼다."""
    tz = profile.schedule.timezone
    day = base
    left = 0
    if sl.task in ("links", "fix"):
        day = post_date(profile, base)
    elif sl.task in ("candidates", "pick"):
        day = post_date(profile, base)
        deadline = datetime.combine(base, _hhmm(sl.base), ZoneInfo(tz)) + timedelta(
            minutes=(minutes(profile.schedule.pick_deadline) - minutes(sl.base)) % 1440
        )
        left = max(0, int((deadline - now).total_seconds() // 60))
    elif sl.task == "themes":
        day = base + timedelta(days=1)
    return Task(sl.account, sl.task, day.isoformat(), sl.first, sl.final, left)


def _extras(tasks: list[Task], now: datetime) -> list[Task]:
    """깨어난 계정마다 같이 하는 작업 (댓글, 상품 링크, 공유리워드)."""
    extra = []
    for account in dict.fromkeys(t.account for t in tasks):
        profile = load_profile(account)
        mine = [t for t in tasks if t.account == account]
        cm = profile.comments
        builtin = cm.enabled and cm.mode == "builtin"
        # 다른 작업으로 깨어난 계정은 댓글도 같이 본다 (따로 깨어나는 것보다 Actions 시간이 덜 듦)
        if builtin and cm.with_other_ticks and all(t.task != "comments" for t in mine):
            extra.append(Task(account, "comments", mine[0].date, True, True, 0))
        # 공유리워드: /ok 뒤 상품 링크 보내기와 링크 답장 반영도 깨어날 때마다 본다 (tools.share)
        if share.sends_list(profile) and all(t.task != "links" for t in mine):
            local = now.astimezone(ZoneInfo(profile.schedule.timezone)).date()
            day = post_date(profile, local).isoformat()
            extra.append(Task(account, "links", day, True, True, 0))
        if share.enabled(profile):
            extra.append(Task(account, "share", mine[0].date, True, True, 0))
        # 피드백 /fix 도 깨어날 때마다 본다 (tools.feedback)
        if profile.feedback.enabled and all(t.task != "fix" for t in mine):
            local = now.astimezone(ZoneInfo(profile.schedule.timezone)).date()
            day = post_date(profile, local).isoformat()
            extra.append(Task(account, "fix", day, True, True, 0))
    return tasks + extra


def slot_key(sl: Slot, base: date) -> str:
    """한 번만 실행 기록(ticks.json)의 열쇠. 예: "women pick 2026-09-30 10:47" """
    return f"{sl.account} {sl.task} {base.isoformat()} {sl.local()}"


def due(cron: str, now: datetime | None = None, accounts: list[str] | None = None) -> list[Task]:
    now = now or datetime.now(UTC)
    cron = " ".join(cron.split())
    tasks = []
    for profile, sl in all_slots(accounts):
        base, _ = slot_date(profile, sl, now)
        if sl.cron(profile.schedule.timezone, base) != cron:
            continue
        t = _task(profile, sl, base, now)
        t.keys = [slot_key(sl, base)]
        tasks.append(t)
    return _extras(tasks, now)


# ---------- auto: 지금까지 해야 했는데 아직 안 한 일 (12단계) ----------

LATE_ALERT = 30  # 분. 이보다 늦게 돈 주요 작업은 텔레그램으로 알린다
ALERT_TASKS = {"candidates": "코디 후보 보내기", "publish": "게시", "themes": "다음 주 주제"}
KEEP_DAYS = 14  # ticks.json 에 남기는 기간
AUTO_EARLY = 3  # 분. 외부 스케줄러 시계가 이만큼 빨라도 그 칸으로 본다


def last_chance(profile: Profile, sl: Slot, at: datetime) -> datetime:
    """이 칸을 늦게라도 해도 되는 마지막 시각. 지나면 건너뛰고 (주요 작업이면) 알린다."""
    s = profile.schedule
    now_min = minutes(at.astimezone(ZoneInfo(s.timezone)).strftime("%H:%M"))

    def next_at(hhmm: str) -> datetime:
        return at + timedelta(minutes=(minutes(hhmm) - now_min) % 1440)

    if sl.task == "candidates":
        # 고를 시간이 30분도 안 남으면 보내지 않는다 (고르기를 끄면 게시 1시간 전까지)
        if profile.pick.enabled:
            return next_at(s.pick_deadline) - timedelta(minutes=30)
        return next_at(s.publish_at) - timedelta(minutes=60)
    if sl.task in ("pick", "links", "fix"):
        return next_at(s.publish_at)
    if sl.task == "publish":
        # 마지막 재시도 칸 + 간격까지 (앞 칸을 놓쳐도 뒤 칸이 이어받는다)
        last = (s.publish_retries + 1) * s.publish_retry_every
        return at - timedelta(minutes=sl.offset) + timedelta(minutes=last)
    if sl.task == "comments":
        return at + timedelta(hours=3)
    return at + timedelta(hours=12)  # themes, collect, token


class Ticks:
    """한 번만 실행 기록. daily-results 브랜치의 ticks.json {열쇠: {"status", "at"}}.
    외부 스케줄러와 GitHub 예약이 같은 칸을 둘 다 깨워도 한 번만 한다.
    "_since" 는 기록을 시작한 시각: 그 전 칸은 놓쳤어도 알리지 않는다."""

    def __init__(self, results: Path | None, now: datetime | None = None):
        self.path = results / "ticks.json" if results is not None else None
        self.data: dict[str, dict] = {}
        if self.path is not None and self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        self.since = datetime.fromisoformat(self.data.setdefault("_since", {"at": stamp})["at"])

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def mark(self, keys: list[str], status: str, now: datetime) -> None:
        for k in keys:
            self.data[k] = {
                "status": status,
                "at": now.astimezone(UTC).isoformat(timespec="seconds"),
            }

    def save(self, now: datetime) -> None:
        if self.path is None:
            return
        cutoff = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
        keep = {
            k: v for k, v in self.data.items() if k.startswith("_") or k.split(" ")[2] >= cutoff
        }
        self.path.write_text(
            json.dumps(keep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )


def _already_sent(profile: Profile, t: Task, results: Path | None) -> bool:
    """기록이 없어도 이미 한 일 (ticks.json 을 쓰기 전에 손으로 돌린 후보 등)."""
    if t.task != "candidates" or results is None or not profile.pick.enabled:
        return False
    from tools import pick

    return pick.latest(results, t.account, date.fromisoformat(t.date)) is not None


def due_auto(
    ticks: Ticks,
    results: Path | None,
    now: datetime | None = None,
    accounts: list[str] | None = None,
) -> tuple[list[Task], list[Task]]:
    """(할 일, 놓친 일). 늦게 깨어나도 밀린 칸을 따라잡는다.
    같은 작업의 밀린 칸이 여럿이면 가장 최근 칸 하나로 한 번만 한다 (첫 칸 표시는 이어받음).
    last_chance 가 지난 칸은 건너뛴다."""
    now = now or datetime.now(UTC)
    groups: dict[tuple[str, str, str], list[tuple[Slot, date, datetime]]] = {}
    missed: list[Task] = []
    for profile, sl in all_slots(accounts):
        # 외부 스케줄러는 정시에 깨우므로 몇 분 일찍 깨어난 경우만 그 칸으로 본다
        base, at = slot_date(profile, sl, now, early=AUTO_EARLY)
        key = slot_key(sl, base)
        if key in ticks:
            continue
        if now > last_chance(profile, sl, at):
            # 놓친 칸: 한 번만 알리고 기록 (기록 시작 전 칸은 조용히)
            ticks.mark([key], "missed", now)
            if at >= ticks.since:
                t = _task(profile, sl, base, now)
                t.keys, t.late = [key], int((now - at).total_seconds() // 60)
                t.at = at.strftime("%H:%M")
                missed.append(t)
            continue
        groups.setdefault((sl.account, sl.task, t_day(profile, sl, base)), []).append(
            (sl, base, at)
        )
    tasks = []
    for (account, _task_name, _), items in groups.items():
        profile = load_profile(account)
        items.sort(key=lambda x: x[2])
        sl, base, at = items[-1]
        t = _task(profile, sl, base, now)
        t.first = any(s.first for s, _, _ in items)
        t.keys = [slot_key(s, b) for s, b, _ in items]
        t.late = max(0, int((now - items[0][2]).total_seconds() // 60))
        if _already_sent(profile, t, results):
            ticks.mark(t.keys, "done", now)
            continue
        tasks.append(t)
    return (_extras(tasks, now) if tasks else []), missed


def external_jobs(accounts: list[str] | None = None) -> list[str]:
    """외부 스케줄러에 만들 작업 목록. auto 모드라 칸 시각에만 깨우면 된다.
    같은 분(minute) 묶음이 이어지는 시간끼리 한 작업으로 모은다 (시간대별)."""
    by_tz: dict[str, dict[tuple[int, ...] | None, dict[int, set[int]]]] = {}
    for profile, sl in all_slots(accounts):
        t = (minutes(sl.base) + sl.offset) % 1440
        hours = by_tz.setdefault(profile.schedule.timezone, {}).setdefault(sl.days, {})
        hours.setdefault(t // 60, set()).add(t % 60)
    out = []
    for tz, groups in by_tz.items():
        daily = groups.get(None, {})
        for days, hours in sorted(groups.items(), key=lambda x: (x[0] is not None, x[0] or ())):
            if days is not None:
                # 매일 작업과 같은 시각이면 따로 만들 필요 없음
                hours = {h: m - daily.get(h, set()) for h, m in hours.items()}
                hours = {h: m for h, m in hours.items() if m}
                if not hours:
                    continue
            label = "매일" if days is None else ",".join(WEEKDAYS[d] for d in days)
            run: list[int] = []
            for h in sorted(hours) + [None]:
                if run and (h is None or h != run[-1] + 1 or hours[h] != hours[run[0]]):
                    span = f"{run[0]:02d}시" if len(run) == 1 else f"{run[0]:02d}~{run[-1]:02d}시"
                    mins = ",".join(f"{m:02d}" for m in sorted(hours[run[0]]))
                    out.append(f"{tz}  {label}  {span}  {mins}분")
                    run = []
                if h is not None:
                    run.append(h)
    return out


def t_day(profile: Profile, sl: Slot, base: date) -> str:
    """같은 작업의 칸을 하나로 묶는 날짜 (고르기·링크는 게시 날짜)."""
    if sl.task in ("candidates", "pick", "links", "fix"):
        return post_date(profile, base).isoformat()
    return base.isoformat()


class GitHub:
    """워크플로 실행 요청 (workflow_dispatch). GITHUB_TOKEN 으로 요청한 실행도 돈다."""

    def __init__(self, token: str, repo: str, ref: str, client: httpx.Client | None = None):
        if not token or not repo:
            raise ValueError("GITHUB_TOKEN, GITHUB_REPOSITORY 가 필요합니다")
        self.token, self.repo, self.ref = token, repo, ref or "main"
        self.client = client or httpx.Client(timeout=30)

    @classmethod
    def from_env(cls) -> GitHub:
        env = os.environ.get
        return cls(
            env("GITHUB_TOKEN", ""), env("GITHUB_REPOSITORY", ""), env("GITHUB_REF_NAME", "")
        )

    def dispatch(self, workflow: str, inputs: dict) -> None:
        body = {
            "ref": self.ref,
            "inputs": {
                k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in inputs.items()
            },
        }
        r = self.client.post(
            f"https://api.github.com/repos/{self.repo}/actions/workflows/{workflow}/dispatches",
            json=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if r.status_code >= 300:
            raise RuntimeError(f"{workflow} 실행 요청 실패 ({r.status_code}): {r.text[:200]}")


class QuietTelegram:
    """--dry-run: 답장은 읽고 보내는 메시지는 출력만 한다."""

    def __init__(self, tg):
        self.tg, self.chat_id = tg, tg.chat_id

    def updates(self) -> list[dict]:
        return self.tg.updates()

    def send(self, text: str, **kw) -> int:
        print(f"(시험) 텔레그램: {text}")
        return 0


class DryRun:
    """--dry-run: 실행 요청을 보내지 않고 기록만 한다."""

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    def dispatch(self, workflow: str, inputs: dict) -> None:
        self.sent.append((workflow, inputs))
        print(f"(시험) {workflow} {json.dumps(inputs, ensure_ascii=False)}")


def execute(
    tasks: list[Task],
    results: Path | None,
    gh,
    tg=None,
    now: datetime | None = None,
    dry_run: bool = False,
    ticks: Ticks | None = None,
) -> list[str]:
    """작업을 돌린다. 실행 요청을 보낸 워크플로 목록(로그)을 돌려준다.
    고른 번호 결정은 results 폴더(daily-results)의 picks/picked.json 에 쓰고,
    주제 다시 정하기 요청은 data/<계정>/themes_review.json 에 쓴다 (커밋은 워크플로가 한다)."""
    from tools import pick

    now = now or datetime.now(UTC)
    log: list[str] = []

    def send(workflow: str, **inputs) -> None:
        gh.dispatch(workflow, inputs)
        log.append(f"{workflow} {inputs}")

    for t in tasks:
        profile = load_profile(t.account)
        tag = f"[{t.account}] {t.date}"
        if tg is not None and t.late > LATE_ALERT and t.task in ALERT_TASKS and t.first:
            tg.send(f"{tag} ⏰ {ALERT_TASKS[t.task]} 자동 실행이 {t.late}분 늦게 돌았어요")
        if t.task in ("candidates", "pick") and tg is not None:
            again = pick.themes_again(profile, Store(t.account), tg)
            if again is not None:
                # 같은 주 주제를 다시 정한다.
                # 후보 시각이면 주제를 정한 뒤 themes 워크플로가 후보를 보낸다
                follow = t.date if t.task == "candidates" else ""
                send("themes.yml", account=t.account, date=again.first, then_candidates=follow)
                if t.task == "candidates":
                    continue
        if t.task == "candidates":
            mode = "candidates" if profile.pick.enabled else "full"
            send("daily.yml", account=t.account, date=t.date, mode=mode, sandbox=False)
        elif t.task == "pick":
            if results is None:
                raise ValueError("pick 작업에는 --results (daily-results 폴더)가 필요합니다")
            act = pick.check(
                profile, date.fromisoformat(t.date), results, tg, t.final, t.minutes_left, now
            )
            print(f"{tag} 코디 고르기: {act.action} {act.detail}")
            if act.action == "render":
                send(
                    "daily.yml",
                    account=t.account,
                    date=t.date,
                    mode="render",
                    pick=f"{act.run}/{act.choice}",
                    sandbox=False,
                )
            elif act.action == "again":
                send(
                    "daily.yml",
                    account=t.account,
                    date=t.date,
                    mode="candidates",
                    pick=act.run,
                    sandbox=False,
                )
        elif t.task == "publish":
            send(
                "publish.yml",
                account=t.account,
                date=t.date,
                dry_run=False,
                scheduled=True,
                notify_wait=t.first or t.final,
            )
        elif t.task == "themes":
            send("themes.yml", account=t.account, date=t.date)
        elif t.task == "collect":
            send("collect.yml", account=t.account, commit=True)
        elif t.task == "token":
            send("token.yml", account=t.account)
        elif t.task == "rules":
            send("feedback-rules.yml", account=t.account)
        elif t.task == "comments":
            from tools import comments

            # 오류가 나도 다른 작업을 막지 않는다 (알림은 tools.comments 가 하루 한 번)
            print(comments.check(t.account, tg, now, dry_run))
        elif t.task == "links":
            try:
                print(share.send_after_ok(t.account, t.date, results, tg, dry_run))
            except (RuntimeError, ValueError, httpx.HTTPError) as e:
                print(f"{tag} 상품 링크 보내기 실패: {e}", file=sys.stderr)
        elif t.task == "fix":
            from tools import feedback

            # 오류가 나도 다른 작업을 막지 않는다
            try:
                fix = feedback.check(
                    t.account, date.fromisoformat(t.date), results, tg, now, dry_run
                )
            except (RuntimeError, ValueError, FileNotFoundError, httpx.HTTPError) as e:
                print(f"{tag} 피드백 확인 실패: {e}", file=sys.stderr)
                fix = None
            if fix is not None:
                send(
                    "daily.yml",
                    account=t.account,
                    date=t.date,
                    mode="fix",
                    pick=json.dumps(fix, ensure_ascii=False),
                    sandbox=False,
                )
        elif t.task == "share":
            # 오류가 나도 다른 작업을 막지 않는다
            try:
                print(share.check(t.account, tg, now, dry_run))
            except (RuntimeError, ValueError, httpx.HTTPError) as e:
                print(f"{tag} 공유리워드 확인 실패: {e}", file=sys.stderr)
        if ticks is not None and t.keys:
            ticks.mark(t.keys, "done", now)
    return log


def report_missed(missed: list[Task], tg) -> None:
    """놓친 주요 작업을 알린다 (고르기·링크 칸은 다음 칸이 이어받아 알리지 않음)."""
    for t in missed:
        print(f"[{t.account}] {t.date} 놓친 칸: {t.task} {t.at} ({t.late}분 지남)")
        if tg is not None and t.task in ALERT_TASKS and t.final:
            tg.send(
                f"[{t.account}] {t.date} ⚠ {ALERT_TASKS[t.task]}({t.at}) 자동 실행을 놓쳤어요. "
                "필요하면 Actions 에서 손으로 실행해 주세요."
            )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("sync")
    s.add_argument("--check", action="store_true", help="고치지 않고 다르면 종료 코드 1")
    sub.add_parser("external", help="외부 스케줄러(cron-job.org)에 만들 작업 시각")
    for name in ("due", "run"):
        s = sub.add_parser(name)
        s.add_argument(
            "--cron",
            required=True,
            help='깨어난 cron 줄 (github.event.schedule), 또는 "auto" (지금까지 안 한 일을 찾아서)',
        )
        s.add_argument("--now", help="지금 시각 ISO (시험용)")
        s.add_argument(
            "--results", type=Path, help="daily-results 브랜치를 꺼낸 폴더 (ticks.json, 고르기)"
        )
        if name == "run":
            s.add_argument(
                "--dry-run", action="store_true", help="실행 요청·텔레그램 알림을 보내지 않음"
            )
    args = ap.parse_args(argv)
    try:
        if args.cmd == "list":
            items = all_slots()
            for profile, sl in items:
                cron = sl.cron(profile.schedule.timezone, date.today())
                print(f"{sl.account:8} {sl.task:10} {sl.local():14} cron(UTC) {cron}")
            return 0
        if args.cmd == "sync":
            changed = sync(check_only=args.check)
            if args.check:
                if changed:
                    print("schedule.yml 이 프로필 시간표와 다릅니다: python -m tools.schedule sync")
                return 1 if changed else 0
            print("schedule.yml 을 고쳤습니다" if changed else "이미 맞습니다")
            return 0
        if args.cmd == "external":
            for line in external_jobs():
                print(line)
            return 0
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        results = args.results
        ticks = Ticks(results, now)
        missed: list[Task] = []
        if args.cron.strip() == "auto":
            tasks, missed = due_auto(ticks, results, now)
        else:
            tasks = due(args.cron, now)
        if args.cmd == "due":
            print(json.dumps([asdict(t) for t in tasks + missed], ensure_ascii=False, indent=2))
            return 0
        from tools import notify

        tg = None
        try:
            tg = notify.Telegram.from_env()
        except ValueError:
            print("텔레그램 설정이 없어 고르기·알림 없이 진행합니다", file=sys.stderr)
        gh = DryRun() if args.dry_run else GitHub.from_env()
        if args.dry_run and tg is not None:
            tg = QuietTelegram(tg)
        report_missed(missed, tg)
        if not tasks:
            print(f"cron '{args.cron}' 에 지금 할 작업이 없습니다")
        try:
            for line in execute(tasks, results, gh, tg, now, args.dry_run, ticks):
                print(f"실행 요청: {line}")
        finally:
            if not args.dry_run:
                ticks.save(now)  # 커밋은 워크플로가 한다
        return 0
    except (ValueError, RuntimeError, FileNotFoundError, httpx.HTTPError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
