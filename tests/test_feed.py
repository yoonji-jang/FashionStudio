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


def test_duplicate_slide_kind_rejected(tmp_path):
    import shutil

    from core.profile import ACCOUNTS_DIR

    root = tmp_path / "accounts"
    shutil.copytree(ACCOUNTS_DIR / "women", root / "women")
    p = root / "women" / "profile.yaml"
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            "slides: [cover, products, summary]", "slides: [cover, products, cover]"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="두 번"):
        load_profile("women", root)


def test_cli_sample(tmp_path):
    assert main(["--spec", "samples/women/feed_leather.yaml", "--out", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*.jpg"))) == 8


def test_feed_samples_are_valid():
    from core.profile import REPO_ROOT
    from tools.feed import load_spec

    for path in (REPO_ROOT / "samples").rglob("feed_*.yaml"):
        spec = load_spec(path)
        load_profile(spec.account)
