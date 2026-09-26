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


class ProductSource(ABC):
    name: str

    @abstractmethod
    def search(self, query: str, category: Category, filters: SearchFilters) -> list[Product]:
        """슬롯 설명(query)으로 후보 상품을 찾는다."""

    @abstractmethod
    def detail(self, url: str) -> Product:
        """상품 상세 페이지에서 추가 이미지와 단독컷 여부까지 채운다."""
