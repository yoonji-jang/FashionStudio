import io

from PIL import Image

from core.profile import AiLabel, load_profile
from tests.test_feed import spec  # noqa: F401 - pytest fixture
from tools import ai_label
from tools.feed import render

ON = AiLabel(mode="metadata")


def jpeg(tmp_path, name="01_cover.jpg", **kw):
    path = tmp_path / name
    Image.new("RGB", (40, 50), (120, 90, 80)).save(path, "JPEG", quality=90, **kw)
    return path


def test_render_marks_only_cover(spec, tmp_path):  # noqa: F811
    profile = load_profile("women")
    profile.ai_label = ON
    paths = render(spec, tmp_path / "out", root=tmp_path, profile=profile)
    marks = {p.name: ai_label.read(p) for p in paths}
    assert marks["01_cover.jpg"] == "compositeWithTrainedAlgorithmicMedia"
    assert [v for k, v in marks.items() if k != "01_cover.jpg"] == [None, None, None, None]


def test_render_without_label(spec, tmp_path):  # noqa: F811
    profile = load_profile("women")
    profile.ai_label = AiLabel(mode="none")
    paths = render(spec, tmp_path / "out", root=tmp_path, profile=profile)
    assert all(ai_label.read(p) is None for p in paths)


def test_generated_type_and_slide_kinds(tmp_path):
    label = AiLabel(mode="metadata", source_type="generated", slides=["cover", "summary"])
    cover = jpeg(tmp_path, "01_cover.jpg", **ai_label.save_options(label, "cover"))
    assert ai_label.read(cover) == "trainedAlgorithmicMedia"
    assert ai_label.save_options(label, "products") == {}
    assert ai_label.slide_kind(tmp_path / "09_summary.jpg") == "summary"
    assert ai_label.slide_kind(tmp_path / "10_comment.jpg") == "comment"
    assert ai_label.slide_kind(tmp_path / "02_아우터.jpg") == "products"


def test_ensure_adds_mark_without_reencoding(tmp_path):
    cover = jpeg(tmp_path)
    product = jpeg(tmp_path, "02_상의.jpg")
    before = Image.open(cover).tobytes()
    product_bytes = product.read_bytes()
    assert ai_label.ensure(ON, [cover, product]) == ["01_cover.jpg"]
    with Image.open(cover) as im:
        assert im.format == "JPEG" and im.tobytes() == before
    assert ai_label.read(cover) == "compositeWithTrainedAlgorithmicMedia"
    assert product.read_bytes() == product_bytes
    # 이미 있으면 그대로
    assert ai_label.ensure(ON, [cover]) == []


def test_insert_replaces_old_xmp(tmp_path):
    old = ai_label.xmp_packet("generated")
    cover = jpeg(tmp_path, xmp=old)
    data = ai_label.insert_xmp(cover.read_bytes(), ai_label.xmp_packet("composite"))
    assert data.count(ai_label.XMP_HEADER) == 1
    with Image.open(io.BytesIO(data)) as im:
        assert b"compositeWithTrainedAlgorithmicMedia" in im.info["xmp"]
