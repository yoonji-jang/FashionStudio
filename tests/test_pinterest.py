"""핀터레스트 링크 → 참고 코디 사진 (2026-10-30 사용자)."""

import httpx
import pytest

from tools import flow, pinterest, scout

BOARD_URL = (
    "https://www.pinterest.co.kr/jjy/%EC%97%B0%EB%A7%90-%ED%8C%8C%ED%8B%B0%EB%A3%A9/?invite_code=x"
)


def test_links_and_refs():
    text = (
        "참고 https://pin.it/1FPLJL6Zr\n"
        "https://www.pinterest.co.kr/pin/123456789/ 이것도\n"
        f"{BOARD_URL}\n"
        "https://pin.it/1FPLJL6Zr"
    )
    assert pinterest.links(text) == [
        "https://pin.it/1FPLJL6Zr",
        "https://www.pinterest.co.kr/pin/123456789",
        BOARD_URL.split("?")[0].rstrip("/"),
    ]
    assert pinterest.ref_of("https://www.pinterest.co.kr/pin/123456789/") == "pin:123456789"
    assert pinterest.ref_of("https://pin.it/1FPLJL6Zr") == "pin:https://pin.it/1FPLJL6Zr"
    # 짧은 링크 중간 주소(api.pinterest.com/url_shortener/…)는 보드가 아니다
    assert pinterest.BOARD.match("https://api.pinterest.com/url_shortener/1FP/redirect/") is None
    assert scout.user_ref("AgAC") == "tg:AgAC" and scout.user_ref("pin:1") == "pin:1"


def fake_client(seen: list[str]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.host == "pin.it":
            return httpx.Response(
                308, headers={"location": "https://api.pinterest.com/url_shortener/1FP/redirect/"}
            )
        if req.url.path.startswith("/url_shortener/"):
            return httpx.Response(302, headers={"location": BOARD_URL})
        if req.url.host.endswith("pinterest.co.kr"):
            raise AssertionError("핀터레스트 웹 페이지는 열지 않는다")
        if req.url.path == "/v5/boards":
            assert req.headers["authorization"] == "Bearer T"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "B0", "name": "니트"},
                        {"id": "B1", "name": "연말 파티룩", "owner": {"username": "jjy"}},
                    ]
                },
            )
        if req.url.path == "/v5/boards/B1/pins":
            img = {"media": {"images": {"736x": {"url": "https://i.pinimg.com/736x/a.jpg"}}}}
            return httpx.Response(
                200, json={"items": [{"id": "11"} | img, {"id": "12"} | img, {"id": "13"}]}
            )
        if req.url.path.startswith("/v5/pins/"):
            img = {"1200x": {"url": "https://i.pinimg.com/1200x/a.jpg"}}
            return httpx.Response(200, json={"id": "11", "media": {"images": img}})
        if req.url.host == "i.pinimg.com":
            return httpx.Response(200, content=b"jpeg")
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_board_link_becomes_pins(monkeypatch):
    monkeypatch.setattr(scout, "_wait_turn", lambda url, sleep: None)
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN", "T")
    seen: list[str] = []
    with fake_client(seen) as c:
        refs, bad = pinterest.expand_refs(["tg:F", "pin:https://pin.it/1FPLJL6Zr", "pin:99"], c)
        assert refs == ["tg:F", "pin:11", "pin:12", "pin:99"] and bad == []
        assert pinterest.fetch("pin:11", c) == b"jpeg"


def test_no_token_is_reported(monkeypatch):
    for k in ("PINTEREST_ACCESS_TOKEN", "PINTEREST_APP_ID", "PINTEREST_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with fake_client([]) as c:
        refs, bad = pinterest.expand_refs([f"pin:{BOARD_URL}"], c)
        assert refs == [] and "토큰이 없음" in bad[0]
        with pytest.raises(pinterest.PinUnavailable):
            pinterest.fetch("pin:11", c)


def test_ref_photo_downloads_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(scout, "_wait_turn", lambda url, sleep: None)
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN", "T")
    monkeypatch.setattr(scout, "client", lambda: fake_client([]))
    dest = scout.ref_photo("pin:11", [], tmp_path / "p.jpg")
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
