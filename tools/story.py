r"""피드 게시 뒤 스토리 올리기 (17단계). AI 없음, Pillow.

인스타 게시 API 로 스토리 사진은 올릴 수 있지만 링크·멘션 스티커나 "게시물 공유" 스토리는 못 만든다.
그래서 피드 앞 장(표지 등)을 1080x1920 세로 판 가운데에 놓고, 위에 "오늘의 코디", 아래에
"새 게시물 보러가기 →" 와 계정 이름을 합성한다. 모양·장 수·올리는 때는 프로필 `story`.
인스타 화면 위(프로필·진행 막대)와 아래(답장 칸)에 가리지 않게 글자는 가운데 쪽에 둔다.

- delay_minutes 0: 게시 job(tools.publish)이 피드 게시 직후 이어서 올린다
- delay_minutes > 0: schedule 이 게시 시각 + 그 분에 publish 워크플로(story)로 `post` 를 부른다.
  그 시각보다 게시가 늦었으면 게시 job 이 바로 올린다
스토리가 실패해도 피드 게시는 성공으로 두고 텔레그램으로 알린다.

사용법:
  python -m tools.story render --account women --slides out/2026-09-30/slides/*.jpg --out <폴더>
      스토리 이미지만 만든다 (미리보기)
  python -m tools.story post --account women --results <daily-results 폴더> [--date YYYY-MM-DD]
      그날 게시한 결과 폴더(published.json)의 스토리를 올린다 (공개 작업, Claude 허용 목록에서 막힘)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageDraw, ImageFilter

from core.models import PublishRecord, StoryRecord
from core.profile import Profile, Story, load_profile, minutes
from tools import ai_label, notify
from tools.ig_publish import IGError, Instagram
from tools.overlay import BASE_WIDTH, _text_layer, load_font

SIZE = (1080, 1920)  # 인스타 스토리 9:16
RECORD = "story.json"


def _cover_fill(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """비율을 지키며 size 를 꽉 채우도록 키우고 가운데를 자른다."""
    w, h = size
    k = max(w / img.width, h / img.height)
    big = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    x, y = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((x, y, x + w, y + h))


def _background(img: Image.Image, cfg: Story, size: tuple[int, int]) -> Image.Image:
    if cfg.background != "blur":
        return Image.new("RGB", size, cfg.background)
    small = _cover_fill(img, (size[0] // 4, size[1] // 4)).filter(ImageFilter.GaussianBlur(12))
    bg = small.resize(size, Image.LANCZOS)
    return Image.blend(bg, Image.new("RGB", size, (0, 0, 0)), cfg.blur_dim)


def render(slide: Image.Image, profile: Profile, size: tuple[int, int] = SIZE) -> Image.Image:
    """피드 한 장을 스토리 한 장(RGB, size)으로."""
    cfg, visual = profile.story, profile.visual
    w, h = size
    k = w / BASE_WIDTH
    slide = slide.convert("RGB")
    out = _background(slide, cfg, size).convert("RGBA")

    cw = round(w * cfg.card_width)
    ch = round(slide.height * cw / slide.width)
    card = slide.resize((cw, ch), Image.LANCZOS)
    x0, y0 = (w - cw) // 2, round(h * cfg.card_center - ch / 2)
    mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw - 1, ch - 1), round(cfg.card_radius * k), 255)
    if cfg.background == "blur":
        # 사진 아래 은은한 그림자
        shade = Image.new("RGBA", size, (0, 0, 0, 0))
        blob = Image.new("L", size, 0)
        blob.paste(mask, (x0, y0 + round(10 * k)))
        shade.putalpha(blob.filter(ImageFilter.GaussianBlur(24 * k)).point(lambda a: a * 0.45))
        out = Image.alpha_composite(out, shade)
    out.paste(card, (x0, y0), mask)

    items = []

    def centered(text: str, font_name: str, px: int, baseline: float) -> None:
        font = load_font(font_name, round(px * k))
        items.append((((w - font.getlength(text)) / 2, baseline), text, font, cfg.text_color))

    gap = cfg.text_gap * k
    if cfg.heading:
        centered(cfg.heading, visual.font, cfg.heading_size, y0 - gap)
    y = y0 + ch + gap
    if cfg.cta:
        y += cfg.cta_size * k * 0.8
        centered(cfg.cta, visual.font, cfg.cta_size, y)
        y += cfg.cta_size * k * 0.5
    if cfg.show_logo and visual.logo_text:
        y += cfg.logo_size * k * 1.4
        centered(visual.logo_text, visual.logo_font or visual.font, cfg.logo_size, y)
    text = _text_layer(size, items, cfg.text_shadow, blur=8 * k)
    return Image.alpha_composite(out, text).convert("RGB")


def make(profile: Profile, slides: list[Path], out_dir: Path) -> list[Path]:
    """피드 앞 story.slides 장을 스토리 JPEG 로 저장한다. 표지처럼 AI 합성 표시를 넣는 장이면
    스토리에도 같은 표시(IPTC)를 넣는다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, src in enumerate(slides[: profile.story.slides], 1):
        with Image.open(src) as im:
            story = render(im, profile)
        dst = out_dir / f"{i:02d}_story.jpg"
        opts = ai_label.save_options(profile.ai_label, ai_label.slide_kind(src))
        story.save(dst, "JPEG", quality=92, **opts)
        paths.append(dst)
    return paths


def due_at(profile: Profile, day: date) -> datetime:
    """delay_minutes 가 있을 때 schedule 이 스토리를 올리는 시각 (계정 시간대)."""
    s = profile.schedule
    tz = ZoneInfo(s.timezone)
    start = datetime.combine(day, datetime.min.time(), tz)
    return start + timedelta(minutes=minutes(s.publish_at) + profile.story.delay_minutes)


def post_now(profile: Profile, day: date, now: datetime | None = None) -> bool:
    """게시 job 이 피드 직후 스토리까지 올릴지.
    곧바로 올리는 설정이거나 schedule 칸이 이미 지났으면 올린다."""
    cfg = profile.story
    if not cfg.enabled:
        return False
    if cfg.delay_minutes == 0 or not profile.schedule.enabled:
        return True
    return (now or datetime.now(ZoneInfo(profile.schedule.timezone))) >= due_at(profile, day)


def post(profile: Profile, out: Path, ig: Instagram, host, keep: bool = False) -> list[str]:
    """결과 폴더(out)의 슬라이드로 스토리를 만들어 올리고 story.json 을 쓴다. 스토리 id 목록."""
    manifest = notify.load_manifest(out)
    paths = make(profile, notify.slide_paths(manifest, out), out / "story")
    uploaded = host.upload(paths, f"{profile.id}/{out.name}-story")
    ids = []
    try:
        for u in uploaded:
            ids.append(ig.publish_story(u.url))
    finally:
        if not keep:
            try:
                host.delete(uploaded)
            except (RuntimeError, httpx.HTTPError) as e:
                print(f"경고: 스토리 사진을 지우지 못했습니다: {e}", file=sys.stderr)
        if ids:
            (out / RECORD).write_text(
                StoryRecord(
                    account=profile.id,
                    date=manifest.date,
                    run=out.name,
                    media_ids=ids,
                    posted_at=int(time.time()),  # type: ignore[arg-type]
                ).model_dump_json(indent=2),
                encoding="utf-8",
            )
    return ids


def notice(profile: Profile, ids: list[str], error: str | None = None) -> str:
    """게시 알림에 덧붙일 한 줄."""
    if error:
        return f"\n스토리는 올리지 못했어요: {error}"
    text = f"\n스토리 {len(ids)}장도 올렸어요"
    if profile.story.sticker_hint:
        text += "\n링크 스티커가 필요하면 인스타 앱에서 방금 올린 스토리에 붙여 주세요"
    return text


def published_run(results: Path, account: str, day: date) -> Path | None:
    """그날 게시한 결과 폴더 (published.json 이 있는 것)."""
    for d in sorted(results.glob(f"{day}-run*"), reverse=True):
        if not re.fullmatch(rf"{day}-run\d+", d.name) or not (d / "published.json").exists():
            continue
        rec = PublishRecord.model_validate_json((d / "published.json").read_text(encoding="utf-8"))
        if rec.account == account:
            return d
    return None


def run_post(
    account: str,
    day: date,
    results: Path,
    ig: Instagram | None = None,
    host=None,
    tg: notify.Telegram | None = None,
) -> int:
    """schedule 이 부르는 스토리 올리기 (delay_minutes > 0). 0 올림·할 것 없음, 1 오류."""
    profile = load_profile(account)
    tag = f"[{account}] {day}"
    if not profile.story.enabled:
        print(f"{tag} 스토리가 꺼져 있습니다 (profile story.enabled)")
        return 0
    out = published_run(results, account, day)
    if out is None:
        print(f"{tag} 게시한 결과가 없어 스토리를 올리지 않습니다")
        return 0
    if (out / RECORD).exists():
        print(f"{tag} 스토리를 이미 올렸습니다 ({out.name}/{RECORD})")
        return 0
    cfg = notify.publish_config()
    host_cfg = cfg.get("image_host", {})
    if ig is None:
        ig = instagram_from_env(profile, cfg.get("instagram", {}))
    if host is None:
        from tools.media_host import GitHubHost

        host = GitHubHost.from_config(host_cfg)
    try:
        ids = post(profile, out, ig, host, keep=host_cfg.get("keep", False))
    except (IGError, RuntimeError, ValueError, OSError, httpx.HTTPError) as e:
        print(f"{tag} 스토리 실패: {e}", file=sys.stderr)
        if tg is not None:
            tg.send(f"{tag} 스토리를 올리지 못했어요: {e}")
        return 1
    print(f"{tag} 스토리 {len(ids)}장 올림")
    if tg is not None:
        tg.send(f"{tag}" + notice(profile, ids))
    return 0


def instagram_from_env(profile: Profile, ig_cfg: dict) -> Instagram:
    """Actions 는 계정별 Secret 을 IG_USER_ID, IG_TOKEN 으로 넘긴다 (로컬은 Secret 이름도 됨)."""
    env = os.environ.get
    return Instagram(
        env("IG_USER_ID") or env(profile.instagram.user_id_secret, ""),
        env("IG_TOKEN") or env(profile.instagram.token_secret, ""),
        ig_cfg.get("api_base", "https://graph.instagram.com/v23.0"),
        wait_sec=ig_cfg.get("wait_sec", 5),
        wait_tries=ig_cfg.get("wait_tries", 36),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="스토리 이미지만 만들기")
    r.add_argument("--account", required=True)
    r.add_argument("--slides", nargs="+", type=Path, required=True, help="피드 슬라이드 (순서대로)")
    r.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("post", help="그날 게시물의 스토리 올리기 (공개 작업)")
    p.add_argument("--account", required=True)
    p.add_argument("--results", type=Path, required=True, help="daily-results 브랜치를 꺼낸 폴더")
    p.add_argument("--date", help="게시 날짜 (기본: 계정 시간대 오늘)")
    args = ap.parse_args(argv)

    profile = load_profile(args.account)
    if args.cmd == "render":
        for path in make(profile, args.slides, args.out):
            print(path)
        return 0
    tz = ZoneInfo(profile.schedule.timezone)
    day = date.fromisoformat(args.date) if args.date else datetime.now(tz).date()
    tg = None
    try:
        tg = notify.Telegram.from_env()
    except ValueError:
        print("텔레그램 설정이 없어 알림 없이 진행합니다", file=sys.stderr)
    try:
        return run_post(args.account, day, args.results, tg=tg)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[{args.account}] {day} 스토리 실패: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
