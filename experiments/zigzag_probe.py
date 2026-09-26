"""4단계 사전 조사: 지그재그 공개 검색·상품 페이지 구조를 GitHub Actions 에서 확인한다 (일회성).

클라우드 개발 환경에서는 zigzag.kr 이 막혀 있어서 Actions 러너에서 돌리고,
결과를 scout-results 브랜치에 커밋한다. 수집기(sources/zigzag.py)를 만들기 위한 조사.
로그인 없이 공개 페이지만, 요청 사이 3초 이상 쉰다. 쿠키·요청 헤더는 기록하지 않는다.

사용법 (Actions): python experiments/zigzag_probe.py --out out/probe
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
KEYWORDS = ["레더자켓", "와이드 슬랙스"]
PAUSE = 3


def probe_browser(out: Path) -> list[str]:
    from playwright.sync_api import sync_playwright

    log: list[dict] = []

    def on_response(resp):
        url = resp.url
        if "zigzag" not in url or not any(k in url for k in ("api", "graphql")):
            return
        entry = {"url": url, "method": resp.request.method, "status": resp.status}
        try:
            entry["post"] = (resp.request.post_data or "")[:20000]
            entry["body"] = resp.text()[:80000]
        except Exception as e:  # noqa: BLE001
            entry["error"] = str(e)
        log.append(entry)

    links: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            user_agent=UA, locale="ko-KR", viewport={"width": 1280, "height": 900}
        )
        page.on("response", on_response)
        for i, kw in enumerate(KEYWORDS):
            try:
                page.goto(
                    f"https://zigzag.kr/search?keyword={kw}",
                    wait_until="networkidle",
                    timeout=60000,
                )
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(4000)
                (out / f"search_{i}.html").write_text(page.content(), encoding="utf-8")
                page.screenshot(path=str(out / f"search_{i}.png"))
                found = page.eval_on_selector_all(
                    "a[href*='/catalog/products/']", "els => els.map(e => e.href)"
                )
                found = list(dict.fromkeys(found))
                (out / f"search_{i}_links.json").write_text(json.dumps(found, indent=1))
                links += found[:3]
                print(f"search {kw}: {len(found)} links")
            except Exception as e:  # noqa: BLE001
                print(f"search {kw}: {e}")
            time.sleep(PAUSE)
        browser.close()
    (out / "api_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"api responses logged: {len(log)}")
    return links


def probe_products(out: Path, links: list[str]) -> None:
    """상품 페이지를 브라우저 없이 받아 JSON-LD·og·__NEXT_DATA__ 가 있는지 본다."""
    with httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "ko-KR"}, follow_redirects=True, timeout=30
    ) as c:
        for i, url in enumerate(links[:4]):
            try:
                r = c.get(url)
                (out / f"product_{i}.html").write_text(r.text, encoding="utf-8")
                ld = re.findall(
                    r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", r.text, re.S
                )
                nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
                print(
                    f"product {url}: {r.status_code} len={len(r.text)} ld={len(ld)} next={bool(nd)}"
                )
                if nd:
                    (out / f"product_{i}_next.json").write_text(nd.group(1), encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                print(f"product {url}: {e}")
            time.sleep(PAUSE)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    probe_products(args.out, probe_browser(args.out))


if __name__ == "__main__":
    main()
