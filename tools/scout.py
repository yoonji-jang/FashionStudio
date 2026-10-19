"""상품 사이트 접근 도구. 상품 사이트는 이 모듈로만 읽는다.

규칙: 로그인 없이 공개 페이지만, 요청 사이 간격 유지(페이지 3초, 이미지 1초),
막히면(403·429·5xx) 잠시 쉬었다 다시 시도, 그래도 막히면 그 요청은 포기한다.

- 페이지·이미지: httpx
- 검색 결과: 검색 페이지를 브라우저(Playwright)로 열어, 페이지가 스스로 받아 오는 검색 결과를
  그대로 읽는다. Playwright 는 수집할 때만 필요하다 (`uv sync --group scout`).

사용법:
  python -m tools.scout image --url https://zigzag.kr/catalog/products/123 --out a.jpg
  python -m tools.scout search --keyword 레더자켓 --out out/scout/leather.json
  python -m tools.scout refs --account women --url https://www.29cm.co.kr/... --out out/refs
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
MIN_INTERVAL_SEC = 3.0  # 같은 사이트 페이지 요청 사이
IMAGE_INTERVAL_SEC = 1.0  # 이미지 서버(CDN) 요청 사이
RETRY_WAITS_SEC = (30, 90)  # 페이지가 막혔을 때 쉬는 시간
IMAGE_RETRY_WAITS_SEC = (5,)  # 사진은 한 번만 다시 시도 (사진 한 장 실패로 수집을 멈추지 않음)
RETRY_STATUS = {403, 429, 500, 502, 503, 504}
SEARCH_URL = "https://zigzag.kr/search?keyword={keyword}"

_last_request: dict[str, float] = {}

_META = re.compile(r"<meta\s+[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z:_-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')


class Blocked(RuntimeError):
    """사이트가 계속 막아서 요청을 포기함. 수집 전체를 멈추는 신호."""


class ImageUnavailable(RuntimeError):
    """사진 한 장을 받지 못함. 그 사진만 건너뛴다."""


_S3_IMAGE = re.compile(r"^https?://s3\.[a-z0-9-]+\.amazonaws\.com/(product-image\.s\.zigzag\.kr)/")


def normalize_image_url(url: str) -> str:
    """S3 원본 주소로 온 상품 사진을 CDN 주소로 바꾼다 (S3 직접 요청은 403)."""
    return _S3_IMAGE.sub(r"https://cf.\1/", url)


def og_image(page_html: str) -> str | None:
    for tag in _META.findall(page_html):
        attrs = {m[0].lower(): (m[2] or m[3]) for m in _ATTR.findall(tag)}
        key = attrs.get("property") or attrs.get("name")
        if key in ("og:image", "og:image:url") and attrs.get("content"):
            return html.unescape(attrs["content"])
    return None


def _is_image_host(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return "image" in host or url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".webp"))


def _wait_turn(url: str, sleep: Callable[[float], None] = time.sleep) -> None:
    host = urlsplit(url).hostname or ""
    gap = IMAGE_INTERVAL_SEC if _is_image_host(url) else MIN_INTERVAL_SEC
    wait = gap - (time.monotonic() - _last_request.get(host, 0.0))
    if wait > 0:
        sleep(wait)
    _last_request[host] = time.monotonic()


def _get(
    client: httpx.Client, url: str, sleep: Callable[[float], None] = time.sleep
) -> httpx.Response:
    """간격을 지키며 GET. 막히면 쉬었다 다시 시도한다. 404 등은 그대로 돌려준다.

    페이지가 계속 막히면 Blocked (수집 중단),
    사진이 계속 안 되면 ImageUnavailable (그 사진만 건너뜀).
    """
    image = _is_image_host(url)
    waits = IMAGE_RETRY_WAITS_SEC if image else RETRY_WAITS_SEC
    for attempt in range(len(waits) + 1):
        _wait_turn(url, sleep)
        try:
            resp = client.get(url)
        except httpx.TransportError as e:
            if attempt == len(waits):
                raise (ImageUnavailable(f"{e}: {url}") if image else e) from e
            sleep(waits[attempt])
            continue
        if resp.status_code not in RETRY_STATUS:
            return resp
        if attempt == len(waits):
            err = ImageUnavailable if image else Blocked
            raise err(f"{resp.status_code} 로 계속 막힘: {url}")
        sleep(waits[attempt])
    raise AssertionError("unreachable")


def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"},
        follow_redirects=True,
        timeout=30,
    )


def fetch_page(c: httpx.Client, url: str) -> tuple[int, str]:
    """(상태 코드, 본문). 판매 종료로 사라진 페이지는 404 가 올 수 있다."""
    resp = _get(c, url)
    return resp.status_code, resp.text


def fetch_bytes(c: httpx.Client, url: str) -> bytes:
    resp = _get(c, url)
    if resp.status_code != 200:
        if _is_image_host(url):
            raise ImageUnavailable(f"{resp.status_code}: {url}")
        resp.raise_for_status()
    return resp.content


def is_placeholder(img_url: str) -> bool:
    """판매가 끝난 페이지는 og:image 로 지그재그 안내 배너(/cms/)를 준다 (run-13 팔찌)."""
    return "/cms/" in img_url


def fetch_product_image(url: str, out: Path) -> tuple[Path, str]:
    """상품 페이지의 대표 이미지를 받아 저장한다. (저장 경로, 이미지 URL) 반환."""
    with client() as c:
        page = _get(c, url)
        page.raise_for_status()
        img_url = og_image(page.text)
        if not img_url:
            raise RuntimeError(f"대표 이미지(og:image)를 찾지 못했습니다: {url}")
        img_url = str(page.url.join(img_url))
        if is_placeholder(img_url):
            raise RuntimeError(f"상품 사진이 아닌 안내 이미지입니다 (판매 종료?): {img_url}")
        data = fetch_bytes(c, img_url)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out, img_url


def search_responses(
    keywords: list[str], min_items: int = 40, max_scrolls: int = 4, marker: str = "GetSearchResult"
) -> dict[str, list[dict]]:
    """검색 페이지를 브라우저로 열고 스크롤해, 페이지가 받아 온 검색 결과(JSON)를 모은다.

    반환: {검색어: [응답 JSON, ...]}. 결과를 해석하는 일은 sources/<쇼핑몰>.py 가 한다.
    """
    from playwright.sync_api import sync_playwright

    results: dict[str, list[dict]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            user_agent=USER_AGENT, locale="ko-KR", viewport={"width": 1280, "height": 900}
        )
        bucket: list[dict] = []

        def on_response(resp) -> None:
            if marker in resp.url and resp.status == 200:
                try:
                    bucket.append(resp.json())
                except Exception:  # noqa: BLE001 - 깨진 응답은 버린다
                    pass

        page.on("response", on_response)
        for kw in keywords:
            bucket.clear()
            url = SEARCH_URL.format(keyword=quote(kw))
            _wait_turn(url)
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                for _ in range(max_scrolls):
                    if _count_cards(bucket) >= min_items:
                        break
                    page.mouse.wheel(0, 5000)
                    page.wait_for_timeout(int(MIN_INTERVAL_SEC * 1000))
            except Exception as e:  # noqa: BLE001 - 한 검색어 실패로 전체를 멈추지 않음
                print(f"검색 실패 ({kw}): {e}", file=sys.stderr)
            results[kw] = list(bucket)
        browser.close()
    return results


def _count_cards(responses: list[dict]) -> int:
    return sum(json.dumps(r).count('"catalog_product_id"') for r in responses)


def robots_allows(c: httpx.Client, url: str) -> bool:
    """robots.txt 가 일반 봇(*)에게 이 주소를 허용하는지. 못 읽으면 막힌 것으로 본다."""
    parts = urlsplit(url)
    try:
        status, text = fetch_page(c, f"{parts.scheme}://{parts.netloc}/robots.txt")
    except (Blocked, httpx.HTTPError):
        return False
    if status != 200:
        return status == 404
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return robots_rules_allow(text, path)


def robots_rules_allow(text: str, path: str) -> bool:
    """User-agent: * 묶음에서 가장 길게 맞는 규칙을 따른다 (Google 방식, 같으면 허용)."""
    rules: list[tuple[str, bool]] = []
    agents: list[str] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(val)
        elif key in ("allow", "disallow"):
            in_rules = True
            if "*" in agents and val:
                rules.append((val, key == "allow"))
    best: tuple[int, bool] = (-1, True)
    for rule, allow in rules:
        pattern = re.escape(rule).replace(r"\*", ".*")
        if pattern.endswith(r"\$"):
            pattern = pattern[:-2] + "$"
        if re.match(pattern, path) and (len(rule), allow) > best:
            best = (len(rule), allow)
    return best[1]


def host_allowed(url: str, sites: list[str]) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == s or host.endswith("." + s) for s in sites)


def page_image_urls(
    urls: list[str], min_width: int = 400, max_scrolls: int = 4
) -> dict[str, list[str]]:
    """공개 코디 페이지를 브라우저로 열고 스크롤해, 화면에 그려진 큰 사진 주소를 모은다."""
    from playwright.sync_api import sync_playwright

    out: dict[str, list[str]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            user_agent=USER_AGENT, locale="ko-KR", viewport={"width": 1280, "height": 900}
        )
        for url in urls:
            _wait_turn(url)
            try:
                # networkidle 은 분석 스크립트가 계속 통신하는 쇼핑몰에서 60초를 다 기다리고
                # 실패한다 (2026-10-13 스탠스트·시엔느 "사진 없음"). 본문이 뜨면 바로 스크롤한다
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(int(MIN_INTERVAL_SEC * 1000))
                for _ in range(max_scrolls):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(int(MIN_INTERVAL_SEC * 1000))
                found = page.evaluate(
                    "(w) => [...document.images].filter(i => i.naturalWidth >= w)"
                    ".map(i => i.currentSrc || i.src)",
                    min_width,
                )
                # 아직 안 불린 사진 (cafe24 ec-data-src 등, 2026-10-13 스탠스트·시엔느 "사진 없음")
                found += page.evaluate(
                    "() => [...document.images].map(i => i.getAttribute('ec-data-src')"
                    " || i.getAttribute('data-src') || i.getAttribute('data-original'))"
                    ".filter(Boolean).map(s => new URL(s, location.href).href)"
                )
            except Exception as e:  # noqa: BLE001 - 한 페이지 실패로 전체를 멈추지 않음
                print(f"페이지 실패 ({url}): {e}", file=sys.stderr)
                found = []
            out[url] = list(dict.fromkeys(u for u in found if u.startswith("http")))
        browser.close()
    return out


def page_links(
    url: str, sites: list[str], limit: int = 200, scrolls: int = 0
) -> list[tuple[str, str]]:
    """참고 코디용: 쇼핑몰 페이지의 같은 사이트 링크(메뉴 이름, 주소)를 모은다 (2026-10-13 사용자:
    첫 화면 한 장이 아니라 베스트·카테고리 메뉴와 상품 페이지까지 들어가 쇼핑몰이 입힌 코디를 보라).
    페이지 원본은 저장하지 않고 링크 글자와 주소만 돌려준다.
    이 페이지를 robots.txt 가 막으면 빈 목록."""
    from playwright.sync_api import sync_playwright

    if not host_allowed(url, sites):
        return []
    with client() as c:
        if not robots_allows(c, url):
            return []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            user_agent=USER_AGENT, locale="ko-KR", viewport={"width": 1280, "height": 900}
        )
        _wait_turn(url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(int(MIN_INTERVAL_SEC * 1000))
            for _ in range(scrolls):  # 상품 목록이 스크롤해야 더 불리는 쇼핑몰
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1500)
            found = page.evaluate(
                "() => [...document.querySelectorAll('a[href]')].map(a => ["
                "(a.innerText || a.title || '').trim().replace(/\\s+/g, ' ').slice(0, 40), a.href])"
            )
            # 메뉴를 못 찾을 때 이유를 보려고 (봇 확인 화면으로 옮겨 갔는지, 링크가 몇 개인지)
            print(
                f"{url} → {page.url} 제목 {page.title()[:40]!r}: 링크 {len(found)}개",
                file=sys.stderr,
            )
        except Exception as e:  # noqa: BLE001 - 실패하면 빈 목록
            print(f"페이지 실패 ({url}): {e}", file=sys.stderr)
            found = []
        browser.close()
    out: dict[str, str] = {}
    for text, href in found:
        href = href.split("#", 1)[0]
        if href.startswith("http") and host_allowed(href, sites) and href not in out:
            out[href] = text
    return [(t, h) for h, t in out.items()][:limit]


MENU_WORDS = re.compile(
    r"best|베스트|new|신상|outer|아우터|top|상의|bottom|하의|skirt|스커트|pants|팬츠|knit|니트|"
    r"dress|원피스|코디|look|룩|most|인기|weekly",
    re.IGNORECASE,
)
SKIP_WORDS = re.compile(
    r"login|join|member|cart|order|mypage|board|notice|review|event|faq|qna|privacy|agreement|"
    r"로그인|회원|장바구니|주문|공지|리뷰|후기|이벤트|문의|약관|개인정보",
    re.IGNORECASE,
)
# 상품 상세 주소. cafe24 의 상품 목록(/product/list.html?cate_no=…)은 메뉴라 빼야 한다
# (2026-10-19 쇼핑몰 시험: 메뉴가 모두 list.html 인 9곳이 메뉴 0개로 끝남).
# /products/ 는 해외형 쇼핑몰
PRODUCT_LINK = re.compile(
    r"/product/(?!list\.html)[^?#]|detail|/goods/|goods_no=|product_no=|/products/[^/?#]+",
    re.IGNORECASE,
)


def product_key(href: str) -> str:
    """같은 상품의 다른 주소(카테고리 경로가 붙은 cafe24 주소 등)를 하나로."""
    m = re.search(r"/product/[^/]*/(\d+)|product_no=(\d+)|/goods/(\d+)|/products/([^/?#]+)", href)
    return next((g for g in m.groups() if g), href) if m else href.split("?", 1)[0]


def diagnose(home: str, sites: list[str]) -> str:
    """메뉴를 하나도 못 찾았을 때 이유 (2026-10-19 쇼핑몰 시험: 12곳이 이유 없이 0).
    robots.txt 상태, 첫 화면 응답, 옮겨 간 주소만 본다 (본문은 저장하지 않는다)."""
    parts = urlsplit(home)
    with client() as c:
        try:
            status, text = fetch_page(c, f"{parts.scheme}://{parts.netloc}/robots.txt")
        except (Blocked, httpx.HTTPError) as e:
            return f"robots.txt 못 읽음 ({type(e).__name__}: {e})"[:200]
        if status not in (200, 404):
            return f"robots.txt 응답 {status} (못 읽으면 막힌 것으로 봄)"
        if status == 200 and not robots_rules_allow(text, parts.path or "/"):
            return "robots.txt 가 첫 화면을 막음"
        try:
            resp = _get(c, home)
        except (Blocked, httpx.HTTPError) as e:
            return f"첫 화면 못 엶 ({type(e).__name__}: {e})"[:200]
        final = str(resp.url)
        if resp.status_code != 200:
            return f"첫 화면 응답 {resp.status_code} ({final})"
        if not host_allowed(final, sites):
            return f"다른 사이트로 옮겨 감: {final} (scout_sites 에 없음)"
    return "첫 화면은 열리지만 메뉴 링크를 못 찾음 (화면을 스크립트로 그리는 쇼핑몰일 수 있음)"


def crawl(
    home: str, sites: list[str], out: Path, menus: int = 5, products: int = 15, max_images: int = 16
) -> list[dict]:
    """쇼핑몰 하나를 메뉴 → 상품 페이지 순서로 돌며 상품 페이지마다 번호 붙인 확인표를 만든다.

    2026-10-13 사용자: 참고 코디를 한 주 100벌쯤 모으기. Claude 가 메뉴를 하나씩 누르는 대신
    코드가 돌고, Claude 는 확인표만 본다. robots.txt·허용 사이트는 page_links·ref_sheets 가 지킨다.
    페이지·사진 원본은 저장하지 않는다."""
    picked: list[str] = []
    for text, href in page_links(home, sites):
        if len(picked) >= menus:
            break
        if (
            SKIP_WORDS.search(text + href)
            or PRODUCT_LINK.search(href)
            or href.rstrip("/") == home.rstrip("/")
        ):
            continue
        if MENU_WORDS.search(text) or MENU_WORDS.search(href):
            picked.append(href)
    lists = [
        [h for _, h in page_links(m, sites, limit=400, scrolls=3) if PRODUCT_LINK.search(h)]
        for m in picked
    ]
    seen: set[str] = set()
    pages: list[str] = []
    width = max(map(len, lists), default=0)
    for row in zip(*[lst + [""] * (width - len(lst)) for lst in lists], strict=True):
        for href in row:  # 메뉴마다 번갈아 (한 메뉴 상품만 몰리지 않게)
            if href and product_key(href) not in seen and len(pages) < products:
                seen.add(product_key(href))
                pages.append(href)
    report = ref_sheets(pages, sites, out, max_images) if pages else []
    info: dict = {"home": home, "menus": picked, "pages": pages}
    if not picked:
        info["why"] = diagnose(home, sites)
    elif not pages:
        info["why"] = "메뉴는 찾았지만 상품 페이지 링크를 못 찾음 (상품 주소 모양이 다를 수 있음)"
    (out / "crawl.json").parent.mkdir(parents=True, exist_ok=True)
    (out / "crawl.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    if info.get("why"):
        print(f"이유: {info['why']}")
    return report


def contact_sheet(images: list[bytes], cols: int = 4, thumb: int = 360, numbered: bool = False):
    """사진 여러 장을 한 장의 확인표로 (원본은 저장하지 않는다).
    numbered 면 칸마다 왼쪽 위에 1부터 번호를 적는다 (refs.json 의 images 순서와 같음)."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    tiles = []
    for data in images:
        try:
            im = Image.open(BytesIO(data)).convert("RGB")
        except Exception:  # noqa: BLE001 - 깨진 사진은 건너뜀
            continue
        im.thumbnail((thumb, int(thumb * 1.5)))
        tiles.append(im)
    if not tiles:
        return None
    rows = (len(tiles) + cols - 1) // cols
    cell_h = max(t.height for t in tiles)
    sheet = Image.new("RGB", (cols * thumb, rows * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    for i, t in enumerate(tiles):
        x, y = (i % cols) * thumb, (i // cols) * cell_h
        sheet.paste(t, (x, y))
        if numbered:
            draw.rectangle((x, y, x + 44, y + 30), fill="black")
            draw.text((x + 8, y + 6), str(i + 1), fill="white", font_size=20)
    return sheet


def ref_sheets(urls: list[str], sites: list[str], out: Path, max_images: int = 12) -> list[dict]:
    """참고 코디용: 허용한 사이트의 공개 코디 페이지 사진을 확인표로 만든다 (2026-10-07).

    profile themes.ref_search.scout_sites 에 있는 사이트만, robots.txt 가 허용한 주소만 연다.
    사진 원본·페이지 원본은 저장하지 않고 확인표(out/…/sheet_N.jpg)만 남긴다.
    """
    out.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    with client() as c:
        ok = []
        for url in urls:
            if not host_allowed(url, sites):
                report.append({"url": url, "skipped": "허용한 사이트가 아님"})
            elif not robots_allows(c, url):
                report.append({"url": url, "skipped": "robots.txt 가 허용하지 않음"})
            else:
                ok.append(url)
        found = page_image_urls(ok) if ok else {}
        for n, url in enumerate(ok, 1):
            imgs = [u for u in found.get(url, []) if host_allowed(u, sites)][:max_images]
            data, kept = [], []
            for u in imgs:
                try:
                    b = fetch_bytes(c, u)
                except (ImageUnavailable, httpx.HTTPError):
                    continue
                if len(b) >= 15000:  # 아이콘·로고처럼 작은 사진은 뺀다
                    data.append(b)
                    kept.append(u)
            sheet = contact_sheet(data, numbered=True)
            if sheet is None:
                report.append({"url": url, "skipped": "사진 없음"})
                continue
            path = out / f"sheet_{n}.jpg"
            sheet.save(path, quality=80)
            # 확인표 칸 번호(1부터) 순서의 사진 주소.
            # 참고 코디에 사진 주소만 남길 때 쓴다 (사진은 저장 안 함)
            report.append({"url": url, "sheet": str(path), "images": kept})
    (out / "refs.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return report


def ref_photo(url: str, sites: list[str], dest: Path) -> Path:
    """참고 코디 사진 한 장 받기 (후보 시트 옆에 붙임). 허용한 사이트·robots.txt 가 허용한 주소만.
    dest 는 out/ 아래 (저장소·결과 브랜치에 올리지 않는 곳)."""
    # 지그재그 상품 사진(창고 상품의 착용컷)은 상품 창고 사진과 같은 방법으로 받는다
    zigzag = host_allowed(url, ["zigzag.kr"])
    if not (zigzag or host_allowed(url, sites)):
        raise ImageUnavailable(f"허용한 사이트가 아님: {url}")
    if not dest.exists():
        with client() as c:
            if not zigzag and not robots_allows(c, url):
                raise ImageUnavailable(f"robots.txt 가 허용하지 않음: {url}")
            data = fetch_bytes(c, normalize_image_url(url) if zigzag else url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("image", help="상품 페이지 대표 이미지 받기")
    p.add_argument("--url", required=True)
    p.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("search", help="검색 결과 원본 JSON 저장 (구조 확인용)")
    s.add_argument("--keyword", required=True)
    s.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("refs", help="참고 코디: 공개 코디 페이지 사진을 확인표로 (원본 저장 안 함)")
    r.add_argument("--url", action="append", required=True)
    r.add_argument("--account", required=True, help="profile themes.ref_search.scout_sites 만 연다")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--max-images", type=int, default=12)
    k = sub.add_parser("links", help="참고 코디: 쇼핑몰 페이지의 메뉴·상품 링크 (원본 저장 안 함)")
    k.add_argument("--url", required=True)
    k.add_argument("--account", required=True, help="profile themes.ref_search.scout_sites 만 연다")
    f = sub.add_parser("refphoto", help="참고 코디 사진 한 장 (out/ 에만)")
    f.add_argument("--url", required=True)
    f.add_argument("--account", required=True, help="profile themes.ref_search.scout_sites 만 연다")
    f.add_argument("--out", type=Path, required=True)
    w = sub.add_parser(
        "crawl", help="참고 코디: 쇼핑몰 메뉴 → 상품 페이지마다 확인표 (원본 저장 안 함)"
    )
    w.add_argument("--site", required=True, help="profile scout_sites 의 쇼핑몰 (예 woonan.co.kr)")
    w.add_argument("--account", required=True)
    w.add_argument("--out", type=Path, required=True)
    w.add_argument("--menus", type=int, default=5)
    w.add_argument("--products", type=int, default=15)
    args = ap.parse_args(argv)

    if args.cmd == "crawl":
        from core.profile import load_profile

        rs = load_profile(args.account).themes.ref_search
        sites = rs.scout_sites if rs else []
        rows = crawl(f"https://{args.site}/", sites, args.out, args.menus, args.products)
        made = [r for r in rows if r.get("sheet")]
        print(f"{args.site}: 상품 페이지 {len(rows)}곳, 확인표 {len(made)}장")
        return 0
    if args.cmd == "refphoto":
        from core.profile import load_profile

        rs = load_profile(args.account).themes.ref_search
        print(ref_photo(args.url, rs.scout_sites if rs else [], args.out))
        return 0
    if args.cmd == "links":
        from core.profile import load_profile

        rs = load_profile(args.account).themes.ref_search
        for text, href in page_links(args.url, rs.scout_sites if rs else []):
            print(f"{text}\t{href}")
        return 0
    if args.cmd == "refs":
        from core.profile import load_profile

        rs = load_profile(args.account).themes.ref_search
        sites = rs.scout_sites if rs else []
        if not sites:
            print("profile themes.ref_search.scout_sites 가 비어 있다", file=sys.stderr)
            return 1
        for row in ref_sheets(args.url, sites, args.out, args.max_images):
            print("\t".join(str(v) for v in row.values()))
        return 0
    if args.cmd == "image":
        path, img_url = fetch_product_image(args.url, args.out)
        print(f"{path}\t{img_url}")
    else:
        data = search_responses([args.keyword])[args.keyword]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{args.out}\t응답 {len(data)}개, 상품 {_count_cards(data)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
