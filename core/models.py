"""파이프라인 전체가 주고받는 데이터 규격.

단계마다 이 모델로 파일을 읽고 쓰기 때문에, 필드를 바꿀 때는 테스트와
.claude/skills 문서도 함께 맞춰야 한다.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

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


class RefSlot(BaseModel):
    """참고 코디 한 칸 (2026-10-13 사용자: 참고 코디를 모은 뒤 그 옷을 창고에 한꺼번에 수집)."""

    category: Category
    color: str  # "차콜"
    desc: str  # "차콜 울 모크넥 니트 베스트, 짧은 기장"
    query: str  # 지그재그 검색어 "모크넥 니트베스트" (색은 빼고 종류만)


class RefLook(BaseModel):
    """참고 코디 한 벌. data/<계정>/refs.json. 쇼핑몰 착용컷을 칸별로 옮긴 것 (사진은 주소만)."""

    n: int = Field(ge=1)
    name: str
    source: str  # "쇼핑몰 코디 (우난)"
    photo: str | None = None  # 착용컷 사진 주소 (profile ref_search.scout_sites 만)
    added: date
    tags: list[str] = []  # 어울리는 주제 (출근룩, 데이트룩, 하객룩, 롱스커트, 올블랙 …)
    note: str = ""  # 한 끗·실루엣·입는 방식
    slots: list[RefSlot] = Field(min_length=2)
    # tools.refs match 가 채움: 칸 번호(0부터, 문자열) → 창고에서 맞을 만한 상품 id (좋은 순)
    matches: dict[str, list[str]] = {}
    ready: bool = False  # 꼭 맞춰야 하는 칸(아우터·상의·하의·원피스·신발)이 모두 창고에 있음


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
    items: list[Product] = Field(min_length=1, max_length=60)  # 개수 규칙은 profile outfit
    reason: str
    looks: list[list[str]] = []  # 여러 벌 게시물이면 벌마다 상품 id (20단계)


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
    # 캡션 코디 포인트 (한 줄씩). 여러 벌 게시물은 벌마다 looks[].point 를 써서 비워도 됨
    points: list[str] = []
    hashtags: list[str] = []  # 기본 해시태그(profile copy.base_hashtags)는 자동으로 더함


class Look(BaseModel):
    """코디 여러 벌 게시물(20단계, profile looks)의 한 벌. 벌마다 코디컷 한 장을 만든다."""

    items: list[PlanItem] = Field(min_length=1, max_length=10)
    reason: str = ""  # 이 벌을 고른 이유. 첫 문장은 텔레그램 후보 목록에 한 줄로 보인다
    point: str = ""  # 캡션에 넣는 이 벌 한 줄 (write-copy)
    # 생성형 코디컷에서 옷을 어떻게 입혀 펼칠지 (영어). 없으면 profile outfit_image.outer_styling
    styling: str | None = None
    cover: str | None = None  # 이미 만든 코디컷 (있으면 이 벌은 새로 만들지 않음)
    # /fix 로 고쳐 그리기 (2026-10-13 사용자: 새로 그리지 말고 고쳐 그리기): 지난 코디컷 경로와
    # 고칠 점(영어). edit_from 이 있으면 처음부터 그리지 않고 이 그림에서 고칠 곳만 바꾼다
    edit_from: str | None = None
    edit_request: str | None = None
    # 모델컷(16단계)의 인물·포즈 (영어). 없으면 profile model_shot.poses 의 첫 포즈
    pose: str | None = None
    # 따라 한 기준 코디 (2026-10-13 사용자: 참고한 코디 사진도 같이 보내 줘).
    # ref 는 refs.json 번호, ref_photo 는 그 코디 사진 주소 (ref_search.scout_sites 만).
    # 후보 시트에 나란히 붙인다
    ref: str | None = None
    ref_photo: str | None = None


class DailyPlan(BaseModel):
    """하루 게시물 기획. daily-feed 스킬이 out/<날짜>/plan.yaml 로 쓰고
    tools.plan 이 검사·빌드한다. 한 벌이면 items, 여러 벌(20단계)이면 looks 를 쓴다."""

    account: str
    date: date
    theme: Theme
    items: list[PlanItem] = Field(default=[], max_length=10)
    looks: list[Look] = Field(default=[], max_length=9)
    reason: str  # 이 조합을 고른 이유 (색, 분위기)
    text: PlanText | None = None
    # 생성형 코디컷에서 옷을 어떻게 입혀 펼칠지 (영어). 없으면 profile outfit_image.outer_styling
    styling: str | None = None
    cover: str | None = None  # 이미 만든 코디컷 (있으면 코디컷을 새로 만들지 않음)
    edit_from: str | None = None  # /fix 로 고쳐 그릴 지난 코디컷 (Look.edit_from 과 같음)
    # 따라 한 기준 코디 사진 주소 (Look.ref_photo). 코디컷을 그릴 때 입는 모양 참고로 같이 보낸다
    ref_photo: str | None = None
    edit_request: str | None = None
    # 16단계: flatlay = 펼친 코디컷, model = 옷을 입은 얼굴 없는 AI 모델컷
    # (전날 게시물 벌을 다시 그림, tools.model_post)
    shot: Literal["flatlay", "model"] = "flatlay"
    # 여러 벌 게시물 표지: None 이면 profile looks.cover_collage,
    # false 면 이 게시물만 1번 벌 코디컷 표지
    # (2026-10-09 사용자 /fix "첫 장은 콜라주 말고 첫 코디 이미지로")
    cover_collage: bool | None = None
    # 콜라주가 아닐 때 표지로 쓰는 벌 번호 (1부터). None 이면 1번 벌.
    # daily-feed 가 고르고, 텔레그램 "/ok 3"·"/cover 3" 으로 바꾼다 (tools.cover_look, 2026-10-12)
    cover_look: int | None = Field(default=None, ge=1)
    source_run: str | None = None  # 모델컷이면 벌을 가져온 전날 결과 폴더 (예 2026-10-05-run90)
    mood: str | None = None  # 모델컷 장소·분위기 (영어). 없으면 profile model_shot.mood
    pose: str | None = None  # 한 벌 기획의 모델컷 인물·포즈 (여러 벌이면 looks[].pose)
    # 장면 모델컷 (2026-10-14 사용자, profile flow): 사용자가 보낸 장면 사진 1장
    # (저장소 기준 경로). 있으면 shot: model 을 profile flow.scene_prompt 지시문으로 그리고
    # 이 사진을 무드 참고로 같이 보낸다. mood 는 이 사진에서 읽은 무드,
    # 벌 pose 는 같은 무드 안에서 벌마다 다른 자리·포즈
    scene_image: str | None = None

    @model_validator(mode="after")
    def _items_or_looks(self) -> DailyPlan:
        if bool(self.items) == bool(self.looks):
            raise ValueError("items(한 벌)와 looks(여러 벌) 중 하나만 써야 합니다")
        return self

    def all_items(self) -> list[PlanItem]:
        """모든 상품 (여러 벌이면 벌 순서대로 이어서, 같은 상품은 처음 한 번만)."""
        if not self.looks:
            return list(self.items)
        seen: dict[str, PlanItem] = {}
        for look in self.looks:
            for it in look.items:
                seen.setdefault(it.product, it)
        return list(seen.values())

    def look_plan(self, index: int) -> DailyPlan:
        """index 번째(0부터) 벌을 한 벌짜리 기획으로. 코디컷·검수 도구가 한 벌 기획 그대로 쓴다."""
        look = self.looks[index]
        return self.model_copy(
            update={
                "items": list(look.items),
                "looks": [],
                "reason": look.reason or self.reason,
                "styling": look.styling,
                "cover": look.cover,
                "edit_from": look.edit_from,
                "edit_request": look.edit_request,
                "pose": look.pose,
                "ref_photo": look.ref_photo,
            }
        )


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


class FeedLook(BaseModel):
    """여러 벌 게시물의 한 벌 장 (20단계). 코디컷 위에 상품 정보 몇 줄을 얹는다."""

    image: str | None = None  # 코디컷 경로 (없으면 이 벌 장을 건너뜀)
    items: list[FeedItem] = Field(min_length=1)


class FeedSpec(BaseModel):
    """게시물 한 개의 캐러셀 재료. 기획 단계가 쓰고 tools.feed 가 읽는다."""

    account: str
    title_lines: list[str] = Field(min_length=1)
    cover: str | None = None  # 제목 없는 코디컷 경로 (없으면 표지 장을 건너뜀)
    cover_has_title: bool = False  # 이미 제목을 얹은 이미지면 true
    # 표지를 벌 전부의 콜라주로 (profile looks.cover_collage, 벌 코디컷은 looks[].image)
    cover_collage: bool = False
    items: list[FeedItem] = Field(min_length=1)
    looks: list[FeedLook] = []  # 여러 벌 게시물이면 벌마다 (feed.slides 의 looks)


class PostLink(BaseModel):
    """게시물 상품 한 개의 링크 (10단계 댓글 DM 에 씀)."""

    product: str  # 상품 창고 id
    category: str
    shop: str
    name: str  # 짧은 이름
    url: str  # 상품 페이지
    image: str = ""  # 대표 사진 주소 (상품 페이지 썸네일)
    look: int | None = None  # 여러 벌 게시물이면 몇 번째 벌인지 (1부터, 20단계)


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
FeedbackResult = Literal[
    "rerender", "limit", "late", "busy", "after_post", "sandbox", "no_preview", "candidates"
]


class FeedbackEntry(BaseModel):
    """텔레그램 /fix 피드백 한 줄 (data/<계정>/feedback.jsonl, 15단계 tools.feedback).
    자주 나오는 피드백은 검수 기준·배치 규칙과 11단계 시험 세트에 더할 후보가 된다."""

    account: str
    date: date  # 게시 날짜
    run: str | None = None  # 피드백을 받은 미리보기 결과 폴더 (daily-results)
    slide: int | None = None  # 장 번호 (1부터). 캡션이나 전체면 None
    kind: Literal["cover", "product", "look", "summary", "comment", "slide", "caption", "all"] = (
        "all"
    )
    product: str | None = None  # 상품 장이면 상품 창고 id
    look: int | None = None  # 벌 장이면 몇 번째 벌인지 (1부터)
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
    count: int = Field(ge=1)  # 보낸 후보 수 (p1 ~ pN). 여러 벌 게시물이면 p1 안의 벌 수
    looks: int = 0  # 여러 벌 게시물(20단계)이면 게시물에 넣을 벌 수 (profile looks.count)
    looks_min: int = 0  # 고르기에서 빼더라도 남아야 하는 벌 수
    sent_at: int  # 보낸 시각 (유닉스 초). 이보다 뒤에 온 답장만 본다
    message_ids: list[int] = []
    # 여러 벌 후보의 벌 시트 메시지 id → 벌 번호 (시트에 답장한 /fix 가 어느 벌인지)
    sheets: dict[int, int] = {}
    # 이미 받은 후보 고치기 /fix 의 update_id. 고친 후보(mode=edit)는 지난 후보의 sent_at 을
    # 그대로 두고 메시지 id 를 이어 붙여서, 고치기 전에 보낸 번호도 그대로 읽는다
    edits: list[int] = []


class PickDecision(BaseModel):
    """후보에 대한 결정 (picks/picked.json). schedule job 이 쓰고, 한 번 정하면 다시 보지 않는다."""

    # 고른 번호 (1부터). 여러 벌 게시물(20단계)이면 쓸 벌 번호 목록
    choice: int | list[int] | Literal["again", "skip"]
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
    reel_sent_at: int | None = None  # 릴스 영상을 텔레그램으로 보낸 때 (reels.delivery telegram)


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

# 사용자가 여러 번 지적한 결함 (2026-10-12). 검수가 찾아도 1점만 깎으면 통과해서, 점수와 따로
# 있다/없다로 받아 하나라도 있으면 불합격으로 다시 그린다 (tools.review.passes)
REVIEW_FAULTS: dict[str, str] = {
    "hem_short": "긴바지·롱스커트 밑단이 발목 위에서 끝나 크롭 기장처럼 짧음",
    "fit_differs": "하의 핏이 착용컷과 다름 (와이드·스트레이트가 테이퍼드·스키니로 좁아짐 등)",
    "shoes_not_neat": (
        "신발이 정한 모양이 아님 (낮은 신발: 위에서 본 두 짝이 나란하지 않음, "
        "부츠: 옆모습 같은 방향이 아님)"
    ),
    "tuck_messy": "넣어 입은 허리가 우글우글 뭉치거나 부풀었고, 또는 밑단·자락이 밖으로 나옴",
    # 2026-10-13~14 사용자 (참고 wyeun_ 비교, /fix):
    # 배바지, 치마가 너무 내려감, 재질 다름, 숫자 그림, 납작함
    "waist_off": (
        "하의 허리 높이가 착용컷과 다름 (배바지처럼 가슴 쪽으로 올라감, 또는 너무 내려감). "
        "상의 몸통이 폭보다 짧게 눌린 것도 해당"
    ),
    "material_differs": (
        "소재·결이 원본과 다름 (니트가 면처럼, 가죽 윤기 없음, 울이 매끈한 천처럼 등)"
    ),
    "text_drawn": "상품에 없는 글자·숫자가 그림 안에 있음",
    "paper_flat": "옷이 종이처럼 납작함 (소매·바지 통·몸판에 두께와 입체감이 없음)",
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
    # /fix 로 다시 그린 후보: 사람이 요청한 수정이 그려졌는지 (모르면 None). 점수 합계와 따로 본다
    feedback_ok: bool | None = None
    # 점수와 따로 보는 결함 (REVIEW_FAULTS, 2026-10-12 사용자: 여러 번 지적해도 1점만 깎여 통과됨).
    # 하나라도 있으면 합계와 상관없이 불합격이라 다시 그린다
    faults: list[str] = Field(default_factory=list)  # REVIEW_FAULTS 키 (모르는 값도 결함으로 본다)

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
