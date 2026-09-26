from datetime import date

import pytest
from pydantic import ValidationError

from core.models import Manifest, OutfitPlan, Product, Theme
from core.store import Store


def product(i: int, category: str = "상의") -> Product:
    return Product(
        id=f"zigzag:{i}",
        source="zigzag",
        url=f"https://zigzag.kr/catalog/products/{i}",
        shop="샵",
        name=f"상품 {i}",
        price=39000,
        category=category,
        colors=["블랙"],
        image_urls=[f"https://example.com/{i}.jpg"],
    )


def theme() -> Theme:
    return Theme(
        date=date(2026, 10, 1),
        title_hint="가을 코디가 고민이라면 레더자켓",
        mood="시크, 레드 포인트",
        slots=[{"category": "아우터", "desc": "블랙 크롭 레더자켓"}],
    )


def test_product_rejects_bad_id_and_category():
    with pytest.raises(ValidationError):
        Product.model_validate({**product(1).model_dump(), "id": "123"})
    with pytest.raises(ValidationError):
        product(1, category="모자")


def test_outfit_plan_item_count():
    OutfitPlan(theme=theme(), items=[product(i) for i in range(5)], reason="톤 통일")
    with pytest.raises(ValidationError):
        OutfitPlan(theme=theme(), items=[product(i) for i in range(4)], reason="")
    with pytest.raises(ValidationError):
        OutfitPlan(theme=theme(), items=[product(i) for i in range(8)], reason="")


def test_manifest_roundtrip():
    plan = OutfitPlan(theme=theme(), items=[product(i) for i in range(6)], reason="톤 통일")
    manifest = Manifest(
        account="women",
        date=date(2026, 10, 1),
        plan=plan,
        images=["out/2026-10-01/cover.jpg"],
        review_score=8.5,
        title_lines=("가을 코디가 고민이라면", "레더자켓 입어보세요"),
        caption="캡션",
        hashtags=["#데일리룩"],
        monetize="none",
    )
    restored = Manifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest


def test_store_roundtrip(tmp_path):
    store = Store("women", root=tmp_path)
    assert store.products() == []
    store.save_products([product(1), product(2)])
    store.save_themes([theme()])
    assert [p.id for p in store.products()] == ["zigzag:1", "zigzag:2"]
    assert store.themes()[0].slots[0].desc == "블랙 크롭 레더자켓"
    assert "상품 1" in (tmp_path / "women" / "products.json").read_text(encoding="utf-8")
