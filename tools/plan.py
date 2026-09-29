r"""하루 게시물 기획을 돕는 도구 (6단계). Claude 스킬이 부르고, 판단은 스킬이 한다.

이 도구는 판단하지 않는다. 스킬이 판단할 재료를 보여 주고(context, candidates, photos),
스킬이 쓴 기획 파일(DailyPlan)을 프로필 규칙으로 검사하고(check), 결과물로 만든다(build).

사용법:
  python -m tools.plan context --account women [--date 2026-09-27]      오늘 정보 (날씨, 재고)
  python -m tools.plan candidates --account women --category 아우터 [--query 트렌치]  후보 상품
  python -m tools.plan themes --account women --file out/themes.yaml     주간 주제 검사·저장
  python -m tools.plan check out/2026-09-27/plan.yaml [--draft]  기획 검사 (오류면 종료 코드 1)
  python -m tools.plan photos out/2026-09-27/plan.yaml    상품 사진 확인표
  python -m tools.plan caption out/2026-09-27/plan.yaml   캡션 미리보기
  python -m tools.plan build out/2026-09-27/plan.yaml [--outfit photoroom|none] [--fake]

build 결과 (기획 파일이 있는 폴더, --out 으로 바꿀 수 있음):
  outfit/     코디컷 작업 (tools.poc 와 같은 구성)   slides/  캐러셀 슬라이드
  caption.txt 캡션 + 해시태그                         preview.jpg  슬라이드 한눈에 보기
  manifest.json  하루 결과물 (core.models.Manifest, 게시 job 이 읽음)
  review/review.json  코디컷 후보 목록 (검수는 tools.review, 7단계)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import get_args
from zoneinfo import ZoneInfo

import httpx
import yaml
from PIL import Image, ImageDraw
from pydantic import TypeAdapter, ValidationError

from core.models import DailyPlan, FeedItem, FeedSpec, Manifest, OutfitPlan, Product, Theme
from core.profile import REPO_ROOT, Profile, load_profile
from core.store import Store
from tools import feed, photoroom
from tools.compose import load_layout
from tools.overlay import load_font

WEEKDAYS = "월화수목금토일"
CONFIG_PATH = REPO_ROOT / "config" / "models.yaml"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
# WMO 날씨 코드 → 짧은 말
WEATHER_CODES = {
    0: "맑음",
    1: "대체로 맑음",
    2: "구름 조금",
    3: "흐림",
    45: "안개",
    48: "안개",
    51: "약한 이슬비",
    53: "이슬비",
    55: "이슬비",
    61: "약한 비",
    63: "비",
    65: "강한 비",
    71: "약한 눈",
    73: "눈",
    75: "강한 눈",
    80: "소나기",
    81: "소나기",
    82: "강한 소나기",
    95: "뇌우",
}
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿️]")


# ---------- 공통 ----------


def default_date(profile: Profile, now: datetime | None = None) -> date:
    """게시 날짜 기본값: 프로필 시간대 기준 오늘 게시 시각 전이면 오늘, 지났으면 내일."""
    now = (now or datetime.now(ZoneInfo(profile.schedule.timezone))).astimezone(
        ZoneInfo(profile.schedule.timezone)
    )
    h, m = map(int, profile.schedule.publish_at.split(":"))
    return now.date() + timedelta(days=0 if (now.hour, now.minute) < (h, m) else 1)


def season(d: date) -> str:
    """월로 본 계절 (초가을, 가을, 늦가을 …)."""
    names = ["겨울", "봄", "여름", "가을"]
    name = names[(d.month % 12) // 3]
    return ["초", "", "늦"][d.month % 3] + name


def load_plan(path: Path) -> DailyPlan:
    return DailyPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def rel(path: Path) -> str:
    """저장소 안이면 저장소 기준 경로, 아니면 절대 경로."""
    path = path.resolve()
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def fetch_weather(profile: Profile, day: date) -> str | None:
    place = profile.themes.weather
    if place is None:
        return None
    params = {
        "latitude": place.lat,
        "longitude": place.lon,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": profile.schedule.timezone,
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
    }
    try:
        r = httpx.get(WEATHER_URL, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()["daily"]
        code = d["weather_code"][0]
        text = WEATHER_CODES.get(code, f"코드 {code}")
        rain = d["precipitation_probability_max"][0]
        lo, hi = d["temperature_2m_min"][0], d["temperature_2m_max"][0]
        return f"{place.name} {text}, {lo:.0f}~{hi:.0f}°C, 강수확률 {rain}%"
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        print(f"날씨를 가져오지 못함: {e}", file=sys.stderr)
        return None


def eligible(
    profile: Profile, store: Store, day: date, products: list[Product] | None = None
) -> tuple[list[Product], dict[str, date]]:
    """고를 수 있는 상품: 판매 중, 평점·리뷰 기준 이상, 최근에 쓰지 않은 것 (좋은 순)."""
    src = profile.product_source
    used = store.used_since(day, profile.outfit.reuse_cooldown_days)
    ok = [
        p
        for p in (products if products is not None else store.products())
        if p.status == "on_sale"
        and (p.rating is None or p.rating >= src.min_rating)
        and (p.review_count or 0) >= src.min_reviews
        and p.id not in used
    ]
    from tools.collect import quality

    ok.sort(key=quality, reverse=True)
    return ok, used


# ---------- context ----------


def context(profile: Profile, store: Store, day: date, with_weather: bool = True) -> str:
    rules = profile.themes
    lines = [
        f"# {profile.display_name} ({profile.id}) 기획 정보",
        "",
        f"- 게시 날짜: {day.isoformat()} ({WEEKDAYS[day.weekday()]}요일), 계절: {season(day)}",
    ]
    if with_weather:
        lines.append(f"- 날씨 예보: {fetch_weather(profile, day) or '없음'}")
    lines += [
        f"- 스타일: {', '.join(profile.style.keywords)} / 색: {', '.join(profile.style.palette)}"
        f" / 피할 것: {', '.join(profile.style.avoid) or '없음'}",
        f"- 챙길 이벤트 후보: {', '.join(rules.events) or '없음'} (날짜와 가까운 것만 반영)",
        f"- 다루지 않을 주제: {', '.join(rules.avoid) or '없음'}",
    ]
    planned = [t for t in store.themes() if t.date == day]
    lines += ["", "## 정해 둔 주제"]
    if planned:
        t = planned[0]
        lines.append(f"- [{t.type or '-'}] {t.title_hint} / 분위기: {t.mood}")
        lines += [f"  - {s.category}: {s.desc}" for s in t.slots]
        for cat, qs in t.search_terms.items():
            lines.append(f"  - 검색어 {cat}: {', '.join(qs)}")
    else:
        lines.append("- 없음 (이 날 주제를 새로 정한다)")

    recent = store.recent_posts(day, rules.no_repeat_days)
    lines += ["", f"## 최근 {rules.no_repeat_days}일 게시 (비슷한 주제 피하기)"]
    lines += [f"- {p.date}: {p.title}" + (f" ({p.theme})" if p.theme else "") for p in recent]
    if not recent:
        lines.append("- 없음")
    week = [t for t in store.themes() if day - timedelta(days=7) <= t.date < day]
    if week:
        lines += ["", "## 지난 7일 주제"]
        lines += [f"- {t.date}: [{t.type or '-'}] {t.title_hint}" for t in week]

    all_products = store.products()
    ok, used = eligible(profile, store, day, all_products)
    lines += [
        "",
        "## 상품 창고",
        "",
        "| 카테고리 | 판매 중 | 고를 수 있음 | 단독컷 있음 |",
        "|---|---|---|---|",
    ]
    for rule in profile.outfit.slots:
        cat = rule.category
        on = [p for p in all_products if p.category == cat and p.status == "on_sale"]
        can = [p for p in ok if p.category == cat]
        flat = [p for p in can if p.has_flat_shot]
        lines.append(
            f"| {cat}{'' if rule.required else ' (선택)'} | {len(on)} | {len(can)} | {len(flat)} |"
        )
    lines.append(
        f"\n최근 {profile.outfit.reuse_cooldown_days}일 안에 쓴 상품 {len(used)}개는 뺐다."
    )
    return "\n".join(lines)


# ---------- candidates ----------


def candidate_line(p: Product) -> str:
    stars = f"★{p.rating}" if p.rating is not None else "★-"
    tags = ",".join(p.style_tags) or "-"
    flat = "단독컷" if p.has_flat_shot else "모델컷"
    return (
        f"{p.id} | {p.category} | {feed.clean_name(p.name)} | {p.shop} | {p.price:,}원 | "
        f"{stars} ({p.review_count or 0}) | {flat} | {tags} | 검색어 {p.query or '-'} | "
        f"원문: {feed.drawable(p.name)[:70]}"
    )


def candidates(
    profile: Profile,
    store: Store,
    day: date,
    category: str | None = None,
    query: str | None = None,
    limit: int = 30,
) -> list[Product]:
    ok, _ = eligible(profile, store, day)
    if category:
        ok = [p for p in ok if p.category == category]
    if query:
        ok = [
            p for p in ok if p.query == query or query.replace(" ", "") in p.name.replace(" ", "")
        ]
    return ok[:limit]


# ---------- themes ----------


def save_themes(profile: Profile, store: Store, new: list[Theme]) -> list[str]:
    """주간 주제를 검사해 themes.json 에 합친다 (같은 날짜는 바꿈). 경고 목록을 돌려준다."""
    warnings = []
    dates = [t.date for t in new]
    if len(set(dates)) != len(dates):
        raise ValueError("같은 날짜 주제가 두 개 있습니다")
    if len(new) == profile.themes.per_week:
        counts: dict[str, int] = {}
        for t in new:
            counts[t.type or "-"] = counts.get(t.type or "-", 0) + 1
        if counts != dict(profile.themes.type_mix) and counts != {
            k: v for k, v in profile.themes.type_mix.items() if v
        }:
            warnings.append(f"유형 비율이 type_mix 와 다릅니다: {counts}")
    for t in new:
        if not t.type:
            warnings.append(f"{t.date}: 주제 유형(type)이 없습니다")
        banned = [w for w in profile.themes.avoid if w in t.title_hint]
        if banned:
            raise ValueError(f"{t.date}: 다루지 않을 주제가 들어 있습니다: {banned}")
    kept = [t for t in store.themes() if t.date not in set(dates)]
    store.save_themes(sorted(kept + new, key=lambda t: t.date))
    return warnings


# ---------- check ----------


def text_problems(text: str, profile: Profile, where: str) -> list[str]:
    return [f"{where}: 금지 표현 '{w}'" for w in profile.copy_rules.banned if w in text]


def check(
    plan: DailyPlan, profile: Profile, store: Store, draft: bool = False
) -> tuple[list[str], list[str]]:
    """기획 파일을 프로필 규칙으로 검사한다. (오류, 경고). 오류가 있으면 빌드하지 않는다.
    draft 는 문구(text)를 쓰기 전 코디 후보 (9단계 tools.pick): text 가 없어도 오류가 아니다."""
    errors: list[str] = []
    warns: list[str] = []
    rules, c = profile.outfit, profile.copy_rules
    if plan.account != profile.id:
        errors.append(f"account 가 {profile.id} 가 아닙니다: {plan.account}")
    if plan.theme.date != plan.date:
        warns.append(f"theme.date({plan.theme.date})와 date({plan.date})가 다릅니다")

    products = {p.id: p for p in store.products()}
    used = store.used_since(plan.date, rules.reuse_cooldown_days)
    ids = [it.product for it in plan.items]
    if len(set(ids)) != len(ids):
        errors.append("같은 상품이 두 번 들어 있습니다")
    if not rules.items_min <= len(plan.items) <= rules.items_max:
        errors.append(
            f"상품은 {rules.items_min}~{rules.items_max}개여야 합니다 (지금 {len(plan.items)})"
        )

    slides = sum(len(plan.items) if k == "products" else 1 for k in profile.feed.slides)
    if slides > feed.MAX_SLIDES:
        errors.append(f"슬라이드가 {slides}장이 됩니다 (최대 {feed.MAX_SLIDES}). 상품을 줄이세요")

    layout_slots = set(load_layout()["slots"])
    modes = set(get_args(photoroom.StyleMode))
    cats: list[str] = []
    shops: dict[str, int] = {}
    for it in plan.items:
        p = products.get(it.product)
        if p is None:
            errors.append(f"{it.product}: 상품 창고에 없습니다")
            continue
        cats.append(p.category)
        shops[p.shop] = shops.get(p.shop, 0) + 1
        label = f"{it.product} ({it.name})"
        if p.status != "on_sale":
            errors.append(f"{label}: 판매 중이 아닙니다 ({p.status})")
        if it.product in used:
            errors.append(f"{label}: {used[it.product]}에 쓴 상품입니다 (재사용 금지 기간)")
        if it.photo is not None and it.photo >= len(p.image_urls):
            errors.append(f"{label}: photo {it.photo} 번 사진이 없습니다 ({len(p.image_urls)}장)")
        if it.cutout_photo >= len(p.image_urls):
            errors.append(f"{label}: cutout_photo {it.cutout_photo} 번 사진이 없습니다")
        if it.slot and it.slot not in layout_slots:
            errors.append(f"{label}: slot '{it.slot}' 은 config/layout.yaml 에 없습니다")
        if it.wear_modes and set(it.wear_modes) - modes:
            errors.append(f"{label}: wear_modes 는 {sorted(modes)} 중에서 고릅니다")
        if p.category == "소품" and not it.slot and not it.wear_with:
            warns.append(
                f"{label}: 소품은 slot(목걸이·귀걸이·팔찌)이나 wear_with 를 정하면 배치가 좋아요"
            )
        if not re.search(r"\([^)]*[A-Za-z][^)]*\)", it.desc):
            warns.append(f"{label}: desc 끝에 영어 설명을 괄호로 붙이세요 (Photoroom 지시문)")
        if len(it.name) > c.name_max_chars:
            warns.append(f"{label}: 이름이 {len(it.name)}자입니다 (권장 {c.name_max_chars}자 이하)")
        if len(it.comment) > c.comment_max_chars:
            warns.append(
                f"{label}: 코멘트가 {len(it.comment)}자입니다 (권장 {c.comment_max_chars}자 이하)"
            )
        if _EMOJI.search(it.name + it.comment):
            errors.append(f"{label}: 상품 장 문구에는 이모지를 쓰지 않습니다")
        errors += text_problems(it.name + " " + it.comment, profile, label)
    for it in plan.items:
        if it.wear_with and it.wear_with not in cats:
            errors.append(f"{it.product}: wear_with {it.wear_with} 상품이 코디에 없습니다")
    for rule in rules.slots:
        if rule.required and rule.category not in cats:
            errors.append(f"필수 카테고리 {rule.category} 상품이 없습니다")
    for shop, n in shops.items():
        if n > rules.max_per_shop:
            errors.append(f"쇼핑몰 {shop} 상품이 {n}개입니다 (최대 {rules.max_per_shop})")

    recent = store.recent_posts(plan.date, profile.themes.no_repeat_days)
    if any(p.theme and p.theme == plan.theme.title_hint for p in recent):
        errors.append(f"최근 {profile.themes.no_repeat_days}일 안에 같은 주제가 있습니다")

    if plan.text is None:
        if not draft:
            errors.append("text(제목·코디 포인트·해시태그)가 아직 없습니다 (write-copy 스킬)")
    else:
        t = plan.text
        if len(t.title_lines) > profile.visual.title_max_lines:
            errors.append(f"제목은 {profile.visual.title_max_lines}줄까지입니다")
        lo, hi = c.title_chars
        for line in t.title_lines:
            if _EMOJI.search(line):
                errors.append(f"제목에는 이모지를 쓰지 않습니다: {line}")
            if not lo <= len(line) <= hi:
                warns.append(f"제목 줄 '{line}' 은 {len(line)}자입니다 (권장 {lo}~{hi}자)")
            errors += text_problems(line, profile, "제목")
        lo, hi = c.points
        if not lo <= len(t.points) <= hi:
            errors.append(f"코디 포인트는 {lo}~{hi}줄이어야 합니다 (지금 {len(t.points)})")
        body = "\n".join(t.points)
        errors += text_problems(body, profile, "코디 포인트")
        if len(_EMOJI.findall(body.replace("️", ""))) > c.max_emoji:
            errors.append(f"캡션 이모지는 {c.max_emoji}개까지입니다")
        if body.count("!") > c.max_exclaim:
            errors.append(f"캡션 느낌표는 {c.max_exclaim}개까지입니다")
        for tag in t.hashtags:
            if not tag.startswith("#") or " " in tag:
                errors.append(f"해시태그 형식이 아닙니다: '{tag}'")
        if profile.monetize == "none" and c.ad_hashtag in t.hashtags:
            errors.append(f"monetize: none 인데 {c.ad_hashtag} 가 있습니다")
        n = len(final_hashtags(plan, profile))
        lo, hi = c.hashtags
        if not lo <= n <= hi:
            errors.append(f"해시태그가 기본 묶음 포함 {n}개입니다 ({lo}~{hi}개)")
    if plan.cover and not (REPO_ROOT / plan.cover).exists():
        errors.append(f"cover 파일이 없습니다: {plan.cover}")
    return errors, warns


# ---------- caption ----------


def final_hashtags(plan: DailyPlan, profile: Profile) -> list[str]:
    c = profile.copy_rules
    tags: list[str] = []
    for t in [*(plan.text.hashtags if plan.text else []), *c.base_hashtags]:
        if t not in tags and t != c.ad_hashtag:
            tags.append(t)
    if profile.monetize == "manual_link":
        tags.insert(0, c.ad_hashtag)
    return tags


def caption(plan: DailyPlan, profile: Profile, store: Store) -> str:
    """캡션 본문 (해시태그 제외). 고지 문구와 제품 목록은 프로필 copy 로 붙인다."""
    if plan.text is None:
        raise ValueError("text 가 없어 캡션을 만들 수 없습니다")
    c = profile.copy_rules
    products = {p.id: p for p in store.products()}
    blocks: list[list[str]] = []
    if profile.monetize == "manual_link":
        blocks.append([c.ad_notice])
    blocks.append(list(plan.text.points))
    blocks.append(
        [
            c.product_line.format(shop=products[it.product].shop, name=it.name)
            for it in plan.items
            if it.product in products
        ]
    )
    cm = profile.comments
    # 댓글 DM 을 켜면 "프로필 링크" 대신 댓글 안내 문구 (10단계)
    keyword = cm.keywords[0] if cm.keywords else ""
    blocks.append([cm.cta.format(keyword=keyword) if cm.enabled else c.link_line])
    blocks.append([c.ai_notice])
    return "\n\n".join("\n".join(b) for b in blocks if any(b))


def full_caption(plan: DailyPlan, profile: Profile, store: Store) -> str:
    return caption(plan, profile, store) + "\n\n" + " ".join(final_hashtags(plan, profile))


# ---------- photos ----------


def fetch_candidates(plan: DailyPlan, profile: Profile, store: Store, dest: Path) -> dict:
    """상품마다 창고 사진을 앞에서부터 받아 얼굴 수를 센다. {상품 id: [{index, file, faces}]}"""
    from tools import faces, scout

    products = {p.id: p for p in store.products()}
    result: dict[str, list[dict]] = {}
    for it in plan.items:
        p = products[it.product]
        pid = p.id.split(":")[-1]
        rows = []
        for i, url in enumerate(p.image_urls[: profile.feed.photo_candidates]):
            try:
                path = feed.fetch_photo(str(url), dest / f"{pid}_{i}.jpg")
                n = len(faces.find_faces(Image.open(path).convert("RGB")))
            except (scout.ImageUnavailable, OSError) as e:
                print(f"사진을 받지 못해 건너뜀: {url} ({e})", file=sys.stderr)
                continue
            rows.append({"index": i, "file": rel(path), "faces": n})
        result[it.product] = rows
    return result


def photo_sheet(plan: DailyPlan, photos: dict, out: Path, thumb: int = 230) -> Path:
    """사진 확인표: 상품마다 한 줄. 왼쪽에 상품 이름·고른 색, 오른쪽에 사진과 번호
    (얼굴이 있으면 빨간 표시)."""
    label_w, pad = 300, 16
    cols = max((len(v) for v in photos.values()), default=1) or 1
    row_h = int(thumb * 1.25) + 34
    w = label_w + cols * (thumb + pad) + pad
    h = pad + len(plan.items) * (row_h + pad)
    sheet = Image.new("RGB", (w, h), (248, 247, 244))
    draw = ImageDraw.Draw(sheet)
    bold, body = load_font("Pretendard-Bold.otf", 24), load_font("Pretendard-Medium.otf", 20)
    for r, it in enumerate(plan.items):
        y = pad + r * (row_h + pad)
        draw.text((pad, y + 6), f"{r + 1:02d}", font=bold, fill=(122, 46, 52))
        for k, line in enumerate(feed.wrap(feed.drawable(it.name), bold, label_w - 70, 2)):
            draw.text((pad + 44, y + 6 + k * 30), line, font=bold, fill=(34, 34, 34))
        draw.text((pad + 44, y + 72), f"색: {it.color or '-'}", font=body, fill=(90, 90, 90))
        chosen = [f"상품 장 {it.photo}" if it.photo is not None else "상품 장 자동"]
        chosen.append(f"누끼 {it.cutout_photo}")
        draw.text((pad + 44, y + 100), " / ".join(chosen), font=body, fill=(90, 90, 90))
        for k, ph in enumerate(photos.get(it.product, [])):
            x = label_w + pad + k * (thumb + pad)
            img = Image.open(REPO_ROOT / ph["file"]).convert("RGB")
            img.thumbnail((thumb, int(thumb * 1.25)))
            sheet.paste(img, (x, y))
            tag = f"#{ph['index']}" + (f"  얼굴 {ph['faces']}" if ph["faces"] else "")
            color = (190, 30, 30) if ph["faces"] else (34, 34, 34)
            draw.text((x, y + int(thumb * 1.25) + 4), tag, font=body, fill=color)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=90)
    return out


# ---------- build ----------


def outfit_config() -> dict:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return data.get("outfit") or {"finish": "ai_shadow", "clean": True}


def outfit_samples(plan: DailyPlan, store: Store, work: Path) -> dict:
    """기획을 tools.poc 상품 목록(SampleSet) 으로 바꾼다. 누끼 사진은 창고 사진에서 받아 둔다."""
    products = {p.id: p for p in store.products()}
    items = []
    for it in plan.items:
        p = products[it.product]
        url = str(p.image_urls[it.cutout_photo])
        src = feed.fetch_photo(url, work / "src" / f"{p.id.split(':')[-1]}_{it.cutout_photo}.jpg")
        item = {"category": p.category, "desc": it.desc, "image": str(src.resolve())}
        for key in ("slot", "segment", "cm", "wear_with", "wear_how", "wear_modes"):
            if getattr(it, key) is not None:
                item[key] = getattr(it, key)
        items.append(item)
    return {"account": plan.account, "name": plan.theme.title_hint, "items": items}


def make_outfit(
    plan: DailyPlan, store: Store, work: Path, fake: bool = False, count: int = 1
) -> list[Path]:
    """Photoroom 으로 코디컷 후보를 count 장 만든다 (2단계와 같은 흐름, 회차마다 AI 결과가 다름).
    성공한 것만 돌려준다. 옷 누끼·입히기·그림자는 후보마다 새로 하고, 잘라내기 누끼는 재사용한다."""
    from tools import poc

    samples = work / "samples.yaml"
    work.mkdir(parents=True, exist_ok=True)
    data = outfit_samples(plan, store, work)
    samples.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    cfg = outfit_config()
    variant = poc.Variant(id="C", name="하루 코디컷", finish=cfg["finish"], clean=cfg["clean"])
    log = poc.run(samples, work, poc.Experiment(repeats=count, variants=[variant]), fake=fake)
    return [work / r["file"] for r in log["runs"] if r["ok"]]


def preview(paths: list[Path], out: Path, per_row: int = 4, w: int = 360) -> Path:
    h = int(w * 1.25)
    rows = (len(paths) + per_row - 1) // per_row
    sheet = Image.new(
        "RGB", (per_row * w + (per_row + 1) * 12, rows * h + (rows + 1) * 12), "white"
    )
    for i, p in enumerate(paths):
        img = Image.open(p).convert("RGB").resize((w, h))
        sheet.paste(img, (12 + (i % per_row) * (w + 12), 12 + (i // per_row) * (h + 12)))
    sheet.save(out, quality=88)
    return out


def build(
    plan: DailyPlan,
    out: Path,
    profile: Profile,
    store: Store,
    outfit: str = "photoroom",
    fake: bool = False,
) -> Manifest:
    errors, warns = check(plan, profile, store)
    if errors:
        raise ValueError("기획 검사 오류:\n" + "\n".join(f"- {e}" for e in errors))
    for w in warns:
        print(f"경고: {w}", file=sys.stderr)
    out.mkdir(parents=True, exist_ok=True)
    products = {p.id: p for p in store.products()}
    photo_dir = out / "photos"
    notes: list[str] = []

    from tools import review as review_tool

    cover: Path | None = REPO_ROOT / plan.cover if plan.cover else None
    made: list[Path] = [cover] if cover else []
    if cover is None and outfit == "photoroom":
        try:
            made = make_outfit(plan, store, out / "outfit", fake, profile.review.candidates)
        except photoroom.QuotaError:
            raise
        except Exception as e:  # noqa: BLE001 - 코디컷 없이 캐러셀은 만든다
            print(f"코디컷 실패: {e}", file=sys.stderr)
        # 검수 전에는 첫 후보를 표지로 두고, 검수(tools.review apply)가 가장 좋은 후보로 바꾼다
        cover = made[0] if made else None
        if cover is None:
            notes.append("코디컷을 만들지 못해 표지 없이 만들었습니다")
    review = review_tool.start(out, made)

    items = []
    for it in plan.items:
        photo = None
        if it.photo is not None:
            p = products[it.product]
            url = str(p.image_urls[it.photo])
            photo = feed.fetch_photo(url, photo_dir / f"{p.id.split(':')[-1]}_{it.photo}.jpg")
        items.append(
            FeedItem(
                product=it.product,
                name=it.name,
                comment=it.comment,
                photo=str(photo.resolve()) if photo else None,
            )
        )
    assert plan.text is not None  # check 가 확인함
    spec = FeedSpec(
        account=plan.account,
        title_lines=plan.text.title_lines,
        cover=str(cover.resolve()) if cover else None,
        items=items,
    )
    (out / "feed.yaml").write_text(
        yaml.safe_dump(spec.model_dump(mode="json", exclude_none=True), allow_unicode=True),
        encoding="utf-8",
    )
    slides = feed.render(
        spec, out / "slides", root=REPO_ROOT, profile=profile, store=store, photo_dir=photo_dir
    )
    preview(slides, out / "preview.jpg")

    body = caption(plan, profile, store)
    tags = final_hashtags(plan, profile)
    (out / "caption.txt").write_text(body + "\n\n" + " ".join(tags) + "\n", encoding="utf-8")
    manifest = Manifest(
        account=plan.account,
        date=plan.date,
        plan=OutfitPlan(
            theme=plan.theme, items=[products[it.product] for it in plan.items], reason=plan.reason
        ),
        images=[rel(p) for p in slides],
        review_score=0,  # 검수(tools.review apply)에서 채운다
        needs_human=True,
        review=review,
        title_lines=plan.text.title_lines,
        caption=body,
        hashtags=tags,
        monetize=profile.monetize,
    )
    (out / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    report = [f"# {plan.date} {' / '.join(plan.text.title_lines)}", ""]
    report += [
        f"- 주제: [{plan.theme.type or '-'}] {plan.theme.title_hint}",
        f"- 조합: {plan.reason}",
    ]
    report += [
        f"- 슬라이드 {len(slides)}장, 코디컷 {f'후보 {len(made)}장' if made else '없음'}"
        " (검수 전: 첫 후보가 표지)"
    ]
    report += [f"- 경고: {w}" for w in warns] + [f"- 참고: {n}" for n in notes]
    (out / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return manifest


# ---------- CLI ----------


def _day(profile: Profile, value: str | None) -> date:
    return date.fromisoformat(value) if value else default_date(profile)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    account = {"default": os.environ.get("ACCOUNT"), "required": "ACCOUNT" not in os.environ}
    s = sub.add_parser("context", help="오늘 정보")
    s.add_argument("--account", **account)
    s.add_argument("--date")
    s.add_argument("--no-weather", action="store_true")
    s = sub.add_parser("candidates", help="고를 수 있는 상품")
    s.add_argument("--account", **account)
    s.add_argument("--date")
    s.add_argument("--category")
    s.add_argument("--query")
    s.add_argument("--limit", type=int, default=30)
    s = sub.add_parser("themes", help="주간 주제 검사·저장")
    s.add_argument("--account", **account)
    s.add_argument("--file", type=Path, required=True, help="Theme 목록 YAML")
    for name in ("check", "photos", "caption", "build"):
        s = sub.add_parser(name)
        s.add_argument("plan", type=Path)
        if name in ("photos", "build"):
            s.add_argument("--out", type=Path, help="결과 폴더 (기본: 기획 파일 폴더)")
        if name == "check":
            s.add_argument("--draft", action="store_true", help="문구 없는 코디 후보 검사")
        if name == "build":
            s.add_argument("--outfit", choices=["photoroom", "none"], default="photoroom")
            s.add_argument("--fake", action="store_true", help="Photoroom 대신 가짜 이미지")
    args = ap.parse_args(argv)

    try:
        if args.cmd in ("context", "candidates", "themes"):
            profile, store = load_profile(args.account), Store(args.account)
            if args.cmd == "context":
                print(context(profile, store, _day(profile, args.date), not args.no_weather))
            elif args.cmd == "candidates":
                found = candidates(
                    profile, store, _day(profile, args.date), args.category, args.query, args.limit
                )
                print("\n".join(candidate_line(p) for p in found) or "후보 없음")
            else:
                raw = yaml.safe_load(args.file.read_text(encoding="utf-8")) or []
                themes = TypeAdapter(list[Theme]).validate_python(raw)
                for w in save_themes(profile, store, themes):
                    print(f"경고: {w}")
                print(f"저장: {len(themes)}개 → {rel(store.dir / 'themes.json')}")
            return 0

        plan = load_plan(args.plan)
        profile, store = load_profile(plan.account), Store(plan.account)
        if args.cmd == "check":
            errors, warns = check(plan, profile, store, args.draft)
            for e in errors:
                print(f"오류: {e}")
            for w in warns:
                print(f"경고: {w}")
            print("통과" if not errors else f"오류 {len(errors)}개")
            return 1 if errors else 0
        if args.cmd == "caption":
            print(full_caption(plan, profile, store))
            return 0
        out = args.out or args.plan.parent
        if args.cmd == "photos":
            photos = fetch_candidates(plan, profile, store, out / "photos")
            (out / "photos.json").write_text(json.dumps(photos, ensure_ascii=False, indent=2))
            print(photo_sheet(plan, photos, out / "photo_check.jpg"))
            return 0
        manifest = build(plan, out, profile, store, args.outfit, args.fake)
        print(f"완료: 슬라이드 {len(manifest.images)}장 → {rel(out / 'manifest.json')}")
        return 0
    except (ValueError, FileNotFoundError, ValidationError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    except photoroom.QuotaError as e:
        print(f"Photoroom 크레딧·요금제 한도에 막혔습니다.\n{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
