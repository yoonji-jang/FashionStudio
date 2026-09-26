"""계정 프로필 로더와 검증.

사용법: python -m core.profile women
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from core.models import Approval, Category, Monetize

REPO_ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS_DIR = REPO_ROOT / "accounts"
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
COLOR = r"^#[0-9A-Fa-f]{6}$"


class Audience(BaseModel):
    gender: Literal["women", "men", "unisex"]
    age_range: tuple[int, int]
    description: str


class Style(BaseModel):
    keywords: list[str] = Field(min_length=1)
    palette: list[str] = []
    avoid: list[str] = []


class SlotRule(BaseModel):
    category: Category
    required: bool = True


class Outfit(BaseModel):
    items_min: int = Field(default=5, ge=1)
    items_max: int = Field(default=7, le=10)  # 한 장에 배치할 수 있는 상품 수
    slots: list[SlotRule] = Field(min_length=1)
    reuse_cooldown_days: int = Field(default=14, ge=0)
    max_per_shop: int = Field(default=2, ge=1)


class ProductSourceConfig(BaseModel):
    name: str  # sources/<name>.py
    min_rating: float = Field(default=4.5, ge=0, le=5)
    min_reviews: int = Field(default=20, ge=0)
    prefer_flat_shot: bool = True
    # 상품 수집 (tools.collect). 카테고리별 검색어로 후보를 모아 data/<계정>/products.json 에 쌓는다
    queries: dict[Category, list[str]] = {}
    per_query: int = Field(default=20, ge=1, le=100)  # 검색어 하나당 상세까지 확인할 상품 수
    max_per_category: int = Field(default=60, ge=1)  # 카테고리별 창고 최대 보관 수
    recheck_days: int = Field(default=3, ge=0)  # 이 날짜가 지나면 판매 상태를 다시 확인


ThemeType = Literal["아이템", "상황", "컬러무드", "타깃", "팁"]


class ThemeRules(BaseModel):
    """주간 주제를 정하는 기준. plan-themes 스킬(5단계)이 읽는다."""

    per_week: int = Field(default=7, ge=1, le=14)
    type_mix: dict[ThemeType, int] = Field(min_length=1)  # 주당 유형별 개수 (합계 = per_week)
    consider: list[str] = []  # 날씨, 요일, 시즌 이벤트 등 반영할 요소
    events: list[str] = []  # 챙길 시즌 이벤트
    no_repeat_days: int = Field(default=30, ge=0)  # 비슷한 주제 반복 금지 기간
    avoid: list[str] = []  # 다루지 않을 주제
    review_before_use: bool = True  # 주간 주제를 사람이 보고 확정할지


class Visual(BaseModel):
    backdrop: str  # 배경 묘사 (프롬프트에 들어감)
    backdrop_ref: str | None = None  # accounts/<id>/ 기준 참고 이미지 파일명
    feed_ratio: Literal["4:5", "1:1"] = "4:5"
    # 제목 합성 (tools.overlay). 크기는 폭 1080px 기준, 위치는 이미지 높이·폭 대비 비율
    font: str  # assets/fonts/ 기준 파일명
    title_font_size: int = Field(default=70, gt=0)  # 긴 줄은 자동으로 줄어듦
    title_line_spacing: float = Field(default=1.34, gt=0)
    title_max_lines: int = Field(default=2, ge=1, le=4)
    title_align: Literal["left", "center"] = "left"
    title_margin_x: float = Field(default=0.104, ge=0.035, lt=0.5)  # 좌우 여백 (격자 양옆 3% 잘림)
    title_bottom: float = Field(default=0.155, ge=0, lt=1)  # 이미지 아래에서 마지막 줄 기준선까지
    title_color: str = Field(default="#FFFFFF", pattern=COLOR)
    text_shadow: float = Field(default=0.45, ge=0, le=1)  # 글자 뒤 그림자 진하기
    accent_color: str | None = Field(default="#7A2E34", pattern=COLOR)  # 제목 옆 세로선 (null=없음)
    accent_width: int = Field(default=6, ge=1)  # 세로선 굵기 (px)
    accent_gap: int = Field(default=26, ge=0)  # 세로선과 제목 사이 (px)
    gradient_height: float = Field(default=0.38, gt=0, le=1)
    gradient_opacity: float = Field(default=0.5, ge=0, le=1)
    logo_text: str  # 계정 표시. 빈 문자열이면 생략
    logo_font: str | None = None  # 없으면 font 사용
    logo_font_size: int = Field(default=22, gt=0)
    logo_align: Literal["left", "center"] = "left"  # left 면 제목 왼쪽 끝에 맞춤
    logo_bottom: float = Field(default=0.065, ge=0, lt=1)  # 이미지 아래에서 계정 표시 기준선까지
    logo_color: str = Field(default="#EBEBEB", pattern=COLOR)


SlideKind = Literal["cover", "products", "summary"]


class Feed(BaseModel):
    """캐러셀 구성과 슬라이드 모양 (tools.feed). 크기는 폭 1080px 기준."""

    # 슬라이드 순서. products 는 상품마다 한 장씩 펼쳐진다. 인스타 캐러셀은 최대 10장
    slides: list[SlideKind] = Field(default=["cover", "products", "summary"], min_length=1)
    product_image: Literal["cutout", "photo"] = "cutout"  # 누끼 또는 쇼핑몰 원본 사진
    background: str = Field(default="#EEEBE6", pattern=COLOR)  # 상품·요약 장 배경색
    background_image: str | None = None  # accounts/<id>/ 기준 배경 사진 (있으면 배경색 대신)
    text_color: str = Field(default="#222222", pattern=COLOR)
    sub_color: str = Field(default="#8C867E", pattern=COLOR)  # 쇼핑몰·카테고리 같은 보조 글자
    body_font: str = "Pretendard-Medium.otf"  # 보조 글자 폰트 (굵은 글자는 visual.font)
    margin_x: float = Field(default=0.104, ge=0.035, lt=0.5)  # 좌우 여백 (폭 대비)
    text_top: float = Field(default=0.70, gt=0.3, lt=0.95)  # 상품 장 글자가 시작하는 높이 (고정)
    show_index: bool = True  # 상품 장 위쪽 "01 아우터"
    show_shop: bool = True
    show_price: bool = True
    show_total: bool = False  # 요약 장에 가격 합계
    index_size: int = Field(default=26, gt=0)
    shop_size: int = Field(default=28, gt=0)
    name_size: int = Field(default=52, gt=0)
    name_max_lines: int = Field(default=2, ge=1, le=3)
    price_size: int = Field(default=40, gt=0)
    comment_size: int = Field(default=31, gt=0)
    comment_max_lines: int = Field(default=3, ge=0, le=5)
    comment_color: str = Field(default="#57524B", pattern=COLOR)
    heading: str = "오늘의 코디"  # 요약 장 제목
    heading_size: int = Field(default=60, gt=0)
    row_name_size: int = Field(default=32, gt=0)
    row_meta_size: int = Field(default=25, gt=0)
    # 요약 장 맨 아래 문구. {logo} 는 visual.logo_text 로 바뀜. 첫 줄 앞에 저장 아이콘
    cta_lines: list[str] = ["저장해 두고 코디 고민될 때 꺼내 보세요", "매일 아침 코디는 {logo}"]
    cta_size: int = Field(default=30, gt=0)


class Schedule(BaseModel):
    timezone: str = "Asia/Seoul"
    generate_at: str = Field(pattern=HHMM)
    publish_at: str = Field(pattern=HHMM)
    reels_at: str | None = Field(default=None, pattern=HHMM)


class Instagram(BaseModel):
    token_secret: str  # GitHub Secret 이름 (값이 아님)
    user_id_secret: str


class Profile(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    display_name: str
    language: str = "ko"
    audience: Audience
    style: Style
    outfit: Outfit
    themes: ThemeRules
    product_source: ProductSourceConfig
    visual: Visual
    feed: Feed = Feed()
    schedule: Schedule
    monetize: Monetize = "none"
    approval: Approval = "required"
    instagram: Instagram


class AccountIndex(BaseModel):
    accounts: list[str]


def account_dir(account: str, root: Path = ACCOUNTS_DIR) -> Path:
    return root / account


def load_profile(account: str, root: Path = ACCOUNTS_DIR) -> Profile:
    path = account_dir(account, root) / "profile.yaml"
    if not path.exists():
        raise FileNotFoundError(f"프로필이 없습니다: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profile = Profile.model_validate(data)
    if profile.id != account:
        raise ValueError(f"폴더 이름({account})과 profile.id({profile.id})가 다릅니다")
    if profile.outfit.items_min > profile.outfit.items_max:
        raise ValueError("outfit.items_min이 items_max보다 큽니다")
    if len(set(profile.feed.slides)) != len(profile.feed.slides):
        raise ValueError("feed.slides 에 같은 종류가 두 번 들어 있습니다")
    if sum(profile.themes.type_mix.values()) != profile.themes.per_week:
        raise ValueError("themes.type_mix 개수의 합이 themes.per_week와 다릅니다")
    return profile


def load_voice(account: str, root: Path = ACCOUNTS_DIR) -> str:
    path = account_dir(account, root) / "voice.md"
    if not path.exists():
        raise FileNotFoundError(f"말투 파일이 없습니다: {path}")
    return path.read_text(encoding="utf-8")


def load_index(root: Path = ACCOUNTS_DIR) -> AccountIndex:
    data = yaml.safe_load((root / "index.yaml").read_text(encoding="utf-8")) or {}
    return AccountIndex.model_validate(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="계정 프로필을 검증하고 출력합니다")
    parser.add_argument("account", nargs="?", help="계정 id (생략하면 index.yaml의 모든 계정)")
    args = parser.parse_args(argv)

    accounts = [args.account] if args.account else load_index().accounts
    ok = True
    for account in accounts:
        try:
            profile = load_profile(account)
            load_voice(account)
        except (FileNotFoundError, ValueError, ValidationError) as e:
            ok = False
            print(f"[실패] {account}\n{e}", file=sys.stderr)
            continue
        print(f"[통과] {account}")
        print(yaml.safe_dump(profile.model_dump(mode="json"), allow_unicode=True, sort_keys=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
