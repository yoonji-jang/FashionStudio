import base64
from datetime import date, timedelta

import httpx

from core.models import PostLink, PostRecord
from core.profile import load_profile
from core.store import Store
from tools import link_page
from tools.media_host import GitHubHost

DAY = date(2026, 9, 28)


def post(d: date, title: str) -> PostRecord:
    links = [
        PostLink(
            product="zigzag:1",
            category="아우터",
            shop="샵<1>",
            name="코트 & 자켓",
            url="https://zigzag.kr/catalog/products/1?a=1&b=2",
        )
    ]
    return PostRecord(date=d, title=title, permalink="https://www.instagram.com/p/x/", links=links)


def test_render_newest_first_escaped_and_limited(tmp_path):
    profile = load_profile("women")
    profile.link_page.keep = 2
    store = Store("women", root=tmp_path)
    posts = [post(DAY - timedelta(days=i), f"제목 {i}") for i in range(3)]
    html = link_page.render(profile, posts, store)
    assert html.index("제목 0") < html.index("제목 1") and "제목 2" not in html
    assert "샵&lt;1&gt;" in html and "코트 &amp; 자켓" in html
    assert 'href="https://zigzag.kr/catalog/products/1?a=1&amp;b=2"' in html
    assert profile.copy_rules.ad_notice not in html
    profile.monetize = "manual_link"
    assert profile.copy_rules.ad_notice in link_page.render(profile, posts, store)


def test_push_skips_when_unchanged(tmp_path):
    profile = load_profile("women")
    store = Store("women", root=tmp_path)
    store.save_posts([post(DAY, "제목")])
    files: dict[str, str] = {}
    puts: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        name = req.url.path.split("/contents/", 1)[1]
        if req.method == "GET":
            if name in files:
                return httpx.Response(200, json={"sha": "s1", "content": files[name]})
            return httpx.Response(404, json={"message": "Not Found"})
        import json

        files[name] = json.loads(req.content)["content"]
        puts.append(name)
        return httpx.Response(201, json={"content": {"sha": "s1"}, "commit": {"sha": "c"}})

    host = GitHubHost(
        "Me/FashionStudio-media", "t", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    url = link_page.push(profile, store, host)
    assert url == "https://me.github.io/FashionStudio-media/women/"
    assert puts == ["women/index.html"] and "제목" in base64.b64decode(files[puts[0]]).decode()
    link_page.push(profile, store, host)
    assert puts == ["women/index.html"]  # 같으면 다시 올리지 않음
