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
# 섬네일 크기 폴더 (i.pinimg.com/236x/…) → 큰 사진
SIZE = re.compile(r"(https://i\.pinimg\.com/)(?:\d+x\d*|originals)/")


class PinUnavailable(RuntimeError):
    """핀 사진을 받지 못함."""


def links(text: str) -> list[str]:
    """글 속 핀 링크 (보낸 순서, 같은 것은 한 번)."""
    found = [(m.start(), m.group(0)) for m in SHORT.finditer(text or "")]
    found += [(m.start(), m.group(0)) for m in PIN.finditer(text or "")]
    return list(dict.fromkeys(url for _, url in sorted(found)))


def pin_id(url: str, c: httpx.Client | None = None) -> str:
    """핀 번호. 짧은 링크는 넘겨주는 주소(Location 머리글)만 읽는다."""
    m = PIN.match(url)
    if m:
        return m.group(1)
    if not SHORT.match(url):
        raise PinUnavailable(f"핀 링크가 아님: {url}")
    from tools import scout

    own = c is None
    c = c or scout.client()
    try:
        for _ in range(3):  # pin.it → api.pinterest.com/url_shortener/… → www.pinterest.com/pin/…
            resp = c.get(url, follow_redirects=False)
            nxt = resp.headers.get("location", "")
            if not nxt:
                break
            url = str(resp.url.join(nxt))
            m = PIN.match(url)
            if m:
                return m.group(1)
    except httpx.HTTPError as e:
        raise PinUnavailable(f"짧은 링크를 풀지 못함: {e}") from e
    finally:
        if own:
            c.close()
    raise PinUnavailable(f"핀 번호를 찾지 못함: {url}")


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
