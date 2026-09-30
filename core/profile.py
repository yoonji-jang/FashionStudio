"""계정 프로필 로더와 검증.

사용법: python -m core.profile women
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class Place(BaseModel):
    name: str  # 사람이 보는 이름 (예: 서울)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


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
    ad_notice: str = (
        "(광고) 이 게시물은 제휴 링크를 포함하고 있어요"  # monetize: manual_link 일 때 첫 줄
    )
    ad_hashtag: str = "#광고"  # monetize: manual_link 일 때 첫 해시태그
    product_line: str = "{shop} · {name}"  # 캡션 제품 목록 한 줄
    link_line: str = "상품 정보는 프로필 링크에서 확인하세요"
    ai_notice: str = "이미지는 AI로 합성했으며 실제 색감·디테일과 다를 수 있어요"


# 캐러셀 슬라이드 종류 (tools.feed). comment 는 "상품 정보는 댓글로" 안내 장
SlideKind = Literal["cover", "products", "summary", "comment"]


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
    themes_at: str | None = Field(default=None, pattern=WEEKLY)  # 주간 주제 (예: "일 18:17")
    collect_at: str | None = Field(default=None, pattern=WEEKLY)  # 상품 수집 (예: "월,목 04:17")
    token_refresh_at: str | None = Field(default=None, pattern=WEEKLY)  # 인스타 토큰 연장
    reels_at: str | None = Field(default=None, pattern=HHMM)
    # GitHub 예약(cron) 줄 (12단계). all: 모든 칸. backup: 외부 스케줄러(cron-job.org)가 정시에
    # 깨울 때 예비로 주요 칸(후보, 고르기 처음·마감, 게시 처음·마지막, 주간 작업)만 둬서
    # Actions 시간을 아낌
    github_cron: Literal["all", "backup"] = "all"


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


class Story(BaseModel):
    """게시 직후 스토리 (17단계, tools.story). 인스타 API 는 스토리에 링크·멘션 스티커를 못 붙여서
    피드 앞 장(표지 등)을 1080x1920 세로 판에 옮기고 안내 문구를 이미지에 합성한다.
    크기는 폭 1080px 기준, 위치는 높이·폭 대비 비율."""

    enabled: bool = False
    # 0: 피드 게시 직후 같은 job 에서. 그 밖: 게시 시각(publish_at) 몇 분 뒤 schedule 이 올림
    delay_minutes: int = Field(default=0, ge=0, le=720)
    slides: int = Field(default=1, ge=1, le=3)  # 피드 앞에서부터 몇 장 (1: 표지만, 2~3: 표지+상품)
    # blur(그 장을 흐리게 깔기) 또는 색 "#RRGGBB"
    background: str = Field(default="blur", pattern=r"^(blur|#[0-9A-Fa-f]{6})$")
    blur_dim: float = Field(default=0.35, ge=0, le=1)  # blur 배경을 어둡게 하는 정도
    card_width: float = Field(default=0.84, gt=0, le=1)  # 가운데 사진 폭 (화면 폭 대비)
    card_center: float = Field(default=0.5, gt=0, lt=1)  # 가운데 사진 세로 중심 (높이 대비)
    card_radius: int = Field(default=18, ge=0)  # 사진 모서리 둥글기
    text_color: str = "#FFFFFF"
    text_shadow: float = Field(default=0.4, ge=0, le=1)
    heading: str = "오늘의 코디"  # 사진 위. 빈 문자열이면 생략
    heading_size: int = Field(default=46, gt=0)
    cta: str = "새 게시물 보러가기 →"  # 사진 아래. 빈 문자열이면 생략
    cta_size: int = Field(default=38, gt=0)
    text_gap: int = Field(default=44, ge=0)  # 사진과 글자 사이
    show_logo: bool = True  # 사진 아래 문구 밑에 visual.logo_text
    logo_size: int = Field(default=26, gt=0)
    # 게시 알림에 "앱에서 스토리에 링크 스티커를 붙여 주세요" 안내를 덧붙인다 (API 로는 못 붙임)
    sticker_hint: bool = False


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
    outfit_image: OutfitImage = OutfitImage()
    schedule: Schedule
    pick: PickRules = PickRules()
    feedback: FeedbackRules = FeedbackRules()
    comments: Comments = Comments()
    link_page: LinkPage = LinkPage()
    story: Story = Story()
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
