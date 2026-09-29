r"""댓글 → DM 코디 링크 (10단계). 외부로 보내는 도구라 Claude 허용 목록에서 막혀 있다.

profile `comments` 를 켜면 캡션에 "댓글을 남겨 주세요" 같은 안내 문구가 들어가고
(tools.plan),
schedule 워크플로가 깨어날 때마다 이 도구가 최근 게시물(comments.days 일 안)의 새 댓글을 본다.
키워드가 든 댓글이면 인스타 비공개 답장으로 그 게시물의 상품 링크를 DM 으로 보낸다.

- 인스타 규칙: 댓글 하나에 DM 한 번, 댓글을 단 뒤 7일 안에만 보낼 수 있다.
- 같은 사람이 키워드 댓글을 여러 개 달아도 한 번만 보낸다 (그때그때 댓글 목록 안에서만 확인).
- 기록(data/<계정>/dm.json)에는 댓글 id 의 해시만 남긴다. 작성자 이름·id·댓글 글은 저장하지 않는다.
- link_mode: share_reward 면 게시 뒤 첫 확인 때 텔레그램으로 공유리워드 링크를 붙여 달라고 한다.
  답장이 오면 그 링크로, share_wait_hours 안에 안 오면 상품 링크로 보낸다.
  (지그재그 공유리워드 링크는 로그인한 앱에서만 만들 수 있어 자동으로 만들지 않는다)

사용법:
  python -m tools.comments run --account women [--dry-run] [--even-if-off]
      댓글 확인하고 DM 보내기 (--even-if-off: 켜기 전 시험. comments 워크플로)
  python -m tools.comments text --account women [--date D]    DM 글 미리 보기 (보내지 않음)
환경 변수: IG_TOKEN, IG_USER_ID (또는 프로필 instagram 의 Secret 이름, 또는 IG_SECRETS JSON),
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from core.models import PostDm, PostLink, PostRecord, ShareRequest
from core.profile import Profile, load_profile
from core.store import Store
from tools import notify
from tools.ig_publish import IGError, Instagram

DM_MAX = 1000  # 인스타 DM 글 최대 길이
REPLY_WINDOW = timedelta(days=7)
URL = re.compile(r"https?://\S+")
# 전체를 멈추고 다음에 다시 볼 오류 (토큰, 권한, 호출 한도). 나머지는 그 댓글만 건너뜀
FATAL_CODES = {190, 3, 4, 10, 17, 32, 613}
WINDOW_SUBCODES = {2018278, 2534022}  # 보낼 수 있는 기간이 지남 (code 10 이어도 그 댓글만 건너뜀)


def fatal(e: IGError) -> bool:
    if e.subcode in WINDOW_SUBCODES:
        return False
    return e.code in FATAL_CODES or (e.code is not None and 200 <= e.code < 300)


def comment_key(account: str, comment_id: str) -> str:
    return hashlib.sha256(f"{account}:{comment_id}".encode()).hexdigest()[:16]


def matches(text: str, keywords: list[str]) -> bool:
    """키워드가 없으면 모든 댓글."""
    if not keywords:
        return True
    t = "".join(text.split()).lower()
    return any("".join(k.split()).lower() in t for k in keywords)


def parse_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")


def post_links(post: PostRecord, store: Store) -> list[PostLink]:
    """게시 기록의 링크. 예전 기록이라 없으면 상품 창고에서 찾는다."""
    if post.links:
        return post.links
    from tools.feed import clean_name

    products = {p.id: p for p in store.products()}
    return [
        PostLink(
            product=pid,
            category=p.category,
            shop=p.shop,
            name=clean_name(p.name),
            url=str(p.url),
            image=str(p.image_urls[0]),
        )
        for pid in post.products
        if (p := products.get(pid))
    ]


def dm_text(
    profile: Profile, post: PostRecord, links: list[PostLink], share: dict[int, str] | None = None
) -> str:
    """DM 글. share 는 상품 순서(1부터) → 공유리워드 링크."""
    cm, share = profile.comments, share or {}

    def build(with_name: bool) -> str:
        rows = [cm.dm_intro.format(date=post.date, title=post.title)]
        if share:
            rows.insert(0, profile.copy_rules.ad_notice)
        items = [
            cm.dm_item.format(
                index=i,
                category=ln.category,
                shop=ln.shop,
                name=ln.name if with_name else "",
                url=share.get(i, ln.url),
            ).replace(" \n", "\n")
            for i, ln in enumerate(links, 1)
        ]
        rows.append("\n\n".join(items))
        if cm.dm_outro:
            rows.append(cm.dm_outro)
        return "\n\n".join(r for r in rows if r)

    text = build(True)
    if len(text) > DM_MAX:
        text = build(False)  # 긴 상품 이름을 빼도 넘으면 자른다
    return text[:DM_MAX]


# ---------- 공유리워드 링크 (텔레그램) ----------


def share_request_text(profile: Profile, post: PostRecord, links: list[PostLink]) -> str:
    h = profile.comments.share_wait_hours
    rows = [
        f"[{profile.id}] {post.date} 게시물 공유리워드 링크를 붙여 주세요",
        "이 메시지에 답장으로 아래 순서대로 한 줄에 하나씩 보내 주세요 (예: 1 https://…).",
        f"{h}시간 안에 안 오면 상품 링크로 DM 을 보내요.",
        "",
    ]
    rows += [f"{i}. {ln.category} · {ln.shop} {ln.name}\n{ln.url}" for i, ln in enumerate(links, 1)]
    return "\n".join(rows)


def parse_share_links(text: str, count: int) -> dict[int, str]:
    """번호("1 https://…")가 있으면 그 번호, 없으면 비어 있는 순서대로."""
    found: dict[int, str] = {}
    loose: list[str] = []
    for line in text.splitlines():
        url = URL.search(line)
        if not url:
            continue
        m = re.match(r"\s*(\d+)\s*[.)·:\-]?\s*https?://", line)
        if m and 1 <= int(m.group(1)) <= count:
            found[int(m.group(1))] = url.group(0)
        else:
            loose.append(url.group(0))
    for i in range(1, count + 1):
        if not loose:
            break
        if i not in found:
            found[i] = loose.pop(0)
    return found


def read_share_links(req: ShareRequest, updates: list[dict], chat_id: str, count: int) -> bool:
    """텔레그램 답장에서 공유 링크를 읽어 req.links 에 넣는다. 새로 읽었으면 True."""
    changed = False
    for u in updates:
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        if msg.get("date", 0) < req.sent_at:
            continue
        to = (msg.get("reply_to_message") or {}).get("message_id")
        if to not in req.message_ids:
            continue
        links = parse_share_links(msg.get("text") or "", count)
        if links and links != req.links:
            req.links = links
            changed = True
    return changed


# ---------- 확인 ----------


@dataclass
class Report:
    sent: int = 0
    held: int = 0  # 공유 링크를 기다리느라 미룬 DM
    skipped: int = 0
    posts: int = 0
    notes: list[str] = field(default_factory=list)
    changed: bool = False

    def line(self, account: str) -> str:
        head = (
            f"[{account}] 댓글 확인: 게시물 {self.posts}개, DM {self.sent}개"
            + (f", 미룸 {self.held}개" if self.held else "")
            + (f", 못 보냄 {self.skipped}개" if self.skipped else "")
        )
        return "\n".join([head, *self.notes])


def find_media_id(post: PostRecord, ig: Instagram, cache: dict) -> str | None:
    if post.media_id:
        return post.media_id
    if post.permalink is None:
        return None
    if "media" not in cache:
        cache["media"] = ig.recent_media()
    for m in cache["media"]:
        if (m.get("permalink") or "").rstrip("/") == post.permalink.rstrip("/"):
            return m["id"]
    return None


def run(
    profile: Profile,
    store: Store,
    ig: Instagram,
    tg=None,
    now: datetime | None = None,
    dry_run: bool = False,
    even_if_off: bool = False,
) -> Report:
    cm = profile.comments
    rep = Report()
    if not (cm.enabled and cm.mode == "builtin") and not even_if_off:
        rep.notes.append("comments 가 꺼져 있거나 mode 가 builtin 이 아니에요")
        return rep
    now = now or datetime.now(UTC)
    today = now.astimezone(ZoneInfo(profile.schedule.timezone)).date()
    state = store.dm()
    before = state.model_dump_json()
    posts = [p for p in store.posts() if timedelta(0) <= today - p.date <= timedelta(days=cm.days)]
    cache: dict = {}
    updates: list[dict] | None = None
    try:
        for post in sorted(posts, key=lambda p: p.date):
            key = str(post.date)
            media_id = find_media_id(post, ig, cache)
            if media_id is None:
                rep.notes.append(f"{key}: 인스타 게시물을 찾지 못했어요")
                continue
            rep.posts += 1
            pd = state.posts.setdefault(key, PostDm())
            pd.media_id = media_id
            links = post_links(post, store)
            if not links:
                rep.notes.append(f"{key}: 보낼 상품 링크가 없어요")
                continue

            share: dict[int, str] = {}
            waiting = False
            if cm.link_mode == "share_reward" and tg is not None:
                if pd.share is None:
                    if not dry_run:
                        mid = tg.send(share_request_text(profile, post, links))
                        pd.share = ShareRequest(sent_at=int(now.timestamp()), message_ids=[mid])
                    waiting = cm.share_wait_hours > 0
                else:
                    if updates is None:
                        updates = tg.updates()
                    read_share_links(pd.share, updates, tg.chat_id, len(links))
                    share = pd.share.links
                    age = now.timestamp() - pd.share.sent_at
                    waiting = len(share) < len(links) and age < cm.share_wait_hours * 3600

            done = set(pd.sent) | set(pd.skipped)
            comments = sorted(ig.comments(media_id), key=lambda c: c.get("timestamp", ""))
            # 이미 DM 을 받은 사람 (이번 댓글 목록 안에서만 알 수 있고 저장하지 않음)
            got = {
                (c.get("from") or {}).get("id")
                for c in comments
                if comment_key(profile.id, c["id"]) in pd.sent
            } - {None}
            text = dm_text(profile, post, links, share)
            for c in comments:
                h = comment_key(profile.id, c["id"])
                if h in done or not matches(c.get("text") or "", cm.keywords):
                    continue
                who = (c.get("from") or {}).get("id")
                too_old = "timestamp" in c and now - parse_time(c["timestamp"]) > REPLY_WINDOW
                if who == ig.user_id or who in got or too_old:
                    pd.skipped.append(h)
                    done.add(h)
                    continue
                if waiting:
                    rep.held += 1
                    continue
                if dry_run:
                    rep.sent += 1
                    continue
                try:
                    ig.private_reply(c["id"], text)
                except IGError as e:
                    if fatal(e):
                        raise
                    pd.skipped.append(h)
                    rep.skipped += 1
                    rep.notes.append(f"{key}: DM 을 보내지 못했어요 ({e})")
                else:
                    pd.sent.append(h)
                    rep.sent += 1
                    if cm.public_reply:
                        try:
                            ig.reply_comment(c["id"], cm.public_reply)
                        except IGError as e:
                            if fatal(e):
                                raise
                done.add(h)
                if who:
                    got.add(who)
    finally:
        # 기간이 지난 기록은 지운다 (comments.days + 하루)
        keep = today - timedelta(days=cm.days + 1)
        state.posts = {k: v for k, v in state.posts.items() if date.fromisoformat(k) >= keep}
        if not dry_run and state.model_dump_json() != before:
            store.save_dm(state)
            rep.changed = True
    return rep


# ---------- 인스타 연결 ----------


def credentials(profile: Profile, env: dict) -> tuple[str, str]:
    """(사용자 id, 토큰). IG_USER_ID/IG_TOKEN → IG_SECRETS(JSON, schedule) → Secret 이름."""
    ig = profile.instagram
    secrets: dict = {}
    if env.get("IG_SECRETS"):
        try:
            secrets = json.loads(env["IG_SECRETS"])
        except ValueError:
            secrets = {}

    def pick(direct: str, name: str) -> str:
        return env.get(direct) or secrets.get(name) or env.get(name, "")

    return pick("IG_USER_ID", ig.user_id_secret), pick("IG_TOKEN", ig.token_secret)


def instagram_for(profile: Profile, env: dict | None = None) -> Instagram:
    env = env if env is not None else dict(os.environ)
    cfg = notify.publish_config().get("instagram", {})
    user_id, token = credentials(profile, env)
    return Instagram(user_id, token, cfg.get("api_base", "https://graph.instagram.com/v23.0"))


def check(account: str, tg=None, now: datetime | None = None, dry_run: bool = False) -> str:
    """schedule 작업용. 오류가 나도 예외를 던지지 않고 결과 한 줄을 돌려준다.
    토큰·권한 오류는 텔레그램으로 하루 한 번만 알린다."""
    profile = load_profile(account)
    store = Store(account)
    now = now or datetime.now(UTC)
    try:
        rep = run(profile, store, instagram_for(profile), tg, now, dry_run)
        return rep.line(account)
    except (IGError, ValueError, RuntimeError, httpx.HTTPError) as e:
        msg = f"[{account}] 댓글 DM 오류: {e}"
        if isinstance(e, IGError) and e.token_problem:
            msg += "\n인스타 토큰이 만료됐거나 취소됐어요. 새 토큰을 Secret 에 다시 등록해 주세요."
        elif isinstance(e, IGError) and fatal(e):
            msg += (
                "\n토큰에 댓글·메시지 권한이 없거나 호출 한도에 닿았어요 "
                "(README 10단계 토큰 다시 받기)."
            )
        today = str(now.astimezone(ZoneInfo(profile.schedule.timezone)).date())
        state = store.dm()
        if tg is not None and not dry_run and state.alerted != today:
            try:
                tg.send(msg)
                state.alerted = today
                store.save_dm(state)
            except (RuntimeError, httpx.HTTPError):
                pass
        return msg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run")
    s.add_argument("--account", required=True)
    s.add_argument("--dry-run", action="store_true", help="DM·텔레그램을 보내지 않고 셀 수만")
    s.add_argument(
        "--even-if-off", action="store_true", help="comments.enabled 가 꺼져 있어도 (켜기 전 시험)"
    )
    s = sub.add_parser("text")
    s.add_argument("--account", required=True)
    s.add_argument("--date", help="게시 날짜 (기본: 마지막 게시물)")
    args = ap.parse_args(argv)
    profile = load_profile(args.account)
    store = Store(args.account)
    if args.cmd == "text":
        posts = store.posts()
        if args.date:
            posts = [p for p in posts if str(p.date) == args.date]
        if not posts:
            print("게시 기록이 없습니다", file=sys.stderr)
            return 1
        post = posts[-1]
        print(dm_text(profile, post, post_links(post, store)))
        return 0
    tg = None
    try:
        tg = notify.Telegram.from_env()
    except ValueError:
        print("텔레그램 설정이 없어 공유 링크 요청·알림 없이 진행합니다", file=sys.stderr)
    try:
        rep = run(
            profile,
            store,
            instagram_for(profile),
            tg,
            dry_run=args.dry_run,
            even_if_off=args.even_if_off,
        )
    except (IGError, ValueError, RuntimeError, httpx.HTTPError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    print(rep.line(args.account))
    return 0


if __name__ == "__main__":
    sys.exit(main())
