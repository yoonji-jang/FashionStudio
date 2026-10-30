"""핀터레스트 핀 링크 → 참고 코디 사진 (2026-10-30 사용자)."""

import httpx
import pytest

from tools import flow, pinterest, scout


def test_links_and_refs():
    text = (
        "참고 https://pin.it/1FPLJL6Zr\n"
        "https://www.pinterest.co.kr/pin/123456789/ 이것도\n"
        "https://pin.it/1FPLJL6Zr"
    )
    assert pinterest.links(text) == [
        "https://pin.it/1FPLJL6Zr",
        "https://www.pinterest.co.kr/pin/123456789",
    ]
    assert pinterest.ref_of("https://www.pinterest.co.kr/pin/123456789/") == "pin:123456789"
    assert pinterest.ref_of("https://pin.it/1FPLJL6Zr") == "pin:https://pin.it/1FPLJL6Zr"
    assert pinterest.big("https://i.pinimg.com/236x/ab/cd/x.jpg") == (
        "https://i.pinimg.com/736x/ab/cd/x.jpg"
    )
    assert scout.user_ref("AgAC") == "tg:AgAC" and scout.user_ref("pin:1") == "pin:1"


def fake_client(robots: str = "User-agent: *\nAllow: /\n") -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if req.url.host == "pin.it":
            return httpx.Response(
                301, headers={"location": "https://www.pinterest.com/pin/987654321/sent/"}
            )
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text=robots)
        if "oembed.json" in url:
            assert "pin/987654321/" in url
            return httpx.Response(
                200, json={"thumbnail_url": "https://i.pinimg.com/236x/aa/bb/c.jpg"}
            )
        if req.url.host == "i.pinimg.com":
            assert "/736x/" in url
            return httpx.Response(200, content=b"jpeg")
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_short_link(monkeypatch):
    monkeypatch.setattr(scout, "_wait_turn", lambda url, sleep: None)
    with fake_client() as c:
        assert pinterest.pin_id("https://pin.it/1FPLJL6Zr", c) == "987654321"
        assert pinterest.fetch("pin:https://pin.it/1FPLJL6Zr", c) == b"jpeg"
        assert pinterest.fetch("pin:987654321", c) == b"jpeg"
    with fake_client("User-agent: *\nDisallow: /\n") as c, pytest.raises(pinterest.PinUnavailable):
        pinterest.fetch("pin:987654321", c)


def test_ref_photo_downloads_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(scout, "_wait_turn", lambda url, sleep: None)
    monkeypatch.setattr(scout, "client", fake_client)
    dest = scout.ref_photo("pin:https://pin.it/1FPLJL6Zr", [], tmp_path / "p.jpg")
    assert dest.read_bytes() == b"jpeg"


def test_flow_reads_pin_links_as_refs():
    msg = {"chat": {"id": 1}, "date": 200, "text": "https://pin.it/1FPLJL6Zr\nhttps://pin.it/AbC"}
    photo = {"chat": {"id": 1}, "date": 210, "photo": [{"file_id": "F1"}], "caption": "참고"}
    updates = [{"update_id": 5, "message": msg}, {"update_id": 6, "message": photo}]
    assert flow.ref_photos(updates, "1", 100) == [
        (5, "pin:https://pin.it/1FPLJL6Zr"),
        (5, "pin:https://pin.it/AbC"),
        (6, "F1"),
    ]
    # 얼굴 사진을 찾을 때는 핀 링크를 보지 않는다
    assert flow.face_photos(updates, "1", 100) == []
