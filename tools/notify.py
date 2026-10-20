r"""텔레그램 미리보기·승인·알림 (8단계). 외부로 보내는 도구라 Claude 허용 목록에 없다.

- preview: 캐러셀 슬라이드, 캡션, 검수 결과를 보내고 결과 폴더에 telegram.json 을 남긴다.
  승인 버튼(/ok, /skip)은 텔레그램 입력창 아래 키보드로 나온다. 누르면 그 글이 채팅에 남는다.
- decision: 미리보기 뒤에 온 답장에서 /ok 또는 /skip 을 찾는다 (게시 job 이 부름).
  고칠 점 /fix 는 tools.feedback 이 읽는다 (15단계).
- send: 알림 한 줄.

봇 서버 없이 동작한다. 답장은 게시 job 이 돌 때 getUpdates 로 읽는다 (텔레그램이 24시간 보관).
schedule 은 받은 글을 daily-results 의 telegram_updates.json 에 남기고(이름 등은 빼고 72시간),
남긴 글은 텔레그램에서 확인 처리한다. 확인하지 않은 옛 글이 쌓이면 새 글이 안 오는 일이 있어서.
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

from core.models import Decision, Manifest, PreviewRecord, ReelsSpec
from core.profile import REPO_ROOT, Profile, load_profile, minutes

CONFIG_PATH = REPO_ROOT / "config" / "publish.yaml"
API = "https://api.telegram.org"
MAX_TEXT = 4096
MAX_PHOTO_CAPTION = 1024
SEND_METHODS = {"sendMessage", "sendPhoto", "sendMediaGroup", "sendVideo", "sendDocument"}
APPROVAL = "approval.json"  # 결과 폴더에 남기는 /ok·/skip 기록 (텔레그램은 답장을 24시간만 보관)
NOW = "now.json"  # /now (지금 게시) 를 처리한 기록
NOW_WORDS = ("지금 올려", "지금 올려줘", "바로 올려", "바로 올려줘", "지금 게시", "바로 게시")


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
UPDATES_KEEP_HOURS = 72  # 받은 글 기록을 남기는 시간


def load_updates_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def slim_update(u: dict) -> dict:
    """기록에 남길 것만: 번호, 시각, 채팅 ID, 글, 답장한 메시지 번호.
    보낸 사람 이름·아이디 같은 개인정보는 남기지 않는다."""
    msg = u.get("message") or {}
    out: dict = {
        "message_id": msg.get("message_id"),
        "date": msg.get("date", 0),
        "chat": {"id": (msg.get("chat") or {}).get("id")},
        "text": msg.get("text") or "",
    }
    reply = (msg.get("reply_to_message") or {}).get("message_id")
    if reply:
        out["reply_to_message"] = {"message_id": reply}
    # 장면 사진 (profile flow): 보낸 사진의 파일 번호만 남긴다 (사진은 tools.flow 가 받아 저장)
    file_id = photo_file_id(msg)
    if file_id:
        out["photo"] = file_id
        out["caption"] = msg.get("caption") or ""
        if msg.get("media_group_id"):
            out["media_group_id"] = msg["media_group_id"]  # 앨범으로 한 번에 보낸 사진들
    return {"update_id": u["update_id"], "message": out}


def photo_file_id(msg: dict) -> str | None:
    """텔레그램 글에 붙은 사진의 파일 번호 (가장 큰 크기). 압축 없이 파일로 보낸 그림도 읽는다.
    기록(slim_update)에서 읽을 때는 photo 가 이미 파일 번호다."""
    photo = msg.get("photo")
    if isinstance(photo, str):
        return photo
    if photo:
        # 텔레그램은 작은 크기부터 보낸다: 크기가 같으면 뒤의 것
        size = [p.get("width", 0) * p.get("height", 0) for p in photo]
        return max(zip(size, range(len(photo)), photo, strict=True))[2]["file_id"]
    doc = msg.get("document") or {}
    if str(doc.get("mime_type", "")).startswith("image/"):
        return doc.get("file_id")
    return None


def save_updates_log(
    path: Path, updates: list[dict], chat_id: str, now: float | None = None
) -> None:
    """등록 채팅에서 온 최근 글만 줄여서 남긴다 (다른 채팅 글, 오래된 글은 버림)."""
    cutoff = (now if now is not None else time.time()) - UPDATES_KEEP_HOURS * 3600
    keep = [
        slim_update(u)
        for u in updates
        if str(((u.get("message") or {}).get("chat") or {}).get("id")) == str(chat_id)
        and (u.get("message") or {}).get("date", 0) >= cutoff
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


class Telegram:
    def __init__(
        self,
        token: str,
        chat_id: str,
        client: httpx.Client | None = None,
        log: Path | None = None,
        save_log: bool = False,
    ):
        if not token or not chat_id:
            raise ValueError("텔레그램 봇 토큰과 채팅 ID 가 필요합니다 (Secrets)")
        self.base = f"{API}/bot{token}"
        self.chat_id = str(chat_id)
        self.client = client or httpx.Client(timeout=60)
        # 받은 글 기록 (daily-results 의 telegram_updates.json). 있으면 기록한 글은 텔레그램에서
        # 확인 처리하고 기록에서 읽는다. 오래된 글을 확인하지 않고 두면 새 글이 안 오는 일이 있어서
        self.log = log
        self.save_log = save_log
        self._base: int | None = None  # 이 실행을 시작할 때 기록에 있던 마지막 update_id

    @classmethod
    def from_env(cls, client: httpx.Client | None = None) -> Telegram:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        log = os.environ.get("TELEGRAM_UPDATES_LOG")
        return cls(
            token,
            os.environ.get("TELEGRAM_CHAT_ID", ""),
            client,
            Path(log) if log else None,
            os.environ.get("TELEGRAM_UPDATES_SAVE") == "1",
        )

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
        """글을 보내고 첫 메시지 번호를 돌려준다. 한도보다 길면 줄 단위로 나눠 보낸다
        (여러 벌 게시물의 상품 링크 목록 등). 버튼(reply_markup)은 마지막 메시지에 단다."""
        chunks = split_text(text, MAX_TEXT)
        first = None
        for i, chunk in enumerate(chunks):
            extra = kw if i == len(chunks) - 1 else {}
            msg = self.call("sendMessage", chat_id=self.chat_id, text=chunk, **extra)
            first = first if first is not None else msg["message_id"]
        return first

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

    def send_document(self, path: Path, caption: str = "") -> int:
        """파일 한 개 (지시문 텍스트 등)."""
        with path.open("rb") as f:
            msg = self.call(
                "sendDocument",
                files={"document": (path.name, f)},
                chat_id=self.chat_id,
                caption=caption[:MAX_PHOTO_CAPTION],
            )
        return msg["message_id"]

    def send_video(
        self, path: Path, caption: str = "", width: int = 1080, height: int = 1920
    ) -> int:
        """영상 한 개 (릴스 미리보기, 텔레그램 봇은 50MB 까지)."""
        with path.open("rb") as f:
            msg = self.call(
                "sendVideo",
                files={"video": (path.name, f, "video/mp4")},
                chat_id=self.chat_id,
                caption=caption[:MAX_PHOTO_CAPTION],
                width=str(width),
                height=str(height),
                supports_streaming="true",
            )
        return msg["message_id"]

    def download(self, file_id: str, dest: Path) -> Path:
        """받은 사진(파일 번호)을 dest 에 저장한다 (getFile, 봇 파일은 20MB 까지)."""
        info = self.call("getFile", file_id=file_id)
        url = self.base.replace("/bot", "/file/bot", 1) + "/" + info["file_path"]
        r = self.client.get(url)
        if r.status_code != 200:
            # 주소에 봇 토큰이 들어 있어서 상태 코드만 남긴다
            raise RuntimeError(f"텔레그램 사진 받기 실패 ({r.status_code})")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest

    def updates(self) -> list[dict]:
        if self.log is not None:
            return self._logged_updates()
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

    def _logged_updates(self) -> list[dict]:
        """기록 + 새 글. 텔레그램에는 이 실행 시작 때 이미 기록(커밋)돼 있던 글까지만 확인 처리한다
        (이번 실행에서 처음 받은 글은 기록이 커밋된 뒤 다음 실행에서 확인)."""
        assert self.log is not None
        logged = load_updates_log(self.log)
        if self._base is None:
            self._base = max((u["update_id"] for u in logged), default=0)
        params: dict = {"allowed_updates": ["message"], "limit": UPDATES_LIMIT}
        if self._base:
            params["offset"] = self._base + 1
        fresh = self.call("getUpdates", **params)
        merged = {u["update_id"]: u for u in logged}
        merged.update({u["update_id"]: u for u in fresh})
        out = [merged[k] for k in sorted(merged)]
        if self.save_log:
            save_updates_log(self.log, out, self.chat_id)
        return out


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
    cover: int | None = None,
) -> str:
    """미리보기 마지막 안내 글. cover 는 표지로 쓴 벌 번호 (tools.cover_look)."""
    day = manifest.date
    flow = profile.flow.enabled  # 차례 흐름 (tools.flow): /ok 를 받으면 바로 게시, 마감 없음
    when = "/ok 하면 바로 게시" if flow else f"{profile.schedule.publish_at} 게시 예정"
    lines = [f"[{profile.id}] {day} {when}"]
    lines.append("제목: " + " / ".join(manifest.title_lines))
    if fix:
        lines.append("")
        lines.append(f"고친 점 ({fix.get('round', 1)}/{profile.feedback.max_fixes}회)")
        from tools.feedback import label

        lines += [f"- {c}" for c in changes or []] or [
            f"- {label(f)}: {f['text']}" for f in fix.get("fixes", [])
        ]
    if cover is not None and profile.looks.repeat_cover_look:
        lines.append(f"표지: LOOK {cover}")
    elif cover is not None:
        # 표지 벌은 벌 장에 다시 나오지 않는다 (2026-10-15): 번호는 벌 장에 보이는 LOOK 번호
        lines.append(
            "표지 벌은 첫 장에만 나와요. 다른 벌로 바꾸려면 /cover 2 처럼 벌 장의 LOOK 번호"
            + (" (표지가 LOOK 1)" if profile.looks.cover_first else "")
        )
        if profile.looks.cover_first:
            lines.append(
                "벌 순서를 바꾸려면 /order 4 2 3 1 처럼 LOOK 번호를 원하는 순서로 (첫 번호가 표지)"
            )
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
    if flow:
        lines.append(f"게시하려면 /ok {label}, 이 주제를 건너뛰려면 /skip {label}")
        lines.append(
            "한 시간 안에 확인해 바로 게시하고 상품 링크를 보내 드려요. 마감 없이 기다려요"
        )
        lines.append("(아래 버튼을 누르거나 이 메시지에 /ok 로 답장해도 돼요)")
    elif profile.approval == "required" or manifest.needs_human:
        lines.append(f"게시하려면 /ok {label}, 건너뛰려면 /skip {label}")
        dl = ok_deadline(profile, day)
        if dl is not None:
            lines.append(f"승인 마감 {dl:%m/%d %H:%M}. 그때까지 /ok 가 없으면 게시하지 않아요")
        lines.append("(아래 버튼을 누르거나 이 메시지에 /ok 로 답장해도 돼요)")
        if cover is not None:
            other = 2 if cover == 1 or not profile.looks.repeat_cover_look else 1
            lines.append(
                f"표지를 다른 벌로 하려면 /ok {other} 처럼 LOOK 번호를 붙여 승인하거나, "
                f"표지만 먼저 보려면 /cover {other}"
            )
        from tools import share

        if share.enabled(profile):
            lines.append("/ok 를 확인하면 공유리워드 링크를 만들 상품 링크를 보내 드려요")
        elif share.sends_list(profile):
            lines.append("/ok 를 확인하면 상품 링크를 보내 드려요")
        lines.append(
            f"/ok 뒤 /now 를 보내면 {profile.schedule.publish_at} 을 기다리지 않고 "
            "다음 매시 확인 때 바로 게시해요"
        )
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


def reel_caption(profile: Profile, spec: ReelsSpec | None) -> str:
    when = profile.schedule.reels_at or "캐러셀 게시 직후"
    sound = f"음원 {spec.audio}" if spec is not None and spec.audio else "소리 없음"
    sec = spec.duration_sec if spec is not None else None
    length = f"{sec:g}초 · " if sec else ""
    if profile.reels.delivery == "telegram":
        resend = f"{when} 에 영상을 다시 보내 드려요 (앱에서 음악 붙여 올리기)"
        return f"[{profile.id}] 릴스 {length}{sound} · {resend}"
    return f"[{profile.id}] 릴스 {length}{sound} · {when} 게시 예정"


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
    from tools import reels

    video = reels.video_path(manifest, out) if profile.reels.enabled else None
    if video is not None:
        # 릴스도 같은 /ok 로 승인한다 (게시는 schedule.reels_at)
        ids.append(tg.send_video(video, reel_caption(profile, manifest.reels)))
    ids.append(tg.send(full_caption(manifest)))
    markup = None
    if not sandbox:
        label = command_label(profile, manifest.date)
        markup = {
            "keyboard": [[{"text": f"/ok {label}"}, {"text": f"/skip {label}"}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }
    from tools import cover_look

    cover = cover_look.current(out, profile)
    text = control_text(manifest, profile, sandbox, fix, feedback.fix_changes(out), cover)
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


def split_text(text: str, limit: int) -> list[str]:
    """limit 글자 안으로 줄 단위로 나눈다 (한 줄이 넘치면 그 줄은 자른다)."""
    chunks: list[str] = []
    cur = ""
    for line in text.split("\n"):
        line = line[:limit]
        cand = f"{cur}\n{line}" if cur else line
        if len(cand) > limit and cur:
            chunks.append(cur)
            cand = line
        cur = cand
    return [*chunks, cur] if cur or not chunks else chunks


NOTE = "note:"  # "다시" 명령 args 에서 사용자 요청을 담은 칸의 머리


def again_note(tail: str) -> list[str]:
    """ "다시" 뒤에 붙인 말 → [NOTE + 요청] (없으면 []).
    "해줘"·"보내줘" 같은 말과 앞뒤 문장부호는 뺀다."""
    t = re.sub(r"^\s*(?:해\s*주세요|해\s*줘|보내\s*주세요|보내\s*줘|줘)", "", tail)
    t = t.strip(" \t\n.,!~:;")
    return [NOTE + t] if t else []


def parse(text: str, account: str, day: date | None) -> Command | None:
    """ "/ok", "/pick 3 women 09-28", "/skip 2026-09-27" 같은 글을 읽는다.
    계정·날짜를 적었으면 둘 다 이 계정·날짜와 맞아야 하고(아니면 None),
    안 적었으면 가장 최근 것으로 본다.
    day 가 None 이면 날짜를 적은 글은 받지 않는다."""
    # 버튼 대신 직접 친 "2번", "2", "다시" 도 코디 고르기 명령으로 읽는다
    plain = text.strip()
    if re.fullmatch(r"\d{1,2}\s*번?", plain):
        return Command("pick", [re.sub(r"\D", "", plain)], False)
    # 여러 벌 게시물(20단계): "1 2 3 5 6 7" 은 쓸 벌 번호들, "4번 빼고" 는 그 벌만 빼기
    nums = r"\d{1,2}(?:\s*[,\s]\s*\d{1,2})*"
    if re.fullmatch(r"\d{1,2}(?:\s*[,\s]\s*\d{1,2})+\s*번?", plain):
        return Command("pick", re.findall(r"\d+", plain), False)
    # 붙여 친 "34678" 도 벌 번호들로 읽는다 (후보가 10벌을 넘지 않으므로 세 자리 이상은 한 자리씩)
    if re.fullmatch(r"[1-9]{3,}\s*번?", plain):
        return Command("pick", re.findall(r"\d", plain), False)
    m = re.fullmatch(rf"({nums})\s*번?\s*(?:은|는)?\s*빼(?:고|줘|주세요|기)?.*", plain)
    if m:
        return Command("drop", re.findall(r"\d+", m.group(1)), False)
    # 벌 순서 바꾸기 (tools.cover_look): "순서 4 2 3 6 1 5" (첫 번호가 표지)
    m = re.fullmatch(
        rf"순서\s*(?:는|를|바꿔|변경)?\s*:?\s*({nums})\s*(?:번|순서로|으로|로)?.*", plain
    )
    if m:
        return Command("order", re.findall(r"\d+", m.group(1)), False)
    # 표지 벌 바꾸기 (tools.cover_look): "표지 3번", "표지3"
    m = re.fullmatch(r"표지\s*(\d{1,2})\s*번?(?:\s*(?:으로|로))?", plain)
    if m:
        return Command("cover", [m.group(1)], False)
    # "다시" 뒤에 붙인 말은 새 후보에 반영할 요청 (2026-10-15 사용자 "다시. 외투가 필수는 아니야.")
    m = re.fullmatch(r"다시(?=$|[\s.,!~:;]|해|보내|줘)(.*)", plain, re.S)
    if m:
        return Command("again", again_note(m.group(1)), False)
    # 다시 짜기 (2026-10-14 사용자 "4번 채택하고 나머지 다 다시해줘. 8장 다 채워서"):
    # "4번 남기고 다시", "4, 6 남기고 나머지 다시 8벌" → 남길 벌 번호 + "8벌"
    m = re.fullmatch(
        rf"({nums})\s*번?\s*(?:은|는|만)?\s*(?:남기고|두고|채택하고|살리고)\s*(?:나머지\s*)?(?:다\s*)?다시(.*)",
        plain,
        re.S,
    )
    if m:
        size = re.search(r"(\d{1,2})\s*(?:벌|장)", plain[m.end(1) :])
        return Command(
            "again",
            re.findall(r"\d+", m.group(1))
            + ([f"{size.group(1)}벌"] if size else [])
            + again_note(m.group(2)),
            False,
        )
    if plain in NOW_WORDS:
        return Command("now", [], False)
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


def cover_number(args: list[str]) -> int | None:
    """/ok·/cover 뒤 표지 벌 번호 ("3", "3번", "표지 3", "표지3"). 아니면 None."""
    m = re.fullmatch(r"(?:표지)?(\d{1,2})번?", "".join(args))
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def is_decision(cmd: Command) -> bool:
    """/ok·/skip 승인 글인지. /ok 뒤에는 표지 벌 번호만 붙을 수 있다 (/ok 3)."""
    if cmd.name not in ("ok", "skip"):
        return False
    return not cmd.args or (cmd.name == "ok" and cover_number(cmd.args) is not None)


def parse_command(text: str, record: PreviewRecord) -> Decision | None:
    """미리보기에 대한 /ok 또는 /skip (다른 글이 붙어 있으면 None, /ok 3 은 ok)."""
    cmd = parse(text, record.account, record.date)
    if cmd and is_decision(cmd):
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
        if is_decision(cmd)
    ]
    return found[-1].name if found else None  # type: ignore[return-value]


def split_lines(updates: list[dict]) -> list[dict]:
    """한 메시지에 여러 줄로 적은 명령("/fix 2 신발 크게" 다음 줄 "/cover 3")을 줄마다 따로 읽게
    나눈다 (같은 update_id)."""
    out = []
    for u in updates:
        msg = u.get("message") or {}
        lines = [x for x in (msg.get("text") or "").split("\n") if x.strip()] or [""]
        out += [u | {"message": msg | {"text": line}} for line in lines]
    return out


def find_cover(
    updates: list[dict],
    record: PreviewRecord,
    chat_id: str,
    until: int | None = None,
    since: int | None = None,
) -> tuple[int, int] | None:
    """미리보기(since 가 있으면 그때) 뒤 온 마지막 표지 벌 고르기 (update_id, 벌 번호).
    "/ok 3", "/ok 표지 3", "/cover 3", "표지 3번" 을 읽는다.
    /fix 와 한 메시지에 줄을 바꿔 써도 된다."""
    found = [
        (uid, n)
        for uid, cmd in commands(
            split_lines(updates),
            chat_id,
            record.sent_at if since is None else since,
            record.account,
            record.date,
            {"ok", "cover"},
            record.message_ids,
            until,
        )
        if (n := cover_number(cmd.args)) is not None
    ]
    return found[-1] if found else None


def order_numbers(args: list[str]) -> list[int] | None:
    """/order 뒤 LOOK 번호 순서 ("4 2 3 6 1 5", "4,2,3,6,1,5"). 숫자가 없으면 None."""
    nums = re.findall(r"\d+", " ".join(args))
    if not nums or re.sub(r"[\d\s,번]", "", " ".join(args)):
        return None
    return [int(n) for n in nums]


def find_order(
    updates: list[dict],
    record: PreviewRecord,
    chat_id: str,
    until: int | None = None,
    since: int | None = None,
) -> tuple[int, list[int]] | None:
    """미리보기(since 가 있으면 그때) 뒤 온 마지막 벌 순서 바꾸기 (update_id, LOOK 번호 순서).
    "/order 4 2 3 6 1 5", "순서 4 2 3 6 1 5" 를 읽는다 (첫 번호가 표지).
    /fix 와 한 메시지에 써도 된다."""
    found = [
        (uid, nums)
        for uid, cmd in commands(
            split_lines(updates),
            chat_id,
            record.sent_at if since is None else since,
            record.account,
            record.date,
            {"order"},
            record.message_ids,
            until,
        )
        if (nums := order_numbers(cmd.args)) is not None
    ]
    return found[-1] if found else None


def find_now(
    updates: list[dict], record: PreviewRecord, chat_id: str, until: int | None = None
) -> int | None:
    """미리보기 뒤 온 마지막 /now ("지금 올려") 의 update_id."""
    found = commands(
        updates,
        chat_id,
        record.sent_at,
        record.account,
        record.date,
        {"now"},
        record.message_ids,
        until,
    )
    found = [(uid, cmd) for uid, cmd in found if not cmd.args]
    return found[-1][0] if found else None


def ok_deadline(profile: Profile, day: date) -> datetime | None:
    """게시 날짜(day)의 승인 마감 시각 (schedule.ok_deadline). 게시 시각보다 늦은 시각이면 전날.
    차례 흐름(profile flow)은 마감이 없다."""
    s = profile.schedule
    if not s.ok_deadline or profile.flow.enabled:
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
