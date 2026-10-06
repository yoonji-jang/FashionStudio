"""실측표 (2026-10-06): 상세 본문 이미지·실측 글, 조각 나누기, 실측을 기획 cm·size 로."""

from __future__ import annotations

import io
import json

import yaml
from PIL import Image

from sources import zigzag
from tests.test_plan import daily, store  # noqa: F401
from tools import plan as plan_tool
from tools import scout, sizes


def page(description: str) -> str:
    data = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": ["getPdpBaseInfo", "1"],
                            "state": {"data": {"product": {"id": "1", "description": description}}},
                        }
                    ]
                }
            }
        }
    }
    script = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    return f"<html>{script}</html>"


DESC = (
    '<p><img src="//shop.example/top.gif"></p>'
    '<p><img src="https://shop.example/model_01.jpg"><img ec-data-src="https://shop.example/size.jpg"></p>'
    "<p>상담 문의 010-0000-0000</p>"
    "<table><tr><td>S 총장 70 어깨 50 가슴 58</td></tr><tr><td>M 총장 72 어깨 52</td></tr></table>"
    "<div>소재: 면 100%</div>"
)


def test_description_images_and_size_lines():
    html = page(DESC)
    assert zigzag.description_images(html) == [
        "https://shop.example/model_01.jpg",
        "https://shop.example/size.jpg",
    ]
    lines = zigzag.size_lines(html)
    assert lines == ["S 총장 70 어깨 50 가슴 58", "M 총장 72 어깨 52"]  # 연락처·소재 줄은 버림
    assert zigzag.description_images("<html></html>") == []


def test_split_tall_overlaps_and_shrinks():
    pieces = sizes.split_tall(Image.new("RGB", (1440, 6000), "white"))
    assert all(p.width == sizes.PIECE_WIDTH for p in pieces)
    assert len(pieces) == 3 and pieces[-1].height < sizes.PIECE_HEIGHT
    assert len(sizes.split_tall(Image.new("RGB", (500, 900)))) == 1


def test_prepare_saves_pieces_under_src(tmp_path, store, monkeypatch):  # noqa: F811
    def fetch_page(c, url):
        return 200, page(DESC)

    def fetch_bytes(c, url):
        buf = io.BytesIO()
        Image.new("RGB", (800, 3000), "white").save(buf, "JPEG")
        return buf.getvalue()

    monkeypatch.setattr(scout, "fetch_page", fetch_page)
    monkeypatch.setattr(scout, "fetch_bytes", fetch_bytes)
    index_path = sizes.prepare(daily(), store, tmp_path)
    index = json.loads(index_path.read_text())
    assert index_path.parent == tmp_path / "src" / "sizes"
    assert set(index) == {"zigzag:1", "zigzag:2", "zigzag:3"}  # 옷만 (가방·신발·소품 제외)
    info = index["zigzag:1"]
    assert info["size_lines"][1] == "M 총장 72 어깨 52"
    assert len(info["pieces"]) == 2 * 3 and info["overview"]
    assert "sizes" in sizes.summary(index_path)


def test_measure_writes_cm_and_size(tmp_path, store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(plan_tool, "Store", lambda account: store)
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump(daily().model_dump(mode="json"), allow_unicode=True))
    measures = [
        {
            "product": "zigzag:1",
            "found": True,
            "size": "M",
            "length": 74,
            "shoulder": 56,
            "chest": 62,
            "sleeve": 58,
        },
        {"product": "zigzag:3", "found": True, "length": 102, "waist": 35, "hem": 27},
        {"product": "zigzag:2", "found": False},
    ]
    (tmp_path / "sizes.yaml").write_text(yaml.safe_dump(measures))
    code = plan_tool.main(["measure", str(path), "--file", str(tmp_path / "sizes.yaml")])
    assert code == 0
    items = {it.product: it for it in plan_tool.load_plan(path).items}
    assert items["zigzag:1"].cm == 74
    assert items["zigzag:1"].size == (
        "about 74 cm long from shoulder to hem, 56 cm across the shoulders, "
        "62 cm across the chest laid flat, 58 cm long sleeves"
    )
    assert items["zigzag:3"].size.startswith("about 102 cm long from waist to hem, 35 cm")
    assert items["zigzag:2"].cm is None and items["zigzag:2"].size is None
