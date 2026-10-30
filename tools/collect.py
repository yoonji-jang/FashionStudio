"""상품 수집. 프로필의 검색어로 후보를 찾아 계정별 상품 창고(data/<계정>/products.json)에 쌓는다.

흐름: 검색(평점·리뷰 기준) → 상품 페이지 확인(판매 중인지, 사진 목록) → 사진 고르기(상품만 나온
사진을 앞으로) → 창고에 합치기. 창고에 있던 상품도 recheck_days 가 지나면 다시 확인해
판매 종료·품절이면 뺀다. 카테고리마다 max_per_category 개까지만 좋은 순서로 남긴다.

사용법:
  python -m tools.collect --account women                      # 프로필의 모든 검색어
  python -m tools.collect --account women --category 아우터 --query 레더자켓 --query 트렌치코트
  python -m tools.collect --account women --max-queries 3 --report out/collect
결과 보고(--report 폴더): report.md (요약·표), sheet.jpg (새로 모은 상품 미리보기), products.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from io import BytesIO
from itertools import zip_longest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from core.models import Category, Product
from core.profile import REPO_ROOT, ProductSourceConfig, Profile, load_profile
from core.store import Store
from sources import ProductSource, SearchFilters, get_source
from sources.base import name_excluded, price_ok
from tools import photo_pick, scout

MAX_SCORED_IMAGES = 8  # 상품마다 점수를 매길 사진 수 (요청 수를 줄이려고)
FONT_DIR = REPO_ROOT / "assets" / "fonts"


@dataclass
class Report:
    account: str
    today: date
    searched: dict[str, int] = field(default_factory=dict)  # 검색어 → 기준 통과 후보 수
    added: list[Product] = field(default_factory=list)
    refreshed: int = 0
    dropped: list[tuple[Product, str]] = field(default_factory=list)  # (상품, 이유)
    trimmed: int = 0
    unfit: int = 0  # 가격대·제외어 기준에 맞지 않아 창고에서 뺀 수
    floors: dict[str, int] = field(
        default_factory=dict
    )  # 카테고리 → 너무 싼 경계 가격 (cheap_percent)
    stopped: str | None = None  # 막혀서 중간에 멈춘 이유
    total: dict[str, int] = field(default_factory=dict)  # 카테고리 → 창고 상품 수
    thumbs: dict[str, bytes] = field(default_factory=dict)  # 상품 id → 대표 사진 (미리보기용)
    # 사진 점수 기록 (상품 id, 순번, 점수, 작은 사진). 보고서 photos/ 에 저장해 규칙을 다듬는다
    photo_log: list[tuple[str, int, photo_pick.PhotoScore, bytes]] = field(default_factory=list)


def quality(p: Product, profile: Profile | None = None) -> float:
    """창고에 남길 우선순위. 평점 × log(리뷰 수), 단독컷이 있으면 가산.

    프로필이 있으면 리뷰 수는 review_cap 까지만 세고 (많이 팔린 기본템이 늘 앞서지 않게),
    이름에 style.point_words 가 있는 상품을 앞으로 (21단계), style.basic_words 가 있으면 뒤로.
    """
    reviews = p.review_count or 0
    if profile and profile.product_source.review_cap:
        reviews = min(reviews, profile.product_source.review_cap)
    base = (p.rating or 0) * math.log1p(reviews)
    if profile and not profile.product_source.rank_by_reviews:
        base = 1.0
    base *= 1.15 if p.has_flat_shot else 1.0
    if profile and is_point(p, profile):
        base *= 1.3
    if profile and name_excluded(p.name, profile.style.basic_words):
        cats = profile.style.basic_penalty_categories
        if not cats or p.category in cats:
            base *= 0.6
    return base


def is_point(p: Product, profile: Profile) -> bool:
    """이름에 코디 포인트가 되는 말(style.point_words)이 있는 상품."""
    return bool(profile.style.point_words) and name_excluded(p.name, profile.style.point_words)


def fits(p: Product, cfg: ProductSourceConfig, floors: dict[Category, int] | None = None) -> bool:
    """프로필의 가격대·제외어 기준에 맞는지 (창고에 이미 있던 상품도 같은 기준으로 거른다).
    floors 는 이번 검색에서 정한 카테고리별 "너무 싼" 경계 가격 (cheap_percent)."""
    low, high = cfg.price_range.get(p.category, (0, 0))
    low = max(low, (floors or {}).get(p.category, 0))
    return price_ok(p.price, low, high) and not name_excluded(p.name, cfg.exclude_words)


def search_filters(
    cfg: ProductSourceConfig, category: Category, per_query: int | None
) -> SearchFilters:
    low, high = cfg.price_range.get(category, (0, 0))
    return SearchFilters(
        min_rating=cfg.min_rating,
        min_reviews=cfg.min_reviews,
        max_results=per_query or cfg.per_query,
        min_price=low,
        max_price=high,
        exclude_words=cfg.exclude_words,
        cheap_percent=cfg.cheap_percent,
    )


def needs_check(p: Product, today: date, recheck_days: int) -> bool:
    return p.checked_at is None or today - p.checked_at >= timedelta(days=recheck_days)


def pick_photos(
    source: ProductSource, p: Product, fetch, limit: int = MAX_SCORED_IMAGES
) -> tuple[Product, dict[str, tuple[photo_pick.PhotoScore, bytes]]]:
    """사진마다 점수를 매겨 상품만 나온 사진을 앞으로 보낸다.

    반환: (새 상품, 사진 주소 → (점수, 작은 사진)).
    """
    urls = [str(u) for u in p.image_urls]
    scored: dict[str, tuple[photo_pick.PhotoScore, bytes]] = {}
    for url in urls[:limit]:
        try:
            data = fetch(source.thumb_url(url))
            scored[url] = (photo_pick.score_bytes(data), data)
        except scout.Blocked:
            raise
        except Exception as e:  # noqa: BLE001 - 사진 한 장 실패는 건너뜀
            print(f"  사진 점수 실패 {url}: {e}", file=sys.stderr)
    if not scored:
        return p, {}
    ranked = photo_pick.rank({u: s for u, (s, _) in scored.items()})
    ordered = ranked + [u for u in urls if u not in scored]
    flat = any(s.flat for s, _ in scored.values())
    updated = Product.model_validate(
        {**p.model_dump(), "image_urls": ordered, "has_flat_shot": flat}
    )
    return updated, scored


class _TimeUp(Exception):
    pass


def collect(
    account: str,
    plan: dict[Category, list[str]] | None = None,
    per_query: int | None = None,
    recheck: bool = True,
    source: ProductSource | None = None,
    store: Store | None = None,
    fetch=None,
    today: date | None = None,
    minutes: float | None = None,
) -> Report:
    """minutes 가 있으면 그 시간이 지나면 새 상품 확인을 멈추고 그때까지 모은 것을 남긴다
    (참고 코디가 많은 주에 워크플로 시간 제한으로 통째로 잃지 않게, 2026-10-19)."""
    profile = load_profile(account)
    stop_at = time.monotonic() + minutes * 60 if minutes else None
    cfg = profile.product_source
    today = today or date.today()
    source = source or get_source(cfg.name)
    store = store or Store(account)
    if fetch is None:
        http = scout.client()
        fetch = lambda url: scout.fetch_bytes(http, url)  # noqa: E731
    plan = plan if plan is not None else cfg.queries

    report = Report(account=account, today=today)
    stock: dict[str, Product] = {p.id: p for p in store.products()}
    seen: set[str] = set()

    batches = list(plan.items())
    if stop_at is not None:
        # 시간 안에 멈출 수 있으니 카테고리를 번갈아 검색어 4개씩 (한 카테고리만 모으고 끝나지 않게)
        chunks = [[(c, qs[i : i + 4]) for i in range(0, len(qs), 4)] for c, qs in plan.items()]
        batches = [b for row in zip_longest(*chunks) for b in row if b]
    try:
        for category, queries in batches:
            if not queries:
                continue
            found = source.search(queries, category, search_filters(cfg, category, per_query))
            for query, products in found.items():
                report.searched[query] = len(products)
                for cand in products:
                    if cand.id in seen:
                        continue
                    seen.add(cand.id)
                    old = stock.get(cand.id)
                    if old and not needs_check(old, today, cfg.recheck_days):
                        # 최근에 확인한 상품: 검색 카드의 가격·평점만 갱신
                        stock[cand.id] = Product.model_validate(
                            {
                                **old.model_dump(),
                                "price": cand.price,
                                "rating": cand.rating,
                                "review_count": cand.review_count,
                            }
                        )
                        continue
                    base = old or cand
                    base = Product.model_validate(
                        {
                            **base.model_dump(),
                            "price": cand.price,
                            "rating": cand.rating,
                            "review_count": cand.review_count,
                        }
                    )
                    if stop_at is not None and time.monotonic() > stop_at:
                        raise _TimeUp(f"{minutes:g}분이 지나 멈춤 (남은 검색어는 다음 수집 때)")
                    checked = source.detail(base)
                    if checked.status != "on_sale":
                        if old:
                            report.dropped.append((old, checked.status))
                            stock.pop(old.id, None)
                        continue
                    if old:
                        stock[old.id] = checked
                        report.refreshed += 1
                        continue
                    picked, scored = pick_photos(source, checked, fetch)
                    stock[picked.id] = picked
                    report.added.append(picked)
                    if scored:
                        report.thumbs[picked.id] = scored[str(picked.image_urls[0])][1]
                    for i, (score, data) in enumerate(scored.values()):
                        report.photo_log.append((picked.id, i, score, data))

        if recheck:
            for p in list(stock.values()):
                if p.id in seen or not needs_check(p, today, cfg.recheck_days):
                    continue
                checked = source.detail(p)
                if checked.status != "on_sale":
                    report.dropped.append((p, checked.status))
                    stock.pop(p.id)
                else:
                    stock[p.id] = checked
                    report.refreshed += 1
    except (scout.Blocked, _TimeUp) as e:
        report.stopped = str(e)

    # 기준이 바뀌면(가격대·제외어) 창고에 있던 상품도 같이 거른다
    report.floors = dict(getattr(source, "floors", {}))
    unfit = [p for p in stock.values() if not fits(p, cfg, report.floors)]
    for p in unfit:
        stock.pop(p.id)
    report.unfit = len(unfit)
    # 참고 코디 칸의 검색어로 모았거나 칸에 맞춰진 상품은 창고 한도와 상관없이 남긴다 (2026-10-13)
    from tools import refs as refs_tool

    pinned = refs_tool.ref_queries(store.refs())
    # 이미 참고 코디 칸에 맞춰진 상품도 남긴다 (다른 검색어로 모였어도)
    matched = {pid for r in store.refs() for ids in r.matches.values() for pid in ids}
    kept: list[Product] = []
    for category in dict.fromkeys(p.category for p in stock.values()):
        items = sorted(
            (p for p in stock.values() if p.category == category),
            key=lambda p: quality(p, profile),
            reverse=True,
        )
        keep = [p for p in items if p.query in pinned or p.id in matched]
        keep_ids = {p.id for p in keep}
        rest = [p for p in items if p.id not in keep_ids]
        keep += rest[: max(0, cfg.max_per_category - len(keep))]
        kept += keep
        report.trimmed += len(items) - len(keep)
        report.total[category] = len(keep)
    kept_ids = {p.id for p in kept}
    report.added = [p for p in report.added if p.id in kept_ids]
    store.save_products(kept)
    return report


# ---------- 보고서 ----------

STATUS_KO = {"sold_out": "품절", "hidden": "판매 종료·비공개"}


def write_report(report: Report, out: Path, store: Store) -> None:
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 상품 수집 결과 ({report.account}, {report.today})",
        "",
        f"- 새로 모은 상품: {len(report.added)}개",
        f"- 다시 확인한 상품: {report.refreshed}개",
        f"- 뺀 상품 (판매 종료·품절): {len(report.dropped)}개",
        f"- 창고 한도로 뺀 상품: {report.trimmed}개",
        f"- 가격대·제외어 기준으로 뺀 상품: {report.unfit}개",
        "- 창고: " + ", ".join(f"{k} {v}" for k, v in report.total.items()),
    ]
    if report.floors:
        lines.append(
            "- 너무 싼 상품 경계 (검색 결과 가격 하위 %, 이보다 싸면 뺌): "
            + ", ".join(f"{k} {v:,}원" for k, v in report.floors.items())
        )
    if report.stopped:
        lines.append(f"- **중간에 멈춤**: {report.stopped}")
    lines += ["", "## 검색어별 후보 (평점·리뷰 기준 통과)", ""]
    lines += [f"- {q}: {n}개" for q, n in report.searched.items()]
    lines += [
        "",
        "## 새로 모은 상품",
        "",
        "| 카테고리 | 상품 | 쇼핑몰 | 가격 | 평점(리뷰) | 단독컷 | 검색어 |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in report.added:
        lines.append(
            f"| {p.category} | [{p.name[:40]}]({p.url}) | {p.shop} | {p.price:,}원 | "
            f"{p.rating or '-'} ({p.review_count or 0}) | {'O' if p.has_flat_shot else ''} | "
            f"{p.query or ''} |"
        )
    if report.dropped:
        lines += ["", "## 뺀 상품", ""]
        lines += [f"- {p.name[:40]} ({STATUS_KO.get(s, s)})" for p, s in report.dropped]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    src = store.dir / "products.json"
    if src.exists():
        shutil.copy(src, out / "products.json")
    if report.added:
        product_sheet(report.added, report.thumbs, out / "sheet.jpg")
    if report.photo_log:
        photos = out / "photos"
        photos.mkdir(exist_ok=True)
        rows = ["file,product,index,score,plain,skin,parts,flat"]
        for pid, i, sc, data in report.photo_log:
            name = f"{pid.split(':')[-1]}_{i}.jpg"
            (photos / name).write_bytes(data)
            rows.append(
                f"{name},{pid},{i},{sc.score:.3f},{sc.plain},{sc.skin},{sc.parts},{int(sc.flat)}"
            )
        (out / "photo_scores.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _drawable(text: str) -> str:
    """폰트에 없는 이모지 등을 뺀다 (미리보기 글자가 네모로 나오지 않게)."""
    return "".join(
        ch
        for ch in text
        if ord(ch) < 0x2000 or 0xAC00 <= ord(ch) <= 0xD7A3 or 0x3000 <= ord(ch) <= 0x318F
    )


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def product_sheet(
    products: list[Product], thumbs: dict[str, bytes], out: Path, per_row: int = 6
) -> Path:
    """새로 모은 상품 미리보기 한 장 (대표 사진, 이름, 쇼핑몰, 가격, 평점)."""
    cell_w, img_h, text_h, pad = 240, 288, 118, 16
    rows = (len(products) + per_row - 1) // per_row
    sheet = Image.new(
        "RGB",
        (per_row * (cell_w + pad) + pad, rows * (img_h + text_h + pad) + pad),
        (250, 250, 248),
    )
    draw = ImageDraw.Draw(sheet)
    bold, regular = _font("Pretendard-Bold.otf", 17), _font("Pretendard-Medium.otf", 15)
    for i, p in enumerate(products):
        x = pad + (i % per_row) * (cell_w + pad)
        y = pad + (i // per_row) * (img_h + text_h + pad)
        draw.rectangle((x, y, x + cell_w, y + img_h), fill=(232, 232, 228))
        if p.id in thumbs:
            img = Image.open(BytesIO(thumbs[p.id])).convert("RGB")
            img.thumbnail((cell_w, img_h))
            sheet.paste(img, (x + (cell_w - img.width) // 2, y + (img_h - img.height) // 2))
        if p.has_flat_shot:
            draw.rectangle((x, y, x + 58, y + 24), fill=(122, 46, 52))
            draw.text((x + 8, y + 3), "단독컷", font=regular, fill="white")
        ty = y + img_h + 8
        name = _drawable(p.name)
        for _ in range(2):
            cut = len(name)
            while cut and draw.textlength(name[:cut], font=regular) > cell_w:
                cut -= 1
            draw.text((x, ty), name[:cut], font=regular, fill=(40, 40, 40))
            name, ty = name[cut:], ty + 21
            if not name:
                break
        draw.text(
            (x, y + img_h + 52), f"{p.category} · {p.shop}"[:22], font=regular, fill=(110, 110, 110)
        )
        draw.text(
            (x, y + img_h + 76),
            f"{p.price:,}원  ★{p.rating or '-'} ({p.review_count or 0})",
            font=bold,
            fill=(40, 40, 40),
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--account", required=True)
    ap.add_argument("--category", help="이 카테고리만 (--query 와 함께 쓰면 그 검색어로)")
    ap.add_argument("--query", action="append", default=[], help="검색어 (여러 번 가능)")
    ap.add_argument("--max-queries", type=int, help="카테고리마다 앞에서 N개 검색어만")
    ap.add_argument("--per-query", type=int, help="검색어마다 확인할 상품 수 (프로필 값 대신)")
    ap.add_argument("--no-recheck", action="store_true", help="창고 상품 다시 확인 안 함")
    ap.add_argument("--report", type=Path, help="보고서 폴더")
    ap.add_argument("--minutes", type=float, help="이 시간(분)이 지나면 멈추고 모은 것까지 저장")
    ap.add_argument(
        "--from-refs",
        action="store_true",
        help="참고 코디(refs.json)에서 창고에 없는 칸의 검색어로 모으고 칸 맞추기를 다시 함",
    )
    ap.add_argument(
        "--ref-photos",
        help="--from-refs 때 이 사진(JSON 목록, 텔레그램 파일 번호)의 참고 코디 칸만 모음",
    )
    args = ap.parse_args(argv)
    # 게시물 만들기(daily) 안의 수집은 시간을 정해 둔다: 창고 전체 다시 확인과 긴 검색으로
    # 90분 제한을 넘겨 후보 없이 끝났다 (2026-10-22 run337). 워크플로가 환경 변수로 기본값을 준다
    if args.minutes is None and os.environ.get("COLLECT_MINUTES"):
        args.minutes = float(os.environ["COLLECT_MINUTES"])
    if os.environ.get("COLLECT_NO_RECHECK") == "1":
        args.no_recheck = True

    from tools import refs as refs_tool

    profile = load_profile(args.account)
    store = Store(args.account)
    plan: dict = dict(profile.product_source.queries)
    if args.from_refs:
        store.save_refs(refs_tool.match(store.refs(), store.products()))
        wanted = store.refs()
        if args.ref_photos:
            # 2026-10-26: 사용자가 보낸 사진만 옮겼으면 그 코디 칸만 (전체를 찾으면 2시간)
            from tools.scout import user_ref

            photos = {user_ref(f) for f in json.loads(args.ref_photos)}
            wanted = [r for r in wanted if r.photo in photos]
        plan = refs_tool.missing_queries(wanted)
        print(f"참고 코디에서 찾을 검색어 {sum(map(len, plan.values()))}개")
        if args.per_query is None:
            args.per_query = 8  # 칸마다 고를 거리가 넉넉하도록 (사용자 2026-10-13 "넉넉하게")
    elif args.category:
        plan = {args.category: args.query or plan.get(args.category, [])}
    elif args.query:
        ap.error("--query 는 --category 와 함께 써 주세요")
    if args.max_queries:
        plan = {k: v[: args.max_queries] for k, v in plan.items()}

    report = collect(
        args.account,
        plan,
        per_query=args.per_query,
        recheck=not args.no_recheck,
        store=store,
        minutes=args.minutes,
    )
    if args.from_refs:
        refs = refs_tool.match(store.refs(), store.products())
        store.save_refs(refs)
        print(f"창고로 맞출 수 있는 참고 코디 {sum(r.ready for r in refs)}/{len(refs)}벌")
    print(
        f"새 상품 {len(report.added)}, 다시 확인 {report.refreshed}, "
        f"뺀 상품 {len(report.dropped)}, 창고 {sum(report.total.values())}"
    )
    if report.stopped:
        print(f"중간에 멈춤: {report.stopped}", file=sys.stderr)
    if args.report:
        write_report(report, args.report, store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
