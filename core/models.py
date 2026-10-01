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
    # 생성형 코디컷(outfit_image.engine: generate)에 참고로 보낼 창고 사진 번호 1~3개 (고른 색만).
    # 없으면 cutout_photo 와 photo 를 쓴다
    ref_photos: list[int] | None = Field(default=None, max_length=4)
    # 생성형 코디컷용 실제 크기 (영어, 예: "about 110 cm long from shoulder to hem").
    # 없으면 cm·기본값으로 도구가 쓴다
    size: str | None = None
    slot: str | None = None  # config/layout.yaml 자리 (예: 목걸이). 없으면 카테고리
    segment: str | None = None  # 누끼에서 남길 물건 (영어 단수, 예: bag, belt, necklace)
    # 누끼 방식 (flatlay, segment). 생략하면 옷은 flatlay. 착용컷을 flatlay 로 바꾸면
    # 핏이 바뀌는 옷은 segment 로 사진 속 모양 그대로 오린다 (2026-09-29 슬랙스가 부츠컷이 됨)
    method: str | None = None
    cm: float | None = Field(default=None, gt=0)  # 실제 치수가 기본값과 다를 때
    wear_with: Category | None = None  # 이 상품을 입힐 기준 상품 (셔츠 → 아우터, 벨트 → 하의)
    wear_how: str | None = None  # 입는 방법 (영어)
    wear_modes: list[str] | None = None  # direct, over, open, peek


class PlanText(BaseModel):
    """제목과 캡션 재료. 캡션 전체는 tools.plan 이 프로필 copy 규칙으로 조립한다."""

    title_lines: list[str] = Field(min_length=1)
    intro: str | None = None  # 캡션 맨 앞 도입 한 줄 (profile copy.intro 가 켜져 있으면 필수)
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
    # 생성형 코디컷에서 옷을 어떻게 입혀 펼칠지 (영어). 없으면 profile outfit_image.outer_styling
    styling: str | None = None
    cover: str | None = None  # 이미 만든 코디컷 (있으면 코디컷을 새로 만들지 않음)


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


class PostLink(BaseModel):
    """게시물 상품 한 개의 링크 (10단계 댓글 DM 에 씀)."""

    product: str  # 상품 창고 id
    category: str
    shop: str
    name: str  # 짧은 이름
    url: str  # 상품 페이지
    image: str = ""  # 대표 사진 주소 (상품 페이지 썸네일)


class ShareRequest(BaseModel):
    """공유리워드 링크를 붙여 달라고 텔레그램으로 보낸 요청.
    monetize: manual_link 면 게시 알림이 이 요청을 겸한다 (tools.share, 상품 페이지에 씀).
    comments.link_mode: share_reward 면 댓글 DM 도구가 따로 보낸다."""

    sent_at: int
    message_ids: list[int] = []
    links: dict[int, str] = {}  # 상품 순서(1부터) → 붙여 준 공유 링크


class PostRecord(BaseModel):
    """게시 기록 한 줄 (data/<계정>/posts.json). 8단계 게시 job 이 쓰고,
    기획이 중복을 피할 때, 10단계 댓글 DM 이 링크를 보낼 때 읽는다.
    상품 정보(링크)만 남긴다."""

    date: date
    title: str  # 제목 두 줄을 이은 것
    theme: str = ""  # 주제 (title_hint)
    products: list[str] = []  # 상품 창고 id
    permalink: str | None = None
    media_id: str | None = None  # 인스타 게시물 id (댓글 확인용)
    links: list[PostLink] = []  # 상품 링크 (없으면 상품 창고에서 찾음)
    share: ShareRequest | None = None  # 공유리워드 링크 요청과 받은 링크 (monetize: manual_link)
    reel_media_id: str | None = None  # 같은 코디로 올린 릴스 (19단계)
    reel_permalink: str | None = None


class PostDm(BaseModel):
    """게시물 하나의 댓글 DM 기록. 댓글 작성자 정보는 남기지 않고 댓글 id 의 해시만 둔다."""

    media_id: str | None = None
    sent: list[str] = []  # DM 을 보낸 댓글 (해시)
    skipped: list[str] = []  # 보내지 못해 다시 시도하지 않을 댓글 (해시)
    share: ShareRequest | None = None


class DmState(BaseModel):
    """댓글 DM 상태 (data/<계정>/dm.json). 게시 날짜 → 기록. 기간(comments.days)이 지나면 지운다."""

    posts: dict[str, PostDm] = {}
    alerted: str | None = None  # 오류 알림을 마지막으로 보낸 날짜 (같은 날 여러 번 보내지 않음)


class PreviewRecord(BaseModel):
    """텔레그램으로 보낸 미리보기 (결과 폴더의 telegram.json).
    게시 job 이 승인 답장을 찾을 때 쓴다."""

    account: str
    date: date
    run: str  # daily-results 브랜치 안 결과 폴더 이름 (예: 2026-09-27-run12)
    sent_at: int  # 보낸 시각 (유닉스 초). 이보다 뒤에 온 답장만 본다
    message_ids: list[int] = []  # 봇이 보낸 메시지 (이 메시지에 답장한 /ok 는 이 미리보기 것)
    sandbox: bool = False  # Photoroom 샌드박스(워터마크) 결과면 게시하지 않는다
    needs_human: bool = False


Decision = Literal["ok", "skip"]

# 피드백 처리 결과. rerender: 고친 게시물을 다시 만듦 / limit: 하루 횟수를 넘음 / late: 게시 마감이
# 가까움 / busy: 앞의 피드백으로 다시 만드는 중 / after_post: 이미 게시함 / sandbox: 시험 미리보기
# / no_preview: 미리보기를 보내기 전
FeedbackResult = Literal["rerender", "limit", "late", "busy", "after_post", "sandbox", "no_preview"]


class FeedbackEntry(BaseModel):
    """텔레그램 /fix 피드백 한 줄 (data/<계정>/feedback.jsonl, 15단계 tools.feedback).
    자주 나오는 피드백은 검수 기준·배치 규칙과 11단계 시험 세트에 더할 후보가 된다."""

    account: str
    date: date  # 게시 날짜
    run: str | None = None  # 피드백을 받은 미리보기 결과 폴더 (daily-results)
    slide: int | None = None  # 장 번호 (1부터). 캡션이나 전체면 None
    kind: Literal["cover", "product", "summary", "comment", "slide", "caption", "all"] = "all"
    product: str | None = None  # 상품 장이면 상품 창고 id
    text: str
    at: int  # 보낸 시각 (유닉스 초)
    update_id: int  # 텔레그램 업데이트 번호 (같은 글을 두 번 처리하지 않음)
    result: FeedbackResult
    round: int = 0  # rerender 면 그날 몇 번째 다시 만들기인지


class PickRecord(BaseModel):
    """텔레그램으로 보낸 코디 후보 (결과 폴더의 picks/pick.json, 9단계 tools.pick).
    후보 기획은 같은 폴더의 p1.yaml … (text 없는 DailyPlan)."""

    account: str
    date: date
    run: str  # daily-results 브랜치 안 결과 폴더 이름
    round: int = Field(default=1, ge=1)  # "다시"로 몇 번째 받은 후보인지
    count: int = Field(ge=1)  # 보낸 후보 수 (p1 ~ pN)
    sent_at: int  # 보낸 시각 (유닉스 초). 이보다 뒤에 온 답장만 본다
    message_ids: list[int] = []


class PickDecision(BaseModel):
    """후보에 대한 결정 (picks/picked.json). schedule job 이 쓰고, 한 번 정하면 다시 보지 않는다."""

    choice: int | Literal["again", "skip"]  # 고른 번호 (1부터)
    by: Literal["user", "timeout"]
    decided_at: int  # 유닉스 초


class ThemesRecord(BaseModel):
    """텔레그램으로 보낸 주간 주제 (data/<계정>/themes_review.json).
    "/themes again" 답장이 오면 schedule job 이 주제를 다시 정하게 한다."""

    account: str
    first: date  # 첫 주제 날짜
    last: date
    sent_at: int
    message_ids: list[int] = []
    handled: bool = False  # 다시 정하기를 이미 요청했으면 true


class PublishRecord(BaseModel):
    """게시 결과 (결과 폴더의 published.json)."""

    account: str
    date: date
    run: str
    media_id: str
    permalink: str | None = None
    images: int
    decision: Decision | Literal["auto", "force"]
    published_at: int  # 유닉스 초
    # 릴스 (19단계). 캐러셀 뒤에 올리면 채운다
    reel_media_id: str | None = None
    reel_permalink: str | None = None
    reel_published_at: int | None = None


class ReelsSpec(BaseModel):
    """릴스 영상 (19단계, tools.reels 가 manifest 에 채운다). 경로는 결과 폴더 기준."""

    video: str | None = None  # reels/reel.mp4
    cover: str | None = None  # 첫 화면 JPEG (텔레그램 미리보기용, 인스타는 영상 첫 프레임)
    audio: str | None = None  # 넣은 음원 파일 이름 (없으면 소리 없음)
    duration_sec: float | None = Field(default=None, gt=0)  # 게시 전 검사가 길이를 본다


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
