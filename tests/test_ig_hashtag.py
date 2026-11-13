"""인스타 해시태그 참고 코디 검색 (tools.ig_hashtag)."""

import base64
import json
from io import BytesIO

import httpx
import pytest
from nacl.public import PrivateKey
from PIL import Image

from tools import ig_hashtag, ref_search, sealed


def _graph(seen: list[httpx.Request], refuse_host: str = "") -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host == refuse_host:
            err = {
                "error": {"message": "Unsupported request", "type": "OAuthException", "code": 100}
            }
            return httpx.Response(400, json=err)
        path = req.url.path
        if path.endswith("/ig_hashtag_search"):
            q = req.url.params["q"]
            return httpx.Response(200, json={"data": [{"id": f"h_{q}"}] if q != "없음" else []})
        if path.endswith("/top_media") and "after" not in req.url.params:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "1", "media_type": "IMAGE", "media_url": "https://cdn/a.jpg"},
                        {"id": "2", "media_type": "VIDEO", "media_url": "https://cdn/v.mp4"},
                        {
                            "id": "3",
                            "media_type": "CAROUSEL_ALBUM",
                            "children": {
                                "data": [
                                    {"media_type": "VIDEO", "media_url": "https://cdn/cv.mp4"},
                                    {"media_type": "IMAGE", "media_url": "https://cdn/c1.jpg"},
                                    {"media_type": "IMAGE", "media_url": "https://cdn/c2.jpg"},
                                ]
                            },
                        },
                    ],
                    "paging": {"next": str(req.url.copy_add_param("after", "x"))},
                },
            )
        if path.endswith("/top_media"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "4", "media_type": "IMAGE", "media_url": "https://cdn/a.jpg"}]
                },
            )
        if path.endswith("/recent_media"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "5", "media_type": "IMAGE", "media_url": "https://cdn/r.jpg"}]
                },
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_photos_skip_video_and_limit_children():
    items = [
        {"media_type": "VIDEO", "media_url": "https://cdn/v.mp4"},
        {
            "media_type": "CAROUSEL_ALBUM",
            "children": {
                "data": [
                    {"media_type": "IMAGE", "media_url": f"https://cdn/{i}.jpg"} for i in range(5)
                ]
            },
        },
        {"media_type": "CAROUSEL_ALBUM", "media_url": "https://cdn/cover.jpg"},
        {"media_type": "IMAGE"},  # 저작권 등으로 주소가 없는 사진
    ]
    hits = ig_hashtag.photos(items, "크리스마스코디", "top_media", children=2)
    assert [h.image for h in hits] == [
        "https://cdn/0.jpg",
        "https://cdn/1.jpg",
        "https://cdn/cover.jpg",
    ]
    assert hits[0].site == "인스타 #크리스마스코디"


def test_search_pages_dedupes_and_never_asks_for_names():
    seen: list[httpx.Request] = []
    hits, notes, base = ig_hashtag.search(
        ["크리스마스코디", "없음"],
        ["top_media", "recent_media"],
        10,
        "https://graph.facebook.com/v23.0",
        "TOKEN",
        "123",
        client=_graph(seen),
    )
    assert [h.image for h in hits] == [
        "https://cdn/a.jpg",
        "https://cdn/r.jpg",
        "https://cdn/c1.jpg",
        "https://cdn/c2.jpg",
    ]
    assert base == "https://graph.facebook.com/v23.0"
    assert any("#없음" in n for n in notes)
    assert any("영상 1개 뺌" in n for n in notes)
    for r in seen:
        fields = r.url.params.get("fields", "")
        assert "username" not in fields and "permalink" not in fields and "caption" not in fields


def test_search_falls_back_to_other_graph_host():
    seen: list[httpx.Request] = []
    hits, _, base = ig_hashtag.search(
        ["크리스마스코디"],
        ["recent_media"],
        5,
        "https://graph.facebook.com/v23.0",
        "TOKEN",
        "123",
        client=_graph(seen, refuse_host="graph.facebook.com"),
    )
    assert base == "https://graph.instagram.com/v23.0"
    assert [h.image for h in hits] == ["https://cdn/r.jpg"]


def test_search_refused_explains_what_to_enable():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "no permission", "code": 10}})

    c = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ig_hashtag.SearchRefused) as e:
        ig_hashtag.search(
            ["a"], ["top_media"], 5, "https://graph.facebook.com/v23.0", "TOKEN", "1", client=c
        )
    msg = str(e.value)
    assert "Instagram Public Content Access" in msg and "code=10" in msg and "TOKEN" not in msg


def _jpeg(color: str) -> bytes:
    buf = BytesIO()
    im = Image.new("RGB", (600, 900), color)
    if color == "black":
        im.paste("white", (0, 0, 600, 450))
    im.save(buf, "JPEG", quality=95)
    return buf.getvalue() + b"\0" * ref_search.MIN_BYTES


def test_sheets_keep_seal_and_adopt(tmp_path, monkeypatch):
    monkeypatch.setattr(ref_search.scout, "IMAGE_INTERVAL_SEC", 0)
    imgs = {"/a.jpg": _jpeg("white"), "/b.jpg": _jpeg("black")}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=imgs[req.url.path], headers={"content-type": "image/jpeg"}
        )

    hits = [ref_search.Hit(f"https://cdn{p}", 0, 0, "인스타 #a", "a", "top_media") for p in imgs]
    out = tmp_path / "out"
    rows = ref_search.sheets(
        hits, out, client=httpx.Client(transport=httpx.MockTransport(handler)), keep=out / "photos"
    )
    assert rows[0]["engines"] == ["top_media", "top_media"]
    assert sorted(p.name for p in (out / "photos").iterdir()) == ["s1_01.jpg", "s1_02.jpg"]

    (out / "summary.json").write_text(json.dumps({"tags": ["a"]}), encoding="utf-8")
    key = PrivateKey.generate()
    n = ig_hashtag.seal_dir(out, tmp_path / "sealed", key.public_key)
    assert n == 3  # 확인표 1장 + 사진 2장
    assert not list((tmp_path / "sealed").glob("*.jpg"))
    keyfile = tmp_path / "view.key"
    keyfile.write_text(base64.b64encode(bytes(key)).decode())
    opened = tmp_path / "opened"
    assert (
        sealed.main(
            ["open", str(tmp_path / "sealed"), "--dest", str(opened), "--key-file", str(keyfile)]
        )
        == 0
    )
    assert (opened / "s1_02.jpg").read_bytes() == imgs["/b.jpg"]

    ref_key = PrivateKey.generate()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sealed, "public_key", lambda: ref_key.public_key)
    urls = ig_hashtag.adopt(opened, ["s1_02"], "ig1113", "women")
    assert urls == ["sealed:ig1113_01"]
    data = (tmp_path / "data/women/user_refs/ig1113_01.sealed").read_bytes()
    assert sealed.unseal(data, ref_key) == imgs["/b.jpg"]
