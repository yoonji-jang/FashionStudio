import io
import json
from pathlib import Path

import httpx
import pytest
import yaml
from PIL import Image, ImageChops

from core.models import Category
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
    for name in ("backdrop", "background", "scene"):
        text = photoroom.render_prompt(name, backdrop=backdrop)
        assert backdrop in text and "$" not in text
    for slot in compose.load_layout()["slots"]:
        text = photoroom.render_prompt("refine", desc="x", hint=photoroom.refine_hint(slot))
        assert "$" not in text
    text = photoroom.render_prompt("style", base_desc="자켓", add_desc="셔츠", how="inside")
    assert "자켓" in text and "셔츠" in text and "$" not in text


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

    layout = compose.load_layout()
    assert set(Category.__args__) <= set(layout["slots"])


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


def _cut_bytes(size=(60, 90)) -> bytes:
    cut = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    cut.paste(Image.new("RGBA", size, (200, 0, 0, 255)), (10, 10))
    return _png_bytes(cut)


def test_refine_edits_with_ai_then_removes_background(tmp_path):
    opaque = _png_bytes(Image.new("RGB", (1024, 1024), "white"))
    client = RecordingClient([opaque, _cut_bytes()])
    out = photoroom.refine(client, _cutout(tmp_path / "c.png"), "신발", "로퍼", tmp_path / "r.png")
    (edit, files), (second, _) = client.sent
    assert edit["editWithAI.mode"] == "ai.auto" and edit["removeBackground"] == "false"
    assert "로퍼" in edit["editWithAI.prompt"] and "insole" in edit["editWithAI.prompt"]
    square = Image.open(io.BytesIO(files["imageFile"]))
    assert square.width == square.height  # 1K 정사각형 결과에 맞춰 흰 정사각형으로 보냄
    assert "editWithAI.mode" not in second
    assert Image.open(out).size == (60, 90)


def test_style_sends_reference_image(tmp_path):
    opaque = _png_bytes(Image.new("RGB", (1024, 1024), "white"))
    client = RecordingClient([opaque, _cut_bytes()])
    base, add = _cutout(tmp_path / "a.png"), _cutout(tmp_path / "b.png")
    photoroom.style(client, base, "자켓", add, "셔츠", "shirt inside", tmp_path / "s.png")
    fields, files = client.sent[0]
    assert "shirt inside" in fields["editWithAI.prompt"]
    assert "editWithAI.additionalImages.worn.imageFile" in files

    client = RecordingClient([opaque, opaque, _cut_bytes()])
    photoroom.style(client, base, "자켓", add, "셔츠", "inside", tmp_path / "o.png", mode="open")
    first, second = client.sent[0], client.sent[1]
    assert "unbuttoned" in first[0]["editWithAI.prompt"] and len(first[1]) == 1  # 자켓만 먼저 연다
    assert "editWithAI.additionalImages.worn.imageFile" in second[1]
    assert (tmp_path / "o_opened.png").exists()

    client = RecordingClient([opaque, _cut_bytes()])
    photoroom.style(client, base, "자켓", add, "셔츠", "inside", tmp_path / "v.png", mode="over")
    fields, files = client.sent[0]
    assert "put on over" in fields["editWithAI.prompt"]
    assert "editWithAI.additionalImages.outer.imageFile" in files

    client = RecordingClient([opaque, _cut_bytes()])
    photoroom.style(
        client, base, "가디건", add, "셔츠", "collar out", tmp_path / "p.png", mode="peek"
    )
    fields, files = client.sent[0]
    assert "collar out" in fields["editWithAI.prompt"] and "셔츠" in fields["editWithAI.prompt"]
    # 셔츠는 깃 부분만 잘라 보낸다 (통째로 보내면 몸판·소매를 붙임)
    assert list(files) == ["imageFile", "editWithAI.additionalImages.collar.imageFile"]
    collar = Image.open(io.BytesIO(files["editWithAI.additionalImages.collar.imageFile"]))
    assert collar.size[0] < Image.open(add).size[0]


def test_scene_keeps_canvas_size(tmp_path):
    client = RecordingClient([_png_bytes(Image.new("RGB", (1024, 1024), "grey"))])
    composed = _img(tmp_path / "c.jpg")
    out = photoroom.scene(client, composed, tmp_path / "s.jpg", (1080, 1350), "grey wall")
    sent = Image.open(io.BytesIO(client.sent[0][1]["imageFile"]))
    assert sent.width == sent.height  # 정사각형으로 보내 가장자리가 잘리지 않게
    assert Image.open(out).size == (1080, 1350)


def test_trim_ignores_faint_watermark():
    img = Image.new("RGBA", (300, 300), (0, 0, 0, 40))  # 샌드박스 워터마크처럼 옅은 알파
    img.paste(Image.new("RGBA", (50, 70), (0, 0, 0, 255)), (100, 100))
    assert photoroom.trim_alpha(img).size == (50, 70)


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
    bottom = _cutout(tmp_path / "bottom.png", color=(0, 0, 255))
    outer = _cutout(tmp_path / "outer.png", color=(255, 0, 0))
    item = {"category": "아우터", "desc": "롱코트", "cm": 110, "url": "u"}
    samples = poc.SampleSet.model_validate({"account": "women", "name": "t", "items": [item]})
    layout = poc.with_item_sizes(layout, samples)
    canvas = compose.layer([("하의", bottom), ("아우터", outer)], layout)
    assert canvas.size == tuple(layout["canvas"])
    w, h = canvas.size
    # 롱코트 밑단 근처는 바지 위쪽과 겹치고, 아우터가 위에 놓인다
    x = round(layout["slots"]["하의"]["center"][0] * w)
    rule = layout["slots"]["아우터"]
    hem = rule["center"][1] * h + rule["cm"] * layout["px_per_cm"] / 2
    assert canvas.getpixel((x, round(hem) - 20))[:3] == (255, 0, 0)
    assert canvas.getpixel((2, 2))[3] == 0


def test_layer_puts_unworn_top_beside_outer(tmp_path):
    layout = compose.load_layout()
    top = _cutout(tmp_path / "top.png", color=(0, 0, 255))
    outer = _cutout(tmp_path / "outer.png", color=(255, 0, 0))
    canvas = compose.layer([("상의", top), ("아우터", outer)], layout)
    w, h = canvas.size
    for cat, color in (("아우터", (255, 0, 0)), ("상의", (0, 0, 255))):
        cx, cy = layout["outer_and_top"][cat]["center"]
        assert canvas.getpixel((round(cx * w), round(cy * h)))[:3] == color  # 가려지지 않음


def test_layer_moves_top_when_no_outer(tmp_path):
    layout = compose.load_layout()
    canvas = compose.layer([("상의", _cutout(tmp_path / "t.png", color=(0, 0, 255)))], layout)
    cx, cy = layout["no_outer"]["상의"]["center"]
    w, h = canvas.size
    assert canvas.getpixel((round(cx * w), round(cy * h)))[:3] == (0, 0, 255)


def test_layer_uses_real_size(tmp_path):
    layout = compose.load_layout()
    rule = layout["slots"]["하의"]
    item = compose.fit_real_size(Image.new("RGBA", (100, 300)), rule, layout)
    assert item.height == round(rule["cm"] * layout["px_per_cm"])
    shoes = compose.fit_real_size(Image.new("RGBA", (400, 100)), layout["slots"]["신발"], layout)
    assert shoes.width == round(layout["slots"]["신발"]["cm"] * layout["px_per_cm"])  # 긴 쪽 기준


def test_jewelry_slots_sit_in_empty_space(tmp_path):
    layout = compose.load_layout()
    clothes = [(c, _cutout(tmp_path / f"{c}.png", size=(300, 700))) for c in ("아우터", "하의")]
    body = compose.layer(clothes, layout).getchannel("A")
    for slot in ("목걸이", "귀걸이", "팔찌"):
        jewel = compose.layer([(slot, _cutout(tmp_path / f"{slot}.png"))], layout).getchannel("A")
        overlap = ImageChops.multiply(body, jewel).getbbox()
        assert overlap is None, slot  # 옷과 겹치지 않는다


def test_layer_keeps_wide_items_inside_canvas(tmp_path):
    layout = compose.load_layout()
    wide = _cutout(tmp_path / "bag.png", size=(800, 300))  # 가방이 가로로 길면 왼쪽이 잘렸다
    alpha = compose.layer([("가방", wide)], layout).getchannel("A")
    x0, _, x1, _ = alpha.getbbox()
    assert x0 > 0 and x1 < layout["canvas"][0]


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
    assert scout.is_placeholder("https://cf.image-farm.s.zigzag.kr/original/cms/2024/04/18/a.png")
    assert not scout.is_placeholder(
        "https://cf.product-image.s.zigzag.kr/original/c/1/2/3/1-6.jpeg"
    )


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


def test_poc_clean_variant_merges_worn_items(tmp_path):
    cats = ["아우터", "상의", "하의", "신발", "소품"]
    items = [
        {"category": c, "desc": f"item {i}", "image": str(_img(tmp_path / f"p{i}.jpg"))}
        for i, c in enumerate(cats)
    ]
    items[1] |= {"wear_with": "아우터", "wear_how": "inside", "wear_modes": ["over", "open"]}
    items[4] |= {"wear_with": "하의"}
    samples = tmp_path / "s.yaml"
    samples.write_text(yaml.safe_dump({"account": "women", "name": "t", "items": items}))
    exp = poc.Experiment.model_validate(
        {
            "repeats": 1,
            "variants": [
                {"id": "A", "name": "a", "finish": "code"},
                {"id": "D", "name": "d", "finish": "ai_scene", "clean": True},
            ],
        }
    )
    out = tmp_path / "out"
    log = poc.run(samples, out, exp, fake=True)
    assert all(r["ok"] for r in log["runs"]), log["runs"]
    stages = [e["stage"] for e in log["cutouts"] if "stage" in e]
    assert stages.count("refine") == 2 and stages.count("style") == 3  # 신발·소품 다듬기
    # 배경 1 + 누끼 5 + 다듬기 2×2 + 입히기(걸치기 2, 열고 입히기 3, 벨트 2) + 장면 1
    assert log["api_calls"] == 1 + 5 + 4 + 2 + 3 + 2 + 1
    assert (out / "styled/r1/01_outer+top_over.png").exists()
    assert (out / "styled/r1/01_outer+top_open_opened.png").exists()
    assert (out / "sheets/clean.jpg").exists() and (out / "results/D_1.jpg").exists()


def test_real_length_from_name():
    layout = compose.load_layout()
    assert compose.real_length("아우터", "차콜 핸드메이드 롱코트", layout) == 110
    assert compose.real_length("아우터", "하프 롱코트", layout) == 88  # 하프가 먼저
    assert compose.real_length("아우터", "크롭 트위드 자켓", layout) == 48
    assert compose.real_length("아우터", "울 자켓", layout) is None
    assert compose.real_length("하의", "플리츠 롱스커트", layout) == 85


def test_long_outer_keeps_shoulder_and_bottom_keeps_waist():
    layout = compose.load_layout()
    items = [
        {"category": "아우터", "desc": "x", "cm": 110, "url": "u"},
        {"category": "하의", "desc": "x", "cm": 40, "url": "u"},
    ]
    samples = poc.SampleSet.model_validate({"account": "women", "name": "t", "items": items})
    changed = poc.with_item_sizes(layout, samples)
    px, h = layout["px_per_cm"], layout["canvas"][1]

    def top(rule):
        return rule["center"][1] * h - rule["cm"] * px / 2

    for group in ("slots", "outer_and_top"):
        for cat in ("아우터", "하의"):
            assert abs(top(changed[group][cat]) - top(layout[group][cat])) < 1e-6, (group, cat)
    assert changed["slots"]["아우터"]["cm"] == 110


def test_item_cm_override_keeps_hem():
    layout = compose.load_layout()
    item = {"category": "아우터", "desc": "x", "cm": 50, "url": "u"}
    samples = poc.SampleSet.model_validate({"account": "women", "name": "t", "items": [item]})
    changed = poc.with_item_sizes(layout, samples)
    old, new = layout["slots"]["아우터"], changed["slots"]["아우터"]
    px, h = layout["px_per_cm"], layout["canvas"][1]

    def hem(rule):
        return rule["center"][1] * h + rule["cm"] * px / 2

    assert new["cm"] == 50 and abs(hem(new) - hem(old)) < 1e-6
    assert layout["slots"]["아우터"]["cm"] == 68  # 원본은 그대로


def test_sample_sets_are_valid():
    for path in (REPO_ROOT / "samples").rglob("*.yaml"):
        if path.name.startswith(("feed_", "plan_")):  # 캐러셀 재료, 하루 기획 (각자 테스트)
            continue
        s = poc.SampleSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        load_profile(s.account)
        assert all(it.url or it.image or it.cutout for it in s.items), path
        slots = compose.load_layout()["slots"]
        assert all(it.place in slots for it in s.items), path
    poc.Experiment.model_validate(yaml.safe_load(poc.EXPERIMENT_PATH.read_text(encoding="utf-8")))


def test_client_sends_multipart_even_without_files():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["ctype"] = req.headers["content-type"]
        seen["body"] = req.content
        return httpx.Response(200, content=b"img")

    client = photoroom.PhotoroomClient("k", transport=httpx.MockTransport(handler))
    client.edit({"imageFromPrompt.prompt": "grey wall"}, {})
    assert seen["ctype"].startswith("multipart/form-data")
    assert b'name="imageFromPrompt.prompt"' in seen["body"] and b"grey wall" in seen["body"]
