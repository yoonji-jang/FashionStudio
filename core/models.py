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
ThemeType = Literal["아이템", "상황", "컬러무드", "타깃", "팁"]


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
    type: ThemeType | None = None  # 주제 유형 (profile themes.type_mix)
    why: str = ""  # 이 날 이 주제인 이유 (계절, 날씨, 요일, 이벤트)
    search_terms: dict[Category, list[str]] = {}  # 주제에 맞는 상품 검색어 (tools.collect)


class OutfitPlan(BaseModel):
    theme: Theme
    items: list[Product] = Field(min_length=1, max_length=10)  # 개수 규칙은 profile outfit
    reason: str


class PlanItem(BaseModel):
    """하루 기획의 상품 한 개. 상품 정보는 상품 창고(products.json)에서 가져온다."""

    product: str = Field(pattern=r"^[a-z0-9_-]+:.+$")  # 상품 창고 id
    color: str | None = None  # 고른 색 옵션 (사진 확인 기준, 예: 블랙)
    desc: str  # 코디컷용 설명 "한국어 (영어)", 예: "블랙 레더자켓 (black leather jacket)"
    name: str  # 상품 장에 보일 짧은 이름
    comment: str = ""  # 상품 장 코멘트 한두 문장
    photo: int | None = None  # 상품 장에 쓸 창고 사진 번호 (사진 확인 뒤, 없으면 자동)
    cutout_photo: int = Field(default=0, ge=0)  # 코디컷 누끼에 쓸 창고 사진 번호
    slot: str | None = None  # config/layout.yaml 자리 (예: 목걸이). 없으면 카테고리
    segment: str | None = None  # 누끼에서 남길 물건 (영어 단수, 예: bag, belt, necklace)
    cm: float | None = Field(default=None, gt=0)  # 실제 치수가 기본값과 다를 때
    wear_with: Category | None = None  # 이 상품을 입힐 기준 상품 (셔츠 → 아우터, 벨트 → 하의)
    wear_how: str | None = None  # 입는 방법 (영어)
    wear_modes: list[str] | None = None  # direct, over, open, peek


class PlanText(BaseModel):
    """제목과 캡션 재료. 캡션 전체는 tools.plan 이 프로필 copy 규칙으로 조립한다."""

    title_lines: list[str] = Field(min_length=1)
    points: list[str] = Field(min_length=1)  # 캡션 코디 포인트 (한 줄씩)
    hashtags: list[str] = []  # 기본 해시태그(profile copy.base_hashtags)는 자동으로 더함


class DailyPlan(BaseModel):
    """하루 게시물 기획. daily-feed 스킬이 out/<날짜>/plan.yaml 로 쓰고
    tools.plan 이 검사·빌드한다."""

    account: str
    date: date
    theme: Theme
    items: list[PlanItem] = Field(min_length=1, max_length=10)
    reason: str  # 이 조합을 고른 이유 (색, 분위기)
    text: PlanText | None = None
    cover: str | None = None  # 이미 만든 코디컷 (있으면 Photoroom 을 부르지 않음)


class FeedItem(BaseModel):
    """캐러셀 상품 장 한 장. product 가 있으면 상품 창고 값을 기본으로 쓰고, 적은 값이 우선한다."""

    product: str | None = None  # "zigzag:12345678"
    url: HttpUrl | None = (
        None  # 창고에 없는 상품은 공개 상품 페이지에서 이름·스토어·가격·사진을 읽음
    )
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


class PostRecord(BaseModel):
    """게시 기록 한 줄 (data/<계정>/posts.json). 8단계 게시 job 이 쓰고,
    기획이 중복을 피할 때 읽는다.
    상품은 id(링크)만 남긴다."""

    date: date
    title: str  # 제목 두 줄을 이은 것
    theme: str = ""  # 주제 (title_hint)
    products: list[str] = []  # 상품 창고 id
    permalink: str | None = None


class ReelsSpec(BaseModel):
    """9단계에서 채운다. 지금은 자리만 잡아 둔다."""

    video: str | None = None
    cover: str | None = None
    audio: str | None = None
    duration_sec: float | None = Field(default=None, ge=5, le=90)


ReviewCheck = Literal["color", "shape", "complete", "clean", "layout"]
REVIEW_CHECKS: dict[str, str] = {
    "color": "색이 원본 상품과 같은지",
    "shape": "모양·디테일(기장, 소매, 단추, 로고)이 원본과 같은지",
    "complete": "기획한 상품이 모두 보이는지",
    "clean": "삐져나온 조각·덩어리·원본에 없는 물건이 없는지",
    "layout": "상품끼리 겹치거나 화면 밖으로 잘리지 않았는지",
}


class CoverScore(BaseModel):
    """검수 에이전트가 코디컷 후보 한 장에 매긴 점수. 항목마다 0(불합격)~2(좋음), 합계 10점."""

    id: str  # 후보 번호 (c1, c2 …)
    color: int = Field(ge=0, le=2)
    shape: int = Field(ge=0, le=2)
    complete: int = Field(ge=0, le=2)
    clean: int = Field(ge=0, le=2)
    layout: int = Field(ge=0, le=2)
    notes: str = ""  # 문제가 있으면 어느 상품의 무엇이 문제인지

    @property
    def total(self) -> int:
        return self.color + self.shape + self.complete + self.clean + self.layout

    @property
    def zeros(self) -> list[str]:
        return [k for k in REVIEW_CHECKS if getattr(self, k) == 0]


class SlideIssue(BaseModel):
    """캐러셀 장의 문제 한 개. by: code(도구가 찾음) 또는 claude(검수 에이전트)."""

    slide: str  # 슬라이드 파일 이름 (예: 03_상의.jpg)
    kind: Literal["색", "얼굴", "글자", "사진", "기타"]
    note: str
    by: Literal["code", "claude"] = "claude"


class Candidate(BaseModel):
    id: str  # c1, c2 …
    file: str  # 결과 폴더 기준 코디컷 경로
    round: int = Field(default=1, ge=1)  # 몇 번째로 만든 묶음인지
    score: CoverScore | None = None


class Review(BaseModel):
    """7단계 검수 결과 (out/<날짜>/review/review.json). manifest.review 에도 들어간다."""

    candidates: list[Candidate] = []
    chosen: str | None = None  # 표지로 고른 후보 id
    rounds: int = Field(default=1, ge=1)
    passed: bool = False  # 표지 기준 통과 + 캐러셀 문제 없음
    status: Literal["passed", "retry", "failed", "not_reviewed"] = "not_reviewed"
    slide_issues: list[SlideIssue] = []
    summary: str = ""


class Manifest(BaseModel):
    """하루치 결과물. 게시 job은 이 파일만 읽는다."""

    account: str
    date: date
    plan: OutfitPlan
    images: list[str] = Field(min_length=1, max_length=10)  # out/ 경로, 첫 장이 커버
    review_score: float = Field(ge=0, le=10)  # 고른 표지 점수 (검수 전에는 0)
    needs_human: bool = False  # 검수 불합격·미검수면 true (8단계 미리보기에서 사람이 판단)
    review: Review | None = None
    title_lines: list[str] = Field(min_length=1)
    caption: str
    hashtags: list[str]
    monetize: Monetize
    reels: ReelsSpec | None = None
