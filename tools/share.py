"""공유리워드 링크 받기 (monetize: manual_link). 외부로 올리는 작업이라 schedule 안에서만 돈다.

지그재그 공유리워드 링크는 로그인한 앱에서만 만들 수 있어 자동으로 만들지 않는다. 그래서
1. 게시가 끝나면 tools.publish 가 텔레그램 게시 알림에 상품 번호 목록을 붙여 보낸다 (request_text).
2. 사용자가 그 메시지에 답장으로 "1 https://…" 처럼 링크를 붙인다. 링크 모양은 거르지 않는다.
3. schedule 이 깨어날 때마다 check 가 답장을 읽어 게시 기록(posts.json)의 share 에 넣고,
   바뀌었으면 상품 페이지(tools.link_page)를 다시 올린다. 링크가 없는 상품은 상품 링크 그대로.

텔레그램은 답장을 24시간만 보관하므로, 답장한 뒤 24시간 안에 schedule 이 한 번은 깨어나야 한다.
게시 뒤 link_page.share_days 일이 지난 게시물은 보지 않는다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from core.models import PostLink, PostRecord, ShareRequest
from core.profile import Profile, load_profile
from core.store import Store

URL = re.compile(r"https?://\S+")


def enabled(profile: Profile) -> bool:
    return profile.monetize == "manual_link" and profile.link_page.enabled


def request_text(head: str, links: list[PostLink]) -> str:
    rows = [
        head,
        "",
        "공유리워드 링크를 이 메시지에 답장으로 붙여 주세요. 한 줄에 하나씩, 번호와 함께요 (예: 1 https://…).",
        "보내 주신 링크로 상품 페이지를 바꿔요. 안 보낸 상품은 상품 링크 그대로 둬요.",
        "",
    ]
    rows += [f"{i}. {ln.category} · {ln.shop} {ln.name}" for i, ln in enumerate(links, 1)]
    return "\n".join(rows)


def parse_links(text: str, count: int) -> dict[int, str]:
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


def read_links(req: ShareRequest, updates: list[dict], chat_id: str, count: int) -> bool:
    """텔레그램 답장에서 링크를 읽어 req.links 에 더한다 (같은 번호는 나중 답장이 이김).
    새로 바뀌었으면 True."""
    before = dict(req.links)
    for u in sorted(updates, key=lambda u: u.get("update_id", 0)):
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        if msg.get("date", 0) < req.sent_at:
            continue
        to = (msg.get("reply_to_message") or {}).get("message_id")
        if to not in req.message_ids:
            continue
        req.links.update(parse_links(msg.get("text") or "", count))
    return req.links != before


def share_url(post: PostRecord, index: int, link: PostLink) -> str:
    """상품 순서(1부터)의 링크. 공유리워드 링크를 받았으면 그것."""
    if post.share and index in post.share.links:
        return post.share.links[index]
    return link.url


def check(account: str, tg, now: datetime | None = None, dry_run: bool = False, host=None) -> str:
    """최근 게시물의 답장을 읽어 반영한다. schedule 이 부른다. 한 줄 요약을 돌려준다."""
    from tools import link_page
    from tools.comments import post_links

    profile = load_profile(account)
    if not enabled(profile) or tg is None:
        return f"[{account}] 공유리워드: 꺼짐"
    now = now or datetime.now(UTC)
    today = now.astimezone(ZoneInfo(profile.schedule.timezone)).date()
    store = Store(account)
    posts = store.posts()
    recent = [
        p
        for p in posts
        if p.share
        and timedelta(0) <= today - p.date <= timedelta(days=profile.link_page.share_days)
    ]
    if not recent:
        return f"[{account}] 공유리워드: 기다리는 게시물 없음"
    updates = tg.updates()
    changed: list[str] = []
    for post in recent:
        assert post.share is not None
        before = len(post.share.links)
        if read_links(post.share, updates, tg.chat_id, len(post_links(post, store))):
            changed.append(
                f"{post.date} {len(post.share.links)}개 (새로 {len(post.share.links) - before})"
            )
    if not changed:
        return f"[{account}] 공유리워드: 새 링크 없음"
    summary = f"[{account}] 공유리워드 링크 반영: " + ", ".join(changed)
    if dry_run:
        return summary + " (시험이라 저장하지 않음)"
    store.save_posts(posts)
    try:
        url = link_page.push(profile, store, host)
    except (RuntimeError, ValueError, httpx.HTTPError) as e:
        tg.send(f"{summary}\n상품 페이지를 올리지 못했어요: {e}")
        return f"{summary} (상품 페이지 실패: {e})"
    tg.send(f"{summary}\n상품 페이지에 반영했어요 {url}")
    return summary
