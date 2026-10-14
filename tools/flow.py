r"""차례 흐름 (2026-10-14 사용자). 시각표 대신 한 게시물씩 차례로 간다.
Claude 는 부르지 않는다.

profile `flow.enabled` 인 계정은 schedule 이 깨어날 때마다(매시, cron=auto) tick 을 한 번 돈다.
사람의 답이 필요한 단계는 마감 없이 답이 올 때까지 기다린다.
한 게시물이 게시되면 다음 주제를 시작한다.

  theme         다음 게시 날짜를 정하고 주제 제안을 요청
                (theme-options 워크플로, Claude plan-themes "제안")
  theme_wait    제안 주제에 답 기다림: "2" / "/theme 2" = 그 주제,
                "/theme 니트 레이어드" = 이 말로 주제를 새로 짜서 바로 진행, "다시" = 새 제안.
                정하면 주제를 themes.json 에 넣고 코디 후보 요청
                (daily mode=candidates, 주제 검색어로 수집)
  pick_wait     코디 고르기(tools.pick)와 장면 사진 1장을 기다림.
                둘 다 오면 만들기 요청 (daily mode=render)
  rendering     미리보기를 기다림
  preview_wait  /fix 면 고쳐 만들기 (daily mode=fix), /cover 면 표지 바꾸기,
                /ok 면 바로 게시 요청 (publish), /skip 이면 이 주제를 버리고 theme 으로
  publishing    게시 기록이 생기면 theme 으로 (다음 주제)

상태는 daily-results 의 flow/<계정>/state.json, 장면 사진은 flow/<계정>/<날짜>/scene.jpg
(사진에 붙인 말은 같은 폴더 scene.txt: 모든 벌에 반영할 요청, 예 "슬릭번")
(커밋은 schedule 워크플로가 한다).
제안 주제는 data/<계정>/theme_options.json (theme-options 워크플로가 커밋).

사용법:
  python -m tools.flow status --account women --results <daily-results 폴더>
  python -m tools.flow options --account women --date 2026-10-16 \
      --file out/theme_options.yaml [--hint …] [--send]
      Claude 가 쓴 제안 주제를 검사해 data/<계정>/theme_options.json 에 저장하고
      텔레그램으로 보낸다
  python -m tools.flow reset --account women --results <폴더>
      상태를 지우고 다음 tick 에 주제부터
  python -m tools.flow use-theme --account women --json '{"theme": {...}}'
      고른 주제를 themes.json 에 (daily candidates 가 부름)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field

from core.models import Theme
from core.profile import WEEKDAYS, Profile, load_profile
from core.store import Store

Stage = Literal["theme", "theme_wait", "pick_wait", "rendering", "preview_wait", "publishing"]
WAIT_ALERT = timedelta(hours=3)  # 워크플로를 요청하고 이만큼 지나도 결과가 없으면 한 번 알림
NO_DEADLINE = 10**6  # tools.pick 에 넘기는 "마감까지 남은 분" (마감 없음)


class FlowState(BaseModel):
    """한 계정의 지금 단계 (daily-results flow/<계정>/state.json)."""

    account: str
    stage: Stage = "theme"
    date: dt.date | None = None  # 지금 만드는 게시물 날짜
    asked_at: int = 0  # 마지막으로 워크플로를 요청한 시각 (유닉스 초)
    hint: str = ""  # 사용자가 직접 적은 주제 (있으면 제안 1개를 바로 씀)
    theme_at: int = 0  # 주제를 정한 시각 (이 뒤에 온 사진을 장면으로 본다)
    pick: str = ""  # 고른 후보 (<결과 폴더>/chosen 또는 /<번호>)
    scene: str = ""  # 장면 사진 (daily-results 기준 경로)
    scene_update: int = 0  # 장면 사진을 받은 텔레그램 update_id
    alerted: bool = False  # 이 단계에서 늦음 알림을 보냈는지
    log: list[str] = []  # 단계 기록 (최근 30줄)

    def note(self, now: datetime, text: str) -> None:
        self.log = [*self.log, f"{now:%m-%d %H:%M} {text}"][-30:]


class ThemeOptions(BaseModel):
    """제안 주제 (data/<계정>/theme_options.json). theme-options 워크플로가 쓰고 보낸다."""

    account: str
    date: dt.date
    options: list[Theme] = Field(min_length=1)
    hint: str = ""
    sent_at: int = 0
    message_ids: list[int] = []


def state_path(results: Path, account: str) -> Path:
    return results / "flow" / account / "state.json"


def load_state(results: Path, account: str) -> FlowState:
    path = state_path(results, account)
    if not path.exists():
        return FlowState(account=account)
    return FlowState.model_validate_json(path.read_text(encoding="utf-8"))


def save_state(results: Path, st: FlowState) -> None:
    path = state_path(results, st.account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(st.model_dump_json(indent=2), encoding="utf-8")


def options_path(store: Store) -> Path:
    return store.dir / "theme_options.json"


def load_options(store: Store) -> ThemeOptions | None:
    path = options_path(store)
    if not path.exists():
        return None
    return ThemeOptions.model_validate_json(path.read_text(encoding="utf-8"))


def next_date(profile: Profile, store: Store, now: datetime) -> date:
    """다음 게시물 날짜: 오늘(계정 시간대)과 마지막 게시 다음 날 중 늦은 날."""
    today = now.astimezone(ZoneInfo(profile.schedule.timezone)).date()
    last = max((p.date for p in store.posts()), default=None)
    return max(today, last + timedelta(days=1)) if last else today


def options_text(profile: Profile, opts: ThemeOptions) -> str:
    d = opts.date
    lines = [f"[{profile.id}] {d:%m-%d}({WEEKDAYS[d.weekday()]}) 게시물 주제 제안", ""]
    for n, t in enumerate(opts.options, 1):
        lines.append(f"{n}. [{t.type or '-'}] {t.title_hint}")
        if t.why:
            lines.append(f"   {t.why}")
    lines += [
        "",
        '• 마음에 드는 번호를 "2" 처럼 보내 주세요 → 그 주제로 코디 후보를 만들어요',
        "• 다른 주제가 좋으면 /theme 니트 레이어드 코디 처럼 적어 주세요 → 그 주제로 바로 진행해요",
        '• 다 별로면 "다시" → 새로 제안해요',
        "답을 받을 때까지 기다려요 (한 시간 안에 확인)",
    ]
    return "\n".join(lines)


def save_options(
    profile: Profile,
    store: Store,
    day: date,
    themes: list[Theme],
    hint: str,
    tg=None,
    sent_at: int | None = None,
) -> ThemeOptions:
    """Claude 가 쓴 제안 주제를 저장하고(보낼 수 있으면) 텔레그램으로 보낸다."""
    opts = ThemeOptions(
        account=profile.id,
        date=day,
        options=[t.model_copy(update={"date": day}) for t in themes],
        hint=hint,
        sent_at=sent_at or int(time.time()),
    )
    if tg is not None and not hint:
        opts.message_ids = [tg.send(options_text(profile, opts))]
    store.dir.mkdir(parents=True, exist_ok=True)
    options_path(store).write_text(opts.model_dump_json(indent=2), encoding="utf-8")
    return opts


def choose_theme(store: Store, theme: Theme) -> None:
    """정한 주제를 themes.json 에 넣는다 (같은 날짜 주제는 바꿈). 코디 후보가 이 주제를 쓴다."""
    themes = [t for t in store.themes() if t.date != theme.date]
    store.save_themes(sorted([*themes, theme], key=lambda t: t.date))


def scene_photo(updates: list[dict], chat_id: str, since: int, after_update: int) -> tuple | None:
    """since 뒤 등록된 채팅에서 온 마지막 사진 (update_id, file_id, 사진에 붙인 말).
    after_update 이하는 이미 받은 것."""
    from tools import notify

    found = None
    for u in updates:
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        if msg.get("date", 0) < since or u["update_id"] <= after_update:
            continue
        fid = notify.photo_file_id(msg)
        if fid:
            found = (u["update_id"], fid, (msg.get("caption") or "").strip())
    return found


def tick(
    account: str,
    results: Path,
    tg,
    send,
    now: datetime | None = None,
    dry_run: bool = False,
    store: Store | None = None,
) -> list[str]:
    """한 번 확인하고 할 일을 한다. send(workflow, **inputs) 로 워크플로를 요청한다.
    단계가 바뀌면 같은 tick 에서 다음 단계도 이어서 본다 (예: 게시 확인 → 다음 주제 요청)."""
    profile = load_profile(account)
    store = store or Store(account)
    now = now or datetime.now(UTC)
    st = load_state(results, account)
    done: list[str] = []
    try:
        for _ in range(4):
            before = st.stage
            line = _step(profile, store, st, results, tg, send, now, dry_run)
            if line:
                done.append(line)
                st.note(now, line)
            if st.stage == before:
                break
            st.alerted = False
    finally:
        # 도중에 실패해도 이미 요청한 워크플로가 다시 요청되지 않게 지금까지 바뀐 단계를 남긴다
        if not dry_run:
            save_state(results, st)
    return done


def _late(st: FlowState, now: datetime, tg, text: str, after: timedelta = WAIT_ALERT) -> None:
    if st.alerted or not st.asked_at or tg is None:
        return
    if now.timestamp() - st.asked_at >= after.total_seconds():
        tg.send(text)
        st.alerted = True


def _step(
    profile: Profile,
    store: Store,
    st: FlowState,
    results: Path,
    tg,
    send,
    now: datetime,
    dry_run: bool,
) -> str:
    from tools import feedback, notify, pick, publish
    from tools import schedule as sched

    acc = profile.id
    ts = int(now.timestamp())
    tag = f"[{acc}] {st.date:%m-%d}" if st.date else f"[{acc}]"

    if st.stage == "theme":
        st.date = next_date(profile, store, now)
        st.pick, st.scene, st.scene_update, st.theme_at = "", "", 0, 0
        send("theme-options.yml", account=acc, date=st.date.isoformat(), hint=st.hint)
        st.asked_at, st.stage = ts, "theme_wait"
        return f"주제 제안 요청 ({st.date})"

    if st.stage == "theme_wait":
        opts = load_options(store)
        if opts is None or opts.date != st.date or opts.sent_at < st.asked_at - 60:
            _late(
                st,
                now,
                tg,
                f"{tag} 주제 제안이 아직 오지 않았어요 (주제 정하기 실행을 확인해 주세요)",
            )
            return ""
        chosen = None
        if opts.hint:
            chosen = opts.options[0]  # 사용자가 적은 주제로 짠 것: 바로 진행
        elif tg is not None:
            cmds = notify.commands(
                tg.updates(), tg.chat_id, opts.sent_at, acc, st.date,
                {"theme", "pick", "again"}, opts.message_ids,
            )  # fmt: skip
            for _, cmd in reversed(cmds):
                arg = " ".join(cmd.args).strip()
                if cmd.name == "again" or (cmd.name == "theme" and arg in ("다시", "again")):
                    st.hint = ""
                    send("theme-options.yml", account=acc, date=st.date.isoformat(), hint="")
                    st.asked_at = ts
                    tg.send(f"{tag} 주제를 새로 제안할게요")
                    return "주제 다시 제안 요청"
                n = arg.rstrip("번").strip()
                if n.isdigit() and 1 <= int(n) <= len(opts.options):
                    chosen = opts.options[int(n) - 1]
                    break
                if cmd.name == "theme" and arg and not n.isdigit():
                    st.hint = arg
                    send("theme-options.yml", account=acc, date=st.date.isoformat(), hint=arg)
                    st.asked_at = ts
                    tg.send(f"{tag} '{arg}' 주제로 짜고 있어요. 다 되면 코디 후보를 보낼게요")
                    return f"사용자 주제로 다시 요청: {arg}"
        if chosen is None:
            return ""
        chosen = chosen.model_copy(update={"date": st.date})
        if not dry_run:
            choose_theme(store, chosen)
        # daily 는 요청한 때의 커밋을 꺼내서 방금 고친 themes.json 을 못 본다: 주제를 같이 넘긴다
        theme = json.dumps({"theme": chosen.model_dump(mode="json")}, ensure_ascii=False)
        send(
            "daily.yml", account=acc, date=st.date.isoformat(), mode="candidates", pick=theme,
            sandbox=False, collect=True,
        )  # fmt: skip
        st.hint, st.theme_at, st.asked_at, st.stage = "", ts, ts, "pick_wait"
        if tg is not None and not opts.hint:
            tg.send(
                f"{tag} '{chosen.title_hint}' 주제로 할게요. 코디 후보를 만들고 있어요.\n"
                "이 주제에 맞는 장면 사진 1장도 이 채팅에 보내 주세요 "
                "(스트리트, 스튜디오, 거울 셀카 등)"
            )
        return f"주제 정함: {chosen.title_hint}"

    assert st.date is not None
    day = st.date

    if st.stage == "pick_wait":
        lines = []
        if tg is not None:
            got = scene_photo(tg.updates(), tg.chat_id, st.theme_at, st.scene_update)
            if got is not None:
                uid, fid, note = got
                rel = f"flow/{acc}/{day}/scene.jpg"
                if not dry_run:
                    tg.download(fid, results / rel)
                    # 사진에 붙인 말(예: "슬릭번")은 모든 벌에 반영할 요청 (daily-feed 가 읽음)
                    note_path = results / rel.replace(".jpg", ".txt")
                    if note:
                        note_path.write_text(note + "\n", encoding="utf-8")
                    else:
                        note_path.unlink(missing_ok=True)
                st.scene, st.scene_update = rel, uid
                more = "바로 만들게요" if st.pick else "코디를 골라 주시면 만들게요"
                tg.send(f"{tag} 장면 사진을 받았어요. {more}")
                lines.append("장면 사진 받음")
        if not st.pick:
            hold = "" if st.scene else "장면 사진 1장을 보내 주시면 바로 만들게요."
            act = pick.check(
                profile,
                day,
                results,
                tg,
                False,
                NO_DEADLINE,
                now,
                hold=hold,
                since=st.theme_at - 60,
            )
            if act.action == "render":
                st.pick = f"{act.run}/{act.choice}"
                lines.append(f"코디 고름 ({st.pick})")
            elif act.action == "again":
                send("daily.yml", account=acc, date=day.isoformat(), mode="candidates",
                     pick=act.run, sandbox=False)  # fmt: skip
                lines.append("새 후보 요청")
            elif act.action == "edit":
                spec = {"run": act.run, "fixes": act.fixes}
                send("daily.yml", account=acc, date=day.isoformat(), mode="edit",
                     pick=json.dumps(spec, ensure_ascii=False), sandbox=False)  # fmt: skip
                lines.append("후보 고치기 요청")
            elif act.action == "skip":
                st.stage = "theme"
                return "이 주제 건너뜀 (/skip)"
        if st.pick and st.scene:
            send("daily.yml", account=acc, date=day.isoformat(), mode="render", pick=st.pick,
                 sandbox=False)  # fmt: skip
            st.asked_at, st.stage = ts, "rendering"
            lines.append("만들기 요청")
        return ", ".join(lines)

    if st.stage == "rendering":
        out = feedback.latest_preview(results, acc, day)
        rec = notify.load_record(out) if out is not None else None
        if rec is None or rec.sent_at < st.asked_at:
            _late(
                st,
                now,
                tg,
                f"{tag} 게시물 미리보기가 아직 오지 않았어요 (만들기 실행을 확인해 주세요)",
            )
            return ""
        st.stage = "preview_wait"
        return f"미리보기 받음 ({out.name})"

    if st.stage == "preview_wait":
        out = feedback.latest_preview(results, acc, day)
        if out is None or tg is None:
            return ""
        rec = notify.load_record(out)
        assert rec is not None
        print(sched.cover_change(acc, day.isoformat(), results, tg, dry_run))
        fix = feedback.check(acc, day, results, tg, now, dry_run, store)
        if fix is not None:
            send("daily.yml", account=acc, date=day.isoformat(), mode="fix",
                 pick=json.dumps(fix, ensure_ascii=False), sandbox=False)  # fmt: skip
            st.asked_at, st.stage = ts, "rendering"
            return f"고쳐 만들기 요청 ({fix.get('round')}회)"
        updates = tg.updates()
        decided = notify.approval_now(out, rec, profile, updates, tg.chat_id)
        if decided and not dry_run:
            notify.remember(out, decided)
        if decided == "skip":
            st.stage = "theme"
            tg.send(f"{tag} 이 게시물은 건너뛰고 다음 주제를 제안할게요")
            return "미리보기 건너뜀 (/skip)"
        if decided != "ok":
            return ""
        if feedback.waiting(updates, rec, tg.chat_id, out, profile, store):
            return ""  # /fix 로 고치는 중: 고친 미리보기에 /ok 가 오면 그때
        send("publish.yml", account=acc, date=day.isoformat(), run=out.name, dry_run=False,
             force=True, scheduled=True)  # fmt: skip
        st.asked_at, st.stage = ts, "publishing"
        tg.send(f"{tag} /ok 확인했어요. 지금 게시할게요")
        return f"게시 요청 ({out.name})"

    if st.stage == "publishing":
        posted = any(p.date == day for p in store.posts())
        try:
            posted = posted or (publish.find_run(results, acc, day) / "published.json").exists()
        except FileNotFoundError:
            pass
        if not posted:
            mins = timedelta(minutes=profile.flow.publish_alert_minutes)
            _late(st, now, tg, f"{tag} 게시 기록이 아직 없어요 (게시 실행을 확인해 주세요)", mins)
            return ""
        st.stage = "theme"
        return "게시 확인"
    return ""


def status(results: Path, account: str) -> str:
    st = load_state(results, account)
    lines = [f"[{account}] 단계 {st.stage}, 날짜 {st.date or '-'}"]
    if st.pick:
        lines.append(f"고른 후보 {st.pick}")
    if st.scene:
        lines.append(f"장면 사진 {st.scene}")
    lines += st.log[-10:]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--account", default="women")
    s.add_argument("--results", type=Path, required=True)
    s = sub.add_parser("reset")
    s.add_argument("--account", default="women")
    s.add_argument("--results", type=Path, required=True)
    s = sub.add_parser(
        "use-theme", help="고른 주제를 themes.json 에 넣음 (daily candidates 가 부름)"
    )
    s.add_argument("--account", default="women")
    s.add_argument("--json", required=True, help='{"theme": {...}}')
    s = sub.add_parser("options")
    s.add_argument("--account", default="women")
    s.add_argument("--date", required=True)
    s.add_argument(
        "--file", type=Path, required=True, help="Claude 가 쓴 제안 주제 YAML (Theme 목록)"
    )
    s.add_argument("--hint", default="")
    s.add_argument("--send", action="store_true", help="텔레그램으로 보냄")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "status":
            print(status(args.results, args.account))
        elif args.cmd == "use-theme":
            theme = Theme.model_validate(json.loads(args.json)["theme"])
            choose_theme(Store(args.account), theme)
            print(f"{theme.date} 주제: {theme.title_hint}")
        elif args.cmd == "reset":
            save_state(args.results, FlowState(account=args.account))
            print(f"[{args.account}] 차례 흐름을 처음(주제)부터 다시 해요")
        else:
            profile = load_profile(args.account)
            day = date.fromisoformat(args.date)
            raw = yaml.safe_load(args.file.read_text(encoding="utf-8")) or []
            themes = [Theme.model_validate({**t, "date": day}) for t in raw]
            if not themes:
                raise ValueError("제안 주제가 없습니다")
            limit = 1 if args.hint else profile.flow.theme_options
            tg = None
            if args.send:
                from tools import notify

                tg = notify.Telegram.from_env()
            opts = save_options(profile, Store(args.account), day, themes[:limit], args.hint, tg)
            for n, t in enumerate(opts.options, 1):
                print(f"{n}. {t.title_hint}")
        return 0
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
