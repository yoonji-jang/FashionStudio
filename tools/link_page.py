r"""최근 게시물 상품 링크 페이지 (10단계). 게시할 때마다 tools.publish 가 새로 써서 올린다.

ManyChat 같은 DM 자동화 서비스의 DM 에 이 페이지 주소 하나만 넣어 두면, 게시물이 바뀌어도
DM 을 고칠 필요가 없다. 설정은 profile `link_page`.
공개 사진 저장소(config/publish.yaml image_host)에
<계정>/index.html 로 올리고, 그 저장소의 GitHub Pages 로 연다.
페이지에는 게시 기록(data/<계정>/posts.json)의 제목·날짜·상품 링크만 들어간다.

사용법:
  python -m tools.link_page build --account women [--out out/link_page.html]   파일로 만들기
  python -m tools.link_page push --account women                               만들어서 올리기
환경 변수(push): MEDIA_REPO_TOKEN, GITHUB_REPOSITORY
"""

from __future__ import annotations

import argparse
import sys
from html import escape
from pathlib import Path

import httpx

from core.models import PostRecord
from core.profile import Profile, load_profile
from core.store import Store
from tools import notify
from tools.media_host import GitHubHost

FONT_CSS = "https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css"


def page_path(profile: Profile) -> str:
    return f"{profile.id}/index.html"


def render(profile: Profile, posts: list[PostRecord], store: Store) -> str:
    from tools.comments import post_links
    from tools.share import share_url

    lp, feed = profile.link_page, profile.feed
    accent = profile.visual.accent_color or feed.text_color
    recent = sorted(posts, key=lambda p: p.date, reverse=True)[: lp.keep]
    photos: dict[str, str] | None = None
    blocks: list[str] = []
    for post in recent:
        rows = []
        for i, ln in enumerate(post_links(post, store), 1):
            url = share_url(post, i, ln)
            meta = lp.item_line.format(category=ln.category, shop=ln.shop)
            image = ln.image
            if not image:  # 썸네일이 생기기 전 기록은 상품 창고의 대표 사진
                if photos is None:
                    photos = {p.id: str(p.image_urls[0]) for p in store.products()}
                image = photos.get(ln.product, "")
            thumb = (
                f'<img class="thumb" src="{escape(image)}" alt="" loading="lazy"'
                ' referrerpolicy="no-referrer">'
                if image
                else '<span class="thumb"></span>'
            )
            rows.append(
                f'<a class="item" href="{escape(url)}" target="_blank" rel="noopener">{thumb}'
                f'<span class="text"><span class="meta">{escape(meta)}</span>'
                f'<span class="name">{escape(ln.name)}</span></span></a>'
            )
        ig = (
            f' · <a href="{escape(post.permalink)}" target="_blank" rel="noopener">게시물 보기</a>'
            if post.permalink
            else ""
        )
        blocks.append(
            f'<section id="d{post.date}"><p class="date">{post.date}{ig}</p>'
            f"<h2>{escape(post.title)}</h2>{''.join(rows)}</section>"
        )
    notice = ""
    if profile.monetize == "manual_link":
        notice = f'<p class="notice">{escape(profile.copy_rules.ad_notice)}</p>'
    logo = escape(profile.visual.logo_text)
    body = "".join(blocks) or '<p class="empty">아직 게시물이 없어요</p>'
    return f"""<!doctype html>
<html lang="{escape(profile.language)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(lp.title)}</title>
<link rel="stylesheet" href="{FONT_CSS}">
<style>
body {{ margin: 0; background: {feed.background}; color: {feed.text_color};
  font-family: Pretendard, -apple-system, "Apple SD Gothic Neo", sans-serif; }}
main {{ max-width: 560px; margin: 0 auto; padding: 28px 16px 48px; }}
header h1 {{ font-size: 24px; margin: 0 0 6px; padding-left: 12px;
  border-left: 4px solid {accent}; }}
header p {{ color: {feed.sub_color}; font-size: 14px; margin: 0 0 4px 16px; }}
.notice {{ font-size: 13px; color: {feed.sub_color}; }}
section {{ margin-top: 32px; }}
.date {{ color: {feed.sub_color}; font-size: 13px; margin: 0 0 4px; }}
.date a {{ color: {feed.sub_color}; }}
h2 {{ font-size: 18px; margin: 0 0 12px; }}
.item {{ display: flex; align-items: center; gap: 12px; background: #fff; border-radius: 12px;
  padding: 10px 14px 10px 10px; margin: 8px 0; text-decoration: none; color: inherit; }}
.thumb {{ flex: none; width: 64px; height: 64px; border-radius: 8px; object-fit: cover;
  background: {feed.background}; }}
.text {{ min-width: 0; }}
.meta {{ display: block; font-size: 12px; color: {feed.sub_color}; }}
.name {{ display: block; font-size: 15px; margin-top: 2px; }}
.empty {{ color: {feed.sub_color}; }}
</style>
</head>
<body><main>
<header><h1>{escape(lp.title)}</h1><p>{escape(lp.intro)}</p><p>{logo}</p></header>
{notice}{body}
</main></body>
</html>
"""


def push(profile: Profile, store: Store, host: GitHubHost | None = None) -> str:
    """페이지를 만들어 올리고 주소를 돌려준다."""
    host = host or GitHubHost.from_config(notify.publish_config().get("image_host", {}))
    html = render(profile, store.posts(), store)
    path = page_path(profile)
    host.put(path, html.encode(), f"상품 페이지 {profile.id}")
    return host.pages_url(f"{profile.id}/")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "push"):
        s = sub.add_parser(name)
        s.add_argument("--account", required=True)
        if name == "build":
            s.add_argument("--out", type=Path, default=Path("out/link_page.html"))
    args = ap.parse_args(argv)
    profile = load_profile(args.account)
    store = Store(args.account)
    if args.cmd == "build":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(profile, store.posts(), store), encoding="utf-8")
        print(args.out)
        return 0
    try:
        print(push(profile, store))
    except (ValueError, RuntimeError, httpx.HTTPError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
