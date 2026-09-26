import io
import json
from pathlib import Path

import httpx
import pytest
import yaml
from PIL import Image

from core.profile import REPO_ROOT, load_profile
from tools import compare, compose, photoroom, poc, scout


def _img(path: Path, color=(120, 60, 30), size=(300, 500), bg="white") -> Path:
    img = Image.new("RGB", (size[0] + 200, size[1] + 100), bg)
    img.paste(Image.new("RGB", size, color), (100, 50))
    img.save(path)
    return path


def _cutout(path: Path, color=(120, 60, 30), size=(300, 400)) -> Path:
    img = Image.new("RGBA", (size[0] + 40, size[1] + 40), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", size, (*color, 255)), (20, 20))
    img.save(path)
    return path


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class RecordingClient:
    """보낸 필드를 기록하고 정해 둔 이미지를 돌려주는 가짜 클라이언트."""

    def __init__(self, responses: list[bytes]):
        self.responses = responses
        self.sent: list[tuple[dict, dict]] = []
        self.calls = 0

    def edit(self, fields, files):
        self.sent.append((fields, files))
        self.calls += 1
        return self.responses.pop(0)


# ---------------------------------------------------------------- 설정·지시문


def test_prompts_render_without_leftover_placeholders():
    text = photoroom.render_prompt("flatlay", desc="브라운 레더자켓")
    assert "브라운 레더자켓" in text and "$" not in text and "<!--" not in text
    backdrop = load_profile("women").visual.backdrop
    for name in ("backdrop", "background"):
        text = photoroom.render_prompt(name, backdrop=backdrop)
        assert backdrop in text and "$" not in text


def test_config_has_required_keys():
    cfg = photoroom.load_config()
    for key in ("endpoint", "shadow_model_version", "flat_lay_size", "backdrop_size"):
        assert cfg[key]


def test_default_method_by_category():
    assert photoroom.default_method("아우터") == "flatlay"
    assert photoroom.default_method("하의") == "flatlay"
    assert photoroom.default_method("가방") == "segment"
    assert photoroom.default_method("소품") == "segment"


def test_layout_covers_every_category():
    from core.models import Category

    layout = compose.load_layout()
    assert set(layout["slots"]) == set(Category.__args__)


# ---------------------------------------------------------------- Photoroom 호출


def test_cutout_flatlay_fields_and_trim(tmp_path):
    transparent = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    transparent.paste(Image.new("RGBA", (50, 80), (10, 10, 10, 255)), (20, 30))
    client = RecordingClient([_png_bytes(transparent)])
    out = photoroom.cutout(client, _img(tmp_path / "a.jpg"), "아우터", "자켓", tmp_path / "o.png")
    fields, files = client.sent[0]
    assert fields["flatLay.mode"] == "ai.auto" and "자켓" in fields["flatLay.prompt"]
    assert "segmentation.prompt" not in fields and "imageFile" in files
    assert Image.open(out).size == (50, 80)  # 투명 가장자리 잘림


def test_cutout_segment_and_second_pass_when_opaque(tmp_path):
    opaque = Image.new("RGB", (100, 100), "white")
    cut = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    cut.paste(Image.new("RGBA", (30, 30), (200, 0, 0, 255)), (10, 10))
    client = RecordingClient([_png_bytes(opaque), _png_bytes(cut)])
    photoroom.cutout(
        client, _img(tmp_path / "a.jpg"), "소품", "벨트", tmp_path / "o.png", segment="belt"
    )
    assert client.sent[0][0]["segmentation.prompt"] == "belt"
    assert len(client.sent) == 2  # 배경이 남아 있어 한 번 더 지움
    assert Image.open(tmp_path / "o.png").size == (30, 30)


def test_finish_modes(tmp_path):
    jpg = _png_bytes(Image.new("RGB", (1080, 1350), "grey"))
    layered = _cutout(tmp_path / "l.png")
    bd = _img(tmp_path / "bd.jpg")
    client = RecordingClient([jpg, jpg])
    photoroom.finish(client, layered, tmp_path / "s.jpg", "ai_shadow", (1080, 1350), bd)
    photoroom.finish(client, layered, tmp_path / "b.jpg", "ai_background", (1080, 1350), None, "x")
    shadow, background = client.sent[0][0], client.sent[1][0]
    for f in (shadow, background):
        assert f["keepExistingAlphaChannel"] == "auto" and f["referenceBox"] == "originalImage"
        assert f["outputSize"] == "1080x1350"
    assert shadow["shadow.subjectPoseOverride"] == "flatlay"
    assert "background.imageFile" in client.sent[0][1]
    assert "background.prompt" in background and "shadow.mode" not in background


def test_client_headers_sandbox_and_quota(tmp_path):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers["x-api-key"]
        seen["shadow"] = req.headers.get("pr-ai-shadows-model-version")
        if b"quota" in req.content:
            return httpx.Response(402, text="no credits")
        return httpx.Response(200, content=b"img")

    cfg = photoroom.load_config()
    client = photoroom.PhotoroomClient(
        "k", sandbox=True, transport=httpx.MockTransport(handler), config=cfg
    )
    fields = {"shadow.mode": "ai.auto-with-overrides"}
    assert client.edit(fields, {"imageFile": _img(tmp_path / "a.jpg")}) == b"img"
    assert seen["key"] == "sandbox_k" and seen["shadow"] == cfg["shadow_model_version"]
    with pytest.raises(photoroom.QuotaError):
        client.edit({"x": "quota"}, {}, attempts=1)


def test_client_requires_key(monkeypatch):
    monkeypatch.delenv("PHOTOROOM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PHOTOROOM_API_KEY"):
        photoroom.PhotoroomClient()


# ---------------------------------------------------------------- 합성·비교표


def test_layer_places_items_in_z_order(tmp_path):
    layout = compose.load_layout()
    top = _cutout(tmp_path / "top.png", color=(0, 0, 255))
    outer = _cutout(tmp_path / "outer.png", color=(255, 0, 0))
    canvas = compose.layer([("상의", top), ("아우터", outer)], layout)
    assert canvas.size == tuple(layout["canvas"])
    w, h = canvas.size
    cx, cy = layout["slots"]["아우터"]["center"]
    assert canvas.getpixel((round(cx * w), round(cy * h)))[:3] == (255, 0, 0)  # 아우터가 위
    assert canvas.getpixel((2, 2))[3] == 0


def test_layer_moves_top_when_no_outer(tmp_path):
    layout = compose.load_layout()
    canvas = compose.layer([("상의", _cutout(tmp_path / "t.png", color=(0, 0, 255)))], layout)
    cx, cy = layout["no_outer"]["상의"]["center"]
    w, h = canvas.size
    assert canvas.getpixel((round(cx * w), round(cy * h)))[:3] == (0, 0, 255)


def test_compose_with_code_shadow(tmp_path):
    out = compose.compose(
        [("하의", _cutout(tmp_path / "b.png"))],
        _img(tmp_path / "bd.jpg", color=(200, 200, 200), bg=(200, 200, 200)),
        tmp_path / "look.jpg",
        layered_out=tmp_path / "layered.png",
    )
    img = Image.open(out)
    assert img.mode == "RGB" and img.size == (1080, 1350)
    assert Image.open(tmp_path / "layered.png").mode == "RGBA"


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


# ---------------------------------------------------------------- 실험 전체


def test_poc_runs_end_to_end_with_fake_client(tmp_path):
    colors = [(20, 20, 20), (240, 200, 180), (90, 90, 90), (120, 70, 40), (10, 60, 10)]
    cats = ["아우터", "상의", "하의", "가방", "신발"]
    items = [
        {"category": cat, "desc": f"item {i}", "image": str(_img(tmp_path / f"p{i}.jpg", color=c))}
        for i, (c, cat) in enumerate(zip(colors, cats, strict=True))
    ]
    items[3]["crop"] = [0.0, 0.0, 0.8, 0.9]
    samples = tmp_path / "s.yaml"
    samples.write_text(yaml.safe_dump({"account": "women", "name": "t", "items": items}))
    exp = poc.Experiment.model_validate(
        {
            "repeats": 2,
            "variants": [
                {"id": "A", "name": "a", "finish": "code"},
                {"id": "B", "name": "b", "finish": "ai_shadow"},
                {"id": "C", "name": "c", "finish": "ai_background"},
            ],
        }
    )
    out = tmp_path / "out"
    log = poc.run(samples, out, exp, fake=True)

    assert all(r["ok"] for r in log["runs"]) and len(log["runs"]) == 6
    # 배경 1 + 누끼(1회차 5, 2회차 옷 3) + AI 마무리(B, C × 2회차)
    assert log["api_calls"] == 1 + 5 + 3 + 4
    for f in ["sheets/cutouts.jpg", "sheets/variant_A.jpg", "sheets/variant_C.jpg", "report.md"]:
        assert (out / f).exists(), f
    assert Image.open(out / "results/A_1.jpg").size == (1080, 1350)
    assert (out / "originals" / "04_bag_crop.png").exists()
    rows = (out / "scores.csv").read_text().splitlines()
    assert len(rows) == 1 + 6 * 5
    assert json.loads((out / "run.json").read_text())["est_cost_usd"] == 0


def test_sample_sets_are_valid():
    for path in (REPO_ROOT / "samples").rglob("*.yaml"):
        s = poc.SampleSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        load_profile(s.account)
        assert all(it.url or it.image for it in s.items), path
    poc.Experiment.model_validate(yaml.safe_load(poc.EXPERIMENT_PATH.read_text(encoding="utf-8")))
