r"""텔레그램 미리보기·승인·알림 (8단계). 외부로 보내는 도구라 Claude 허용 목록에 없다.

- preview: 캐러셀 슬라이드, 캡션, 검수 결과를 보내고 결과 폴더에 telegram.json 을 남긴다.
  승인 버튼(/ok, /skip)은 텔레그램 입력창 아래 키보드로 나온다. 누르면 그 글이 채팅에 남는다.
- decision: 미리보기 뒤에 온 답장에서 /ok 또는 /skip 을 찾는다 (게시 job 이 부름).
  고칠 점 /fix 는 tools.feedback 이 읽는다 (15단계).
- send: 알림 한 줄.

봇 서버 없이 동작한다. 답장은 게시 job 이 돌 때 getUpdates 로 읽는다 (텔레그램이 24시간 보관).
등록된 채팅 ID 에서 온 글만 본다.

사용법 (환경 변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요):
  python -m tools.notify preview out/2026-09-27 --run 2026-09-27-run12 [--sandbox]
  python -m tools.notify decision out/2026-09-27
  python -m tools.notify send "메시지"
  python -m tools.notify chats   봇 토큰 확인, 봇에게 말을 건 채팅 ID 찾기 (CHAT_ID 없어도 됨)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import httpx
import yaml

from core.models import Decision, Manifest, PreviewRecord
from core.profile import REPO_ROOT, Profile, load_profile, minutes

CONFIG_PATH = REPO_ROOT / "config" / "publish.yaml"
API = "https://api.telegram.org"
MAX_TEXT = 4096
MAX_PHOTO_CAPTION = 1024
SEND_METHODS = {"sendMessage", "sendPhoto", "sendMediaGroup"}
APPROVAL = "approval.json"  # 결과 폴더에 남기는 /ok·/skip 기록 (텔레그램은 답장을 24시간만 보관)


def publish_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def quiet_now(cfg: dict | None = None, now: datetime | None = None) -> bool:
    """config/publish.yaml telegram.quiet 시간대(예: 23:00~07:00)면 알림 소리 없이 보낸다."""
    tcfg = (publish_config() if cfg is None else cfg).get("telegram") or {}
    quiet = tcfg.get("quiet")
    if not quiet:
        return False
    start, end = (minutes(t) for t in quiet)
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(tcfg.get("timezone", "Asia/Seoul")))
    t = local.hour * 60 + local.minute
    return start <= t < end if start < end else t >= start or t < end


UPDATES_LIMIT = 100  # getUpdates 한 번에 받는 최대 개수


class Telegram:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None):
        if not token or not chat_id:
            raise ValueError("텔레그램 봇 토큰과 채팅 ID 가 필요합니다 (Secrets)")
        self.base = f"{API}/bot{token}"
        self.chat_id = str(chat_id)
        self.client = client or httpx.Client(timeout=60)

    @classmethod
    def from_env(cls, client: httpx.Client | None = None) -> Telegram:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        return cls(token, os.environ.get("TELEGRAM_CHAT_ID", ""), client)

    def call(self, method: str, files: dict | None = None, **params) -> dict | list:
        if method in SEND_METHODS and "disable_notification" not in params and quiet_now():
            params["disable_notification"] = True
        data = {
            k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            for k, v in params.items()
            if v is not None
        }
        r = self.client.post(f"{self.base}/{method}", data=data, files=files)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not body.get("ok"):
            # 주소에 봇 토큰이 들어 있어서 응답 설명만 남긴다
            raise RuntimeError(
                f"텔레그램 {method} 실패 ({r.status_code}): {body.get('description')}"
            )
        return body["result"]

    def send(self, text: str, **kw) -> int:
        msg = self.call("sendMessage", chat_id=self.chat_id, text=text[:MAX_TEXT], **kw)
        return msg["message_id"]

    def send_photos(self, paths: list[Path], caption: str = "") -> list[int]:
        if len(paths) == 1:
            with paths[0].open("rb") as f:
                msg = self.call(
                    "sendPhoto",
                    files={"photo": f},
                    chat_id=self.chat_id,
                    caption=caption[:MAX_PHOTO_CAPTION],
                )
            return [msg["message_id"]]
        ids: list[int] = []
        for start in range(0, len(paths), 10):  # 미디어 묶음은 10장까지
            chunk = paths[start : start + 10]
            media = [{"type": "photo", "media": f"attach://p{i}"} for i in range(len(chunk))]
            if start == 0 and caption:
                media[0]["caption"] = caption[:MAX_PHOTO_CAPTION]
            handles = [p.open("rb") for p in chunk]
            try:
                files = {f"p{i}": (f"p{i}.jpg", h, "image/jpeg") for i, h in enumerate(handles)}
                msgs = self.call("sendMediaGroup", files=files, chat_id=self.chat_id, media=media)
            finally:
                for h in handles:
                    h.close()
            ids += [m["message_id"] for m in msgs]
        return ids

    def updates(self) -> list[dict]:
        # offset 을 넘기지 않아 읽은 답장을 지우지 않는다 (다른 계정 게시 job 도 읽어야 해서)
        old = self.call("getUpdates", allowed_updates=["message"], limit=UPDATES_LIMIT)
        if len(old) < UPDATES_LIMIT:
            return old
        # 쌓인 글이 100개를 넘으면 오래된 100개만 와서 새 답장(/ok, /fix)이 안 보인다.
        # 끝에서 100개를 다시 받는다 (텔레그램이 그보다 오래된 글은 지움)
        new = self.call(
            "getUpdates", allowed_updates=["message"], limit=UPDATES_LIMIT, offset=-UPDATES_LIMIT
        )
        seen = {u["update_id"] for u in new}
        return [u for u in old if u["update_id"] not in seen] + new


# ---------- 미리보기 ----------


def load_manifest(out: Path) -> Manifest:
    return Manifest.model_validate_json((out / "manifest.json").read_text(encoding="utf-8"))


def slide_paths(manifest: Manifest, out: Path) -> list[Path]:
    """manifest.images(out/<날짜>/slides/…)를 결과 폴더 안 파일로 바꾼다.
    daily-results 브랜치로 옮긴 폴더에서도 찾을 수 있게 날짜 폴더 뒤 경로를 쓴다."""
    key = str(manifest.date)
    paths = []
    for img in manifest.images:
        parts = Path(img).parts
        tail = Path(*parts[parts.index(key) + 1 :]) if key in parts else Path(Path(img).name)
        for cand in (out / tail, REPO_ROOT / img):
            if cand.exists():
                paths.append(cand)
                break
        else:
            raise FileNotFoundError(f"슬라이드가 없습니다: {img}")
    return paths


def full_caption(manifest: Manifest) -> str:
    return manifest.caption.rstrip() + "\n\n" + " ".join(manifest.hashtags)


def human_reasons(manifest: Manifest) -> list[str]:
    r = manifest.review
    if r is None or r.status == "not_reviewed":
        return ["이미지 검수를 하지 않았어요"]
    reasons = []
    if r.status != "passed":
        chosen = next((c for c in r.candidates if c.id == r.chosen), None)
        if chosen and chosen.score:
            note = f" ({chosen.score.notes})" if chosen.score.notes else ""
            reasons.append(f"표지 점수 {chosen.score.total}/10{note}")
        else:
            reasons.append("표지 후보가 기준을 넘지 못했어요")
    reasons += [f"{i.slide} {i.kind}: {i.note}" for i in r.slide_issues]
    if not reasons and manifest.needs_human:
        reasons.append(r.summary or "검수에서 사람 확인으로 표시했어요")
    return reasons


def command_label(profile: Profile, day: date) -> str:
    return f"{profile.id} {day:%m-%d}"


def control_text(
    manifest: Manifest,
    profile: Profile,
    sandbox: bool,
    fix: dict | None = None,
    changes: list[str] | None = None,
) -> str:
    day = manifest.date
    lines = [f"[{profile.id}] {day} {profile.schedule.publish_at} 게시 예정"]
    lines.append("제목: " + " / ".join(manifest.title_lines))
    if fix:
        lines.append("")
        lines.append(f"고친 점 ({fix.get('round', 1)}/{profile.feedback.max_fixes}회)")
        from tools.feedback import label

        lines += [f"- {c}" for c in changes or []] or [
            f"- {label(f)}: {f['text']}" for f in fix.get("fixes", [])
        ]
    if manifest.review and manifest.review.status != "not_reviewed":
        lines.append(f"검수: 표지 {manifest.review_score:g}/10")
    if manifest.needs_human:
        lines.append("")
        lines.append("⚠ 사람 확인 필요")
        lines += [f"- {r}" for r in human_reasons(manifest)]
    lines.append("")
    if sandbox:
        lines.append("샌드박스(워터마크) 결과라 게시하지 않아요. 흐름 확인용이에요.")
        return "\n".join(lines)
    label = command_label(profile, day)
    if profile.approval == "required" or manifest.needs_human:
        lines.append(f"게시하려면 /ok {label}, 건너뛰려면 /skip {label}")
        dl = ok_deadline(profile, day)
        if dl is not None:
            lines.append(f"승인 마감 {dl:%m/%d %H:%M}. 그때까지 /ok 가 없으면 게시하지 않아요")
        lines.append("(아래 버튼을 누르거나 이 메시지에 /ok 로 답장해도 돼요)")
        from tools import share

        if share.enabled(profile):
            lines.append("/ok 를 확인하면 공유리워드 링크를 만들 상품 링크를 보내 드려요")
        elif share.sends_list(profile):
            lines.append("/ok 를 확인하면 상품 링크를 보내 드려요")
    else:
        lines.append(
            f"/skip {label} 을 보내지 않으면 {profile.schedule.publish_at} 에 자동 게시해요"
        )
    fb = profile.feedback
    left = fb.max_fixes - (fix or {}).get("round", 0)
    if fb.enabled and left > 0:
        lines.append(
            f"고칠 점은 /fix 1 신발 더 크게 처럼 보내 주세요 (1 = 사진 순서, 캡션은 /fix 캡션 …, "
            f"사진에 답장해도 돼요. 남은 횟수 {left}번)"
        )
    return "\n".join(lines)


def preview(
    out: Path, run: str, tg: Telegram, sandbox: bool = False, profile: Profile | None = None
) -> PreviewRecord:
    from tools import feedback

    manifest = load_manifest(out)
    profile = profile or load_profile(manifest.account)
    slides = slide_paths(manifest, out)
    fix = feedback.load_fix(out)
    kind = (
        f"고친 미리보기 {fix.get('round', 1)}/{profile.feedback.max_fixes}회" if fix else "미리보기"
    )
    head = f"[{profile.id}] {manifest.date} {kind} · {len(slides)}장"
    ids = tg.send_photos(slides, head)
    ids.append(tg.send(full_caption(manifest)))
    markup = None
    if not sandbox:
        label = command_label(profile, manifest.date)
        markup = {
            "keyboard": [[{"text": f"/ok {label}"}, {"text": f"/skip {label}"}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }
    text = control_text(manifest, profile, sandbox, fix, feedback.fix_changes(out))
    ids.append(tg.send(text, reply_markup=markup))
    record = PreviewRecord(
        account=manifest.account,
        date=manifest.date,
        run=run,
        sent_at=int(time.time()),
        message_ids=ids,
        sandbox=sandbox,
        needs_human=manifest.needs_human,
    )
    (out / "telegram.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return record


# ---------- 승인 읽기 ----------

CMD = re.compile(r"^/([a-z]+)(?:@\w+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
DAY = re.compile(r"^(?:(\d{4})-)?(\d{1,2})-(\d{1,2})$")


class Command(NamedTuple):
    name: str  # 소문자 명령 (ok, skip, pick, again, themes …)
    args: list[str]  # 계정·날짜를 뺀 나머지 (예: /pick 3 → ["3"])
    labeled: bool  # 계정이나 날짜를 적었는지


def parse(text: str, account: str, day: date | None) -> Command | None:
    """ "/ok", "/pick 3 women 09-28", "/skip 2026-09-27" 같은 글을 읽는다.
    계정·날짜를 적었으면 둘 다 이 계정·날짜와 맞아야 하고(아니면 None),
    안 적었으면 가장 최근 것으로 본다.
    day 가 None 이면 날짜를 적은 글은 받지 않는다."""
    # 버튼 대신 직접 친 "2번", "2", "다시" 도 코디 고르기 명령으로 읽는다
    plain = text.strip()
    if re.fullmatch(r"\d{1,2}\s*번?", plain):
        return Command("pick", [re.sub(r"\D", "", plain)], False)
    if plain in ("다시", "다시 보내줘"):
        return Command("again", [], False)
    m = CMD.match(plain)
    if not m:
        return None
    args: list[str] = []
    labeled = False
    for token in (m.group(2) or "").split():
        d = DAY.match(token)
        if d:
            if day is None:
                return None
            year = int(d.group(1) or day.year)
            if (year, int(d.group(2)), int(d.group(3))) != (day.year, day.month, day.day):
                return None
            labeled = True
        elif token.lower() == account.lower():
            labeled = True
        else:
            args.append(token)
    return Command(m.group(1).lower(), args, labeled)


def commands(
    updates: list[dict],
    chat_id: str,
    since: int,
    account: str,
    day: date | None,
    names: set[str],
    own_ids: list[int],
    until: int | None = None,
) -> list[tuple[int, Command]]:
    """since 뒤(until 이 있으면 그때까지) 등록된 채팅에서 온, 이 계정·날짜에 대한 명령들
    (update_id 순).
    계정·날짜 없이 봇의 다른 메시지(다른 계정·다른 날)에 답장한 글은 그쪽 것이라 뺀다."""
    found = []
    for u in updates:
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        if msg.get("date", 0) < since:
            continue
        if until is not None and msg.get("date", 0) > until:
            continue
        cmd = parse(msg.get("text") or "", account, day)
        if cmd is None or cmd.name not in names:
            continue
        reply_to = (msg.get("reply_to_message") or {}).get("message_id")
        if not cmd.labeled and reply_to and reply_to not in own_ids:
            continue
        found.append((u["update_id"], cmd))
    return sorted(found, key=lambda x: x[0])


def parse_command(text: str, record: PreviewRecord) -> Decision | None:
    """미리보기에 대한 /ok 또는 /skip (다른 글이 붙어 있으면 None)."""
    cmd = parse(text, record.account, record.date)
    if cmd and cmd.name in ("ok", "skip") and not cmd.args:
        return cmd.name  # type: ignore[return-value]
    return None


def find_decision(
    updates: list[dict], record: PreviewRecord, chat_id: str, until: int | None = None
) -> Decision | None:
    """미리보기 뒤(until 이 있으면 그때까지) 등록된 채팅에서 온 마지막 /ok 또는 /skip."""
    found = [
        cmd
        for _, cmd in commands(
            updates,
            chat_id,
            record.sent_at,
            record.account,
            record.date,
            {"ok", "skip"},
            record.message_ids,
            until,
        )
        if not cmd.args
    ]
    return found[-1].name if found else None  # type: ignore[return-value]


def ok_deadline(profile: Profile, day: date) -> datetime | None:
    """게시 날짜(day)의 승인 마감 시각 (schedule.ok_deadline). 게시 시각보다 늦은 시각이면 전날."""
    s = profile.schedule
    if not s.ok_deadline:
        return None
    at = datetime.combine(day, datetime.min.time(), ZoneInfo(s.timezone)) + timedelta(
        minutes=minutes(s.ok_deadline)
    )
    if minutes(s.ok_deadline) > minutes(s.publish_at):
        at -= timedelta(days=1)
    return at


def remembered(out: Path) -> Decision | None:
    """매시 확인 때 남긴 /ok·/skip (텔레그램에서 답장이 사라져도 쓴다)."""
    path = out / APPROVAL
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("decision")


def approval_now(
    out: Path, record: PreviewRecord, profile: Profile, updates: list[dict], chat_id: str
) -> Decision | None:
    """승인 마감까지 온 마지막 /ok·/skip. 텔레그램에 없으면 남겨 둔 기록."""
    dl = ok_deadline(profile, record.date)
    decided = find_decision(updates, record, chat_id, int(dl.timestamp()) if dl else None)
    return decided or remembered(out)


def remember(out: Path, decided: Decision) -> bool:
    """/ok·/skip 을 결과 폴더에 남긴다 (schedule 이 매시 확인할 때). 바뀌었으면 True."""
    if remembered(out) == decided:
        return False
    (out / APPROVAL).write_text(
        json.dumps({"decision": decided, "at": int(time.time())}), encoding="utf-8"
    )
    return True


def load_record(out: Path) -> PreviewRecord | None:
    path = out / "telegram.json"
    if not path.exists():
        return None
    return PreviewRecord.model_validate_json(path.read_text(encoding="utf-8"))


def decision(out: Path, tg: Telegram) -> Decision | None:
    record = load_record(out)
    if record is None:
        raise FileNotFoundError(f"미리보기를 보낸 기록이 없습니다: {out / 'telegram.json'}")
    return find_decision(tg.updates(), record, tg.chat_id)


# ---------- 처음 설정 ----------


def find_chats(token: str, client: httpx.Client | None = None) -> tuple[str, dict[int, int]]:
    """(봇 아이디, 1:1 채팅 ID → 메시지 수). 봇 토큰이 맞는지도 여기서 알 수 있다."""
    tg = Telegram(token, "-", client)
    me = tg.call("getMe")
    chats: dict[int, int] = {}
    hook = tg.call("getWebhookInfo")
    print(
        f"웹훅 {'있음' if hook.get('url') else '없음'}, "
        f"대기 중 업데이트 {hook.get('pending_update_count', 0)}개, "
        f"받는 종류 {hook.get('allowed_updates') or '기본'}"
    )
    updates = tg.call("getUpdates", limit=100, allowed_updates=["message"])
    kinds = sorted({k for u in updates for k in u if k != "update_id"})
    print(f"받은 업데이트 {len(updates)}개 {kinds}")
    for u in updates:
        chat = (u.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private":
            chats[chat["id"]] = chats.get(chat["id"], 0) + 1
    return me.get("username", ""), chats


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("preview")
    s.add_argument("out", type=Path)
    s.add_argument("--run", required=True, help="결과 폴더 이름 (daily-results 브랜치)")
    s.add_argument("--sandbox", action="store_true")
    s = sub.add_parser("decision")
    s.add_argument("out", type=Path)
    s = sub.add_parser("send")
    s.add_argument("text")
    sub.add_parser("chats")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "chats":
            bot, chats = find_chats(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
            print(f"봇 토큰 정상: @{bot}")
            if not chats:
                print("봇에게 온 메시지가 없습니다. 봇에게 아무 말이나 보내고 다시 실행하세요")
            for cid, n in chats.items():
                print(f"채팅 ID: {cid} (메시지 {n}개)")
                if len(chats) == 1:
                    Telegram(os.environ["TELEGRAM_BOT_TOKEN"], str(cid)).send(
                        f"연결 확인: 이 채팅 ID({cid})를 TELEGRAM_CHAT_ID 로 등록하세요"
                    )
            return 0
        tg = Telegram.from_env()
        if args.cmd == "preview":
            rec = preview(args.out, args.run, tg, args.sandbox)
            print(f"미리보기 보냄: 메시지 {len(rec.message_ids)}개 → {args.out / 'telegram.json'}")
        elif args.cmd == "decision":
            print(decision(args.out, tg) or "답장 없음")
        else:
            tg.send(args.text)
        return 0
    except (ValueError, FileNotFoundError, RuntimeError, httpx.HTTPError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
