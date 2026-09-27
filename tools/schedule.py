r"""매일 자동 실행 시간표 (9단계). Claude 는 부르지 않는다.

계정마다 profile.yaml `schedule` 에 적은 시각을 GitHub Actions cron(UTC) 줄로 바꾸고,
schedule 워크플로가 깨어나면 그 cron 줄에 해당하는 작업을 찾아 돌린다.
Actions 는 늦게 깨어날 때가 많아서 지금 시각이 아니라 "어느 cron 줄로 깨어났는지"로 작업을 찾는다.

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

사용법:
  python -m tools.schedule list                    계정별 시간표와 cron 줄
  python -m tools.schedule sync [--check]          schedule.yml 의 cron 줄을 프로필에 맞춤
  python -m tools.schedule due --cron "17 1 * * *" 그 cron 줄에 해당하는 작업 (JSON)
  python -m tools.schedule run --cron "17 1 * * *" --results <daily-results 폴더> [--dry-run]
      작업을 실행한다 (워크플로 실행 요청, 고른 번호 확인).
      환경 변수 GITHUB_TOKEN, GITHUB_REPOSITORY,
      GITHUB_REF_NAME, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, IG_SECRETS(댓글 DM, Secrets JSON)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from core.profile import REPO_ROOT, WEEKDAYS, Profile, load_index, load_profile, minutes
from core.store import Store

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
    if profile.comments.enabled:
        out += [Slot(a, "comments", hhmm) for hhmm in profile.comments.check_at]
    return out


def all_slots(accounts: list[str] | None = None) -> list[tuple[Profile, Slot]]:
    result = []
    for account in accounts or load_index().accounts:
        profile = load_profile(account)
        result += [(profile, sl) for sl in slots(profile)]
    return result


def cron_lines(items: list[tuple[Profile, Slot]], ref: date) -> dict[str, list[str]]:
    """cron 줄 → 설명 목록 (같은 줄에 여러 작업이 겹칠 수 있다)."""
    lines: dict[str, list[str]] = {}
    for profile, sl in items:
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


def slot_date(profile: Profile, sl: Slot, now: datetime) -> tuple[date, datetime]:
    """이 칸이 원래 돌았어야 할 (기준 날짜, 시각). Actions 가 늦게 깨워도 앞 날짜로 가지 않는다."""
    tz = ZoneInfo(profile.schedule.timezone)
    local = now.astimezone(tz)
    for back in range(0, 8):
        d = local.date() - timedelta(days=back)
        at = datetime.combine(d, _hhmm(sl.base), tz) + timedelta(minutes=sl.offset)
        if sl.days is not None and at.weekday() not in sl.days:
            continue
        if at <= local + timedelta(minutes=10):
            return d, at
    raise ValueError(f"{sl.account} {sl.task}: 기준 날짜를 찾지 못했습니다")


def post_date(profile: Profile, base: date) -> date:
    """후보를 보낸 날(base)의 게시 날짜. 게시 시각이 후보 시각보다 앞이면 다음 날 게시."""
    s = profile.schedule
    return base + timedelta(days=0 if minutes(s.candidates_at) < minutes(s.publish_at) else 1)


def due(cron: str, now: datetime | None = None, accounts: list[str] | None = None) -> list[Task]:
    now = now or datetime.now(UTC)
    cron = " ".join(cron.split())
    tasks = []
    for profile, sl in all_slots(accounts):
        tz = profile.schedule.timezone
        base, at = slot_date(profile, sl, now)
        if sl.cron(tz, base) != cron:
            continue
        day = base
        left = 0
        if sl.task in ("candidates", "pick"):
            day = post_date(profile, base)
            deadline = datetime.combine(base, _hhmm(sl.base), ZoneInfo(tz)) + timedelta(
                minutes=(minutes(profile.schedule.pick_deadline) - minutes(sl.base)) % 1440
            )
            left = max(0, int((deadline - now).total_seconds() // 60))
        elif sl.task == "themes":
            day = base + timedelta(days=1)
        tasks.append(Task(sl.account, sl.task, day.isoformat(), sl.first, sl.final, left))
    # 다른 작업으로 깨어난 계정은 댓글도 같이 본다 (따로 깨어나는 것보다 Actions 시간이 덜 듦)
    for account in dict.fromkeys(t.account for t in tasks):
        profile = load_profile(account)
        mine = [t for t in tasks if t.account == account]
        cm = profile.comments
        if cm.enabled and cm.with_other_ticks and all(t.task != "comments" for t in mine):
            tasks.append(Task(account, "comments", mine[0].date, True, True, 0))
    return tasks


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
        elif t.task == "comments":
            from tools import comments

            # 오류가 나도 다른 작업을 막지 않는다 (알림은 tools.comments 가 하루 한 번)
            print(comments.check(t.account, tg, now, dry_run))
    return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("sync")
    s.add_argument("--check", action="store_true", help="고치지 않고 다르면 종료 코드 1")
    for name in ("due", "run"):
        s = sub.add_parser(name)
        s.add_argument("--cron", required=True, help="깨어난 cron 줄 (github.event.schedule)")
        s.add_argument("--now", help="지금 시각 ISO (시험용)")
        if name == "run":
            s.add_argument("--results", type=Path, help="daily-results 브랜치를 꺼낸 폴더")
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
        now = datetime.fromisoformat(args.now) if args.now else None
        if now and now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        tasks = due(args.cron, now)
        if args.cmd == "due":
            print(json.dumps([asdict(t) for t in tasks], ensure_ascii=False, indent=2))
            return 0
        if not tasks:
            print(f"cron '{args.cron}' 에 해당하는 작업이 없습니다")
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
        for line in execute(tasks, args.results, gh, tg, now, args.dry_run):
            print(f"실행 요청: {line}")
        return 0
    except (ValueError, RuntimeError, FileNotFoundError, httpx.HTTPError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
