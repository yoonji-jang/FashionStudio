"""계정 프로필 로더와 검증.

사용법: python -m core.profile women
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.models import Approval, Category, Monetize, ThemeType

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
    # 상품 이름에 이 말이 있으면 코디 포인트가 되는 상품으로 보고 창고·후보 순서에서 앞으로 (21단계)
    point_words: list[str] = []
    # 이름에 이 말이 있으면 기본템으로 보고 창고·후보 순서에서 뒤로 (2026-10-07)
    basic_words: list[str] = []
    # 벌마다 넘으면 tools.plan check 가 경고 (2026-10-06). None 이면 보지 않음
    pattern_words: list[str] = []  # 이름에 이 말이 있으면 무늬 있는 상품
    max_layers: int | None = None  # 아우터 + 상의 개수
    # 이름에 이 말이 있는 상의는 안에 얇은 이너(티·나시)를 받쳐 입는다 (2026-10-06, 딥 브이넥 등)
    inner_words: list[str] = []
    max_patterns: int | None = None
    max_accessories: int | None = None
    # basic_words 감점을 이 카테고리에만 (비면 전부). 무지 하의·신발·가방은 바탕이라 남긴다
    basic_penalty_categories: list[str] = []
    # 한 벌로 묶기 (2026-10-07 "상의 하의가 따로 놀고 색감·재질 조합이 이상하다")
    # 이름에 이 말이 있는 상의는 맨 바깥에만 (위에 아우터·다른 상의를 입히지 않음, 래글런 등)
    outer_only_words: list[str] = []
    # 안에 받치는 상의(wear_with: 상의)는 베이직만: 이름에 이 말이 있으면 경고 (2026-10-07,
    # "포인트 있는 티·나시는 이너로 쓰지 마", 가디건 세트 나시는 사진에 가디건이 같이 나옴)
    inner_avoid_words: list[str] = []
    # 한 벌에 두 쪽 말이 같이 있으면 재질 분위기가 섞인 것 (스웨트·래글런 + 새틴·핀스트라이프)
    casual_words: list[str] = []
    dressy_words: list[str] = []
    # 색 계열 → 색 옵션에 나오는 말. 한 벌(소품 빼고)의 계열 수가 max_colors 를 넘으면 경고
    color_families: dict[str, list[str]] = {}
    max_colors: int | None = None


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
    # 가성비 베스트셀러만 쌓이지 않게 (21단계). 카테고리별 [최저, 최고] 가격(원), 없으면 제한 없음
    price_range: dict[Category, tuple[int, int]] = {}
    exclude_words: list[str] = []  # 이름에 있으면 빼는 말 (만장, 1위, 특가 같은 판매량 광고)
    review_cap: int = Field(default=0, ge=0)  # 순위에 리뷰 수를 이 수까지만 반영 (0이면 제한 없음)


class Place(BaseModel):
    name: str  # 사람이 보는 이름 (예: 서울)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RefSearch(BaseModel):
    """주간 주제 때 쇼핑몰 코디를 모아 data/<계정>/refs.json 에 칸별로 더하는 기준."""

    per_week: int = Field(default=8, ge=1, le=150)  # 한 주에 더할 참고 코디 수
    keep_weeks: int = Field(default=4, ge=1)  # 자동으로 더한 참고 코디를 남겨 둘 주 수
    queries: list[str] = []  # 웹 검색어 (계절·날씨에 맞는 말을 Claude 가 더한다)
    sources: list[str] = []  # 찾아볼 공개 페이지 종류 (매거진, 브랜드 룩북 …)
    avoid_sites: list[str] = []  # 로그인이 필요하거나 약관상 자동으로 읽으면 안 되는 곳
    # 사진만 있는 쇼핑몰 코디 페이지: `tools.scout refs` 로 확인표를 만들어 보고 글로 옮긴다
    scout_sites: list[str] = []


class ThemeRules(BaseModel):
    """주간 주제를 정하는 기준. plan-themes 스킬(5단계)이 읽는다."""

    per_week: int = Field(default=7, ge=1, le=14)
    type_mix: dict[ThemeType, int] = Field(min_length=1)  # 주당 유형별 개수 (합계 = per_week)
    consider: list[str] = []  # 날씨, 요일, 시즌 이벤트 등 반영할 요소
    events: list[str] = []  # 챙길 시즌 이벤트
    no_repeat_days: int = Field(default=30, ge=0)  # 비슷한 주제 반복 금지 기간
    avoid: list[str] = []  # 다루지 않을 주제
    review_before_use: bool = True  # 주간 주제를 사람이 보고 확정할지
    weather: Place | None = None  # 날씨 예보를 볼 곳 (없으면 날씨를 보지 않음)
    ref_search: RefSearch | None = None  # 참고 코디 웹 검색 (없으면 하지 않음)


class Copy(BaseModel):
    """제목·문구·캡션 규칙. write-copy 스킬이 따르고 tools.plan check 가 검사한다.
    말투와 좋은 예시는 voice.md 에 둔다."""

    title_chars: tuple[int, int] = (8, 16)  # 제목 한 줄 글자 수 (공백 포함, 벗어나면 경고)
    name_max_chars: int = Field(default=18, ge=4)  # 상품 장 짧은 이름
    comment_max_chars: int = Field(default=60, ge=0)  # 상품 장 코멘트
    intro: bool = False  # 캡션 맨 앞에 그날 상황을 여는 도입 한 줄 (plan text.intro)
    points: tuple[int, int] = (3, 3)  # 캡션 코디 포인트 줄 수
    hashtags: tuple[int, int] = (10, 15)  # 기본 해시태그 포함 최종 개수
    base_hashtags: list[str] = []  # 매번 넣는 해시태그
    max_emoji: int = Field(default=2, ge=0)  # 도입·코디 포인트 이모지 수 (제목·상품 장에는 금지)
    max_emoji_per_line: int = Field(default=1, ge=0)  # 도입·코디 포인트 한 줄의 이모지 수
    max_exclaim: int = Field(default=1, ge=0)  # 캡션 전체 느낌표 수
    banned: list[str] = []  # 쓰면 안 되는 표현
    title_banned: list[str] = []  # 표지 제목에만 쓰면 안 되는 말 (예: 요일)
    ad_notice: str = (
        "(광고) 이 게시물은 제휴 링크를 포함하고 있어요"  # monetize: manual_link 일 때 첫 줄
    )
    ad_hashtag: str = "#광고"  # monetize: manual_link 일 때 첫 해시태그
    product_line: str = "{shop} · {name}"  # 캡션 제품 목록 한 줄
    link_line: str = "상품 정보는 프로필 링크에서 확인하세요"
    ai_notice: str = "이미지는 AI로 합성했으며 실제 색감·디테일과 다를 수 있어요"


# 캐러셀 슬라이드 종류 (tools.feed). comment 는 "상품 정보는 댓글로" 안내 장,
# looks 는 여러 벌 게시물(20단계)의 벌마다 한 장 (코디컷 + 상품 정보 몇 줄)
SlideKind = Literal["cover", "products", "looks", "summary", "comment"]


class AiLabel(BaseModel):
    """이미지 파일 안에 AI 합성 표시(IPTC DigitalSourceType, XMP)를 넣는다 (13단계, tools.ai_label).
    인스타 게시 API 에는 AI 레이블 값이 없어서,
    Meta 가 이 표시를 읽어 "AI 정보" 레이블을 붙이게 한다."""

    mode: Literal["metadata", "none"] = "none"
    # composite: 실제 사진 + AI 편집 (누끼·AI 그림자 코디컷), generated: AI 가 통째로 만든 이미지
    source_type: Literal["composite", "generated"] = "composite"
    slides: list[SlideKind] = ["cover"]  # 표시를 넣는 슬라이드 종류


class CommentTags(BaseModel):
    """게시 직후 내 댓글(예: 🤍)을 달고, 그 대댓글로 해시태그를 단다 (tools.publish).
    켜면 캡션에는 해시태그를 넣지 않는다."""

    enabled: bool = False
    comment: str = "🤍"
    follow_hashtags: list[str] = []  # 코디 해시태그 뒤에 붙이는 소통용 (예: #맞팔)
    max: int = Field(default=15, ge=1, le=30)  # 대댓글 해시태그 최대 개수


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


class Feed(BaseModel):
    """캐러셀 구성과 슬라이드 모양 (tools.feed). 크기는 폭 1080px 기준."""

    # 슬라이드 순서. products 는 상품마다 한 장씩 펼쳐진다. 인스타 캐러셀은 최대 10장
    slides: list[SlideKind] = Field(default=["cover", "products", "summary"], min_length=1)
    product_image: Literal["cutout", "photo"] = "photo"  # 쇼핑몰 원본 사진 또는 누끼
    hide_faces: bool = True  # 원본 사진에서 모델 얼굴이 없는 사진을 고르고, 없으면 얼굴 아래로 자름
    photo_candidates: int = Field(default=6, ge=1, le=20)  # 상품마다 살펴볼 창고 사진 수
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
    # 댓글 안내 장 (slides 의 comment). 상품 정보는 댓글로 받으라는 안내. {logo} 는 visual.logo_text
    comment_title: list[str] = ["상품 정보가 궁금하다면", "댓글을 남겨 주세요"]
    comment_sub: list[str] = ["댓글을 남기면 DM으로", "상품 링크를 보내 드려요"]
    comment_box: str = "댓글 달기..."  # 가운데 댓글 입력창 모양 안의 글자. 빈 문자열이면 창 없음
    comment_title_size: int = Field(default=64, gt=0)
    comment_sub_size: int = Field(default=32, gt=0)
    # 사진 출처 표기. {source} 는 source_names 의 이름, {shop} 은 스토어 이름. 빈 문자열이면 생략
    photo_credit: str = "사진 출처 {source} · {shop}"
    summary_credit: str = ""  # 요약 장 목록 아래 출처 한 줄
    credit_size: int = Field(default=20, gt=0)
    source_names: dict[str, str] = {"zigzag": "지그재그"}  # 상품 소스 이름 → 보이는 이름


class CoverCollage(BaseModel):
    """여러 벌 게시물 표지를 벌 전부의 콜라주로 (tools.cover_collage, 2026-10-07 사용자).
    벌마다 코디컷에서 옷만 코드로 오려(AI 없음) 한 배경 위에 위아래 두 줄로 놓고,
    두 줄 사이 가운데에 박스 없는 제목. 위치·크기는 4:5 장 기준 비율, 글자는 폭 1080px 기준."""

    enabled: bool = False  # false 면 예전처럼 1번 벌 코디컷 + 제목
    top_color: str = Field(default="#969694", pattern=COLOR)  # 배경 위쪽 색
    bottom_color: str = Field(default="#767675", pattern=COLOR)  # 배경 아래쪽 색
    texture: float = Field(default=7, ge=0, le=20)  # 배경 결 세기 (0 이면 매끈한 단색)
    rows: tuple[float, float] = (0.222, 0.781)  # 위·아래 줄 벌들의 가운데 높이
    look_height: float = Field(default=0.344, gt=0.1, le=0.5)  # 벌 한 개 높이
    max_width: float = Field(default=0.31, gt=0.1, le=0.5)  # 벌 한 개 최대 폭 (옆 벌과 안 겹치게)
    shadow: float = Field(default=0.45, ge=0, le=1)  # 옷 아래 그림자 진하기
    title_size: int = Field(default=80, gt=0)  # 긴 줄은 자동으로 줄어듦
    title_line_spacing: float = Field(default=1.25, gt=0)
    title_top: float = Field(default=0.483, gt=0, lt=1)  # 제목 첫 줄 기준선 높이
    title_color: str = Field(default="#FFFFFF", pattern=COLOR)
    title_shadow: float = Field(default=0.6, ge=0, le=1)
    logo_size: int = Field(default=26, gt=0)  # 계정 이름 (visual.logo_text, 글꼴 visual.logo_font)
    logo_bottom: float = Field(default=0.019, ge=0, le=0.2)  # 이미지 아래에서 계정 이름까지
    logo_color: str = Field(default="#EBEBEB", pattern=COLOR)


class Looks(BaseModel):
    """코디 여러 벌 게시물 (20단계). 같은 주제로 코디 여러 벌을 한 게시물에 담고, 벌마다
    코디컷 한 장 위에 상품 정보 몇 줄을 얹는다. 켜면 feed.slides 에 looks 를 넣는다."""

    enabled: bool = False  # false 면 예전처럼 한 벌 + 상품 장
    count: int = Field(default=6, ge=2, le=8)  # 게시물에 넣는 벌 수
    min: int = Field(default=5, ge=2, le=8)  # 고르기에서 빼더라도 이만큼은 남아야 함
    spare: int = Field(default=2, ge=0, le=6)  # 후보로 더 보내는 벌 수 (고르기에서 바꿔 넣기)
    # 같은 게시물 안에서 다시 써도 되는 카테고리와 횟수 (가방·신발은 두 벌까지 같이 써도 자연스러움)
    share_categories: list[Category] = ["가방", "신발", "소품"]
    max_share: int = Field(default=2, ge=1, le=4)
    # 옷(아우터·상의·하의)도 max_share 벌까지 같이 쓸 수 있는 상품 수. 0 이면 옷은 한 벌에만
    # (2026-10-12 women 사용자: "한 게시물에 같은 옷 1~2개 정도는 겹쳐도 돼")
    max_shared_clothes: int = Field(default=0, ge=0, le=6)
    # 옷(아우터·상의·하의)의 같은 포인트 말(style.point_words, 예: 스웨이드)이 이 벌 수보다
    # 많으면 경고. 한 아이템이 여섯 벌을 다 차지하지 않게 (2026-10-06). None 이면 보지 않음
    max_same_kind: int | None = None
    # 소품(주얼리·양말·모자·벨트·시계)이 있는 벌이 이보다 적으면 경고 (2026-10-06)
    min_accessory_looks: int | None = None
    # 상의·아우터 종류별 최대 벌 수. 이름에 그 말이 든 상의·아우터가 있는 벌을 센다
    # (2026-10-07 "전부 셔츠고 너무 무난한 기본템"). 예: {셔츠: 2, 베스트: 2}
    type_limits: dict[str, int] = {}
    # type_limits 를 셀 때 이름에서 먼저 지우는 말 (예: "티셔츠"가 셔츠로 세지지 않게)
    type_ignore: list[str] = []
    candidates: int = Field(
        default=1, ge=1, le=3
    )  # 벌마다 한 번에 만드는 코디컷 수 (장당 약 $0.15)
    # 표지(1장, 제목) 다음 벌 장에 1번 벌을 다시 넣을지. false 면 1번 벌은 표지로만 보임
    repeat_cover_look: bool = True
    # 표지 제목에 벌 수("여섯 벌", "6벌")를 써도 되는지. false 면 tools.plan check 가 막는다
    title_count: bool = True
    # 벌 장 글자 (tools.feed). 크기는 폭 1080px 기준, 위치는 높이 대비
    label: str = "LOOK {n}"  # 벌 번호 (빈 문자열이면 생략)
    label_size: int = Field(default=26, gt=0)
    # 상품 한 줄: 카테고리(보조 색) 이름(굵게) 스토어·가격(작게, 자리가 있을 때)
    line_size: int = Field(default=28, gt=0)
    shop_size: int = Field(default=21, gt=0)
    text_width: float = Field(default=0.5, gt=0.2, le=1)  # 글자가 들어가는 폭 (이미지 폭 대비)
    show_shop: bool = True
    show_price: bool = False  # 가격은 바뀔 수 있어 기본으로 넣지 않음
    # 이미지 출처 한 줄. 벌 장은 상품 목록 아래, 표지는 오른쪽 아래. 빈 문자열이면 생략
    credit: str = ""
    credit_size: int = Field(default=18, gt=0)
    cover_collage: CoverCollage = CoverCollage()  # 표지를 벌 전부의 콜라주로 (모델컷 날 제외)
    line_spacing: float = Field(default=1.55, ge=1.0)
    bottom: float = Field(default=0.07, ge=0, le=0.4)  # 이미지 아래에서 마지막 줄까지
    text_color: str = Field(default="#FFFFFF", pattern=COLOR)
    sub_color: str = Field(default="#E3E3E3", pattern=COLOR)
    gradient_height: float = Field(default=0.42, ge=0, le=1)
    gradient_opacity: float = Field(default=0.55, ge=0, le=1)
    # 코디컷 고정 구도 (영어, 지시문 $layout). 모든 벌이 같은 구도다.
    # 2026-10-07 사용자: 옷이 오른쪽으로 치우침 → 옷은 가운데, 가방은 왼쪽 허리 높이.
    # 2026-10-12 사용자 참고 코디컷: 소품은 오른쪽 위 가슴 높이,
    # 신발은 오른쪽 아래 하의 옆으로 하나로 고정
    # (짧은 하의일 때의 예외 자리와 '위 구도를 덮어쓴다' 문장은 뺐다)
    # 2026-10-14 사용자 참고(wyeun_): 옷이 작고 바닥이 많이 보임
    # → 왼쪽 아래 40%x35% 빈자리 규칙을 빼고 옷이 화면을 크게 채운다.
    # 상품 글자는 그라데이션 위에 쓰고, 가방·신발만 왼쪽 아래를 피한다.
    composition: str = (
        "the outfit is large and fills the frame: the clothes form one figure centered"
        " horizontally, the collar near the top edge and the hem near the bottom edge"
        " (short bottoms: the figure moves down a little). Jewelry or a watch, only if"
        " listed, sit neatly on the right at chest height. The bag lies on the left beside"
        " the top at waist height, slightly overlapping the clothes. The shoes sit in the"
        " lower right beside the bottoms, not touching them. Keep the bag and shoes out of"
        " the lower-left corner; the clothes may reach it."
    )


class ReviewRules(BaseModel):
    """7단계 이미지 검수 (tools.review). 점수는 항목 5개 × 0~2점 = 10점 만점."""

    candidates: int = Field(default=2, ge=1, le=4)  # 한 번에 만드는 코디컷 후보 수
    max_rounds: int = Field(
        default=2, ge=1, le=4
    )  # 모두 기준 미달이면 다시 만드는 횟수 (첫 회 포함)
    min_score: int = Field(default=8, ge=0, le=10)  # 이 점수 이상이고 0점 항목이 없어야 통과
    check_slides: bool = True  # 상품 장 사진 색·얼굴·글자 잘림도 본다


class OutfitImage(BaseModel):
    """표지 코디컷 만드는 방식 (11단계). generate 는 쇼핑몰 사진을 참고로 생성형 AI 가 코디 전체를
    한 장으로 그린다 (tools.outfit_gen). photoroom 은 예전 방식 (누끼를 코드로 배치, tools.poc)."""

    engine: Literal["generate", "photoroom"] = "generate"
    model: str = "nano_banana_pro"  # config/models.yaml generate.models 키
    prompt: str = "outfit_generate"  # prompts/<이름>.md
    scene: str = (
        "smooth warm light-grey linen-textured floor, soft natural window light from the upper "
        "left, calm and clean"
    )
    # 아우터가 있는 날 기본으로 입히는 방법 (영어). 기획의 styling 이 있으면 그것을 쓴다
    outer_styling: str = ""
    # 상의 두 개를 겹쳐 입는 레이어드 (21단계). 기획에서 안에 입는 상의에 wear_with: 상의 를 적으면
    # 이 문장으로 입힌다. {inner} 안쪽 상의, {top} 겉 상의 (영어 이름은 도구가 채움)
    layer_styling: str = (
        "The {inner} is layered under the {top}: its collar shows at the neckline, its cuffs peek "
        "out about 3 cm beyond the sleeves of the {top}, and its hem shows a little below the hem "
        "of the {top}."
    )


class Reels(BaseModel):
    """릴스 (19단계, tools.reels). 그날 캐러셀 슬라이드를 세로 영상(1080×1920)으로 이어 붙인다.
    영상 AI 는 쓰지 않는다 (코드로 장을 옆으로 넘기기만). 게시는 schedule.reels_at
    (없으면 캐러셀 게시 직후)."""

    enabled: bool = False
    # 넣을 장 종류. null 이면 캐러셀 장 전부를 캐러셀 순서대로 (펼친컷·모델컷 어느 날이든)
    slides: list[SlideKind] | None = None
    # 장 종류별로 보여 주는 시간 (초). 넘기는 시간은 앞 장에 포함. 없는 종류는 default_sec
    seconds: dict[SlideKind, float] = {"cover": 2.5, "comment": 2.0}
    default_sec: float = Field(default=1.6, gt=0.3, le=10)
    # cut: 바로 다음 장 / slide: 캐러셀 넘기듯 옆으로 / fade
    transition: Literal["slide", "fade", "cut"] = "cut"
    transition_sec: float = Field(default=0.35, ge=0, le=1.5)
    # 슬라이드(4:5) 위쪽 끝 위치 (세로 1920 대비). 아래쪽은 인스타 캡션·버튼이 덮는다
    top: float = Field(default=0.105, ge=0, le=0.3)
    # 펼친컷 날 표지·벌 장은 코디컷 위아래 바닥을 코드로 늘려 화면(9:16)을 꽉 채운다 (위아래 띠
    # 없음, AI 비용 없음, 2026-10-07 사용자). 글자는 4:5 장을 top 에 올렸을 때와 같은 자리
    fill: bool = False
    background: str | None = Field(default=None, pattern=COLOR)  # 없으면 feed.background
    fps: int = Field(default=30, ge=24, le=60)
    # 배경 음악 폴더 (저장소 기준). 상업 이용이 허락된 음원만 (mp3, m4a, wav).
    # 여러 곡이면 날짜마다 돌려 쓴다. 비어 있거나 없으면 소리 없는 영상
    music_dir: str | None = None
    music_volume: float = Field(default=0.8, ge=0, le=2)
    # 이 날 수마다 새 곡을 코드로 만들어 music_dir 에 넣는다 (tools.music_gen, music 워크플로).
    # null: 안 함
    music_refresh_days: int | None = Field(default=None, ge=1)
    music_refresh_count: int = Field(default=3, ge=1, le=10)  # 한 번에 만드는 곡 수
    music_keep: int = Field(default=9, ge=1, le=60)  # 자동으로 만든 곡(gen_)은 최근 이만큼만 남김
    share_to_feed: bool = True  # 릴스를 프로필 그리드에도 보이게 (false: 릴스 탭에만)
    # telegram: reels_at 에 영상과 캡션을 텔레그램으로 보냄 (사람이 인스타 앱에서 음악을 붙여 올림.
    # 앱 음악은 게시 API 로 넣을 수 없다, 2026-10-06 사용자) / instagram: 자동 게시
    delivery: Literal["telegram", "instagram"] = "telegram"


class ModelShot(BaseModel):
    """모델컷 (16단계). 전날 게시물의 벌들을 옷을 입은 얼굴 없는 AI 인물 사진으로 다시 그린다
    (tools.model_post, schedule.post_days 의 model 날). 얼굴은 자르지 않고 포즈·물건으로 가린다."""

    prompt: str = "outfit_model"  # prompts/<이름>.md
    # 인물·포즈 (영어). 벌마다 하나씩 돌려 쓴다 (날짜마다 시작 위치가 바뀜). 인물 묘사를 같게 써서
    # 한 게시물이 한 사람의 코디 일기처럼 보이게 한다
    poses: list[str] = Field(
        default=[
            "a slim young woman with long straight dark hair takes a full-body mirror selfie; "
            "she holds her phone up in front of her face so the phone hides her whole face"
        ],
        min_length=1,
    )
    mood: str = "a calm, bright minimal space with soft natural daylight, Pinterest editorial"


WEEKDAYS = "월화수목금토일"
WEEKLY = r"^[월화수목금토일](,[월화수목금토일])* ([01]\d|2[0-3]):[0-5]\d$"


def minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class Schedule(BaseModel):
    """매일 자동 실행 시간표 (9단계, tools.schedule). 시간은 timezone 기준 HH:MM.
    정각은 GitHub Actions 지연이 커서 피한다. 바꾼 뒤에는 `python -m tools.schedule sync`."""

    enabled: bool = True  # false 면 이 계정은 자동으로 돌지 않음 (버튼으로만)
    timezone: str = "Asia/Seoul"
    candidates_at: str = Field(pattern=HHMM)  # 코디 후보를 텔레그램으로 보내는 시각
    pick_check_every: int = Field(default=30, ge=15, le=240)  # 고른 번호를 확인하는 간격 (분)
    pick_deadline: str = Field(pattern=HHMM)  # 이때까지 안 고르면 pick.on_timeout
    publish_at: str = Field(pattern=HHMM)  # 게시 시각
    publish_retry_every: int = Field(default=30, ge=15, le=240)  # 승인이 늦을 때 다시 볼 간격 (분)
    publish_retries: int = Field(default=4, ge=0, le=12)  # 게시 시각 뒤 다시 보는 횟수
    # monetize: manual_link 일 때 고르기 마감~게시 사이에 /ok 를 확인하는 간격 (분, 0 이면 안 봄).
    # /ok 가 보이면 상품별 지그재그 링크를 보내 공유리워드 링크를 미리 만들게 한다 (tools.share)
    ok_check_every: int = Field(default=60, ge=0, le=240)
    # 승인 마감 (12단계). 이 시각 뒤에 온 /ok 는 게시에 쓰지 않는다 (없으면 게시 마지막 확인까지).
    # 게시가 다음 날 아침이면 그 전 새벽. /ok·/fix 확인은 후보를 보낸 뒤부터 이때까지 매 간격
    ok_deadline: str | None = Field(default=None, pattern=HHMM)
    ok_remind_at: str | None = Field(
        default=None, pattern=HHMM
    )  # 이때까지 /ok 가 없으면 한 번 알림
    themes_at: str | None = Field(default=None, pattern=WEEKLY)  # 주간 주제 (예: "일 18:17")
    collect_at: str | None = Field(default=None, pattern=WEEKLY)  # 상품 수집 (예: "월,목 04:17")
    token_refresh_at: str | None = Field(default=None, pattern=WEEKLY)  # 인스타 토큰 연장
    # 릴스 게시 시각 (19단계, profile reels). 캐러셀을 게시한 날에만 올린다.
    # null 이면 캐러셀 게시 직후
    reels_at: str | None = Field(default=None, pattern=HHMM)
    # GitHub 예약(cron) 줄 (12단계). all: 모든 칸. backup: 외부 스케줄러(cron-job.org)가 정시에
    # 깨울 때 예비로 주요 칸(후보, 고르기 처음·마감, 게시 처음·마지막, 주간 작업)만 둬서
    # Actions 시간을 아낌. minimal: 첫 게시 칸 하루 한 줄만 (외부 스케줄러가 멈춰도 하루 한 번은
    # 깨어나 밀린 칸을 따라잡음)
    github_cron: Literal["all", "backup", "minimal"] = "all"
    # 요일별 게시물 종류 (16단계, 게시 날짜 기준). flatlay: 펼친 코디컷 (후보 고르기부터),
    # model: 전날 게시물 벌을 모델컷으로 (고르기 없이 후보 시각에 바로 만듦), 빠진 요일은 쉼.
    # 없으면 매일 flatlay
    post_days: dict[str, Literal["flatlay", "model"]] | None = None
    # 이 게시 날짜부터 post_days 를 따른다. 그 전은 매일 flatlay (이미 준비된 게시물을 그대로)
    post_days_from: date | None = None

    @field_validator("post_days")
    @classmethod
    def _days(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        bad = [d for d in v or {} if d not in WEEKDAYS or len(d) != 1]
        if bad:
            raise ValueError(f"post_days 요일은 {'·'.join(WEEKDAYS)} 중 하나씩: {bad}")
        return v

    def post_kind(self, day: date) -> Literal["flatlay", "model"] | None:
        """게시 날짜의 게시물 종류. None 이면 그날은 쉰다."""
        if self.post_days is None or (self.post_days_from and day < self.post_days_from):
            return "flatlay"
        return self.post_days.get(WEEKDAYS[day.weekday()])


class PickRules(BaseModel):
    """텔레그램으로 코디 후보를 보내고 사람이 고른 것만 이미지로 만든다 (9단계, tools.pick).
    후보는 쇼핑몰 원본 사진으로 보여 주고 코디컷 이미지 AI 는 고른 뒤에만 부른다."""

    enabled: bool = True  # false 면 고르지 않고 Claude 가 한 벌을 정해 바로 만든다
    count: int = Field(default=5, ge=2, le=8)  # 보내는 후보 수
    on_timeout: Literal["first", "skip"] = "first"  # 마감까지 안 고르면: 1번으로 / 그날 건너뜀
    max_again: int = Field(default=2, ge=0, le=5)  # "다시"로 새 후보를 받을 수 있는 횟수


class FeedbackRules(BaseModel):
    """최종 이미지 피드백 (15단계, tools.feedback). 미리보기를 받은 뒤 텔레그램에
    `/fix 1 신발 더 크게` (1 = 장 번호), `/fix 캡션 더 짧게` 를 보내면 schedule 이 읽어
    같은 기획으로 고친 게시물을 다시 만들고(daily mode=fix) 새 미리보기를 보낸다.
    받은 피드백은 모두 data/<계정>/feedback.jsonl 에 쌓는다."""

    enabled: bool = True
    max_fixes: int = Field(default=2, ge=0, le=5)  # 하루에 다시 만드는 횟수
    # /fix 를 확인하는 간격 (분). 고르기 마감(고르기를 끄면 후보 시각)부터 게시 시각까지.
    # 0 이면 따로 깨어나지 않고 다른 자동 실행 때만 본다
    check_every: int = Field(default=30, ge=0, le=240)
    # 게시 마지막 확인 시각까지 이만큼(분) 남아야 다시 만든다 (Claude·코디컷·검수 시간)
    render_minutes: int = Field(default=40, ge=10, le=180)
    # 기획(tools.plan context)과 검수(tools.review prep)에 보여 줄 최근 피드백 기간
    # (일, 0 이면 안 보임)
    recent_days: int = Field(default=14, ge=0, le=60)
    # 새 피드백을 규칙(지시문·검수 기준·스킬·계정 설정)에 반영하는 PR 을 만드는 시각
    # (계정 시간대 HH:MM, 하루 한 번, feedback-rules 워크플로). 새 피드백이 없으면 건너뛴다.
    # null 이면 안 함
    rules_at: str | None = Field(default=None, pattern=HHMM)


class Comments(BaseModel):
    """댓글 → DM 코디 링크 (10단계, tools.comments). 캡션에 안내 문구를 넣고,
    키워드 댓글이 달리면 인스타 비공개 답장(댓글 1개에 DM 1번, 댓글 후 7일 안)으로
    상품 링크를 보낸다. 댓글 확인은 schedule 워크플로가 다른 자동 실행 때 같이 하고,
    check_at 에 적은 시각에 따로 깨어나서도 한다."""

    enabled: bool = False
    # DM 을 누가 보내나: external(ManyChat 같은 공식 파트너가 댓글 즉시 보냄, 여기서는 캡션만)
    #   builtin(tools.comments 가 자동 실행 때 댓글을 확인해 보냄, 30분~몇 시간 늦음)
    mode: Literal["external", "builtin"] = "external"
    # 캡션에 넣는 안내 문구 (copy.link_line 대신). {keyword} 는 keywords 첫 번째 (있을 때)
    cta: str = "코디 정보가 궁금하면 댓글을 남겨 주세요. DM으로 상품 링크를 보내 드려요"
    keywords: list[str] = []  # 댓글에 이 말이 있어야 DM. 비어 있으면 모든 댓글에 DM
    check_at: list[str] = []  # 댓글 확인만 하러 따로 깨어나는 시각 (계정 시간대 HH:MM)
    with_other_ticks: bool = True  # 후보·고르기·게시 같은 다른 자동 실행 때도 같이 확인
    days: int = Field(default=7, ge=1, le=7)  # 게시 후 며칠 동안 댓글을 볼지 (인스타 규칙상 7일)
    # DM 글. {date} 게시 날짜, {title} 제목. 상품 줄 {index} {category} {shop} {name} {url}
    dm_intro: str = "댓글 남겨 주셔서 고마워요. '{title}' 코디 상품 정보예요"
    dm_item: str = "{index}. {category} · {shop} {name}\n{url}"
    dm_outro: str = "사이즈·재고는 링크에서 확인해 주세요"
    public_reply: str = ""  # 댓글에 공개 답글도 달기 (예: "DM 보내 드렸어요"). 빈 문자열이면 안 함
    # 링크 방식: zigzag(상품 링크 그대로)
    #   share_reward(게시 뒤 텔레그램으로 공유리워드 링크를 받아 씀)
    link_mode: Literal["zigzag", "share_reward"] = "zigzag"
    share_wait_hours: int = Field(default=6, ge=0, le=72)  # 공유 링크 대기 (넘으면 상품 링크)


class LinkPage(BaseModel):
    """최근 게시물 상품 링크를 모은 공개 페이지 (10단계, tools.link_page). 게시할 때마다 새로 쓴다.
    ManyChat DM 에 이 주소 하나만 넣어 두면 게시물마다 DM 을 고치지 않아도 된다.
    공개 사진 저장소(config/publish.yaml image_host)의 GitHub Pages 에 <계정>/index.html 로
    올린다."""

    enabled: bool = False
    title: str = "코디 상품 모음"  # 페이지 맨 위 제목
    intro: str = "게시물에 나온 상품 링크예요. 최신 게시물이 맨 위에 있어요"
    keep: int = Field(default=14, ge=1, le=60)  # 보여 줄 최근 게시물 수
    item_line: str = "{category} · {shop}"  # 상품 이름 위 작은 글자
    # monetize: manual_link 일 때 게시 알림 답장의 공유리워드 링크를 반영하는 기간 (일)
    share_days: int = Field(default=7, ge=1, le=30)
    # /ok 뒤 상품별 지그재그 링크를 텔레그램으로 보낸다
    # (monetize 가 none 이어도, 링크를 직접 만들 때 쓰게).
    # manual_link 이면 늘 보내고 답장한 공유리워드 링크를 상품 페이지에 반영한다
    send_links: bool = False


class Instagram(BaseModel):
    token_secret: str  # GitHub Secret 이름 (값이 아님)
    user_id_secret: str


class Profile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

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
    copy_rules: Copy = Field(default=Copy(), alias="copy")
    comment_tags: CommentTags = CommentTags()
    ai_label: AiLabel = AiLabel()
    review: ReviewRules = ReviewRules()
    looks: Looks = Looks()
    outfit_image: OutfitImage = OutfitImage()
    model_shot: ModelShot = ModelShot()
    schedule: Schedule
    pick: PickRules = PickRules()
    feedback: FeedbackRules = FeedbackRules()
    comments: Comments = Comments()
    link_page: LinkPage = LinkPage()
    reels: Reels = Reels()
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
    if profile.reels.slides and len(set(profile.reels.slides)) != len(profile.reels.slides):
        raise ValueError("reels.slides 에 같은 종류가 두 번 들어 있습니다")
    if len(set(profile.feed.slides)) != len(profile.feed.slides):
        raise ValueError("feed.slides 에 같은 종류가 두 번 들어 있습니다")
    if sum(profile.themes.type_mix.values()) != profile.themes.per_week:
        raise ValueError("themes.type_mix 개수의 합이 themes.per_week와 다릅니다")
    lk = profile.looks
    if lk.enabled:
        if "looks" not in profile.feed.slides:
            raise ValueError("looks.enabled 면 feed.slides 에 looks 를 넣어야 합니다")
        if lk.min > lk.count:
            raise ValueError("looks.min 이 looks.count 보다 큽니다")
        slides = (
            len(profile.feed.slides)
            - 1
            + lk.count
            - (0 if lk.repeat_cover_look or "cover" not in profile.feed.slides else 1)
        )
        if "products" in profile.feed.slides:
            raise ValueError("looks 와 products 는 함께 쓸 수 없습니다 (슬라이드가 너무 많아짐)")
        if slides > 10:
            raise ValueError(f"looks: 슬라이드가 {slides}장이 됩니다 (최대 10). count 를 줄이세요")
    elif "looks" in profile.feed.slides:
        raise ValueError("feed.slides 에 looks 가 있으면 looks.enabled 를 켜야 합니다")
    c = profile.copy_rules
    for name in ("title_chars", "points", "hashtags"):
        lo, hi = getattr(c, name)
        if lo > hi:
            raise ValueError(f"copy.{name} 의 앞 숫자가 뒤 숫자보다 큽니다")
    sch = profile.schedule
    start, deadline, publish = (
        minutes(sch.candidates_at),
        minutes(sch.pick_deadline),
        minutes(sch.publish_at),
    )
    # 후보 → 마감 → 게시 순서 (자정을 넘겨도 됨). 마감 뒤 이미지 만들 시간이 1시간은 있어야 함
    if not 0 < (deadline - start) % 1440 < (publish - start) % 1440:
        raise ValueError("schedule: candidates_at → pick_deadline → publish_at 순서여야 합니다")
    if (publish - deadline) % 1440 < 60:
        raise ValueError("schedule: pick_deadline 은 publish_at 보다 1시간 이상 앞서야 합니다")
    end = publish
    if sch.ok_deadline:
        end = minutes(sch.ok_deadline)
        if not (deadline - start) % 1440 < (end - start) % 1440 < (publish - start) % 1440:
            raise ValueError("schedule: pick_deadline → ok_deadline → publish_at 순서여야 합니다")
    if (
        sch.ok_remind_at
        and not 0 < (minutes(sch.ok_remind_at) - start) % 1440 < (end - start) % 1440
    ):
        raise ValueError(
            "schedule: ok_remind_at 은 candidates_at 과 승인 마감(게시) 사이여야 합니다"
        )
    cm = profile.comments
    if any(not re.fullmatch(HHMM, t) for t in cm.check_at):
        raise ValueError("comments.check_at 은 HH:MM 목록이어야 합니다")
    if cm.link_mode == "share_reward" and profile.monetize != "manual_link":
        raise ValueError(
            "comments.link_mode: share_reward 는 monetize: manual_link 일 때만 쓸 수 있습니다 "
            "(광고 표기)"
        )
    if any(not t.startswith("#") or " " in t for t in [*c.base_hashtags, c.ad_hashtag]):
        raise ValueError("copy 해시태그는 #으로 시작하고 공백이 없어야 합니다")
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
        print(
            yaml.safe_dump(
                profile.model_dump(mode="json", by_alias=True), allow_unicode=True, sort_keys=False
            )
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
