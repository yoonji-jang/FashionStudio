"""주제별 참고 코디 이미지 검색 (tools.ref_search): 검색 API 응답 읽기, 걸러내기, 확인표."""

import json
from io import BytesIO

import httpx
from PIL import Image

from tools import ref_search, scout

AVOID = ["instagram.com", "cdninstagram.com"]


def _jpeg(color: str, size=(600, 900)) -> bytes:
    buf = BytesIO()
    im = Image.new("RGB", size, color)
    if color == "black":  # 평균 해시가 다르게 위쪽 절반을 흰색으로
        im.paste("white", (0, 0, size[0], size[1] // 2))
    im.save(buf, "JPEG", quality=95)
    return buf.getvalue() + b"\0" * ref_search.MIN_BYTES


def _search_client(seen: list[httpx.Request]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host == "dapi.kakao.com":
            docs = [
                {
                    "image_url": "https://shop.example/a.jpg",
                    "width": 800,
                    "height": 1200,
                    "display_sitename": "쇼핑몰",
                },
                {
                    "image_url": "https://i.pinimg.com/b.jpg",
                    "width": 700,
                    "height": 1000,
                    "display_sitename": "Pinterest",
                },
                {
                    "image_url": "https://scontent.cdninstagram.com/c.jpg",
                    "width": 800,
                    "height": 1200,
                    "display_sitename": "Instagram",
                },
                {"image_url": "https://shop.example/small.jpg", "width": 300, "height": 400},
                {"image_url": "https://shop.example/wide.jpg", "width": 1600, "height": 600},
            ]
            return httpx.Response(200, json={"documents": docs, "meta": {}})
        if req.url.host == "naverapihub.apigw.ntruss.com":
            items = [
                {"link": "https://shop.example/a.jpg", "sizewidth": "800", "sizeheight": "1200"},
                {"link": "https://blog.example/d.jpg", "sizewidth": "640", "sizeheight": "960"},
            ]
            return httpx.Response(200, json={"items": items})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_filters_and_interleaves():
    seen: list[httpx.Request] = []
    env = {"KAKAO_REST_API_KEY": "k", "NAVER_API_KEY_ID": "i", "NAVER_API_KEY": "s"}
    hits, notes = ref_search.search(
        ["롱스커트 코디"], ["daum", "naver"], 60, AVOID, 600, env, _search_client(seen)
    )
    # 인스타그램·작은 사진·넓은 사진은 빠지고, 핀터레스트는 남고, 같은 주소는 한 번만
    assert [h.image for h in hits] == [
        "https://shop.example/a.jpg",
        "https://i.pinimg.com/b.jpg",
        "https://blog.example/d.jpg",
    ]
    assert notes == []
    assert seen[0].headers["Authorization"] == "KakaoAK k"
    assert seen[1].headers["X-NCP-APIGW-API-KEY-ID"] == "i"


def test_search_skips_engine_without_key():
    seen: list[httpx.Request] = []
    hits, notes = ref_search.search(
        ["코디"],
        ["daum", "naver"],
        60,
        AVOID,
        600,
        {"KAKAO_REST_API_KEY": "k"},
        _search_client(seen),
    )
    assert {h.engine for h in hits} == {"daum"}
    assert notes == ["naver: NAVER_API_KEY_ID·NAVER_API_KEY 없음, 건너뜀"]
    assert all(r.url.host == "dapi.kakao.com" for r in seen)


def test_search_failure_hides_key():
    def handler(req):
        return httpx.Response(401, json={"msg": "bad key"})

    c = httpx.Client(transport=httpx.MockTransport(handler))
    hits, notes = ref_search.search(
        ["코디"], ["daum"], 60, AVOID, 600, {"KAKAO_REST_API_KEY": "secret"}, c
    )
    assert hits == [] and notes == ["daum '코디': 검색 실패 (401)"]
    assert "secret" not in notes[0]


def test_sheets_dedupes_and_writes_refs(tmp_path, monkeypatch):
    monkeypatch.setattr(scout, "IMAGE_INTERVAL_SEC", 0)
    red, black = _jpeg("red"), _jpeg("black")
    bodies = {"/a.jpg": red, "/b.jpg": red, "/c.jpg": black, "/d.jpg": b"tiny"}

    def handler(req):
        body = bodies.get(req.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"content-type": "image/jpeg"})

    hits = [
        ref_search.Hit(f"https://x.example/{n}.jpg", 600, 900, "쇼핑몰", "코디", "daum")
        for n in "abcde"
    ]
    rows = ref_search.sheets(
        hits, tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    # b 는 a 와 같은 사진, d 는 너무 작고, e 는 없음
    assert len(rows) == 1
    assert rows[0]["images"] == ["https://x.example/a.jpg", "https://x.example/c.jpg"]
    assert rows[0]["sites"] == ["쇼핑몰", "쇼핑몰"]
    assert (tmp_path / "sheet_1.jpg").exists()
    assert json.loads((tmp_path / "refs.json").read_text())[0]["sheet"].endswith("sheet_1.jpg")
