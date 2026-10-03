r"""7단계 이미지 검수 도구. 판단은 image-reviewer 에이전트(Claude)가 하고, 이 도구는 재료를 만들고
결과를 적용한다. 규칙(후보 수, 통과 점수, 최대 회차)은 profile.yaml 의 review 에서 읽는다.

사용법 (out 은 tools.plan build 결과 폴더):
  python -m tools.review prep out/2026-09-27     검수표·도구 검사 (review/ 폴더)
  python -m tools.review apply out/2026-09-27 --scores out/2026-09-27/review/scores.yaml
  python -m tools.review more out/2026-09-27 [--fake]   코디컷 후보 한 묶음 더 (build 와 같은 방식)
  python -m tools.review finish out/2026-10-02           여러 벌: 고른 코디컷으로 슬라이드 다시

여러 벌 게시물(20단계, tools.looks)은 벌마다 out/looks/<n>/ 에 한 벌 기획·후보·검수가 있다.
prep/apply/more 는 벌 폴더(out/looks/1)에 그대로 쓰고, 다 끝나면 finish 로 게시물 폴더를
다시 만든다.
게시물 폴더에 prep 을 하면 모든 벌 폴더를 prep 한다.

apply 종료 코드: 0 통과 / 3 표지 후보가 모두 기준 미달 (more 로 다시 만들 수 있음)
                 / 4 사람 확인 필요 (최대 회차를 넘었거나 캐러셀 장에 문제가 남음) / 1 입력 오류
more 종료 코드: 0 성공 / 2 이미지 서비스 한도 / 3 최대 회차를 넘음

만드는 파일 (out/review/):
  review.json   후보 목록·점수·선택 (core.models.Review, manifest.review 에도 복사)
  covers.jpg    위: 원본 상품 사진, 아래: 코디컷 후보 (c1, c2 …)
  slides.jpg    상품 장 모아 보기 (장마다 고른 색 표시)
  input.md      검수 에이전트에게 넘길 안내 (상품 설명·색, 후보 파일, 도구가 찾은 문제)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from PIL import Image, ImageDraw
from pydantic import BaseModel, ValidationError

from core.models import (
    REVIEW_CHECKS,
    Candidate,
    CoverScore,
    DailyPlan,
    FeedSpec,
    Manifest,
    Review,
    SlideIssue,
)
from core.profile import REPO_ROOT, Profile, load_profile
from core.store import Store
from tools import compare, faces, feed, overlay

EXIT_RETRY = 3
EXIT_HUMAN = 4
CATEGORY_EN = {
    "아우터": "outer",
    "상의": "top",
    "하의": "bottoms",
    "원피스": "dress",
    "가방": "bag",
    "신발": "shoes",
    "소품": "accessory",
}


class Scores(BaseModel):
    """검수 에이전트가 쓰는 점수 파일 (review/scores.yaml)."""

    covers: list[CoverScore] = []
    slides: list[SlideIssue] = []
    summary: str = ""


# ---------- 파일 ----------


def review_path(out: Path) -> Path:
    return out / "review" / "review.json"


def load_review(out: Path) -> Review:
    path = review_path(out)
    if not path.exists():
        raise FileNotFoundError(f"검수 파일이 없습니다 (tools.plan build 를 먼저): {path}")
    return Review.model_validate_json(path.read_text(encoding="utf-8"))


def save_review(out: Path, review: Review) -> None:
    path = review_path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(review.model_dump_json(indent=2), encoding="utf-8")


def _ref(out: Path, path: Path) -> str:
    """결과 폴더 안이면 폴더 기준 경로, 밖이면 절대 경로."""
    try:
        return str(path.resolve().relative_to(out.resolve()))
    except ValueError:
        return str(path.resolve())


def cand_path(out: Path, c: Candidate) -> Path:
    return out / c.file  # 절대 경로면 그대로


def start(out: Path, made: list[Path]) -> Review:
    """build 가 부른다. 새 후보로 검수 파일을 만든다. 기획의 cover 로 이미 검수한 후보 하나를
    다시 쓰는 경우(캐러셀만 고쳐 다시 빌드)에는 후보와 점수를 두고 캐러셀 검사만 다시 한다."""
    old = load_review(out) if review_path(out).exists() else None
    if old and len(made) == 1:
        for c in old.candidates:
            if cand_path(out, c).resolve() == made[0].resolve():
                review = old.model_copy(
                    update={"chosen": c.id, "slide_issues": [], "status": "not_reviewed"}
                )
                review.passed = False
                save_review(out, review)
                return review
    review = Review(
        candidates=[Candidate(id=f"c{i}", file=_ref(out, p)) for i, p in enumerate(made, 1)]
    )
    review.chosen = review.candidates[0].id if review.candidates else None
    save_review(out, review)
    return review


def face_crops(out: Path, plan: DailyPlan, review: Review) -> dict[str, list[Path]]:
    """모델컷(기획 shot: model, 16단계) 후보마다 얼굴 검출 도구(YuNet)가 찾은 자리를 크게 잘라
    review/face_<후보>_<n>.jpg 로 저장한다. 펼친 코디컷이면 보지 않는다.
    거울 셀카처럼 폰이 얼굴을 다 가려도 얼굴로 잡으므로(2026-10-02 시안 gen-23·24) 도구가
    떨어뜨리지 않고, image-reviewer 가 잘라 낸 사진을 열어 정면 얼굴이 또렷한지 판단한다."""
    if plan.shot != "model":
        return {}
    found: dict[str, list[Path]] = {}
    for c in review.candidates:
        path = cand_path(out, c)
        if not path.exists():
            continue
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            for n, (x0, y0, x1, y1) in enumerate(faces.find_faces(rgb), 1):
                pad = (x1 - x0) * 0.6
                box = (max(0, x0 - pad), max(0, y0 - pad), min(rgb.width, x1 + pad),
                       min(rgb.height, y1 + pad))
                dest = out / "review" / f"face_{c.id}_{n}.jpg"
                dest.parent.mkdir(parents=True, exist_ok=True)
                rgb.crop(tuple(round(v) for v in box)).save(dest, quality=90)
                found.setdefault(c.id, []).append(dest)
    return found


# ---------- prep ----------


def load_plan_of(out: Path) -> DailyPlan:
    return DailyPlan.model_validate(yaml.safe_load((out / "plan.yaml").read_text(encoding="utf-8")))


def reference_photos(plan: DailyPlan, out: Path) -> list[Path | None]:
    """상품마다 코디컷을 만들 때 쓴 원본 사진 (없으면 누끼, 둘 다 없으면 None)."""
    refs: list[Path | None] = []
    for i, it in enumerate(plan.items, 1):
        pid = it.product.split(":")[-1]
        cands = [out / "outfit" / "src" / f"{pid}_{it.cutout_photo}.jpg"]
        for folder in ("outfit/originals", "outfit/cutouts/r1"):
            cands += sorted((out / folder).glob(f"{i:02d}_*"))
        refs.append(next((p for p in cands if p.exists() and "_collar" not in p.stem), None))
    return refs


def product_slides(manifest: Manifest, out: Path) -> list[Path]:
    """manifest 의 슬라이드 중 상품 장 (표지·요약 제외), 기획 상품 순서와 같다."""
    paths = [out / "slides" / Path(p).name for p in manifest.images]
    return [p for p in paths if not p.stem.endswith(("_cover", "_summary", "_comment"))]


def slide_checks(
    plan: DailyPlan, manifest: Manifest, out: Path, profile: Profile
) -> list[SlideIssue]:
    """도구가 확실히 알 수 있는 문제: 상품 장에 남은 얼굴, 줄여 쓴(…) 이름·코멘트, 넘치는 제목."""
    from tools import faces

    issues: list[SlideIssue] = []
    f = profile.feed
    painter = feed.Painter(profile, feed.canvas_size(profile))
    width = painter.w - 2 * painter.m
    slides = product_slides(manifest, out)
    for it, slide in zip(plan.items, slides, strict=False):
        if f.hide_faces and slide.exists():
            n = len(faces.find_faces(Image.open(slide).convert("RGB")))
            if n:
                issues.append(
                    SlideIssue(slide=slide.name, kind="얼굴", note=f"얼굴 {n}개가 보임", by="code")
                )
        checks = [("이름", it.name, painter.bold(f.name_size), f.name_max_lines)]
        if it.comment:
            checks.append(("코멘트", it.comment, painter.body(f.comment_size), f.comment_max_lines))
        for label, text, font, max_lines in checks:
            text = feed.drawable(text)
            lines = feed.wrap(text, font, width, max_lines)
            if lines and lines[-1].endswith("…") and not text.endswith("…"):
                issues.append(
                    SlideIssue(
                        slide=slide.name,
                        kind="글자",
                        note=f"{label}이 {max_lines}줄을 넘어 잘림: {text}",
                        by="code",
                    )
                )
    if plan.text:
        v = profile.visual
        w = feed.canvas_size(profile)[0]
        try:
            overlay.fit_font(
                plan.text.title_lines,
                v.font,
                v.title_font_size,
                w - 2 * round(w * v.title_margin_x),
            )
        except ValueError as e:
            issues.append(SlideIssue(slide="01_cover.jpg", kind="글자", note=str(e), by="code"))
    return issues


def slides_sheet(plan: DailyPlan, slides: list[Path], out: Path, w: int = 360) -> Path:
    """상품 장을 모아 보고, 장마다 위에 고른 색을 적는다."""
    h, label, pad, per_row = int(w * 1.25), 40, 14, 4
    rows = max(1, (len(slides) + per_row - 1) // per_row)
    sheet = Image.new(
        "RGB", (per_row * (w + pad) + pad, rows * (h + label + pad) + pad), (250, 250, 248)
    )
    draw = ImageDraw.Draw(sheet)
    font = overlay.load_font("Pretendard-Medium.otf", 22)
    for i, (it, path) in enumerate(zip(plan.items, slides, strict=False)):
        x = pad + (i % per_row) * (w + pad)
        y = pad + (i // per_row) * (h + label + pad)
        draw.text(
            (x, y + 6), f"{path.stem} · 고른 색: {it.color or '-'}", font=font, fill=(40, 40, 40)
        )
        sheet.paste(Image.open(path).convert("RGB").resize((w, h)), (x, y + label))
    dest = out / "review" / "slides.jpg"
    sheet.save(dest, quality=88)
    return dest


def prep(out: Path, profile: Profile | None = None) -> Path:
    plan = load_plan_of(out)
    profile = profile or load_profile(plan.account)
    review = load_review(out)
    (out / "review").mkdir(parents=True, exist_ok=True)
    refs = reference_photos(plan, out)
    cats = categories(plan)

    todo = [c for c in review.candidates if c.score is None]
    if review.candidates:
        compare.contact_sheet(
            [
                (f"{i} {CATEGORY_EN.get(cat, 'item')}", p)
                for i, (cat, p) in enumerate(zip(cats, refs, strict=True), 1)
                if p is not None
            ],
            [(f"{c.id} (round {c.round})", cand_path(out, c)) for c in review.candidates],
            out / "review" / "covers.jpg",
            title="Top: original products / Bottom: outfit candidates",
            per_row=4,
        )

    manifest = _manifest(out)
    slides = product_slides(manifest, out) if manifest else []
    code_issues: list[SlideIssue] = []
    if profile.review.check_slides and manifest:
        code_issues = slide_checks(plan, manifest, out, profile)
        if slides:
            slides_sheet(plan, slides, out)
    review.slide_issues = [s for s in review.slide_issues if s.by != "code"] + code_issues
    save_review(out, review)

    lines = [
        f"# 이미지 검수 재료 ({plan.date} {plan.theme.title_hint})",
        "",
        "점수 파일 형식과 기준은 `.claude/agents/image-reviewer.md` 를 따른다.",
        f"통과 기준: 합계 {profile.review.min_score}점 이상이고 0점 항목이 없을 것.",
        "",
        "코디컷 종류: "
        + (
            "**모델컷** (옷을 입은 얼굴 없는 AI 인물. image-reviewer.md 의 모델컷 기준도 본다)"
            if plan.shot == "model"
            else "펼친 코디컷"
        ),
        "",
        "## 기획 상품 (covers.jpg 위 줄 번호)",
        "",
        "| # | 카테고리 | 코디컷 설명 | 고른 색 | 입는 방법 | 실제 크기 | 원본 사진 |",
        "|---|---|---|---|---|---|---|",
    ]
    sizes = real_sizes(plan)
    for i, (it, ref) in enumerate(zip(plan.items, refs, strict=True), 1):
        how = f"{it.wear_with}에 입힘" if it.wear_with else "-"
        lines.append(
            f"| {i} | {cats[i - 1]} | {it.desc} | {it.color or '-'} | {how} | {sizes[i - 1]} | "
            f"{_ref(out, ref) if ref else '없음'} |"
        )
    lines += ["", "## 점수를 매길 코디컷 후보", ""]
    if todo:
        lines += [f"- {c.id}: `{_ref(out, cand_path(out, c))}`" for c in todo]
    else:
        lines.append("- 없음 (이미 점수를 매긴 후보만 있음. covers 는 비워도 된다)")
    scored = [c for c in review.candidates if c.score]
    if scored:
        lines += ["", "이미 매긴 점수: " + ", ".join(f"{c.id} {c.score.total}점" for c in scored)]
    lines += ["", "## 캐러셀 상품 장", ""]
    if slides:
        lines.append(
            "모아 보기: `review/slides.jpg`. 한 장씩: "
            + ", ".join(f"`slides/{p.name}`" for p in slides)
        )
    else:
        lines.append("- 없음")
    from tools import feedback

    fb = feedback.recent_lines(
        Store(plan.account),
        plan.date,
        profile.feedback.recent_days,
        {"cover", "product", "look", "all"},
    )
    if fb:
        # 15단계 /fix 로 사람이 지적한 것. 같은 문제가 다시 보이면 해당 항목 점수에 반영한다
        lines += ["", "## 최근 사람 피드백 (같은 문제가 보이면 감점)", ""] + fb
    lines += ["", "## 도구가 찾은 문제 (점수 파일에 다시 적지 않아도 됨)", ""]
    todo_ids = {c.id for c in todo}
    found = [
        f"- {cid} [얼굴 확인] 얼굴 검출 도구가 얼굴로 본 자리: "
        + ", ".join(f"`{_ref(out, f)}`" for f in crops)
        + " (열어 보고 정면 얼굴이 또렷하면 clean 0, 고개 숙임·돌림·가림으로 일부만 보이면 괜찮음)"
        for cid, crops in face_crops(out, plan, review).items()
        if cid in todo_ids
    ]
    lines += found + [f"- {s.slide} [{s.kind}] {s.note}" for s in code_issues] or ["- 없음"]
    dest = out / "review" / "input.md"
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def real_sizes(plan: DailyPlan) -> list[str]:
    """상품마다 코디컷에 쓴 실제 크기 ("기장 110cm"). 크기 비율을 검수할 때 본다."""
    from tools import plan as plan_tool
    from tools.compose import load_layout

    layout = load_layout()
    products = {p.id: p for p in Store(plan.account).products()}
    rows = []
    for it in plan.items:
        p = products.get(it.product)
        if p is None:
            rows.append("-")
            continue
        place = it.slot or p.category
        rule = layout["slots"].get(place, {})
        cm = plan_tool.item_length(it, p, layout) or rule.get("cm")
        kind = "기장" if rule.get("measure", "height") == "height" else "긴 쪽"
        rows.append(f"{kind} {cm:g}cm" if cm else "-")
    return rows


def categories(plan: DailyPlan) -> list[str]:
    """기획 상품의 카테고리 (상품 창고 기준, 창고에 없으면 '상품')."""
    products = {p.id: p for p in Store(plan.account).products()}
    return [
        products[it.product].category if it.product in products else "상품" for it in plan.items
    ]


def _manifest(out: Path) -> Manifest | None:
    path = out / "manifest.json"
    return Manifest.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


# ---------- apply ----------


def passes(score: CoverScore, profile: Profile) -> bool:
    return score.total >= profile.review.min_score and not score.zeros


def pick(review: Review, profile: Profile) -> Candidate | None:
    """/fix 요청을 반영한 후보를 먼저, 그 안에서 통과한 것, 점수가 높은 것, 같으면 먼저 만든 것."""
    scored = [c for c in review.candidates if c.score]
    if not scored:
        return None
    return max(
        scored,
        key=lambda c: (
            c.score.feedback_ok is not False,
            passes(c.score, profile),
            c.score.total,
            -review.candidates.index(c),
        ),
    )


def rerender_cover(out: Path, cover: Path, profile: Profile) -> None:
    """고른 후보로 표지 장만 다시 그린다 (상품 장은 그대로, 쇼핑몰 사진을 다시 받지 않음)."""
    spec_path = out / "feed.yaml"
    spec = FeedSpec.model_validate(yaml.safe_load(spec_path.read_text(encoding="utf-8")))
    spec = spec.model_copy(update={"cover": str(cover.resolve())})
    spec_path.write_text(
        yaml.safe_dump(spec.model_dump(mode="json", exclude_none=True), allow_unicode=True),
        encoding="utf-8",
    )
    size = feed.canvas_size(profile)
    feed.save_slide(
        feed.cover_slide(spec, profile, size, REPO_ROOT), out / "slides" / "01_cover.jpg", profile
    )


def apply(out: Path, scores: Scores, profile: Profile | None = None) -> tuple[Review, int]:
    plan = load_plan_of(out)
    profile = profile or load_profile(plan.account)
    review = load_review(out)
    ids = {c.id: c for c in review.candidates}
    for s in scores.covers:
        if s.id not in ids:
            raise ValueError(f"없는 후보입니다: {s.id} (후보: {', '.join(ids) or '없음'})")
        ids[s.id].score = s
    missing = [c.id for c in review.candidates if c.score is None]
    if missing:
        raise ValueError(f"점수가 없는 후보가 있습니다: {', '.join(missing)}")
    review.slide_issues = [s for s in review.slide_issues if s.by == "code"] + [
        s.model_copy(update={"by": "claude"}) for s in scores.slides
    ]
    if scores.summary:
        review.summary = scores.summary

    best = pick(review, profile)
    cover_ok = best is not None and passes(best.score, profile)
    if best is None:
        # 코디컷이 없는 날: --outfit none 이면 캐러셀만 본다. 만들려다 실패했으면 사람이 본다
        failed = (out / "outfit" / "engine.txt").exists()
        code = 0 if not review.slide_issues and not failed else EXIT_HUMAN
        review.status = "passed" if code == 0 else "failed"
    elif not cover_ok and review.rounds < profile.review.max_rounds:
        review.status, code = "retry", EXIT_RETRY
    else:
        review.status = "passed" if cover_ok and not review.slide_issues else "failed"
        code = 0 if review.status == "passed" else EXIT_HUMAN
    review.passed = review.status == "passed"

    manifest = _manifest(out)
    if best is not None and review.status != "retry":
        if review.chosen != best.id and manifest and manifest.images[0].endswith("01_cover.jpg"):
            rerender_cover(out, cand_path(out, best), profile)
            from tools.plan import preview

            preview([out / "slides" / Path(p).name for p in manifest.images], out / "preview.jpg")
        review.chosen = best.id
    save_review(out, review)

    if manifest:
        chosen = next((c for c in review.candidates if c.id == review.chosen), None)
        manifest.review_score = chosen.score.total if chosen and chosen.score else 0
        manifest.needs_human = not review.passed
        manifest.review = review
        (out / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    _append_report(out, review, profile)
    return review, code


def _append_report(out: Path, review: Review, profile: Profile) -> None:
    status = {
        "passed": "통과",
        "retry": "후보 모두 기준 미달, 다시 만들기",
        "failed": "사람 확인 필요",
        "not_reviewed": "검수 안 함",
    }[review.status]
    lines = ["", f"## 이미지 검수: {status} (회차 {review.rounds}/{profile.review.max_rounds})", ""]
    for c in review.candidates:
        if c.score:
            mark = " ← 표지" if c.id == review.chosen and review.status != "retry" else ""
            detail = ", ".join(f"{k} {getattr(c.score, k)}" for k in REVIEW_CHECKS)
            lines.append(f"- {c.id}: {c.score.total}/10 ({detail}){mark} {c.score.notes}".rstrip())
    lines += [f"- 캐러셀 {s.slide} [{s.kind}] {s.note}" for s in review.slide_issues]
    if review.summary:
        lines.append(f"- 요약: {review.summary}")
    path = out / "report.md"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(old + "\n".join(lines) + "\n", encoding="utf-8")


# ---------- more ----------


def more(out: Path, fake: bool = False) -> list[Candidate]:
    """코디컷 후보를 한 묶음(profile review.candidates 장) 더 만든다."""
    from tools import plan as plan_tool

    plan = load_plan_of(out)
    profile = load_profile(plan.account)
    review = load_review(out)
    if review.rounds >= profile.review.max_rounds:
        raise OverflowError(f"최대 회차({profile.review.max_rounds})를 넘습니다")
    rnd = review.rounds + 1
    from tools import looks

    count = profile.looks.candidates if looks.is_look_dir(out) else profile.review.candidates
    engine_file = out / "outfit" / "engine.txt"
    engine = (
        engine_file.read_text(encoding="utf-8").strip()
        if engine_file.exists()
        else profile.outfit_image.engine
    )
    made = plan_tool.make_outfit(
        plan,
        Store(plan.account),
        out / f"outfit_r{rnd}",
        fake,
        count,
        engine,
        profile,
    )
    start_no = len(review.candidates)
    new = [
        Candidate(id=f"c{start_no + i}", file=_ref(out, p), round=rnd)
        for i, p in enumerate(made, 1)
    ]
    review.candidates += new
    review.rounds = rnd
    review.status, review.passed = "not_reviewed", False
    save_review(out, review)
    return new


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prep", help="검수표와 도구 검사")
    s.add_argument("out", type=Path)
    s = sub.add_parser("apply", help="검수 점수 적용, 표지 고르기")
    s.add_argument("out", type=Path)
    s.add_argument("--scores", type=Path, required=True)
    s = sub.add_parser("more", help="코디컷 후보 더 만들기")
    s.add_argument("out", type=Path)
    s.add_argument("--fake", action="store_true", help="이미지 API 대신 가짜 이미지")
    s = sub.add_parser("finish", help="여러 벌: 고른 코디컷으로 슬라이드·manifest 다시 만들기")
    s.add_argument("out", type=Path)
    args = ap.parse_args(argv)

    from tools import looks
    from tools.plan import QUOTA_ERRORS

    try:
        if args.cmd == "finish":
            m = looks.finish(args.out)
            print(
                json.dumps(
                    {
                        "status": m.review.status if m.review else "-",
                        "slides": len(m.images),
                        "summary": m.review.summary if m.review else "",
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if m.review and m.review.passed else EXIT_HUMAN
        if args.cmd == "prep" and not review_path(args.out).exists() and looks.look_dirs(args.out):
            for d in looks.look_dirs(args.out):
                print(prep(d))
            return 0
        if args.cmd == "prep":
            print(prep(args.out))
            review = load_review(args.out)
            print(
                f"후보 {len(review.candidates)}장, "
                f"도구가 찾은 캐러셀 문제 {len(review.slide_issues)}개"
            )
            return 0
        if args.cmd == "apply":
            raw = yaml.safe_load(args.scores.read_text(encoding="utf-8")) or {}
            review, code = apply(args.out, Scores.model_validate(raw))
            chosen = next((c for c in review.candidates if c.id == review.chosen), None)
            score = f"{chosen.score.total}점" if chosen and chosen.score else "-"
            print(
                json.dumps(
                    {"status": review.status, "chosen": review.chosen, "score": score},
                    ensure_ascii=False,
                )
            )
            return code
        try:
            new = more(args.out, args.fake)
        except OverflowError as e:
            print(f"오류: {e}", file=sys.stderr)
            return EXIT_RETRY
        print(f"새 후보: {', '.join(c.id for c in new) or '없음 (모두 실패)'}")
        return 0 if new else 1
    except (ValueError, FileNotFoundError, ValidationError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    except QUOTA_ERRORS as e:
        print(f"코디컷 이미지 서비스 크레딧·요금제 한도에 막혔습니다.\n{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
