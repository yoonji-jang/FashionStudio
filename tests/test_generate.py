import base64
import io

import pytest
import yaml
from PIL import Image

from tools import generate


def _png(color=(200, 30, 30, 255), size=(60, 90)) -> bytes:
    im = Image.new("RGBA", (100, 120), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", size, color), (20, 15))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_prepare_input_puts_cutout_on_white():
    out = Image.open(io.BytesIO(generate.prepare_input(_png(), 1024)))
    assert out.mode == "RGB"
    assert min(out.getpixel((1, 1))) > 235  # 흰 바탕 (JPEG 오차 허용)
    assert out.width < 100  # 투명 여백을 잘라냄


def test_build_prompt_fills_items_and_shot_values():
    spec = {
        "items": [
            {"category": "아우터", "desc": "gray coat"},
            {"category": "신발", "desc": "loafers"},
        ],
        "styling": "coat open",
        "shots": {"flatlay": {"repeats": 2, "scene": "linen floor"}},
    }
    text = generate.build_prompt("flatlay", spec)
    assert "Image 1: the outer, gray coat." in text
    assert "Image 2: the shoes, loafers." in text
    assert "coat open" in text and "linen floor" in text
    assert "$" not in text and "<!--" not in text


@pytest.mark.parametrize("shot", generate.SHOTS)
def test_all_prompt_templates_render(shot):
    spec = yaml.safe_load(
        (generate.REPO_ROOT / "experiments" / "gen_compare.yaml").read_text(encoding="utf-8")
    )
    assert "$" not in generate.build_prompt(shot, spec)


def test_payload_and_parse():
    cfg = generate.load_config()
    model = cfg["models"]["seedream_5_pro"]
    payload = generate.build_payload(model, "hi", [b"abc"], cfg)
    assert payload["modalities"] == ["image"]
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg")
    b64 = base64.b64encode(b"img").decode()
    image = {"image_url": {"url": f"data:image/png;base64,{b64}"}}
    body = {"choices": [{"message": {"images": [image]}}], "usage": {"cost": 0.05}}
    assert generate.parse_response(body) == (b"img", 0.05)
    with pytest.raises(RuntimeError):
        generate.parse_response({"choices": [{"message": {"content": "no image"}}]})


def test_fit_canvas_crops_to_4_5():
    buf = io.BytesIO()
    Image.new("RGB", (1024, 1536), "gray").save(buf, "PNG")
    assert generate.fit_canvas(buf.getvalue()).size == (1080, 1350)


def test_run_with_fake_client(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(_png())
    spec = {
        "title": "t",
        "guide": str(img),
        "compare_with": str(img),
        "items": [{"category": "상의", "desc": "knit", "image": str(img)}],
        "styling": "s",
        "shots": {"flatlay": {"repeats": 1, "scene": "x"}, "mannequin": {"mannequin": "m"}},
    }
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    cfg = generate.load_config()
    rec = generate.run(
        spec_path, tmp_path / "out", generate.FakeClient(), cfg, models=["nano_banana_pro"]
    )
    assert [r["ok"] for r in rec["results"]] == [True, True]
    assert (tmp_path / "out" / "sheet.jpg").exists()
    assert "2/2" in (tmp_path / "out" / "report.md").read_text(encoding="utf-8")


def test_reference_panel_joins_photos_side_by_side():
    buf = io.BytesIO()
    Image.new("RGB", (400, 600), "gray").save(buf, "JPEG")
    panel = Image.open(io.BytesIO(generate.reference_panel([_png(), buf.getvalue()], 1536)))
    assert panel.width > panel.height


def test_list_values_rotate_by_repeat():
    spec = {
        "items": [{"category": "상의", "desc": "knit"}],
        "shots": {"person": {"person": ["mirror selfie", "head turned"], "mood": "m"}},
    }
    assert "mirror selfie" in generate.build_prompt("person", spec, 1)
    assert "head turned" in generate.build_prompt("person", spec, 2)
    assert "mirror selfie" in generate.build_prompt("person", spec, 3)
