import pytest
from PIL import Image

from core.models import FeedItem, FeedSpec
from core.profile import load_profile
from tools.feed import clean_name, main, render, wrap
from tools.overlay import load_font


def test_clean_name_drops_ads_and_codes():
    assert (
        clean_name("[🍂CHUSEOK20%][MADE] mjk467 헤일리 오버핏 레더 자켓")
        == "헤일리 오버핏 레더 자켓"
    )
    assert clean_name("(woman) 싱글 레더 하프자켓_Black") == "싱글 레더 하프자켓 Black"
    assert clean_name("[직잭단독🎀]단작 비대칭 벨티드 셔츠_MIWYWG921C") == "단작 비대칭 벨티드 셔츠"
    assert clean_name("반하이 모찌슬랙스 #세미부츠컷 (4type)") == "반하이 모찌슬랙스"


def test_wrap_limits_lines_with_ellipsis():
    font = load_font("Pretendard-Bold.otf", 40)
    lines = wrap("아주 긴 상품 이름이 여러 줄로 넘어가는 경우를 확인합니다 " * 3, font, 400, 2)
    assert len(lines) == 2 and lines[-1].endswith("…")
    assert all(font.getlength(s) <= 400 for s in lines)


@pytest.fixture
def spec(tmp_path):
    cover = tmp_path / "look.jpg"
    Image.new("RGB", (1080, 1350), (190, 190, 190)).save(cover)
    cut = Image.new("RGBA", (300, 500), (0, 0, 0, 0))
    cut.paste((40, 30, 30, 255), (50, 50, 250, 450))
    cut.save(tmp_path / "item.png")
    Image.new("RGB", (400, 480), (230, 230, 230)).save(tmp_path / "photo.jpg")
    items = [
        FeedItem(
            category="아우터",
            name="레더 자켓",
            shop="샵",
            price=69000,
            comment="코멘트",
            cutout="item.png",
        ),
        FeedItem(category="하의", name="슬랙스", price=30000, photo="photo.jpg"),
    ]
    return FeedSpec(
        account="women", title_lines=["첫 줄", "둘째 줄"], cover="look.jpg", items=items
    )


def test_render_default_order(spec, tmp_path):
    paths = render(spec, tmp_path / "out", root=tmp_path)
    assert [p.name for p in paths] == [
        "01_cover.jpg",
        "02_아우터.jpg",
        "03_하의.jpg",
        "04_summary.jpg",
        "05_comment.jpg",
    ]
    for p in paths:
        assert Image.open(p).size == (1080, 1350)
    # 상품 장 배경은 프로필 배경색
    bg = load_profile("women").feed.background
    rgb = tuple(int(bg[i : i + 2], 16) for i in (1, 3, 5))
    px = Image.open(paths[1]).getpixel((5, 5))
    assert all(abs(a - b) <= 3 for a, b in zip(px, rgb, strict=True))


def test_slides_follow_profile(spec, tmp_path):
    profile = load_profile("women")
    profile.feed.slides = ["products", "cover"]
    paths = render(spec, tmp_path / "out", root=tmp_path, profile=profile)
    assert [p.name for p in paths] == ["01_아우터.jpg", "02_하의.jpg", "03_cover.jpg"]


def test_comment_slide_texts_from_profile(spec, tmp_path):
    profile = load_profile("women")
    profile.feed.slides = ["comment"]
    profile.feed.comment_box = ""  # 입력창 없이도 그려진다
    paths = render(spec, tmp_path / "out", root=tmp_path, profile=profile)
    assert [p.name for p in paths] == ["01_comment.jpg"]
    img = Image.open(paths[0])
    assert img.size == (1080, 1350)
    # 가운데에 글자가 있어 배경색만 있지 않다
    assert len(img.crop((200, 500, 880, 850)).getcolors(1 << 16) or []) > 10


def test_no_cover_skips_cover(spec, tmp_path):
    paths = render(spec.model_copy(update={"cover": None}), tmp_path / "out", root=tmp_path)
    assert paths[0].name == "01_아우터.jpg"


def test_too_many_slides(spec, tmp_path):
    many = spec.model_copy(update={"items": spec.items * 5})
    with pytest.raises(ValueError, match="최대 10장"):
        render(many, tmp_path / "out", root=tmp_path)


def test_item_needs_image(spec, tmp_path):
    bad = spec.model_copy(update={"items": [FeedItem(category="가방", name="가방")]})
    with pytest.raises(ValueError, match="cutout"):
        render(bad, tmp_path / "out", root=tmp_path)


@pytest.mark.looks
def test_duplicate_slide_kind_rejected(tmp_path):
    import shutil

    from core.profile import ACCOUNTS_DIR

    root = tmp_path / "accounts"
    shutil.copytree(ACCOUNTS_DIR / "women", root / "women")
    p = root / "women" / "profile.yaml"
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            "slides: [cover, looks, comment]", "slides: [cover, looks, cover]"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="두 번"):
        load_profile("women", root)


def test_cli_sample(tmp_path):
    assert main(["--spec", "samples/women/feed_leather.yaml", "--out", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*.jpg"))) == 9  # 표지 + 상품 6 + 요약 + 댓글 안내


def test_feed_samples_are_valid():
    from core.profile import REPO_ROOT
    from tools.feed import load_spec

    for path in (REPO_ROOT / "samples").rglob("feed_*.yaml"):
        spec = load_spec(path)
        load_profile(spec.account)


def test_crop_below_faces():
    from tools.faces import crop_below_faces

    img = Image.new("RGB", (400, 1000), (200, 200, 200))
    assert crop_below_faces(img, []) is img
    out = crop_below_faces(img, [(150, 50, 250, 180)])
    assert out.size == (400, 1000 - 220)  # 얼굴 아래 + 여백 4%
    assert crop_below_faces(img, [(150, 400, 250, 600)]) is None  # 남는 부분이 너무 작음


def test_find_faces_on_plain_image():
    from tools.faces import find_faces

    assert find_faces(Image.new("RGB", (300, 400), (230, 230, 230))) == []


def _product(n=3):
    from core.models import Product

    return Product(
        id="zigzag:1",
        source="zigzag",
        url="https://zigzag.kr/catalog/products/1",
        shop="샵",
        name="셔츠",
        price=1000,
        category="상의",
        image_urls=[f"https://example.com/{i}.jpg" for i in range(n)],
    )


def _fake_fetch(monkeypatch, tmp_path):
    import tools.feed as feed_mod

    def fetch(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 1000), (200, 200, 200)).save(dest)
        return dest

    monkeypatch.setattr(feed_mod, "fetch_photo", fetch)

    import tools.scout as scout

    def og(url, out):
        raise RuntimeError("대표 사진 없음")

    monkeypatch.setattr(scout, "fetch_product_image", og)


def test_pick_photo_prefers_photo_without_face(monkeypatch, tmp_path):
    import tools.faces as faces
    from tools.feed import pick_photo

    _fake_fetch(monkeypatch, tmp_path)
    calls = iter([[(1, 1, 50, 100)], []])  # 첫 사진에만 얼굴
    monkeypatch.setattr(faces, "find_faces", lambda img: next(calls))
    path = pick_photo(_product(), tmp_path, load_profile("women").feed)
    assert path.name == "1_1.jpg"


def test_pick_photo_crops_when_all_have_faces(monkeypatch, tmp_path):
    import tools.faces as faces
    from tools.feed import pick_photo

    _fake_fetch(monkeypatch, tmp_path)
    monkeypatch.setattr(faces, "find_faces", lambda img: [(100, 20, 200, 150)])
    path = pick_photo(_product(), tmp_path, load_profile("women").feed)
    assert path.name == "1_0_noface.jpg"
    assert Image.open(path).height == 1000 - 190


def test_photo_credit_text():
    from tools.feed import Card, photo_credit

    feed = load_profile("women").feed
    card = Card("상의", "셔츠", "데일리쥬", 1000, "", None, False, source="지그재그")
    assert photo_credit(card, feed) == "사진 출처 지그재그 · 데일리쥬"


def test_page_price_prefers_final_price():
    from sources.zigzag import page_price

    product = {"name": "x", "product_price": {"max_price": 50000, "final_price": 39000}}
    assert page_price(product) == 39000
    assert page_price({"price": 12000}) == 12000
    assert page_price({"name": "x"}) is None


def test_pick_photo_falls_back_to_page_image(monkeypatch, tmp_path):
    import tools.faces as faces
    import tools.feed as feed_mod
    import tools.scout as scout
    from tools.feed import pick_photo

    def broken(url, dest):
        raise scout.ImageUnavailable("404")

    def og(url, out):
        Image.new("RGB", (400, 1000), (200, 200, 200)).save(out)
        return out, "https://example.com/og.jpg"

    monkeypatch.setattr(feed_mod, "fetch_photo", broken)
    monkeypatch.setattr(scout, "fetch_product_image", og)
    monkeypatch.setattr(faces, "find_faces", lambda img: [])
    assert pick_photo(_product(2), tmp_path, load_profile("women").feed).name == "1_og.jpg"


def test_look_credit_line(tmp_path):
    """profile looks.credit 이 있으면 벌 장 목록 아래와 표지에 출처 한 줄을 넣는다."""
    from PIL import Image

    from core.profile import load_profile
    from tools.feed import LookLine, canvas_size, credit_overlay, look_slide

    women = load_profile("women")
    assert not women.looks.credit and not women.looks.show_items  # 2026-10-15 사용자: 벌 번호만
    show = {"credit": "이미지 출처 지그재그", "show_items": True}
    profile = women.model_copy(update={"looks": women.looks.model_copy(update=show)})
    size = canvas_size(profile)
    base = tmp_path / "look.jpg"
    Image.new("RGB", size, "#808080").save(base)
    lines = [LookLine("상의", "니트", "스토어", None)]
    plain = profile.model_copy(update={"looks": profile.looks.model_copy(update={"credit": ""})})
    with_credit = look_slide(base, lines, 1, profile, size)
    without = look_slide(base, lines, 1, plain, size)
    assert with_credit.tobytes() != without.tobytes()
    # 상품 목록을 빼면 목록·출처 없이 벌 번호만 (목록이 있던 자리 아래쪽에)
    only_label = look_slide(base, lines, 1, women, size)
    no_label = women.model_copy(update={"looks": women.looks.model_copy(update={"label": ""})})
    assert only_label.tobytes() != without.tobytes()
    assert (
        look_slide(base, lines, 1, no_label, size).tobytes()
        == look_slide(base, [], 1, no_label, size).tobytes()
    )
    cover = credit_overlay(Image.new("RGB", size, "#808080"), profile)
    assert cover.size == size


def test_reel_version_extends_floor(tmp_path):
    """reels.fill: 4:5 코디컷 위아래에 바닥을 이어 붙인 9:16 판. 피드 장은 그대로 4:5, 글자는
    4:5 장을 릴스에 올렸을 때와 같은 화면 자리."""
    from PIL import Image

    from core.profile import load_profile
    from tools import feed

    profile = load_profile("women")
    src = tmp_path / "look.jpg"
    Image.new("RGB", (1080, 1350), (150, 160, 170)).save(src, quality=100)
    big = feed.extend_floor(Image.open(src).convert("RGB"))
    assert big.size == feed.TALL_SIZE
    for y in (5, 1915):  # 늘린 곳도 같은 바닥색
        assert all(
            abs(a - b) <= 3 for a, b in zip(big.getpixel((540, y)), (150, 160, 170), strict=True)
        )
    lines = [feed.LookLine("상의", "니트", "스토어", None)]
    assert feed.look_slide(src, lines, 1, profile, (1080, 1350)).size == (1080, 1350)
    assert feed.look_slide(src, lines, 1, profile, feed.TALL_SIZE).size == feed.TALL_SIZE
    tp = feed.tall_profile(profile)
    want = profile.reels.top * 1920 + 1350 - 1350 * profile.looks.bottom
    assert 1920 * (1 - tp.looks.bottom) == pytest.approx(want)
    assert tp.looks.bottom > 0.2  # 인스타 캡션이 덮는 아래쪽보다 위


def test_reel_version_crops_sides(tmp_path):
    """reels.fill_mode crop: 높이는 그대로 두고 양옆만 잘라 9:16 (확대 없음).
    사람을 못 찾으면 가운데."""
    from PIL import Image

    from tools import feed

    img = Image.new("RGB", (1080, 1350), (200, 200, 200))
    img.paste((0, 0, 0), (0, 0, 100, 1350))  # 왼쪽 끝 띠는 잘려 나간다
    big = feed.crop_tall(img)
    assert big.size == feed.TALL_SIZE
    assert big.getpixel((5, 960)) == (200, 200, 200)
    src = tmp_path / "look.jpg"
    img.save(src)
    assert feed.open_outfit(src, tall=True, mode="crop").size == feed.TALL_SIZE
    assert feed.open_outfit(src, tall=True).size == feed.TALL_SIZE
    assert feed.open_outfit(src).size == (1080, 1350)
