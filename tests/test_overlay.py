import pytest
from PIL import Image

from core.profile import load_profile
from tools.overlay import apply, fit_font, gradient, main


@pytest.fixture
def visual():
    return load_profile("women").visual


def _grey(size=(1080, 1350)):
    return Image.new("RGB", size, (200, 200, 200))


def test_gradient_dark_at_bottom_clear_above(visual):
    g = gradient((100, 1000), visual.gradient_height, visual.gradient_opacity)
    top = round(1000 * (1 - visual.gradient_height)) - 1
    assert g.getpixel((50, top))[3] == 0
    assert g.getpixel((50, 999))[3] == pytest.approx(255 * visual.gradient_opacity, abs=2)


def test_apply_keeps_size_and_top_untouched(visual):
    img = _grey()
    out = apply(img, ["가을 필수템 야상", "고급스럽게 입는 법"], visual)
    assert out.size == img.size and out.mode == "RGB"
    assert out.getpixel((540, 100)) == (200, 200, 200)
    assert out.crop((0, 1000, 1080, 1350)).getextrema()[0][1] == 255  # 흰 글자가 들어감


def _accent_x(visual) -> int:
    margin = round(1080 * visual.title_margin_x)
    return margin - visual.accent_gap - visual.accent_width // 2 - 1


def test_accent_bar_left_of_title(visual):
    out = apply(_grey(), ["가을 필수템 가디건", "단정하게 입는 법"], visual)
    y = round(1350 * (1 - visual.title_bottom)) - 20
    accent = tuple(int(visual.accent_color[i : i + 2], 16) for i in (1, 3, 5))
    assert out.getpixel((_accent_x(visual), y)) == accent


def test_no_accent_bar_when_unset_or_centered(visual):
    y = round(1350 * (1 - visual.title_bottom)) - 20
    for update in ({"accent_color": None}, {"title_align": "center"}):
        v = visual.model_copy(update=update)
        out = apply(_grey(), ["가을 필수템 가디건", "단정하게 입는 법"], v)
        r, g, b = out.getpixel((_accent_x(visual), y))
        assert abs(r - g) < 4 and abs(g - b) < 4  # 회색 그대로 (그라데이션만)


def test_title_stays_inside_margins(visual):
    lines = ["아주 아주 긴 제목이 들어와도 잘리지 않게", "둘째 줄"]
    margin = round(1080 * visual.title_margin_x)
    font = fit_font(lines, visual.font, visual.title_font_size, 1080 - 2 * margin)
    assert font.size < visual.title_font_size
    assert max(font.getlength(s) for s in lines) <= 1080 - 2 * margin


def test_rejects_too_long_or_too_many_lines(visual):
    with pytest.raises(ValueError):
        apply(_grey(), ["가" * 60], visual)
    with pytest.raises(ValueError):
        apply(_grey(), ["하나", "둘", "셋"], visual)


def test_scales_with_image_width(visual):
    assert apply(_grey((540, 675)), ["가을 코디", "모음"], visual).size == (540, 675)


def test_cli(tmp_path):
    src = tmp_path / "in.jpg"
    _grey().save(src)
    out = tmp_path / "out.jpg"
    args = ["--account", "women", "--image", str(src), "--title", "첫 줄", "둘째 줄"]
    assert main([*args, "--out", str(out)]) == 0
    assert Image.open(out).size == (1080, 1350)
