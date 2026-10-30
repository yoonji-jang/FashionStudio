"""핀터레스트 링크 → 참고 코디 사진 (2026-10-30 사용자: "핀터레스트 링크 필요해").

사용자가 텔레그램으로 보낸 링크만 읽는다: 핀 하나(pinterest.com/pin/<번호>/), 사용자 보드
(pinterest.com/<사용자>/<보드>/), 그 둘의 짧은 공유 링크(pin.it/…).
웹 페이지·임베드 정보·보드 RSS 는 robots.txt 가 막아서 (2026-10-30 Actions 확인) 열지 않고,
핀터레스트 공식 API v5 로 사용자 본인 계정의 보드와 핀만 읽는다 (권한 boards:read, pins:read).
짧은 링크는 넘겨주는 주소(Location 머리글)만 읽어 핀 번호·보드 이름을 알아낸다.
참고 사진 주소로는 "pin:<핀 번호>" 만 남긴다 (사진 파일은 저장소에 올리지 않음).

비밀값 (Actions Secrets): PINTEREST_APP_ID, PINTEREST_APP_SECRET, PINTEREST_REFRESH_TOKEN
(또는 시험용 PINTEREST_ACCESS_TOKEN 하나).
"""

from __future__ import annotations

import os
import re
import sys
from urllib.parse import unquote

import httpx

PIN_PHOTO = "pin:"  # 참고 사진 주소 앞머리 (사용자가 보낸 핀터레스트 링크)
API = "https://api.pinterest.com/v5"
SHORT = re.compile(r"https?://pin\.it/[A-Za-z0-9]+")
PIN = re.compile(r"https?://(?:www\.|[a-z]{2}\.)?pinterest\.[a-z.]+/pin/(?:[^/\s]*--)?(\d+)")
# 보드 주소: pinterest.<나라>/<사용자>/<보드>/ (핀 주소·검색 등 예약된 첫 칸은 뺌)
BOARD = re.compile(
    r"https?://(?:www\.|[a-z]{2}\.)?pinterest\.[a-z.]+/"
    r"(?!pin/|search/|ideas/|_/)([^/?#\s]+)/([^/?#\s]+)"
)
BOARD_MAX = 30  # 보드 하나에서 가져올 핀 수 (최근 것부터)
SIZES = ("1200x", "736x", "600x", "400x300", "236x", "150x150")


class PinUnavailable(RuntimeError):
    """핀 사진을 받지 못함."""


def links(text: str) -> list[str]:
    """글 속 핀터레스트 링크 (보낸 순서, 같은 것은 한 번)."""
    found: dict[int, str] = {}
    for pattern in (SHORT, BOARD, PIN):  # 같은 자리면 핀 주소가 보드 모양보다 앞선다
        for m in pattern.finditer(text or ""):
            found[m.start()] = m.group(0)
    return list(dict.fromkeys(found[k] for k in sorted(found)))


def ref_of(url: str) -> str:
    """링크 → 참고 사진 주소. 핀 주소면 번호만, 짧은 링크·보드는 그대로 (받을 때 푼다)."""
    m = PIN.match(url)
    return PIN_PHOTO + (m.group(1) if m else url)


def expand(url: str, c: httpx.Client) -> str:
    """짧은 링크(pin.it)가 가리키는 주소. 넘겨주는 주소(Location 머리글)만 따라가고,
    핀·보드 주소가 나오면 그 페이지는 열지 않는다."""
    if not SHORT.match(url):
        return url
    try:
        for _ in range(4):  # pin.it → api.pinterest.com/url_shortener/… → <나라>.pinterest.com/…
            resp = c.get(url, follow_redirects=False)
            nxt = resp.headers.get("location", "")
            if not nxt:
                break
            url = str(resp.url.join(nxt))
            if PIN.match(url) or BOARD.match(url):
                return url
    except httpx.HTTPError as e:
        raise PinUnavailable(f"짧은 링크를 풀지 못함: {e}") from e
    raise PinUnavailable(f"짧은 링크가 핀·보드를 가리키지 않음: {url}")


def token(c: httpx.Client) -> str:
    """API 접근 토큰. 갱신 토큰이 있으면 새로 받는다."""
    if os.environ.get("PINTEREST_ACCESS_TOKEN"):
        return os.environ["PINTEREST_ACCESS_TOKEN"]
    app, secret = os.environ.get("PINTEREST_APP_ID"), os.environ.get("PINTEREST_APP_SECRET")
    refresh = os.environ.get("PINTEREST_REFRESH_TOKEN")
    if not (app and secret and refresh):
        raise PinUnavailable(
            "핀터레스트 API 토큰이 없음 (개발자 앱 승인 뒤 PINTEREST_* Secrets 등록 필요)"
        )
    try:
        resp = c.post(
            f"{API}/oauth/token",
            auth=(app, secret),
            data={"grant_type": "refresh_token", "refresh_token": refresh},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise PinUnavailable(f"핀터레스트 토큰을 갱신하지 못함: {e}") from e


def _get(c: httpx.Client, tok: str, path: str, **params) -> dict:
    try:
        resp = c.get(f"{API}{path}", headers={"Authorization": f"Bearer {tok}"}, params=params)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise PinUnavailable(f"핀터레스트 API {path}: {e}") from e


def _pages(c: httpx.Client, tok: str, path: str, limit: int) -> list[dict]:
    items: list[dict] = []
    bookmark = None
    while len(items) < limit:
        params = {"page_size": 100} | ({"bookmark": bookmark} if bookmark else {})
        data = _get(c, tok, path, **params)
        items += data.get("items") or []
        bookmark = data.get("bookmark")
        if not bookmark:
            break
    return items[:limit]


def image_of(pin: dict) -> str | None:
    """핀 정보의 가장 큰 사진 주소."""
    images = (pin.get("media") or {}).get("images") or {}
    for size in SIZES:
        if (images.get(size) or {}).get("url"):
            return images[size]["url"]
    return None


def _norm(name: str) -> str:
    return re.sub(r"[\s\-_]+", "", unquote(name)).lower()


def board_pins(user: str, slug: str, c: httpx.Client, tok: str) -> list[str]:
    """사용자 본인 보드(이름이 주소와 같은 것)의 핀 번호 (최근 BOARD_MAX 개)."""
    boards = _pages(c, tok, "/boards", 500)
    want = _norm(slug)
    hit = next((b for b in boards if _norm(b.get("name", "")) == want), None)
    if hit is None:
        names = ", ".join(b.get("name", "") for b in boards[:20])
        raise PinUnavailable(f"내 보드에서 '{unquote(slug)}' 를 찾지 못함 (보드: {names})")
    owner = ((hit.get("owner") or {}).get("username") or "").lower()
    if owner and owner != unquote(user).lower():
        print(f"보드 주인({owner})이 링크의 사용자({user})와 다름", file=sys.stderr)
    pins = _pages(c, tok, f"/boards/{hit['id']}/pins", BOARD_MAX)
    return [str(p["id"]) for p in pins if p.get("id") and image_of(p)]


def expand_refs(refs: list[str], c: httpx.Client | None = None) -> tuple[list[str], list[str]]:
    """참고 사진 주소 목록에서 핀터레스트 링크를 "pin:<번호>" 로 푼다 (보드는 핀 여러 개로).
    다른 주소(tg: 등)는 그대로. (풀린 목록, 못 푼 링크와 이유)."""
    from tools import scout

    own = c is None
    c = c or scout.client()
    out: list[str] = []
    bad: list[str] = []
    tok: str | None = None
    try:
        for ref in refs:
            body = ref[len(PIN_PHOTO) :] if ref.startswith(PIN_PHOTO) else None
            if body is None or body.isdigit():
                out.append(ref)
                continue
            try:
                url = expand(body, c)
                m = PIN.match(url)
                if m:
                    out.append(PIN_PHOTO + m.group(1))
                    continue
                ub = BOARD.match(url)
                if ub is None:
                    raise PinUnavailable(f"핀도 보드도 아님: {url}")
                tok = tok or token(c)
                out += [PIN_PHOTO + p for p in board_pins(ub.group(1), ub.group(2), c, tok)]
            except PinUnavailable as e:
                bad.append(f"{body} ({e})")
    finally:
        if own:
            c.close()
    return list(dict.fromkeys(out)), bad


def fetch(ref: str, c: httpx.Client) -> bytes:
    """ "pin:<번호>" 의 사진 (공식 API 가 준 사진 주소에서)."""
    from tools import scout

    pid = ref[len(PIN_PHOTO) :] if ref.startswith(PIN_PHOTO) else ref
    if not pid.isdigit():
        pids, bad = expand_refs([PIN_PHOTO + pid], c)
        if not pids:
            raise PinUnavailable("; ".join(bad) or f"핀을 찾지 못함: {pid}")
        pid = pids[0][len(PIN_PHOTO) :]
    url = image_of(_get(c, token(c), f"/pins/{pid}"))
    if not url:
        raise PinUnavailable(f"핀 {pid} 에 사진이 없음")
    try:
        return scout.fetch_bytes(c, url)
    except scout.ImageUnavailable as e:
        raise PinUnavailable(f"핀 {pid} 사진을 받지 못함: {e}") from e


def check(url: str) -> int:
    """링크 하나를 시험한다: 풀린 핀 번호를 보이고 사진을 몇 장 텔레그램으로 보낸다."""
    import tempfile
    from pathlib import Path

    from tools import scout
    from tools.notify import Telegram

    with scout.client() as c:
        print("주소:", expand(url, c))
        pids, bad = expand_refs([ref_of(url)], c)
        print(f"핀 {len(pids)}개", *bad, sep="\n")
        tmp = Path(tempfile.mkdtemp())
        photos = []
        for i, pid in enumerate(pids[:10]):
            try:
                (tmp / f"{i}.jpg").write_bytes(fetch(pid, c))
                photos.append(tmp / f"{i}.jpg")
            except PinUnavailable as e:
                print("사진 못 받음:", e)
    if photos:
        Telegram.from_env().send_photos(photos, f"[핀터레스트 링크 시험] {url} → {len(photos)}장")
    return 0 if photos else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "check":
        sys.exit(check(sys.argv[2]))
    print("사용법: python -m tools.pinterest check <링크>")
    sys.exit(2)
