r"""코디 여러 벌 게시물 만들기 (20단계, profile looks). tools.plan build 와 tools.review 가 부른다.

기획(plan.yaml)의 looks 마다 폴더를 따로 두고, 그 안에서는 한 벌짜리 기획 그대로 코디컷을 만들고
검수한다. 그래서 tools.review prep/apply/more 를 벌 폴더에 그대로 쓸 수 있다.

  out/looks/<n>/plan.yaml          n번 벌만 담은 한 벌 기획 (DailyPlan.look_plan)
  out/looks/<n>/outfit/            코디컷 후보 (profile looks.candidates 장, 떨어지면 more 로 더)
  out/looks/<n>/review/review.json 이 벌의 후보·점수·고른 코디컷

슬라이드: 표지(plan.cover_look 번 벌 코디컷 + 제목, 또는 콜라주) → 벌 장(코디컷 + 상품 정보 몇 줄)
→ 댓글 안내 장 (profile feed.slides). 검수로 고른 코디컷이 바뀌면 finish 가 슬라이드·manifest 를
다시 만든다.

사용법:
  python -m tools.plan build out/2026-10-02/plan.yaml        (looks 가 있으면 여기로 옴)
  python -m tools.review finish out/2026-10-02               검수 뒤 슬라이드·manifest 다시 만들기
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from core.models import (
    DailyPlan,
    FeedItem,
    FeedLook,
    FeedSpec,
    Manifest,
    OutfitPlan,
    Review,
    SlideIssue,
)
from core.profile import REPO_ROOT, Profile, load_profile
from core.store import Store
from tools import feed, overlay

LOOKS_DIR = "looks"


def look_dir(out: Path, n: int) -> Path:
    """n(1부터)번 벌 폴더."""
    return out / LOOKS_DIR / str(n)


def is_look_dir(out: Path) -> bool:
    return out.parent.name == LOOKS_DIR and out.name.isdigit()


def look_dirs(out: Path) -> list[Path]:
    found = [d for d in (out / LOOKS_DIR).glob("*") if d.is_dir() and d.name.isdigit()]
    return sorted(found, key=lambda d: int(d.name))


def write_plan(plan: DailyPlan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(plan.model_dump(mode="json", exclude_none=True), allow_unicode=True),
        encoding="utf-8",
    )


def build(
    plan: DailyPlan,
    out: Path,
    profile: Profile,
    store: Store,
    outfit: str = "auto",
    fake: bool = False,
    warns: list[str] | None = None,
) -> Manifest:
    """벌마다 코디컷 후보를 만들고(검수 전에는 첫 후보) 슬라이드·캡션·manifest 를 만든다."""
    from tools import plan as plan_tool
    from tools import review as review_tool

    engine = profile.outfit_image.engine if outfit == "auto" else outfit
    notes: list[str] = []
    for n in range(1, len(plan.looks) + 1):
        sub = plan.look_plan(n - 1)
        d = look_dir(out, n)
        write_plan(sub, d / "plan.yaml")
        made: list[Path] = [REPO_ROOT / sub.cover] if sub.cover else []
        if not made and review_tool.review_path(d).exists():
            # /fix 로 다시 그리는 벌: 같은 파일 이름의 지난 후보 점수를 남기지 않는다
            review_tool.review_path(d).unlink()
        if not made and engine in plan_tool.ENGINES:
            work = d / "outfit"
            work.mkdir(parents=True, exist_ok=True)
            # tools.review more 가 같은 방식으로 후보를 더 만든다
            (work / "engine.txt").write_text(engine, encoding="utf-8")
            try:
                made = plan_tool.make_outfit(
                    sub, store, work, fake, profile.looks.candidates, engine, profile
                )
            except plan_tool.QUOTA_ERRORS:
                raise
            except Exception as e:  # noqa: BLE001 - 한 벌이 실패해도 나머지는 만든다
                print(f"{n}번 벌 코디컷 실패: {e}", file=sys.stderr)
            if not made:
                notes.append(f"{n}번 벌 코디컷을 만들지 못했습니다 (그 벌 장은 빠짐)")
        elif not made:
            d.mkdir(parents=True, exist_ok=True)
        kept = review_tool.load_review(d) if made and review_tool.review_path(d).exists() else None
        review = review_tool.start(d, made)
        if kept and review.chosen == kept.chosen and _same_chosen(d, kept, review):
            # 지난 코디컷을 그대로 쓰는 벌 (/fix 로 안 고친 벌): 벌 장에는 상품 장 검사가 없어서
            # 지난 검수 결과를 그대로 둔다 (다시 검수하지 않아도 되게)
            review_tool.save_review(d, kept)
    return finish(out, profile, store, warns or [], notes)


def _same_chosen(d: Path, a: Review, b: Review) -> bool:
    """두 검수가 같은 코디컷 파일을 골랐는지. 후보 번호(c1)만 같고 파일이 다르면
    (/fix 로 cover 를 다른 사진으로 바꿈, 2026-10-07 10/8 LOOK 4) 지난 검수를 남기면
    지난 사진이 다시 쓰인다."""
    from tools import review as review_tool

    def path(r: Review) -> Path | None:
        c = next((c for c in r.candidates if c.id == r.chosen), None)
        return review_tool.cand_path(d, c).resolve() if c else None

    return path(a) == path(b)


def chosen_image(d: Path) -> Path | None:
    """벌 폴더의 검수로 고른 코디컷 (검수 전이면 첫 후보)."""
    from tools import review as review_tool

    path = review_tool.review_path(d)
    if not path.exists():
        return None
    review = Review.model_validate_json(path.read_text(encoding="utf-8"))
    c = next((c for c in review.candidates if c.id == review.chosen), None)
    return review_tool.cand_path(d, c) if c else None


def look_reviews(out: Path) -> list[Review | None]:
    from tools import review as review_tool

    rows = []
    for d in look_dirs(out):
        path = review_tool.review_path(d)
        rows.append(
            Review.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None
        )
    return rows


def summary_review(out: Path, issues: list[SlideIssue]) -> Review:
    """벌마다 검수 결과를 합친 게시물 검수 (manifest.review). 한 벌이라도 통과 못 하면 사람 확인."""
    reviews = look_reviews(out)
    parts, states = [], []
    for n, r in enumerate(reviews, 1):
        if r is None:
            parts.append(f"{n}번 벌 코디컷 없음")
            states.append("failed")
            continue
        c = next((c for c in r.candidates if c.id == r.chosen), None)
        score = f"{c.score.total}점" if c and c.score else "점수 없음"
        parts.append(f"{n}번 {r.chosen or '-'} {score} ({r.status})")
        states.append(r.status)
    if any(s == "not_reviewed" for s in states):
        status = "not_reviewed"
    elif all(s == "passed" for s in states) and not issues:
        status = "passed"
    else:
        status = "failed"
    return Review(
        rounds=max([r.rounds for r in reviews if r] or [1]),
        status=status,
        passed=status == "passed",
        slide_issues=issues,
        summary=" / ".join(parts),
    )


def min_score(out: Path) -> float:
    scores = []
    for r in look_reviews(out):
        c = next((c for c in r.candidates if c.id == r.chosen), None) if r else None
        scores.append(c.score.total if c and c.score else 0)
    return float(min(scores or [0]))


def cover_index(plan: DailyPlan) -> int:
    """표지로 쓸 벌 (0부터). plan.cover_look 이 없거나 벌 수를 넘으면 1번 벌."""
    n = plan.cover_look or 1
    if not 1 <= n <= len(plan.looks):
        print(f"경고: 표지 벌 {n}번이 없어 1번 벌로 표지를 만듭니다", file=sys.stderr)
        n = 1
    return n - 1


def finish(
    out: Path,
    profile: Profile | None = None,
    store: Store | None = None,
    warns: list[str] | None = None,
    notes: list[str] | None = None,
) -> Manifest:
    """벌마다 고른 코디컷으로 슬라이드·미리보기·캡션·manifest 를 (다시) 만든다."""
    from tools import plan as plan_tool

    plan = plan_tool.load_plan(out / "plan.yaml")
    profile = profile or load_profile(plan.account)
    store = store or Store(plan.account)
    products = {p.id: p for p in store.products()}
    assert plan.text is not None  # check 가 확인함

    images = [chosen_image(d) for d in look_dirs(out)][: len(plan.looks)]
    images += [None] * (len(plan.looks) - len(images))
    looks = [
        FeedLook(
            image=str(img.resolve()) if img else None,
            items=[FeedItem(product=it.product, name=it.name) for it in look.items],
        )
        for img, look in zip(images, plan.looks, strict=True)
    ]
    cover = images[cover_index(plan)]
    # 펼친컷 날은 표지를 벌 전부의 콜라주로 (모델컷은 바닥이 고르지 않아 오려 내지 않는다)
    collage = profile.looks.cover_collage.enabled and plan.shot != "model" and all(images)
    if plan.cover_collage is not None:
        collage = plan.cover_collage and plan.shot != "model" and all(images)  # 이 게시물만 정함
    spec = FeedSpec(
        account=plan.account,
        title_lines=plan.text.title_lines,
        cover=str(cover.resolve()) if cover else None,
        items=[FeedItem(product=it.product, name=it.name) for it in plan.all_items()],
        looks=looks,
        cover_collage=collage,
    )
    (out / "feed.yaml").write_text(
        yaml.safe_dump(spec.model_dump(mode="json", exclude_none=True), allow_unicode=True),
        encoding="utf-8",
    )
    slides_dir = out / "slides"
    for old in slides_dir.glob("*.jpg"):
        old.unlink()  # 벌 수가 바뀌면 지난 장이 남지 않게
    # 펼친컷 날은 릴스용 9:16 판도 (reels.fill). 모델컷은 바닥이 아니라 늘리지 않는다
    tall = profile.reels.enabled and profile.reels.fill and plan.shot != "model"
    slides = feed.render(spec, slides_dir, root=REPO_ROOT, profile=profile, store=store, tall=tall)
    plan_tool.preview(slides, out / "preview.jpg")

    issues = title_issues(plan, profile)
    review = summary_review(out, issues)
    body = plan_tool.caption(plan, profile, store)
    tags = plan_tool.final_hashtags(plan, profile)
    (out / "caption.txt").write_text(body + "\n\n" + " ".join(tags) + "\n", encoding="utf-8")
    manifest = Manifest(
        account=plan.account,
        date=plan.date,
        plan=OutfitPlan(
            theme=plan.theme,
            items=[products[it.product] for it in plan.all_items()],
            reason=plan.reason,
            looks=[[it.product for it in look.items] for look in plan.looks],
        ),
        images=[plan_tool.rel(p) for p in slides],
        review_score=min_score(out),
        needs_human=not review.passed,
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
        f"- 코디 {len(plan.looks)}벌, 슬라이드 {len(slides)}장",
        f"- 검수: {review.summary}",
    ]
    for n, look in enumerate(plan.looks, 1):
        report.append(f"  - {n}번 벌: {look.reason.split('. ')[0] if look.reason else '-'}")
    total = cost(out)
    if total:
        report.append(f"- 참고: 코디컷 생성 비용 약 ${total:.2f}")
    report += [f"- 경고: {w}" for w in warns or []] + [f"- 참고: {n}" for n in notes or []]
    report += [f"- 장 문제: {s.slide} [{s.kind}] {s.note}" for s in issues]
    (out / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return manifest


def cost(out: Path) -> float:
    """모든 벌·회차의 코디컷 생성 비용 합계 (달러)."""
    from tools import outfit_gen

    return sum(outfit_gen.cost(r.parent) for r in out.glob(f"{LOOKS_DIR}/*/outfit*/gen.json"))


def title_issues(plan: DailyPlan, profile: Profile) -> list[SlideIssue]:
    """표지 제목이 넘치면 장 문제로 남긴다 (도구가 확실히 아는 것만)."""
    if not plan.text:
        return []
    v = profile.visual
    w = feed.canvas_size(profile)[0]
    try:
        overlay.fit_font(
            plan.text.title_lines, v.font, v.title_font_size, w - 2 * round(w * v.title_margin_x)
        )
    except ValueError as e:
        return [SlideIssue(slide="01_cover.jpg", kind="글자", note=str(e), by="code")]
    return []
