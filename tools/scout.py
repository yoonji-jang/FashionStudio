"""상품 사이트 접근 도구. 상품 페이지는 이 모듈로만 읽는다.

지금(2단계)은 공개 상품 페이지의 대표 이미지(og:image)만 받는다.
4단계에서 검색·상세 수집(sources/)이 이 위에 붙는다.
규칙: 로그인 없이 공개 페이지만, 요청 사이 간격 유지.

사용법:
  python -m tools.scout image --url https://zigzag.kr/catalog/products/123 --out a.jpg
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from pathlib import Path

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
MIN_INTERVAL_SEC = 3.0
_last_request = 0.0

_META = re.compile(r"<meta\s+[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z:_-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')


def og_image(page_html: str) -> str | None:
    for tag in _META.findall(page_html):
        attrs = {m[0].lower(): (m[2] or m[3]) for m in _ATTR.findall(tag)}
        key = attrs.get("property") or attrs.get("name")
        if key in ("og:image", "og:image:url") and attrs.get("content"):
            return html.unescape(attrs["content"])
    return None


def _get(client: httpx.Client, url: str) -> httpx.Response:
    global _last_request
    wait = MIN_INTERVAL_SEC - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    resp = client.get(url)
    resp.raise_for_status()
    return resp


def is_placeholder(img_url: str) -> bool:
    """판매가 끝난 페이지는 og:image 로 지그재그 안내 배너(/cms/)를 준다 (run-13 팔찌)."""
    return "/cms/" in img_url


def fetch_product_image(url: str, out: Path) -> tuple[Path, str]:
    """상품 페이지의 대표 이미지를 받아 저장한다. (저장 경로, 이미지 URL) 반환."""
    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"},
        follow_redirects=True,
        timeout=30,
    ) as client:
        page = _get(client, url)
        img_url = og_image(page.text)
        if not img_url:
            raise RuntimeError(f"대표 이미지(og:image)를 찾지 못했습니다: {url}")
        img_url = str(page.url.join(img_url))
        if is_placeholder(img_url):
            raise RuntimeError(f"상품 사진이 아닌 안내 이미지입니다 (판매 종료?): {img_url}")
        data = _get(client, img_url).content
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out, img_url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("image", help="상품 페이지 대표 이미지 받기")
    p.add_argument("--url", required=True)
    p.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    path, img_url = fetch_product_image(args.url, args.out)
    print(f"{path}\t{img_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
