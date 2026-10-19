"""참고 코디용 scout refs: 허용 사이트·robots.txt 확인, 확인표 만들기."""

from io import BytesIO

import httpx
from PIL import Image

from tools import scout

ROBOTS = "User-agent: *\nAllow: /\nDisallow: /my-page/\nDisallow: /content/post/preview\n"


def _client(robots_status=200):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(robots_status, text=ROBOTS)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_host_allowed():
    sites = ["29cm.co.kr"]
    assert scout.host_allowed("https://www.29cm.co.kr/content/post/1", sites)
    assert scout.host_allowed("https://img.29cm.co.kr/cms/a.jpg", sites)
    assert not scout.host_allowed("https://www.musinsa.com/snap/1", sites)
    assert not scout.host_allowed("https://evil29cm.co.kr/a", sites)


def test_robots_allows(monkeypatch):
    monkeypatch.setattr(scout, "_wait_turn", lambda url, sleep=None: None)
    c = _client()
    assert scout.robots_allows(c, "https://www.29cm.co.kr/content/post/1")
    assert not scout.robots_allows(c, "https://www.29cm.co.kr/content/post/preview?id=1")
    assert not scout.robots_allows(c, "https://www.29cm.co.kr/my-page/")
    # robots.txt 가 없으면(404) 허용, 막혀 있으면(403) 열지 않음
    assert scout.robots_allows(_client(404), "https://a.example/x")
    monkeypatch.setattr(scout, "RETRY_WAITS_SEC", ())
    assert not scout.robots_allows(_client(403), "https://a.example/x")


def test_contact_sheet_skips_broken():
    buf = BytesIO()
    Image.new("RGB", (800, 1200), "red").save(buf, "JPEG")
    sheet = scout.contact_sheet(
        [buf.getvalue(), b"not an image", buf.getvalue()], cols=2, thumb=100
    )
    assert sheet is not None and sheet.size == (200, 150)
    assert scout.contact_sheet([b"x"]) is None


def test_product_link_skips_cafe24_list_pages():
    from tools.scout import PRODUCT_LINK, product_key

    menus = [
        "https://the-ore.kr/product/list.html?cate_no=24",
        "https://woonan.co.kr/category/outer/24/",
    ]
    products = [
        "https://the-ore.kr/product/detail.html?product_no=123&cate_no=24",
        "https://woonan.co.kr/product/니트-베스트/456/category/24/",
        "https://lowclassic.com/products/wool-coat",
    ]
    assert not any(PRODUCT_LINK.search(u) for u in menus)
    assert all(PRODUCT_LINK.search(u) for u in products)
    assert product_key(products[0]) == "123" and product_key(products[2]) == "wool-coat"
