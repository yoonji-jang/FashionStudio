"""상품 사이트 접근 도구. 상품 사이트는 이 모듈로만 읽는다.

규칙: 로그인 없이 공개 페이지만, 요청 사이 간격 유지(페이지 3초, 이미지 1초),
막히면(403·429·5xx) 잠시 쉬었다 다시 시도, 그래도 막히면 그 요청은 포기한다.

- 페이지·이미지: httpx
- 검색 결과: 검색 페이지를 브라우저(Playwright)로 열어, 페이지가 스스로 받아 오는 검색 결과를
  그대로 읽는다. Playwright 는 수집할 때만 필요하다 (`uv sync --group scout`).

사용법:
  python -m tools.scout image --url https://zigzag.kr/catalog/products/123 --out a.jpg
  python -m tools.scout search --keyword 레더자켓 --out out/scout/leather.json
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("image", help="상품 페이지 대표 이미지 받기")
    p.add_argument("--url", required=True)
    p.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("search", help="검색 결과 원본 JSON 저장 (구조 확인용)")
    s.add_argument("--keyword", required=True)
    s.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

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
