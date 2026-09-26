import base64
import io
import json
from pathlib import Path

import pytest
import yaml
from PIL import Image

from core.profile import REPO_ROOT, load_profile
from tools import compare, poc, scout
from tools.gemini_image import (
    CATEGORY_EN,
    ComposeItem,
    FakeClient,
    NoImageError,
    build_request,
    compose_parts,
    layout_lines,
    parse_image,
    prep_image,
    render_prompt,
    resolve_model,
)


def _img(path: Path, color=(120, 60, 30), size=(300, 500), bg="white") -> Path:
    img = Image.new("RGB", (size[0] + 200, size[1] + 100), bg)
    img.paste(Image.new("RGB", size, color), (100, 50))
    img.save(path)
    return path


def test_resolve_model_uses_config_keys():
    models = yaml.safe_load((REPO_ROOT / "config/models.yaml").read_text())["image"]
    assert resolve_model("default") == models["default"]
    assert resolve_model("some-explicit-id") == "some-explicit-id"


def test_prompts_render_without_leftover_placeholders():
    text = render_prompt("extract", category_en="outerwear", desc="브라운 레더자켓")
    assert "<!--" not in text and "$" not in text
    assert "브라운 레더자켓" in text

    backdrop = load_profile("women").visual.backdrop
    items = [ComposeItem(category="아우터", desc="자켓", image=Path("a.png"))]
    parts = compose_parts(items, backdrop, layout=True)
    prompt = parts[-1]
    assert backdrop in prompt and "$" not in prompt
    assert "Place the outer layer" in prompt


def test_layout_lines_cover_every_category():
    lines = layout_lines(list(CATEGORY_EN))
    assert len(lines.splitlines()) == len(CATEGORY_EN)


def test_compose_parts_label_precedes_each_image(tmp_path):
    items = [
        ComposeItem(category="상의", desc="셔츠", image=tmp_path / "a.png"),
        ComposeItem(category="신발", desc="로퍼", image=tmp_path / "b.png"),
    ]
    parts = compose_parts(items, "grey", layout=False)
    assert parts[0].startswith("Photo 1: top") and parts[1] == items[0].image
    assert parts[2].startswith("Photo 2: shoes") and parts[3] == items[1].image
    assert "Place" not in parts[-1]


def test_prep_image_trims_and_squares(tmp_path):
    out = prep_image(_img(tmp_path / "in.jpg"), tmp_path / "out.png", size=512)
    img = Image.open(out)
    assert img.size == (512, 512)
    # 상품(세로로 긴 사각형)이 가운데에 크게 들어가고 가장자리는 흰색
    assert img.getpixel((256, 256))[0] < 200
    assert img.getpixel((5, 256)) == (255, 255, 255)


def test_prep_image_flattens_transparency(tmp_path):
    src = tmp_path / "t.png"
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (40, 40), (0, 0, 255, 255)), (30, 30))
    img.save(src)
    out = Image.open(prep_image(src, tmp_path / "o.png", size=200))
    assert out.mode == "RGB" and out.getpixel((2, 2)) == (255, 255, 255)


def test_build_request_and_parse_roundtrip(tmp_path):
    body = build_request(["hello", _img(tmp_path / "a.png")], "4:5", "2K")
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "hello"}
    assert parts[1]["inlineData"]["mimeType"] == "image/png"
    cfg = body["generationConfig"]
    assert cfg["responseModalities"] == ["IMAGE"]
    assert cfg["imageConfig"] == {"aspectRatio": "4:5", "imageSize": "2K"}

    png = io.BytesIO()
    Image.new("RGB", (4, 5)).save(png, format="PNG")
    data = base64.b64encode(png.getvalue()).decode()
    resp = {
        "candidates": [{"content": {"parts": [{"text": "hi"}, {"inlineData": {"data": data}}]}}]
    }
    assert parse_image(resp) == png.getvalue()


def test_parse_image_explains_missing_image():
    resp = {"candidates": [{"finishReason": "SAFETY", "content": {"parts": [{"text": "no"}]}}]}
    with pytest.raises(NoImageError, match="SAFETY"):
        parse_image(resp)


def test_og_image_parsing():
    page = """<html><head>
    <meta name="description" content="x">
    <meta content="https://img.example.com/a.jpg?w=1&amp;h=2" property="og:image" />
    </head></html>"""
    assert scout.og_image(page) == "https://img.example.com/a.jpg?w=1&h=2"
    assert scout.og_image("<html></html>") is None


def test_contact_sheet(tmp_path):
    refs = [(f"r{i}", _img(tmp_path / f"r{i}.png")) for i in range(3)]
    results = [(f"x{i}", _img(tmp_path / f"x{i}.png", size=(400, 500))) for i in range(6)]
    out = compare.contact_sheet(refs, results, tmp_path / "s.jpg", title="t", per_row=5)
    w, h = Image.open(out).size
    assert w > 5 * 500 and h > 2 * 720


def test_poc_runs_end_to_end_with_fake_client(tmp_path, monkeypatch):
    colors = [(20, 20, 20), (240, 235, 220), (90, 90, 90), (120, 70, 40), (10, 10, 10)]
    cats = ["아우터", "상의", "하의", "가방", "신발"]
    items = []
    for i, (c, cat) in enumerate(zip(colors, cats, strict=True)):
        p = _img(tmp_path / f"p{i}.jpg", color=c)
        items.append({"category": cat, "desc": f"item {i}", "image": str(p)})
    samples = tmp_path / "s.yaml"
    samples.write_text(yaml.safe_dump({"account": "women", "name": "t", "items": items}))
    exp = poc.Experiment.model_validate(
        {
            "repeats": 2,
            "variants": [
                {"id": "A", "name": "a", "inputs": "extracted", "layout": False},
                {"id": "C", "name": "c", "inputs": "raw", "layout": True},
            ],
        }
    )
    out = tmp_path / "out"
    log = poc.run(samples, out, exp, fake=True)

    assert all(r["ok"] for r in log["runs"]) and len(log["runs"]) == 4
    assert log["images_generated"] == 5 + 4
    for f in ["sheets/extract.jpg", "sheets/variant_A.jpg", "sheets/variant_C.jpg", "report.md"]:
        assert (out / f).exists(), f
    assert Image.open(out / "results/A_1.jpg").size == (400, 500)  # 4:5
    rows = (out / "scores.csv").read_text().splitlines()
    assert len(rows) == 1 + 4 * 5
    assert json.loads((out / "run.json").read_text())["account"] == "women"


def test_sample_sets_are_valid():
    for path in (REPO_ROOT / "samples").rglob("*.yaml"):
        s = poc.SampleSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        load_profile(s.account)
        assert all(it.url or it.image for it in s.items), path
    poc.Experiment.model_validate(yaml.safe_load(poc.EXPERIMENT_PATH.read_text(encoding="utf-8")))


def test_fake_client_respects_ratio(tmp_path):
    data = FakeClient().generate([_img(tmp_path / "a.png"), "x"], "9:16", "1K")
    assert Image.open(io.BytesIO(data)).size == (900, 1600)


def test_gemini_client_raises_quota_error_on_429(tmp_path):
    import httpx

    from tools.gemini_image import GeminiClient, QuotaError

    transport = httpx.MockTransport(lambda req: httpx.Response(429, json={"error": "quota"}))
    client = GeminiClient("m", api_key="k", transport=transport)
    with pytest.raises(QuotaError, match="429"):
        client.generate(["x"], "1:1", "1K", attempts=1)


def test_gemini_client_sends_key_header_and_parses(tmp_path):
    import httpx

    from tools.gemini_image import GeminiClient

    png = io.BytesIO()
    Image.new("RGB", (2, 2)).save(png, format="PNG")
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers["x-goog-api-key"]
        seen["url"] = str(req.url)
        data = base64.b64encode(png.getvalue()).decode()
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"inlineData": {"data": data}}]}}]}
        )

    client = GeminiClient("gemini-x", api_key="k", transport=httpx.MockTransport(handler))
    assert client.generate(["x"], "1:1", "1K") == png.getvalue()
    assert seen["key"] == "k" and "models/gemini-x:generateContent" in seen["url"]
