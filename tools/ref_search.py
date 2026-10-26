r"""주제별 참고 코디 이미지 검색.

2026-10-26 사용자: 쇼핑몰 주간 수집 대신 주제마다 검색해서 고르기.

공식 검색 API 만 쓴다 (봇 검색 허용, 무료):
- daum: 카카오 Daum 이미지 검색 (KAKAO_REST_API_KEY, 하루 3만 번)
- naver: 네이버 클라우드 API HUB 이미지 검색 (NAVER_API_KEY_ID, NAVER_API_KEY)
키가 없는 검색은 건너뛴다. 검색 결과에 나온 핀터레스트 사진도 쓴다
(사용자 결정, 페이지는 열지 않고 사진만).

코드가 검색 → 걸러내기(profile avoid_sites, 작은 사진, 같은 사진) → 번호 붙인 확인표를 만들고,
Claude 는 확인표만 보고 참고 코디를 고른다. 사진 원본·검색 응답은 저장하지 않는다.
글 주소(블로그·카페)와 작성자 이름은 남기지 않는다 (개인정보).

사용법:
  python -m tools.ref_search sheets --account women --out out/ref_search \
      --query "롱스커트 가을 코디" --query "롱스커트 데일리룩"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from tools import scout

DAUM_URL = "https://dapi.kakao.com/v2/search/image"
NAVER_URL = "https://naverapihub.apigw.ntruss.com/search/v1/image"
PER_SHEET = 16  # 확인표 한 장에 4x4
MIN_BYTES = 15000  # 아이콘·작은 사진은 뺀다
MAX_BYTES = 8_000_000
MIN_RATIO = 0.9  # 세로/가로. 코디 사진은 대개 세로라 아주 넓은 사진(배너·콜라주)은 뺀다


@dataclass
class Hit:
    image: str  # 사진 주소
    width: int
    height: int
    site: str  # 출처 이름 (쇼핑몰·블로그 서비스 이름. 사람 이름은 쓰지 않는다)
    query: str
    engine: str


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def daum(c: httpx.Client, key: str, query: str, size: int) -> list[Hit]:
    r = c.get(
        DAUM_URL,
        params={"query": query, "size": min(size, 80), "sort": "accuracy"},
        headers={"Authorization": f"KakaoAK {key}"},
    )
    r.raise_for_status()
    return [
        Hit(
            d.get("image_url", ""),
            _int(d.get("width")),
            _int(d.get("height")),
            d.get("display_sitename", "") or d.get("collection", ""),
            query,
            "daum",
        )
        for d in r.json().get("documents", [])
    ]


def naver(c: httpx.Client, key_id: str, key: str, query: str, size: int) -> list[Hit]:
    r = c.get(
        NAVER_URL,
        params={"query": query, "display": min(size, 100), "sort": "sim", "filter": "large"},
        headers={"X-NCP-APIGW-API-KEY-ID": key_id, "X-NCP-APIGW-API-KEY": key},
    )
    r.raise_for_status()
    return [
        Hit(
            d.get("link", ""),
            _int(d.get("sizewidth")),
            _int(d.get("sizeheight")),
            "네이버 검색",
            query,
            "naver",
        )
        for d in r.json().get("items", [])
    ]


def keep(hit: Hit, avoid: list[str], min_height: int) -> bool:
    if not hit.image.startswith(("http://", "https://")) or scout.host_allowed(hit.image, avoid):
        return False
    if hit.height and hit.height < min_height:
        return False
    return not (hit.width and hit.height and hit.height / hit.width < MIN_RATIO)


def search(
    queries: list[str],
    engines: list[str],
    per_query: int,
    avoid: list[str],
    min_height: int,
    env: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> tuple[list[Hit], list[str]]:
    """(걸러낸 결과, 알림). 같은 사진 주소는 한 번만.
    검색어·검색 엔진 결과를 번갈아 섞어 앞 확인표가 한 검색어로만 차지 않게."""
    env = os.environ if env is None else env
    notes: list[str] = []
    calls = []
    if "daum" in engines:
        if env.get("KAKAO_REST_API_KEY"):
            calls.append(("daum", lambda c, q: daum(c, env["KAKAO_REST_API_KEY"], q, per_query)))
        else:
            notes.append("daum: KAKAO_REST_API_KEY 없음, 건너뜀")
    if "naver" in engines:
        if env.get("NAVER_API_KEY_ID") and env.get("NAVER_API_KEY"):
            ids = (env["NAVER_API_KEY_ID"], env["NAVER_API_KEY"])
            calls.append(("naver", lambda c, q: naver(c, *ids, q, per_query)))
        else:
            notes.append("naver: NAVER_API_KEY_ID·NAVER_API_KEY 없음, 건너뜀")
    per: list[list[Hit]] = []
    c = client or httpx.Client(timeout=20)
    try:
        for q in queries:
            for name, call in calls:
                try:
                    per.append(call(c, q))
                except httpx.HTTPError as e:
                    # 주소·헤더에 키가 있어 상태만 남긴다
                    status = getattr(getattr(e, "response", None), "status_code", "연결 실패")
                    notes.append(f"{name} '{q}': 검색 실패 ({status})")
    finally:
        if client is None:
            c.close()
    seen: set[str] = set()
    hits: list[Hit] = []
    for i in range(max((len(p) for p in per), default=0)):
        for p in per:
            if i < len(p) and p[i].image not in seen and keep(p[i], avoid, min_height):
                seen.add(p[i].image)
                hits.append(p[i])
    return hits, notes


def ahash(data: bytes) -> str | None:
    """같은 사진(크기·압축만 다름)을 찾는 8x8 평균 해시."""
    from PIL import Image

    try:
        im = Image.open(BytesIO(data)).convert("L").resize((8, 8))
    except Exception:  # noqa: BLE001 - 깨진 사진
        return None
    px = im.tobytes()
    avg = sum(px) / len(px)
    return hashlib.md5(bytes(p > avg for p in px)).hexdigest()


def fetch(c: httpx.Client, url: str) -> bytes | None:
    """사진 한 장. 서버마다 간격(1초)을 두고 한 번만 시도한다 (실패하면 그 사진만 뺀다)."""
    host = urlsplit(url).hostname or ""
    wait = scout.IMAGE_INTERVAL_SEC - (time.monotonic() - scout._last_request.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    scout._last_request[host] = time.monotonic()
    try:
        r = c.get(url)
    except httpx.HTTPError:
        return None
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not ctype.startswith("image/"):
        return None
    if not MIN_BYTES <= len(r.content) <= MAX_BYTES:
        return None
    return r.content


def sheets(
    hits: list[Hit], out: Path, limit: int = 320, client: httpx.Client | None = None
) -> list[dict]:
    """번호 붙인 확인표(out/sheet_N.jpg)와 out/refs.json (칸 번호 순 사진 주소·출처).
    refs.json 은 tools.scout crawl 확인표와 같은 꼴이라 plan-themes 스킬이 그대로 읽는다."""
    out.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    data: list[bytes] = []
    kept: list[Hit] = []
    hashes: set[str] = set()

    def flush() -> None:
        if not data:
            return
        n = len(report) + 1
        sheet = scout.contact_sheet(data, numbered=True)
        if sheet is not None:
            path = out / f"sheet_{n}.jpg"
            sheet.save(path, quality=80)
            report.append(
                {
                    "url": "이미지 검색",
                    "sheet": str(path),
                    "images": [h.image for h in kept],
                    "sites": [h.site for h in kept],
                    "queries": [h.query for h in kept],
                }
            )
        data.clear()
        kept.clear()

    with client or scout.client() as c:
        for h in hits[:limit]:
            b = fetch(c, h.image)
            if b is None:
                continue
            key = ahash(b)
            if key is None or key in hashes:
                continue
            hashes.add(key)
            data.append(b)
            kept.append(h)
            if len(data) == PER_SHEET:
                flush()
        flush()
    text = json.dumps(report, ensure_ascii=False, indent=1)
    (out / "refs.json").write_text(text, encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheets", help="검색해서 확인표 만들기 (원본 저장 안 함)")
    s.add_argument("--account", required=True)
    s.add_argument("--query", action="append", required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--limit", type=int, default=320, help="확인표에 넣을 최대 사진 수")
    args = ap.parse_args(argv)

    from core.profile import load_profile

    rs = load_profile(args.account).themes.ref_search
    if rs is None or not rs.image_search:
        print("profile themes.ref_search.image_search 가 비어 있다", file=sys.stderr)
        return 1
    queries = [q.strip() for q in args.query if q.strip()]
    hits, notes = search(
        queries, rs.image_search, rs.search_per_query, rs.avoid_sites, rs.search_min_height
    )
    for n in notes:
        print(n, file=sys.stderr)
    rows = sheets(hits, args.out, args.limit)
    shown = sum(len(r["images"]) for r in rows)
    by_engine: dict[str, int] = {}
    for h in hits:
        by_engine[h.engine] = by_engine.get(h.engine, 0) + 1
    summary = {
        "queries": queries,
        "found": len(hits),
        "by_engine": by_engine,
        "sheets": len(rows),
        "images": shown,
        "notes": notes,
        "sample": [asdict(h) for h in hits[:3]],
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"검색 결과 {len(hits)}장 → 확인표 {len(rows)}장 (사진 {shown}장)")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
