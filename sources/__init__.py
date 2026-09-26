"""상품 소스. 쇼핑몰마다 ProductSource 구현을 하나씩 둔다 (예: zigzag.py, 4단계)."""

from sources.base import ProductSource, SearchFilters

__all__ = ["ProductSource", "SearchFilters", "get_source"]


def get_source(name: str) -> ProductSource:
    """프로필의 product_source.name 으로 구현체를 찾는다."""
    import importlib

    try:
        module = importlib.import_module(f"sources.{name}")
    except ModuleNotFoundError as e:
        raise ValueError(f"상품 소스가 아직 없습니다: sources/{name}.py") from e
    return module.Source()
