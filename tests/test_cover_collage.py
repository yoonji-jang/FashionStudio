"""여러 벌 콜라주 표지 (tools.cover_collage)."""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.models import FeedItem, FeedLook, FeedSpec
from core.profile import load_profile
from tools import cover_collage, feed


def flatlay(path, color, floor=(150, 152, 154)):
    """회색 바닥 위에 옷(직사각형 둘)과 바닥 그림자가 있는 가짜 코디컷."""
    img = Image.new("RGB", (540, 675), floor)
    d = ImageDraw.Draw(img)
    d.rectangle((200, 170, 360, 520), fill=(120, 122, 124))  # 그림자 (바닥보다 어둡고 색 없음)
    d.rectangle((180, 150, 340, 330), fill=color)
    d.rectangle((210, 330, 310, 500), fill=(235, 228, 210))
    d.rectangle((0, 0, 60, 30), fill=(245, 245, 245))  # 테두리에 닿는 창빛
    img.save(path)
    return path


def test_cutout_keeps_clothes_drops_floor_and_window_light(tmp_path):
    cut = cover_collage.cutout(flatlay(tmp_path / "a.jpg", (120, 70, 40)))
    a = np.asarray(cut.getchannel("A"))
    # 옷 범위로 잘림 (창빛·바닥은 빠짐)
    assert 140 <= cut.width <= 180 and 330 <= cut.height <= 370
    assert a[cut.height // 4, cut.width // 2] > 200  # 갈색 윗옷
    assert a[cut.height * 3 // 4, cut.width // 2] > 200  # 아이보리 바지
    assert a[cut.height * 3 // 4, cut.width - 3] < 50  # 바지 옆 그림자는 뺌


def test_cutout_fails_on_empty_floor(tmp_path):
    path = tmp_path / "empty.jpg"
    Image.new("RGB", (540, 675), (150, 152, 154)).save(path)
    with pytest.raises(ValueError):
        cover_collage.cutout(path)


def test_row_counts():
    assert cover_collage.row_counts(6) == (3, 3)
    assert cover_collage.row_counts(5) == (3, 2)


@pytest.mark.looks
def test_collage_cover_slide_and_tall(tmp_path):
    profile = load_profile("women")
    assert profile.looks.cover_collage.enabled
    colors = [(120, 70, 40), (30, 40, 60), (90, 30, 40), (40, 70, 50), (60, 60, 64), (140, 110, 60)]
    images = [flatlay(tmp_path / f"{i}.jpg", c) for i, c in enumerate(colors)]
    looks = [FeedLook(image=str(p), items=[FeedItem(name="니트")]) for p in images]
    spec = FeedSpec(
        account="women",
        title_lines=["브라운 코디 촌스럽지 않게", "하늘색 한 방울 공식"],
        cover=str(images[0]),
        items=[FeedItem(name="니트")],
        looks=looks,
        cover_collage=True,
    )
    size = feed.canvas_size(profile)
    img = feed.cover_slide(spec, profile, size, tmp_path)
    assert img.size == size
    old = feed.cover_slide(
        spec.model_copy(update={"cover_collage": False}), profile, size, tmp_path
    )
    assert img.tobytes() != old.tobytes()
    # 가운데 제목 줄(흰 글자)이 있고, 위아래 줄에 벌이 놓임
    a = np.asarray(img).astype(int)
    mid = a[round(1350 * 0.45) : round(1350 * 0.56)]
    assert (mid.min(axis=2) > 235).sum() > 2000
    top_row = a[round(1350 * 0.222)]
    assert (np.abs(top_row - top_row.mean(axis=0)).sum(axis=1) > 60).sum() > 100
    tall = feed.cover_slide(spec, profile, feed.TALL_SIZE, tmp_path)
    assert tall.size == feed.TALL_SIZE


@pytest.mark.looks
def test_collage_falls_back_to_single_cover(tmp_path, capsys):
    profile = load_profile("women")
    path = tmp_path / "empty.jpg"
    Image.new("RGB", (1080, 1350), (150, 152, 154)).save(path)
    spec = FeedSpec(
        account="women",
        title_lines=["첫 줄 제목", "둘째 줄 제목"],
        cover=str(path),
        items=[FeedItem(name="니트")],
        looks=[FeedLook(image=str(path), items=[FeedItem(name="니트")])],
        cover_collage=True,
    )
    img = feed.cover_slide(spec, profile, feed.canvas_size(profile), tmp_path)
    assert img.size == (1080, 1350)
    assert "1번 벌 표지" in capsys.readouterr().err
