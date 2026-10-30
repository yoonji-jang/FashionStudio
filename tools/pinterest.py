"""핀터레스트 핀 링크 → 참고 코디 사진 한 장 (2026-10-30 사용자: "핀터레스트 링크 필요해").

사용자가 텔레그램으로 보낸 핀 링크(pin.it/…, pinterest.com/pin/<번호>/)만 읽는다.
검색·보드 훑기는 하지 않는다. 핀 번호를 알아내면 핀터레스트 공개 임베드 정보(oEmbed)에서
사진 주소를 받아 그 사진 한 장만 받는다. 핀 페이지 본문은 열지 않고, robots.txt 가 막은 주소는
부르지 않는다. 참고 사진 주소로는 "pin:<핀 번호>" 만 남긴다 (사진 파일은 저장소에 올리지 않음).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

PIN_PHOTO = "pin:"  # 참고 사진 주소 앞머리 (사용자가 보낸 핀 링크)
OEMBED = "https://www.pinterest.com/oembed.json?url=https://www.pinterest.com/pin/{id}/"
# 핀 링크: 짧은 공유 링크(pin.it) 또는 핀 주소 (나라별 도메인 포함)
SHORT = re.compile(r"https?://pin\.it/[A-Za-z0-9]+")
PIN = re.compile(r"https?://(?:[a-z]{2,3}\.)?pinterest\.[a-z.]+/pin/(?:[^/\s]*--)?(\d+)")
# 보드 주소: pinterest.<나라>/<사용자>/<보드>/ (핀 주소·검색 등 예약된 첫 칸은 뺌)
BOARD = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?pinterest\.[a-z.]+/(?!pin/|search/|ideas/|_/)([^/?#\s]+)/([^/?#\s]+)"
)
WIDGET = "https://widgets.pinterest.com/v3/pidgets/boards/{user}/{board}/pins/"
RSS = "https://www.pinterest.com/{user}/{board}.rss"
# 섬네일 크기 폴더 (i.pinimg.com/236x/…) → 큰 사진
SIZE = re.compile(r"(https://i\.pinimg\.com/)(?:\d+x\d*|originals)/")


class PinUnavailable(RuntimeError):
    """핀 사진을 받지 못함."""


def links(text: str) -> list[str]:
    """글 속 핀 링크 (보낸 순서, 같은 것은 한 번)."""
    found = [(m.start(), m.group(0)) for m in SHORT.finditer(text or "")]
    found += [(m.start(), m.group(0)) for m in PIN.finditer(text or "")]
    return list(dict.fromkeys(url for _, url in sorted(found)))


def expand(url: str, c: httpx.Client) -> str:
    """짧은 링크(pin.it)가 가리키는 주소. 넘겨주는 주소(Location 머리글)만 읽는다."""
    if not SHORT.match(url):
        return url
    try:
        for _ in range(3):  # pin.it → api.pinterest.com/url_shortener/… → www.pinterest.com/…
            resp = c.get(url, follow_redirects=False)
            nxt = resp.headers.get("location", "")
            if not nxt:
                break
            url = str(resp.url.join(nxt))
            if PIN.match(url) or BOARD.match(url):
                break
    except httpx.HTTPError as e:
        raise PinUnavailable(f"짧은 링크를 풀지 못함: {e}") from e
    return url


def pin_id(url: str, c: httpx.Client | None = None) -> str:
    """핀 번호 (보드 링크면 PinUnavailable)."""
    from tools import scout

    own = c is None
    c = c or scout.client()
    try:
        url = expand(url, c)
    finally:
        if own:
            c.close()
    m = PIN.match(url)
    if m:
        return m.group(1)
    raise PinUnavailable(f"핀 번호를 찾지 못함: {url}")


def board_of(url: str) -> tuple[str, str] | None:
    """보드 주소의 (사용자, 보드)."""
    m = BOARD.match(url)
    return (m.group(1), m.group(2)) if m else None


def board_images(user: str, board: str, c: httpx.Client, limit: int = 50) -> list[str]:
    """공개 보드의 핀 사진 주소 (보드 위젯 정보, 안 되면 보드 RSS). 비공개 보드는 못 읽는다."""
    from tools import scout

    urls: list[str] = []
    widget = WIDGET.format(user=user, board=board)
    if scout.robots_allows(c, widget):
        try:
            resp = c.get(widget)
            resp.raise_for_status()
            for pin in (resp.json().get("data") or {}).get("pins") or []:
                imgs = pin.get("images") or {}
                best = imgs.get("564x") or imgs.get("237x") or {}
                if best.get("url"):
                    urls.append(big(best["url"]))
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            print(f"보드 위젯 정보를 읽지 못함: {e}")
    else:
        print("robots.txt 가 보드 위젯 정보를 허용하지 않음")
    if not urls:
        rss = RSS.format(user=user, board=board)
        if scout.robots_allows(c, rss):
            try:
                resp = c.get(rss)
                resp.raise_for_status()
                found = re.findall(r'https://i\.pinimg\.com/[^"\s<>&]+', resp.text)
                urls = [big(u) for u in found]
            except httpx.HTTPError as e:
                print(f"보드 RSS 를 읽지 못함: {e}")
        else:
            print("robots.txt 가 보드 RSS 를 허용하지 않음")
    return list(dict.fromkeys(urls))[:limit]


def big(url: str) -> str:
    """i.pinimg.com 섬네일 주소를 736px 사진 주소로."""
    return SIZE.sub(r"\g<1>736x/", url, count=1)


def image_url(pid: str, c: httpx.Client) -> str:
    """핀의 사진 주소 (공개 임베드 정보 thumbnail_url, 큰 크기로)."""
    from tools import scout

    url = OEMBED.format(id=pid)
    if not scout.robots_allows(c, url):
        raise PinUnavailable("robots.txt 가 핀 임베드 정보를 허용하지 않음")
    try:
        resp = c.get(url)
        resp.raise_for_status()
        thumb = resp.json().get("thumbnail_url") or ""
    except (httpx.HTTPError, ValueError) as e:
        raise PinUnavailable(f"핀 {pid} 정보를 받지 못함: {e}") from e
    if urlsplit(thumb).hostname != "i.pinimg.com":
        raise PinUnavailable(f"핀 {pid} 사진 주소가 없음")
    return big(thumb)


def fetch(ref: str, c: httpx.Client) -> bytes:
    """ "pin:<번호>" 의 사진. 큰 사진이 없으면 원래 섬네일."""
    from tools import scout

    ref = ref[len(PIN_PHOTO) :] if ref.startswith(PIN_PHOTO) else ref
    # "pin:<번호>" 또는 "pin:<핀 링크>" (흐름은 네트워크 없이 링크를 그대로 적는다)
    pid = ref if ref.isdigit() else pin_id(ref, c)
    url = image_url(pid, c)
    try:
        return scout.fetch_bytes(c, url)
    except scout.ImageUnavailable as e:
        raise PinUnavailable(f"핀 {pid} 사진을 받지 못함: {e}") from e


def ref_of(url: str) -> str:
    """핀 링크 → 참고 사진 주소. 핀 주소면 번호만, 짧은 링크는 그대로 (받을 때 푼다)."""
    m = PIN.match(url)
    return PIN_PHOTO + (m.group(1) if m else url)


def refs(text: str, c: httpx.Client | None = None) -> tuple[list[str], list[str]]:
    """글 속 핀 링크 → ("pin:<번호>" 목록, 못 읽은 링크와 이유)."""
    from tools import scout

    own = c is None
    c = c or scout.client()
    ok, bad = [], []
    try:
        for url in links(text):
            try:
                ok.append(PIN_PHOTO + pin_id(url, c))
            except PinUnavailable as e:
                bad.append(f"{url} ({e})")
    finally:
        if own:
            c.close()
    return list(dict.fromkeys(ok)), bad


def check(url: str) -> int:
    """링크 하나를 시험한다: 핀이면 사진 한 장, 보드면 사진 주소들 (텔레그램으로 몇 장 보냄)."""
    import tempfile
    from pathlib import Path

    from tools import scout
    from tools.notify import Telegram

    with scout.client() as c:
        hop = url
        for _ in range(4):
            r = c.get(hop, follow_redirects=False)
            print("단계", r.status_code, hop, "→", r.headers.get("location", ""))
            print("  robots", "허용" if scout.robots_allows(c, hop) else "막음")
            if not r.headers.get("location"):
                body = r.text[:3000]
                print("  본문 단서:", re.findall(r"pinterest\.[a-z.]+/[^\"'<>\s]{0,120}", body)[:8])
                break
            hop = str(r.url.join(r.headers["location"]))
        full = expand(url, c)
        print("주소:", full)
        for probe in (
            OEMBED.format(id="1"),
            WIDGET.format(user="u", board="b"),
            RSS.format(user="u", board="b"),
        ):
            print("robots 허용" if scout.robots_allows(c, probe) else "robots 막음", probe)
        tmp = Path(tempfile.mkdtemp())
        photos = []
        if PIN.match(full):
            photos.append(tmp / "pin.jpg")
            photos[0].write_bytes(fetch(PIN_PHOTO + pin_id(full, c), c))
        elif (ub := board_of(full)) is not None:
            imgs = board_images(*ub, c)
            print(f"보드 {ub}: 사진 {len(imgs)}장")
            for i, img in enumerate(imgs[:10]):
                try:
                    (tmp / f"b{i}.jpg").write_bytes(scout.fetch_bytes(c, img))
                    photos.append(tmp / f"b{i}.jpg")
                except scout.ImageUnavailable as e:
                    print("사진 못 받음:", e)
        else:
            print("핀도 보드도 아닌 주소")
    if photos:
        Telegram.from_env().send_photos(photos, f"[핀터레스트 링크 시험] {url} → {len(photos)}장")
    return 0 if photos else 1


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "check":
        sys.exit(check(sys.argv[2]))
    print("사용법: python -m tools.pinterest check <링크>")
    sys.exit(2)
