"""파이프라인 전체가 주고받는 데이터 규격.

단계마다 이 모델로 파일을 읽고 쓰기 때문에, 필드를 바꿀 때는 테스트와
.claude/skills 문서도 함께 맞춰야 한다.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

Category = Literal["아우터", "상의", "하의", "원피스", "가방", "신발", "소품"]
Monetize = Literal["none", "manual_link"]
Approval = Literal["required", "optional"]
SaleStatus = Literal["on_sale", "sold_out", "hidden"]


class Product(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+:.+$", description='"zigzag:12345678" 형식')
    source: str
    url: HttpUrl
    shop: str
    name: str
    price: int = Field(ge=0)
    category: Category
    colors: list[str] = []
    image_urls: list[HttpUrl] = Field(min_length=1)  # 누끼에 쓰기 좋은 사진부터 (첫 장이 대표)
    has_flat_shot: bool = False  # 사람 없이 상품만 나온 사진이 있는지
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    last_used: date | None = None
    # 4단계 수집 정보
    status: SaleStatus = "on_sale"
    style_tags: list[str] = []  # 쇼핑몰 스타일 (예: 심플베이직, 오피스룩)
    query: str | None = None  # 이 상품을 찾은 검색어
    collected_at: date | None = None  # 처음 모은 날
    checked_at: date | None = None  # 판매 상태를 마지막으로 확인한 날


class Slot(BaseModel):
    category: Category
    desc: str  # "블랙 크롭 레더자켓"


class Theme(BaseModel):
    date: date
    title_hint: str
    mood: str
    slots: list[Slot] = Field(min_length=1)


class OutfitPlan(BaseModel):
    theme: Theme
    items: list[Product] = Field(min_length=5, max_length=7)
    reason: str


class FeedItem(BaseModel):
    """캐러셀 상품 장 한 장. product 가 있으면 상품 창고 값을 기본으로 쓰고, 적은 값이 우선한다."""

    product: str | None = None  # "zigzag:12345678"
    category: Category | None = None
    name: str | None = None  # 슬라이드에 보일 짧은 상품명 (없으면 원래 이름에서 광고 문구를 뺌)
    shop: str | None = None
    source: str | None = None  # 사진 출처 (없으면 상품 창고의 source, 예: zigzag)
    price: int | None = Field(default=None, ge=0)
    comment: str = ""  # 한두 문장 코멘트
    cutout: str | None = None  # 저장소 기준 누끼 PNG
    photo: str | None = None  # 저장소 기준 원본 사진


class FeedSpec(BaseModel):
    """게시물 한 개의 캐러셀 재료. 기획 단계가 쓰고 tools.feed 가 읽는다."""

    account: str
    title_lines: list[str] = Field(min_length=1)
    cover: str | None = None  # 제목 없는 코디컷 경로 (없으면 표지 장을 건너뜀)
    cover_has_title: bool = False  # 이미 제목을 얹은 이미지면 true
    items: list[FeedItem] = Field(min_length=1)


class ReelsSpec(BaseModel):
    """9단계에서 채운다. 지금은 자리만 잡아 둔다."""

    video: str | None = None
    cover: str | None = None
    audio: str | None = None
    duration_sec: float | None = Field(default=None, ge=5, le=90)


class Manifest(BaseModel):
    """하루치 결과물. 게시 job은 이 파일만 읽는다."""

    account: str
    date: date
    plan: OutfitPlan
    images: list[str] = Field(min_length=1, max_length=10)  # out/ 경로, 첫 장이 커버
    review_score: float = Field(ge=0, le=10)
    needs_human: bool = False
    title_lines: tuple[str, str]
    caption: str
    hashtags: list[str]
    monetize: Monetize
    reels: ReelsSpec | None = None
