"""상품 소스 인터페이스. 계정마다 다른 쇼핑몰을 쓸 수 있게 분리해 둔다."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from core.models import Category, Product


class SearchFilters(BaseModel):
    min_rating: float = 0
    min_reviews: int = 0
    exclude_sold_out: bool = True
    max_results: int = 30
    min_price: int = 0
    max_price: int = 0  # 0이면 제한 없음
    exclude_words: list[str] = []  # 상품 이름에 있으면 뺀다


def name_excluded(name: str, words: list[str]) -> bool:
    low = name.lower()
    return any(w.lower() in low for w in words)


def price_ok(price: int | None, low: int, high: int) -> bool:
    if price is None:
        return True
    return price >= low and (not high or price <= high)


class ProductSource(ABC):
    name: str

    @abstractmethod
    def search(
        self, queries: list[str], category: Category, filters: SearchFilters
    ) -> dict[str, list[Product]]:
        """검색어마다 후보 상품을 찾는다 (검색 결과 카드 정보만, 평점·리뷰 기준 적용)."""

    @abstractmethod
    def detail(self, product: Product) -> Product:
        """상품 페이지에서 판매 상태와 전체 사진을 채운다. 사라진 상품은 status 를 바꿔 돌려준다."""

    def thumb_url(self, image_url: str, width: int = 240) -> str:
        """사진 고르기용 작은 사진 주소. 쇼핑몰이 크기 조절을 지원하면 덮어쓴다."""
        return image_url
