"""오래 걸리는 작업의 진행 현황을 한 시간마다 텔레그램으로 (2026-10-22 사용자: "1시간 이상 작업이
진행될 때는 뭐하고 있는지, 얼마나 더 걸릴지 한시간마다 텔레그램으로").

    python -m tools.progress note "참고 코디 3/11벌 맞추는 중" --done 3 --total 11
        Claude 나 스크립트가 지금 하는 일을 남긴다 (PROGRESS_FILE, 기본 out/progress.json).
    python -m tools.progress watch --label "코디 후보 만들기" --limit 150 &
        워크플로 job 첫머리에 백그라운드로 띄운다. --every(기본 60분)마다 지금 단계(GitHub job 의
        진행 중 단계 이름)와 note 내용, 남은 예상 시간을 보낸다.
        그 전에 끝나면 아무것도 보내지 않는다.
        job 이 끝나면 러너가 남은 프로세스를 정리한다.

남은 시간: note 에 done/total 이 있으면 그 단계가 시작된 뒤 걸린 시간으로 비율 계산.
없으면 지난번 같은 단계 실행 시간을 모르니 시간 제한까지 남은 시간만 알린다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

DEFAULT_FILE = "out/progress.json"


def progress_file() -> Path:
    return Path(os.environ.get("PROGRESS_FILE") or DEFAULT_FILE)


def note(text: str, done: int | None = None, total: int | None = None, path: Path | None = None):
    path = path or progress_file()
    old = read(path)
    now = time.time()
    # 같은 단계(total 이 같음)가 이어지면 시작 시각을 그대로 둔다 (비율 계산용)
    started = old.get("started", now) if old and old.get("total") == total and total else now
    data = {"text": text, "done": done, "total": total, "at": now, "started": started}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")


def read(path: Path | None = None) -> dict:
    path = path or progress_file()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _minutes(sec: float) -> str:
    m = max(1, round(sec / 60))
    if m < 60:
        return f"{m}분"
    return f"{m // 60}시간 {m % 60}분" if m % 60 else f"{m // 60}시간"


@dataclass
class Snapshot:
    elapsed: float  # job 시작부터 (초)
    step: str = ""  # GitHub job 의 진행 중 단계 이름
    info: dict | None = None  # note 내용
    limit: float | None = None  # job 시간 제한 (초)


def eta(info: dict | None, now: float) -> float | None:
    """note 의 done/total 로 남은 시간(초). 모르면 None."""
    if not info:
        return None
    done, total = info.get("done"), info.get("total")
    if not total or not done or done <= 0 or done > total:
        return None
    spent = now - info.get("started", now)
    if spent <= 0:
        return None
    return spent * (total - done) / done


def message(label: str, s: Snapshot, now: float | None = None) -> str:
    now = now or time.time()
    lines = [f"⏳ {label} {_minutes(s.elapsed)}째 진행 중"]
    if s.step:
        lines.append(f"지금 단계: {s.step}")
    info = s.info or {}
    if info.get("text") and now - info.get("at", 0) < 3 * 3600:
        part = f" ({info['done']}/{info['total']})" if info.get("total") else ""
        lines.append(f"하는 일: {info['text']}{part} · {_minutes(now - info['at'])} 전 기록")
    left = eta(info, now)
    if left is not None:
        lines.append(f"예상: 이 단계 약 {_minutes(left)} 더 (지금까지 속도로 추정)")
    elif s.limit:
        left_max = _minutes(s.limit - s.elapsed)
        lines.append(f"예상: 정확히 알 수 없어요. 늦어도 {left_max} 뒤 시간 제한에 끝나요")
    else:
        lines.append("예상: 정확히 알 수 없어요")
    return "\n".join(lines)


def current_step(client: httpx.Client | None = None) -> str:
    """GitHub API 로 이 job 의 진행 중 단계 이름. 권한(actions: read)이 없거나 실패하면 ""."""
    repo, run = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not repo or not run or not token:
        return ""
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run}/attempts/{attempt}/jobs"
    try:
        c = client or httpx.Client(timeout=30)
        r = c.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
    except (httpx.HTTPError, ValueError):
        return ""
    job_name = os.environ.get("PROGRESS_JOB")
    for job in jobs:
        if job.get("status") != "in_progress" or (job_name and job.get("name") != job_name):
            continue
        for step in job.get("steps", []):
            if step.get("status") == "in_progress":
                return step.get("name", "")
    return ""


def watch(label: str, every: float, limit: float | None, tg=None, sleep=time.sleep) -> None:
    from tools import notify

    start = time.time()
    tg = tg or notify.Telegram.from_env()
    while True:
        sleep(every)
        s = Snapshot(time.time() - start, current_step(), read(), limit)
        try:
            tg.send(message(label, s))
        except (RuntimeError, httpx.HTTPError) as e:
            print(f"진행 알림 실패: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools.progress")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("note", help="지금 하는 일 남기기")
    n.add_argument("text")
    n.add_argument("--done", type=int)
    n.add_argument("--total", type=int)
    w = sub.add_parser("watch", help="한 시간마다 진행 현황 보내기 (백그라운드)")
    w.add_argument("--label", required=True, help="작업 이름 (예: [women] 코디 후보 만들기)")
    w.add_argument("--every", type=float, default=60, help="몇 분마다 (기본 60)")
    w.add_argument("--limit", type=float, help="job 시간 제한(분)")
    args = ap.parse_args(argv)
    if args.cmd == "note":
        note(args.text, args.done, args.total)
        print("기록함")
        return 0
    from tools import notify

    try:
        tg = notify.Telegram.from_env()
    except ValueError:
        print("텔레그램 설정이 없어 진행 알림을 보내지 않습니다", file=sys.stderr)
        return 0
    watch(args.label, args.every * 60, args.limit * 60 if args.limit else None, tg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
