r"""하루 게시물을 인스타에 올리는 게시 job (8단계). Claude 는 부르지 않는다 (허용 목록에서 막힘).

1. 결과 폴더 고르기: daily-results 브랜치의 <날짜>-run<번호>/ 중
   이 계정의, 미리보기를 보낸 가장 최근 것
2. 게시 전 검사: 사진 수·비율·용량, 캡션 길이, 해시태그 수, AI 합성 고지, 광고 고지(manual_link)
3. 승인 확인: 텔레그램 답장 /ok, /skip (profile approval)
   - required: /ok 가 있어야 게시
   - optional: /skip 이 없으면 게시. 단 검수에서 사람 확인(needs_human)이 붙은 날은 /ok 가 있어야 함
   - 마지막 답장이 다시 만들 /fix 면 고친 미리보기를 기다림 (15단계 tools.feedback)
4. 프로필 ai_label 이 켜져 있으면 표지 등에 AI 합성 표시(IPTC)가 없을 때 넣기 (tools.ai_label)
   사진을 공개 저장 위치에 올리고(tools.media_host) 인스타에 게시(tools.ig_publish), 사진 지우기
5. 게시 기록(data/<계정>/posts.json)과 결과 폴더 published.json 을 쓰고 텔레그램으로 알림
6. 릴스 (19단계, profile reels): schedule.reels_at 이 있으면 그 시각에 --reels 로 따로,
   없으면 캐러셀 바로 뒤에 같은 캡션으로 올린다. 캐러셀 /ok 가 릴스 승인을 겸한다

종료 코드: 0 게시·건너뜀·이미 게시됨, 3 아직 승인 없음(게시 안 함), 1 오류

사용법:
  python -m tools.publish --account women --date 2026-09-27 --results <daily-results 폴더>
      [--run 2026-09-27-run12] [--dry-run] [--force] [--allow-sandbox] [--scheduled] [--quiet-wait]
  python -m tools.publish --account women --date 2026-10-01 --results <폴더> --reels [--dry-run]
환경 변수: IG_TOKEN, IG_USER_ID (또는 프로필 instagram 의 Secret 이름), TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID, MEDIA_REPO_TOKEN
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from PIL import Image

from core.models import (
    Manifest,
    PostLink,
    PostRecord,
    PreviewRecord,
    PublishRecord,
    ShareRequest,
)
from core.profile import Profile, load_profile
from core.store import Store
from tools import ai_label, feedback, notify, share
from tools.ig_publish import IGError, Instagram
from tools.media_host import GitHubHost

WAITING = 3
MAX_BYTES = 8 * 1024 * 1024  # 인스타 사진 최대 8MB
RATIO = (0.8, 1.91)  # 폭/높이 (4:5 ~ 1.91:1)


class Blocked(Exception):
    """게시하면 안 되는 상태 (오류로 알림)."""


def find_run(results: Path, account: str, day: date, run: str | None = None) -> Path:
    if run:
        path = results / run
        if not (path / "manifest.json").exists():
            raise FileNotFoundError(f"결과 폴더에 manifest.json 이 없습니다: {path}")
        return path
    found: list[tuple[bool, int, Path]] = []
    for d in results.glob(f"{day}-run*"):
        m = re.fullmatch(rf"{day}-run(\d+)", d.name)
        if not m or not (d / "manifest.json").exists():
            continue
        if notify.load_manifest(d).account != account:
            continue
        found.append(((d / "telegram.json").exists(), int(m.group(1)), d))
    if not found:
        raise FileNotFoundError(f"{account} {day} 결과가 없습니다 ({results})")
    return max(found)[2]  # 미리보기를 보낸 것 중 가장 최근, 없으면 가장 최근


def made_with(out: Path) -> dict:
    """daily 워크플로가 남긴 run.json (run, account, sandbox). 없으면 빈 dict."""
    path = out / "run.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def guard(manifest: Manifest, profile: Profile, images: list[Path], cfg: dict) -> list[str]:
    """게시 전 규칙 검사. 문제 목록 (비어 있으면 통과)."""
    errors: list[str] = []
    c = profile.copy_rules
    if manifest.account != profile.id:
        errors.append(f"계정이 다릅니다 ({manifest.account} ≠ {profile.id})")
    if manifest.monetize != profile.monetize:
        errors.append(
            f"만든 뒤 수익 방식이 바뀌었습니다 ({manifest.monetize} → {profile.monetize}). "
            "다시 만드세요"
        )
    if not 1 <= len(images) <= 10:
        errors.append(f"사진은 1~10장이어야 합니다 (지금 {len(images)}장)")
    for p in images:
        if p.stat().st_size > MAX_BYTES:
            errors.append(f"{p.name}: 8MB 를 넘습니다")
        with Image.open(p) as im:
            if im.format != "JPEG":
                errors.append(f"{p.name}: JPEG 가 아닙니다 ({im.format})")
            ratio = im.width / im.height
            if not RATIO[0] - 0.005 <= ratio <= RATIO[1]:
                errors.append(f"{p.name}: 비율 {im.width}x{im.height} 은 인스타에서 잘립니다")
    text = notify.full_caption(manifest)
    if len(text) > cfg.get("caption_max", 2200):
        errors.append(f"캡션이 {len(text)}자로 {cfg.get('caption_max', 2200)}자를 넘습니다")
    if len(manifest.hashtags) > cfg.get("hashtags_max", 30):
        errors.append(f"해시태그가 {len(manifest.hashtags)}개로 너무 많습니다")
    if c.ai_notice not in manifest.caption:
        errors.append("캡션에 AI 합성 고지가 없습니다")
    if profile.monetize == "manual_link":
        if not manifest.caption.lstrip().startswith(c.ad_notice):
            errors.append("manual_link 인데 캡션 맨 앞에 광고 고지가 없습니다")
        if not manifest.hashtags or manifest.hashtags[0] != c.ad_hashtag:
            errors.append(f"manual_link 인데 첫 해시태그가 {c.ad_hashtag} 가 아닙니다")
    return errors


def approval(
    profile: Profile, manifest: Manifest, record: PreviewRecord | None, decided: str | None
) -> tuple[str, str]:
    """(ok·auto 게시 / skip 건너뜀 / wait 기다림, 설명)."""
    if record is None:
        return "wait", "미리보기를 보낸 기록이 없어 승인을 받을 수 없습니다"
    if decided == "skip":
        return "skip", "/skip 으로 건너뛰었습니다"
    if decided == "ok":
        return "ok", "/ok 로 승인했습니다"
    if profile.approval == "optional" and not manifest.needs_human:
        return "auto", "승인 생략 설정이라 자동 게시합니다"
    why = "사람 확인이 필요한 날이라" if manifest.needs_human else "승인 필수 설정이라"
    return "wait", f"{why} /ok 를 기다립니다"


def post_links(manifest: Manifest) -> list[PostLink]:
    """게시물 상품 링크 (댓글 DM 용). 짧은 이름은 캡션 제품 목록과 같은 규칙으로 다듬는다."""
    from tools.feed import clean_name

    return [
        PostLink(
            product=p.id,
            category=p.category,
            shop=p.shop,
            name=clean_name(p.name),
            url=str(p.url),
            image=str(p.image_urls[0]),
        )
        for p in manifest.plan.items
    ]


def run(
    account: str,
    day: date,
    results: Path,
    run_name: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    allow_sandbox: bool = False,
    store: Store | None = None,
    tg: notify.Telegram | None = None,
    ig: Instagram | None = None,
    host: GitHubHost | None = None,
    scheduled: bool = False,
    notify_wait: bool = True,
) -> int:
    profile = load_profile(account)
    store = store or Store(account)
    cfg = notify.publish_config()
    tag = f"[{account}] {day}"

    if any(p.date == day for p in store.posts()):
        print(f"{tag} 이미 게시했습니다 (posts.json)")
        return 0
    try:
        out = find_run(results, account, day, run_name)
    except FileNotFoundError:
        if not scheduled:
            raise
        # 자동 실행: 그날 고르기를 건너뛰었거나 아직 만들지 않은 날 (만들기 실패는 daily 가 알림)
        print(f"{tag} 게시할 결과가 없습니다")
        return 0
    if (out / "published.json").exists():
        print(f"{tag} 이미 게시했습니다 ({out.name}/published.json)")
        return 0
    manifest = notify.load_manifest(out)
    if manifest.date != day:
        raise Blocked(f"결과 날짜가 다릅니다 ({manifest.date})")
    images = notify.slide_paths(manifest, out)
    errors = guard(manifest, profile, images, cfg.get("instagram", {}))
    if errors:
        raise Blocked("게시 전 검사 실패:\n" + "\n".join(f"- {e}" for e in errors))

    record = notify.load_record(out)
    if not allow_sandbox:
        if (record and record.sandbox) or made_with(out).get("sandbox", True):
            raise Blocked(
                "샌드박스(워터마크) 결과이거나 만든 방식(run.json)을 알 수 없어 게시하지 않습니다"
            )
    if force:
        how, why = "force", "승인 확인 없이 게시합니다 (--force)"
    else:
        if tg is None and not dry_run:
            tg = notify.Telegram.from_env()
        decided = None
        fixing = None
        if record and tg is not None:
            updates = tg.updates()
            # 승인 마감(schedule.ok_deadline) 뒤 /ok 는 빼고, 텔레그램에서 사라졌으면 매시 남긴 기록
            decided = notify.approval_now(out, record, profile, updates, tg.chat_id)
            if decided != "skip":
                # /fix 뒤에는 고친 미리보기를 기다린다 (15단계, /fix 뒤 /ok 면 지금 것으로 게시)
                fixing = feedback.waiting(updates, record, tg.chat_id, out, profile, store)
        how, why = approval(profile, manifest, record, decided)
        if fixing and how != "skip":
            how, why = "wait", fixing
        dl = notify.ok_deadline(profile, day)
        if how == "wait" and dl is not None and datetime.now(UTC) > dl:
            # 승인 마감이 지남: 게시하지 않는다 (마감 알림은 schedule remind 가 한 번 보냄)
            print(f"{tag} {out.name}: 승인 마감 {dl:%m/%d %H:%M} 이 지나 게시하지 않습니다 ({why})")
            return 0
    print(f"{tag} {out.name}: {why}")
    if how == "skip":
        return 0
    if how == "wait":
        if tg is not None and not dry_run and notify_wait:
            tg.send(f"{tag} 아직 게시하지 않았어요. {why}")
        return WAITING

    if dry_run:
        n = len(notify.full_caption(manifest))
        print(f"(시험) 사진 {len(images)}장, 캡션 {n}자를 게시할 예정")
        for p in images:
            marked = ai_label.labeled(profile.ai_label, ai_label.slide_kind(p))
            mark = " (AI 합성 표시)" if marked else ""
            print(f"  {p.name}{mark}")
        return 0

    ig = ig or make_instagram(profile, cfg)
    host_cfg = cfg.get("image_host", {})
    host = host or GitHubHost.from_config(host_cfg)
    limit = ig.publish_limit()
    if limit and limit[0] >= limit[1]:
        raise Blocked(f"인스타 24시간 게시 한도에 닿았습니다 ({limit[0]}/{limit[1]})")

    for name in ai_label.ensure(profile.ai_label, images):
        print(f"{tag} {name}: AI 합성 표시(IPTC)를 넣었습니다")
    uploaded = host.upload(images, f"{account}/{out.name}")
    try:
        media_id = ig.publish([u.url for u in uploaded], post_caption(profile, manifest))
    finally:
        if not host_cfg.get("keep", False):
            try:
                host.delete(uploaded)
            except (RuntimeError, httpx.HTTPError) as e:
                print(f"경고: 게시용 사진을 지우지 못했습니다: {e}", file=sys.stderr)
    link = ig.permalink(media_id)
    tagged = ""
    if profile.comment_tags.enabled:
        # 내 댓글 + 대댓글 해시태그. 실패해도 게시는 끝났으니 알림만
        try:
            n = comment_hashtags(ig, media_id, profile, manifest)
            print(f"{tag} 댓글 해시태그 {n}개")
        except (IGError, httpx.HTTPError) as e:
            print(f"경고: 댓글 해시태그를 달지 못했습니다: {e}", file=sys.stderr)
            tagged = f"\n댓글 해시태그를 달지 못했어요: {e}"

    now = int(time.time())
    (out / "published.json").write_text(
        PublishRecord(
            account=account,
            date=day,
            run=out.name,
            media_id=media_id,
            permalink=link,
            images=len(images),
            decision=how,
            published_at=now,  # type: ignore[arg-type]
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    links = post_links(manifest)
    ask = None
    if share.enabled(profile):
        # 게시 전에 답장으로 붙여 둔 공유리워드 링크가 있으면 첫 상품 페이지부터 쓴다
        # 미리보기와 /ok 뒤 보낸 상품 링크 목록(share.send_after_ok)에 단 답장을 모두 읽는다
        ask = ShareRequest(
            sent_at=record.sent_at if record else now,
            message_ids=[*(record.message_ids if record else []), *share.asked_ids(out)],
        )
        if tg is not None and ask.message_ids:
            share.read_links(ask, tg.updates(), tg.chat_id, len(links))
    posts = store.posts()
    posts.append(
        PostRecord(
            date=day,
            title=" ".join(manifest.title_lines),
            theme=manifest.plan.theme.title_hint,
            products=[p.id for p in manifest.plan.items],
            permalink=link,
            media_id=media_id,
            links=links,
            share=ask,
        )
    )
    store.save_posts(sorted(posts, key=lambda p: p.date))
    print(f"{tag} 게시 완료: {link or media_id}")
    if profile.reels.enabled and profile.schedule.reels_at is None:
        # 릴스 시각이 없으면 캐러셀 바로 뒤에 올린다. 실패해도 캐러셀은 올라갔으니 알림만
        try:
            _, reel, reel_tags = publish_reel(profile, manifest, out, ig, cfg, store, host)
            tagged += f"\n릴스도 올렸어요: {reel or ''}{reel_tags}"
        except (Blocked, IGError, RuntimeError, ValueError, httpx.HTTPError) as e:
            print(f"경고: 릴스를 올리지 못했습니다: {e}", file=sys.stderr)
            tagged += f"\n릴스를 올리지 못했어요: {e}"
    if profile.link_page.enabled:
        # 상품 페이지 (ManyChat DM 이 가리키는 곳). 실패해도 게시는 끝났으니 경고만
        from tools import link_page

        try:
            print(f"{tag} 상품 페이지: {link_page.push(profile, store, host)}")
        except (RuntimeError, ValueError, httpx.HTTPError) as e:
            print(f"경고: 상품 페이지를 올리지 못했습니다: {e}", file=sys.stderr)
            if tg is not None:
                tg.send(f"{tag} 게시는 했지만 상품 페이지를 올리지 못했어요: {e}")
    if tg is not None:
        done = (f"{tag} 게시했어요 ✓\n{link or ''}".strip() + tagged).strip()
        if ask is None and links and share.sends_list(profile) and not share.asked_ids(out):
            # 링크를 직접 만들 때 쓰는 목록 (/ok 뒤에 아직 못 보냈으면 여기서)
            tg.send(share.request_text(done, links, ask_reply=False))
        elif ask is not None and links:
            # 게시 알림이 공유리워드 링크 요청을 겸한다. 답장은 schedule 이 읽어 상품 페이지에 반영
            mid = tg.send(share.request_text(done, links, ask.links))
            posts = store.posts()
            for p in posts:
                if p.date == day and p.share is not None:
                    p.share.message_ids.append(mid)
            store.save_posts(posts)
        else:
            tg.send(done)
    return 0


def make_instagram(profile: Profile, cfg: dict) -> Instagram:
    ig_cfg = cfg.get("instagram", {})
    # Actions 는 계정별 Secret 을 IG_USER_ID, IG_TOKEN 으로 넘긴다 (로컬은 Secret 이름도 됨)
    env = os.environ.get
    return Instagram(
        env("IG_USER_ID") or env(profile.instagram.user_id_secret, ""),
        env("IG_TOKEN") or env(profile.instagram.token_secret, ""),
        ig_cfg.get("api_base", "https://graph.instagram.com/v23.0"),
        wait_sec=ig_cfg.get("wait_sec", 5),
        wait_tries=ig_cfg.get("wait_tries", 36),
    )


# ---------- 릴스 (19단계) ----------

REEL_SECONDS = (3, 90)  # 인스타 릴스 길이 (초)
REEL_MAX_BYTES = 300 * 1024 * 1024


def reel_guard(manifest: Manifest, video: Path | None) -> list[str]:
    if video is None:
        return ["릴스 영상이 없습니다 (daily 의 릴스 만들기 단계 확인)"]
    errors = []
    sec = manifest.reels.duration_sec if manifest.reels else None
    if sec is None or not REEL_SECONDS[0] <= sec <= REEL_SECONDS[1]:
        errors.append(f"릴스 길이 {sec}초는 {REEL_SECONDS[0]}~{REEL_SECONDS[1]}초여야 합니다")
    if video.stat().st_size > REEL_MAX_BYTES:
        errors.append("릴스 영상이 300MB 를 넘습니다")
    return errors


def publish_reel(
    profile: Profile,
    manifest: Manifest,
    out: Path,
    ig: Instagram,
    cfg: dict,
    store: Store,
    host: GitHubHost | None = None,
) -> tuple[str, str | None, str]:
    """릴스를 올리고 (게시물 id, 주소, 댓글 해시태그 오류) 를 돌려준다.
    published.json 과 posts.json 에 릴스를 적는다."""
    from tools import reels

    video = reels.video_path(manifest, out)
    errors = reel_guard(manifest, video)
    if errors:
        raise Blocked("릴스 게시 전 검사 실패:\n" + "\n".join(f"- {e}" for e in errors))
    assert video is not None
    ig_cfg = cfg.get("instagram", {})
    caption = post_caption(profile, manifest)
    kw = dict(share_to_feed=profile.reels.share_to_feed, wait_tries=ig_cfg.get("reels_wait_tries"))
    if ig_cfg.get("reels_upload", "file") == "url":
        host_cfg = cfg.get("image_host", {})
        host = host or GitHubHost.from_config(host_cfg)
        uploaded = host.upload([video], f"{profile.id}/{out.name}-reel")
        try:
            media_id = ig.publish_reel(caption, video_url=uploaded[0].url, **kw)
        finally:
            if not host_cfg.get("keep", False):
                try:
                    host.delete(uploaded)
                except (RuntimeError, httpx.HTTPError) as e:
                    print(f"경고: 게시용 영상을 지우지 못했습니다: {e}", file=sys.stderr)
    else:
        media_id = ig.publish_reel(caption, video=video, **kw)
    link = ig.permalink(media_id)
    tag_error = ""
    if profile.comment_tags.enabled:
        try:
            comment_hashtags(ig, media_id, profile, manifest)
        except (IGError, httpx.HTTPError) as e:
            print(f"경고: 릴스 댓글 해시태그를 달지 못했습니다: {e}", file=sys.stderr)
            tag_error = f"\n릴스 댓글 해시태그를 달지 못했어요: {e}"
    done = out / "published.json"
    rec = PublishRecord.model_validate_json(done.read_text(encoding="utf-8"))
    rec.reel_media_id, rec.reel_permalink = media_id, link
    rec.reel_published_at = int(time.time())
    done.write_text(rec.model_dump_json(indent=2), encoding="utf-8")
    posts = store.posts()
    for p in posts:
        if p.date == manifest.date:
            p.reel_media_id, p.reel_permalink = media_id, link
    store.save_posts(posts)
    return media_id, link, tag_error


def run_reels(
    account: str,
    day: date,
    results: Path,
    run_name: str | None = None,
    dry_run: bool = False,
    store: Store | None = None,
    tg: notify.Telegram | None = None,
    ig: Instagram | None = None,
    host: GitHubHost | None = None,
) -> int:
    """schedule.reels_at 에 릴스만 올린다. 그날 캐러셀을 게시한 결과 폴더의 영상을 쓴다
    (캐러셀 /ok 가 릴스 승인을 겸한다). 캐러셀을 안 올린 날은 조용히 끝낸다."""
    profile = load_profile(account)
    store = store or Store(account)
    tag = f"[{account}] {day}"
    if not profile.reels.enabled:
        print(f"{tag} 릴스가 꺼져 있습니다 (profile reels.enabled)")
        return 0
    try:
        out = find_run(results, account, day, run_name)
    except FileNotFoundError:
        print(f"{tag} 결과가 없어 릴스를 올리지 않습니다")
        return 0
    done = out / "published.json"
    if not done.exists():
        print(f"{tag} 캐러셀을 게시하지 않은 날이라 릴스도 올리지 않습니다 ({out.name})")
        return 0
    rec = PublishRecord.model_validate_json(done.read_text(encoding="utf-8"))
    if rec.reel_media_id:
        print(f"{tag} 릴스를 이미 올렸습니다 ({rec.reel_permalink or rec.reel_media_id})")
        return 0
    manifest = notify.load_manifest(out)
    from tools import reels

    errors = reel_guard(manifest, reels.video_path(manifest, out))
    if errors:
        raise Blocked("릴스 게시 전 검사 실패:\n" + "\n".join(f"- {e}" for e in errors))
    if dry_run:
        print(f"(시험) {out.name} 릴스 {manifest.reels.duration_sec:g}초를 게시할 예정")  # type: ignore[union-attr]
        return 0
    cfg = notify.publish_config()
    ig = ig or make_instagram(profile, cfg)
    limit = ig.publish_limit()
    if limit and limit[0] >= limit[1]:
        raise Blocked(f"인스타 24시간 게시 한도에 닿았습니다 ({limit[0]}/{limit[1]})")
    _, link, tag_error = publish_reel(profile, manifest, out, ig, cfg, store, host)
    print(f"{tag} 릴스 게시 완료: {link}")
    if tg is not None:
        tg.send(f"{tag} 릴스도 올렸어요 ✓\n{link or ''}".strip() + tag_error)
    return 0


def comment_tag_list(profile: Profile, manifest: Manifest) -> list[str]:
    """대댓글에 달 해시태그: 코디 해시태그(기획+기본) 뒤에 소통용, 겹치지 않게 최대 max 개.
    넘치면 소통용을 남기고 코디 해시태그를 줄인다."""
    ct = profile.comment_tags
    follow = [t for t in dict.fromkeys(ct.follow_hashtags)][: ct.max]
    main = [t for t in dict.fromkeys(manifest.hashtags) if t not in follow]
    return main[: ct.max - len(follow)] + follow


def post_caption(profile: Profile, manifest: Manifest) -> str:
    """인스타에 올릴 캡션. 댓글 해시태그를 켜면 캡션에는 해시태그를 넣지 않는다.
    광고 해시태그(#광고)는 고지라서 캡션에 남긴다."""
    if not profile.comment_tags.enabled:
        return notify.full_caption(manifest)
    c = profile.copy_rules
    text = manifest.caption.rstrip()
    if profile.monetize == "manual_link" and c.ad_hashtag in manifest.hashtags:
        text += "\n\n" + c.ad_hashtag
    return text


def comment_hashtags(ig: Instagram, media_id: str, profile: Profile, manifest: Manifest) -> int:
    ad = profile.copy_rules.ad_hashtag
    tags = [t for t in comment_tag_list(profile, manifest) if t != ad]
    if not tags:
        return 0
    comment_id = ig.comment(media_id, profile.comment_tags.comment)
    ig.reply_comment(comment_id, " ".join(tags))
    return len(tags)


def today(profile: Profile) -> date:
    return datetime.now(ZoneInfo(profile.schedule.timezone)).date()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "--account", default=os.environ.get("ACCOUNT"), required="ACCOUNT" not in os.environ
    )
    ap.add_argument("--date", help="게시 날짜 (기본: 계정 시간대 오늘)")
    ap.add_argument("--results", type=Path, required=True, help="daily-results 브랜치를 꺼낸 폴더")
    ap.add_argument("--run", help="게시할 결과 폴더 이름 (기본: 미리보기를 보낸 가장 최근 것)")
    ap.add_argument("--dry-run", action="store_true", help="검사와 승인 확인까지만")
    ap.add_argument("--force", action="store_true", help="텔레그램 승인 없이 게시")
    ap.add_argument("--allow-sandbox", action="store_true", help="샌드박스 결과도 게시 (시험용)")
    ap.add_argument("--scheduled", action="store_true", help="자동 실행: 결과가 없으면 조용히 끝냄")
    ap.add_argument("--quiet-wait", action="store_true", help="승인 대기 알림을 보내지 않음")
    ap.add_argument(
        "--reels", action="store_true", help="릴스만 올림 (그날 캐러셀을 게시한 결과의 영상)"
    )
    args = ap.parse_args(argv)

    profile = load_profile(args.account)
    day = date.fromisoformat(args.date) if args.date else today(profile)
    tg = None
    try:
        tg = notify.Telegram.from_env()
    except ValueError:
        print("텔레그램 설정이 없어 승인·알림 없이 진행합니다", file=sys.stderr)
    try:
        if args.reels:
            return run_reels(args.account, day, args.results, args.run, args.dry_run, tg=tg)
        return run(
            args.account,
            day,
            args.results,
            args.run,
            args.dry_run,
            args.force,
            args.allow_sandbox,
            tg=tg,
            scheduled=args.scheduled,
            notify_wait=not args.quiet_wait,
        )
    except (Blocked, IGError, ValueError, FileNotFoundError, RuntimeError, httpx.HTTPError) as e:
        what = "릴스 게시" if args.reels else "게시"
        msg = f"[{args.account}] {day} {what} 실패: {e}"
        if isinstance(e, IGError) and e.token_problem:
            msg += "\n인스타 토큰이 만료됐거나 취소됐어요. 새 토큰을 Secret 에 다시 등록해 주세요."
        print(msg, file=sys.stderr)
        if tg is not None and not args.dry_run:
            try:
                tg.send(msg)
            except (RuntimeError, httpx.HTTPError):
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
