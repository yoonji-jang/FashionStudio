r"""표지 벌 바꾸기 (2026-10-12 사용자 "6벌 콜라주가 아닌 코디 중 하나로").
Claude·이미지 AI 없이 코드만.

여러 벌 게시물 표지는 한 벌 코디컷 + 제목이다 (profile looks.cover_collage.enabled false).
어느 벌로 할지는 daily-feed 가 plan.yaml `cover_look` 에 고르고,
미리보기를 보낸 뒤 텔레그램으로 바꾼다.
- "/ok 3", "/ok 표지 3"   3번 벌을 표지로 하고 승인
- "/cover 3", "표지 3번"  승인 없이 표지만 3번 벌로 (새 표지 사진을 받아 보고 /ok)
- "/order 4 2 3 6 1 5", "순서 4 2 3 6 1 5"  벌 순서를 그 LOOK 번호 순서로, 첫 번호가 표지
  (2026-10-20). 번호를 일부만 적으면 적지 않은 벌은 게시물에서 뺀다 (2026-10-22 사용자,
  profile looks.min 벌까지. 뺀 벌 폴더는 looks_dropped/ 에 둔다)
번호는 미리보기의 LOOK 번호. 승인 마감(schedule.ok_deadline)까지 온 마지막 글을 쓴다.

바꿀 때는 결과 폴더의 표지 장(slides/01_cover.jpg, 릴스용 slides/tall/01_cover.jpg)과 미리보기
(preview.jpg)를 그 벌 코디컷으로 다시 만들고 plan.yaml·feed.yaml 에 남긴 뒤,
새 표지 사진을 텔레그램으로 보낸다. 릴스 영상은 첫 장이 바뀌므로 cover.json 의 reel_stale 을 보고
릴스 게시 때 다시 만든다.
같은 글은 한 번만 처리한다 (cover.json·order.json 의 update_id).
순서를 바꿀 때는 벌 폴더(looks/<n>)와 기획의 벌 순서를 바꾸고 슬라이드·캡션·manifest 를 다시 만든다
(코드만, AI 없음). 같은 글의 /cover 는 순서 바꾸기보다 앞이면 버린다 (첫 번호가 표지).

schedule 매시 확인(links 칸)과 게시(tools.publish) 직전에 부른다.

사용법 (결과 폴더에서 바로 바꿔 보기, 텔레그램 없이):
  python -m tools.cover_look apply out/2026-10-13 3
  python -m tools.cover_look order out/2026-10-13 4 2 3 6 1 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import yaml

from core.models import DailyPlan, FeedSpec, PreviewRecord
from core.profile import Profile, load_profile
from tools import feed, looks, notify

RECORD = "cover.json"  # 처리한 표지 바꾸기 (결과 폴더)
ORDER_RECORD = "order.json"  # 처리한 벌 순서 바꾸기 (결과 폴더)
# 처리한 표지 제목 바꾸기 (결과 폴더). schedule.yml 은 고치면 실행마다 승인 대기에 걸려서
# 이미 커밋하는 picks/** 아래에 둔다 (2026-10-28)
TITLE_RECORD = "picks/title.json"
# 텔레그램으로 보낸 최종본(모든 슬라이드 + 릴스 영상)의 지문 (2026-10-29 사용자가 받아 씀).
# schedule 이 커밋하는 picks/ 아래에 둔다
FINAL_RECORD = "picks/final.json"
DROPPED_DIR = "looks_dropped"  # 순서 바꾸기에서 뺀 벌 폴더


def effective_collage(plan: DailyPlan, profile: Profile) -> bool:
    """이 게시물 표지가 콜라주인지 (tools.looks.finish 와 같은 규칙)."""
    if plan.cover_collage is not None:
        return plan.cover_collage and plan.shot != "model"
    return profile.looks.cover_collage.enabled and plan.shot != "model"


def current(out: Path, profile: Profile) -> int | None:
    """지금 표지로 쓰는 벌 번호 (콜라주면 None)."""
    path = out / "plan.yaml"
    if not path.exists():
        return None
    plan = DailyPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if not plan.looks or effective_collage(plan, profile):
        return None
    return plan.cover_look or 1


def look_number(out: Path, profile: Profile, shown: int) -> int:
    """텔레그램에서 고른 LOOK 번호 → 벌 번호.
    표지 벌을 벌 장에서 빼는 설정(repeat_cover_look 꺼짐)이면 벌 장에 보이는 LOOK 번호
    (표지 벌을 뺀 순서)라서 벌 번호로 바꾼다."""
    if profile.looks.repeat_cover_look:
        return shown
    plan = DailyPlan.model_validate(yaml.safe_load((out / "plan.yaml").read_text(encoding="utf-8")))
    cover = current(out, profile)
    rest = [n for n in range(1, len(plan.looks) + 1) if n != cover]
    if profile.looks.cover_first and cover is not None:
        # 표지가 LOOK 1, 벌 장이 LOOK 2 부터 (2026-10-16)
        rest = [cover, *rest]
    if not 1 <= shown <= len(rest):
        raise ValueError(f"LOOK {shown} 이 없어요 (1~{len(rest)})")
    return rest[shown - 1]


def load_record(out: Path) -> dict:
    path = out / RECORD
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_order(out: Path) -> dict:
    path = out / ORDER_RECORD
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_title(out: Path) -> dict:
    path = out / TITLE_RECORD
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def reel_stale(out: Path) -> bool:
    """표지·벌 순서·제목을 바꾼 뒤 릴스 영상을 아직 다시 만들지 않았는지."""
    return bool(
        load_record(out).get("reel_stale")
        or load_order(out).get("reel_stale")
        or load_title(out).get("reel_stale")
    )


def _write_yaml(data: dict, path: Path) -> None:
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def apply(out: Path, n: int, profile: Profile | None = None) -> list[Path]:
    """결과 폴더 out 의 표지를 n(1부터)번 벌 코디컷으로 다시 만든다. 다시 만든 파일 목록."""
    from tools import plan as plan_tool

    plan = plan_tool.load_plan(out / "plan.yaml")
    profile = profile or load_profile(plan.account)
    if not plan.looks:
        raise ValueError("여러 벌 게시물이 아니라 표지 벌을 바꿀 수 없습니다")
    if not 1 <= n <= len(plan.looks):
        raise ValueError(f"LOOK {n} 이 없어요 (1~{len(plan.looks)})")
    image = looks.chosen_image(looks.look_dir(out, n))
    if image is None or not image.exists():
        raise FileNotFoundError(f"LOOK {n} 코디컷이 결과 폴더에 없습니다")
    if not profile.looks.repeat_cover_look:
        # 표지 벌이 벌 장에서 빠지는 설정 (2026-10-15): 지난 표지 벌이 벌 장으로 돌아오고
        # 새 표지 벌이 빠져서 슬라이드 전체를 다시 만든다 (코드만, AI 없음). 릴스 정보는 그대로 둔다
        reels = notify.load_manifest(out).reels
        data = plan.model_dump(mode="json", exclude_none=True)
        data["cover_look"] = n
        if effective_collage(plan, profile):
            data["cover_collage"] = False
        _write_yaml(data, out / "plan.yaml")
        manifest = looks.finish(out, profile)
        if reels is not None:
            manifest = manifest.model_copy(update={"reels": reels})
            (out / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        slides = notify.slide_paths(manifest, out)
        return [slides[0], out / "preview.jpg"]
    manifest = notify.load_manifest(out)
    slides = notify.slide_paths(manifest, out)
    if not slides or not slides[0].stem.endswith("_cover"):
        raise ValueError("표지 장이 없는 결과입니다")

    spec = FeedSpec.model_validate(yaml.safe_load((out / "feed.yaml").read_text(encoding="utf-8")))
    spec = spec.model_copy(update={"cover": str(image.resolve()), "cover_collage": False})
    made = []
    cover = slides[0]
    feed.save_slide(feed.cover_slide(spec, profile, feed.canvas_size(profile), out), cover, profile)
    made.append(cover)
    tall = cover.parent / feed.TALL_DIR / cover.name
    if tall.exists():
        img = feed.cover_slide(spec, profile, feed.TALL_SIZE, out)
        feed.save_slide(img, tall, profile)
        made.append(tall)
    plan_tool.preview(slides, out / "preview.jpg")
    made.append(out / "preview.jpg")

    _write_yaml(spec.model_dump(mode="json", exclude_none=True), out / "feed.yaml")
    data = plan.model_dump(mode="json", exclude_none=True)
    data["cover_look"] = n
    if effective_collage(plan, profile):
        data["cover_collage"] = False  # 이 게시물만 한 벌 표지로
    _write_yaml(data, out / "plan.yaml")
    return made


def reorder(out: Path, shown: list[int], profile: Profile | None = None) -> list[Path]:
    """벌 순서를 미리보기 LOOK 번호 shown 순서로 바꾸고 첫 벌을 표지로 (코드만, AI 없음).
    다시 만든 표지 장과 미리보기 경로."""
    from tools import plan as plan_tool

    plan = plan_tool.load_plan(out / "plan.yaml")
    profile = profile or load_profile(plan.account)
    if not plan.looks:
        raise ValueError("여러 벌 게시물이 아니라 순서를 바꿀 수 없습니다")
    total = len(plan.looks)
    if len(set(shown)) != len(shown) or not all(1 <= s <= total for s in shown):
        raise ValueError(f"LOOK 1~{total} 중에서 한 번씩만 적어 주세요 (받은 번호: {shown})")
    if len(shown) < min(profile.looks.min, total):
        raise ValueError(
            f"벌은 {profile.looks.min}벌 이상 남겨야 해요 (받은 번호 {len(shown)}개: {shown})"
        )
    # 보이는 LOOK 번호 → 기획의 벌 번호 (표지 벌 때문에 다를 수 있음)
    return _reorder(out, [look_number(out, profile, s) for s in shown], profile)


def _look_keys(out: Path) -> list[tuple[str, ...]]:
    from tools import plan as plan_tool

    return [
        tuple(it.product for it in lk.items) for lk in plan_tool.load_plan(out / "plan.yaml").looks
    ]


def _reorder(out: Path, order: list[int], profile: Profile) -> list[Path]:
    """기획의 벌 번호 order 순서로 벌 폴더·기획을 바꾸고 다시 만든다. 이미 그 순서면 []."""
    from tools import plan as plan_tool

    plan = plan_tool.load_plan(out / "plan.yaml")
    if order == list(range(1, len(plan.looks) + 1)) and current(out, profile) == 1:
        return []
    reels = notify.load_manifest(out).reels
    tmp = out / (looks.LOOKS_DIR + ".order")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    for new, old in enumerate(order, 1):
        src = looks.look_dir(out, old)
        if src.exists():
            src.rename(tmp / str(new))
    # 순서에 없는 벌은 게시물에서 뺀다 (폴더는 looks_dropped/<지난 벌 번호> 로 옮겨 둔다)
    for n in range(1, len(plan.looks) + 1):
        if n not in order and (src := looks.look_dir(out, n)).exists():
            dropped = out / DROPPED_DIR / str(n)
            shutil.rmtree(dropped, ignore_errors=True)
            dropped.parent.mkdir(exist_ok=True)
            src.rename(dropped)
    for d in sorted(tmp.iterdir()):
        d.rename(looks.look_dir(out, int(d.name)))
    tmp.rmdir()
    data = plan.model_dump(mode="json", exclude_none=True)
    data["looks"] = [data["looks"][n - 1] for n in order]
    data["cover_look"] = 1
    if effective_collage(plan, profile):
        data["cover_collage"] = False
    _write_yaml(data, out / "plan.yaml")
    return _refinish(out, profile, reels)


def _refinish(out: Path, profile: Profile, reels=None) -> list[Path]:
    """기획대로 슬라이드·미리보기·manifest 를 다시 만든다 (릴스 설정은 그대로).
    표지 장과 미리보기 경로."""
    manifest = looks.finish(out, profile)
    if reels is not None:
        manifest = manifest.model_copy(update={"reels": reels})
        (out / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    slides = notify.slide_paths(manifest, out)
    return [slides[0], out / "preview.jpg"]


def _half_done(out: Path) -> bool:
    """순서 바꾸기가 기획·벌 폴더만 바꾸고 manifest 를 못 만든 채 멈췄는지
    (기획과 manifest 의 벌이 다름)."""
    return [list(k) for k in _look_keys(out)] != notify.load_manifest(out).plan.looks


def chain(out: Path) -> list[Path]:
    """/fix 로 다시 만든 결과면 지난 결과 폴더들 (fix.json 의 run, 가까운 것부터)."""
    from tools import feedback

    runs: list[Path] = []
    cur = out
    while (fix := feedback.load_fix(cur)) and fix.get("run"):
        prev = out.parent / fix["run"]
        if not prev.is_dir() or prev in runs or prev == out:
            break
        runs.append(prev)
        cur = prev
    return runs


def fix_changed_cover(out: Path) -> bool:
    """/fix 로 다시 만든 결과에서 표지 벌이 지난 기획과 달라졌는지 ("표지를 5번 벌로" 같은 /fix)."""
    from tools import feedback
    from tools import plan as plan_tool

    fix = feedback.load_fix(out)
    if not fix or "cover_look" not in fix or not (out / "plan.yaml").exists():
        return False
    return plan_tool.load_plan(out / "plan.yaml").cover_look != fix["cover_look"]


def requested(
    out: Path, record: PreviewRecord, profile: Profile, updates: list[dict], chat_id: str
) -> tuple[int, int] | None:
    """승인 마감까지 온 마지막 표지 벌 고르기 (update_id, 벌 번호).
    /fix 로 다시 만든 미리보기면 처음 미리보기 뒤에 온 글부터 본다: /fix 와 같이 보낸 표지 번호가
    고친 미리보기에 빠지지 않게."""
    dl = notify.ok_deadline(profile, record.date)
    sent = [r.sent_at for p in chain(out) if (r := notify.load_record(p))]
    since = min([record.sent_at, *sent])
    return notify.find_cover(updates, record, chat_id, int(dl.timestamp()) if dl else None, since)


def requested_order(
    out: Path, record: PreviewRecord, profile: Profile, updates: list[dict], chat_id: str
) -> tuple[int, list[int]] | None:
    """승인 마감까지 온 마지막 벌 순서 바꾸기 (update_id, LOOK 번호 순서).
    requested 와 같은 범위."""
    dl = notify.ok_deadline(profile, record.date)
    sent = [r.sent_at for p in chain(out) if (r := notify.load_record(p))]
    since = min([record.sent_at, *sent])
    return notify.find_order(updates, record, chat_id, int(dl.timestamp()) if dl else None, since)


def check(out: Path, tg, updates: list[dict] | None = None, dry_run: bool = False) -> str:
    """텔레그램에 벌 순서 바꾸기나 표지 벌 고르기가 왔으면 다시 만들고 새 표지를 보낸다. 로그."""
    record = notify.load_record(out)
    if record is None or record.sandbox or tg is None:
        return ""
    if (out / "published.json").exists():
        return ""
    updates = tg.updates() if updates is None else updates
    lines = [
        check_order(out, tg, updates, dry_run),
        check_cover(out, tg, updates, dry_run),
        check_title(out, tg, updates, dry_run),
    ]
    if not dry_run:
        sent = load_final(out)
        if final_key(out) != sent.get("key"):
            # 표지·순서·제목을 바꿨으면 최종본을 통째로 다시 보낸다 (2026-10-29 사용자)
            lines.append(send_final(out, tg, "바뀐 최종본"))
        elif not sent.get("video") and _has_reel(out):
            # 사진만 보내고 영상은 못 만든 최종본: 영상만 마저 보낸다
            lines.append(send_final(out, tg, "최종본 영상", photos=False))
    return "\n".join(x for x in lines if x)


def _has_reel(out: Path) -> bool:
    manifest = notify.load_manifest(out)
    return load_profile(manifest.account).reels.enabled and manifest.reels is not None


def load_final(out: Path) -> dict:
    f = out / FINAL_RECORD
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def final_key(out: Path) -> str:
    """지금 슬라이드·캡션의 지문. 보낸 최종본과 같으면 다시 보내지 않는다."""
    manifest = notify.load_manifest(out)
    h = hashlib.sha1(notify.full_caption(manifest).encode())
    for path in notify.slide_paths(manifest, out):
        h.update(path.read_bytes())
    return h.hexdigest()


def mark_final(out: Path, video: bool) -> None:
    data = {"key": final_key(out), "video": video, "at": int(time.time())}
    (out / FINAL_RECORD).parent.mkdir(parents=True, exist_ok=True)
    (out / FINAL_RECORD).write_text(json.dumps(data), encoding="utf-8")


def send_final(out: Path, tg, why: str = "최종본", photos: bool = True) -> str:
    """모든 슬라이드와 릴스 영상을 텔레그램으로 통째로 보낸다 (2026-10-29 사용자: "제목이나 순서
    바꿔도 최종 사진 모음, 영상 통째로 다시 보내줘. 항상 게시 전엔 최종본"). 영상은 옛 순서면
    다시 만든다. 영상을 못 만들면 사진만 보낸다."""
    from core.store import Store
    from tools import publish, reels

    manifest = notify.load_manifest(out)
    profile = load_profile(manifest.account)
    tag = f"[{manifest.account}] {manifest.date}"
    slides = notify.slide_paths(manifest, out)
    if photos:
        tg.send_photos(slides, f"{tag} {why} · 사진 {len(slides)}장 (게시할 순서)")
    video, note = None, ""
    if profile.reels.enabled and manifest.reels is not None:
        try:
            if reel_stale(out):
                publish.rebuild_reel(out, profile, Store(manifest.account), tag)
                manifest = notify.load_manifest(out)
            video = reels.video_path(manifest, out)
        except (RuntimeError, ValueError, FileNotFoundError) as e:
            note = f" (영상은 다시 만들지 못했어요: {e})"
        if video is not None:
            tg.send_video(video, f"{tag} {why} · 릴스 영상")
        elif photos:
            tg.send(f"{tag} 영상은 아직 못 만들었어요. 다음 확인 때 영상만 다시 보내요{note}")
    mark_final(out, video is not None)
    if not photos:
        return f"{tag} {why}을 보냄" if video else f"{tag} {why}을 못 만듦{note}"
    return f"{tag} {why}을 보냄 (사진 {len(slides)}장{', 영상' if video else ''}){note}"


def check_order(out: Path, tg, updates: list[dict], dry_run: bool = False) -> str:
    """벌 순서 바꾸기 (/order 4 2 3 6 1 5).
    같은 글은 한 번만 (지난 미리보기에서 처리했으면 넘김)."""
    record = notify.load_record(out)
    assert record is not None
    profile = load_profile(record.account)
    tag = f"[{record.account}] {record.date}"
    found = requested_order(out, record, profile, updates, tg.chat_id)
    if found is None:
        return ""
    uid, shown = found
    if (rec := load_order(out)).get("update_id") == uid:
        if dry_run or not rec.get("error") or not _half_done(out):
            return ""
        # 지난번에 기획·벌 폴더만 바꾸고 실패한 순서 바꾸기: 바뀐 기획대로 미리보기를 마저 만든다
        reels = notify.load_manifest(out).reels
        made = _refinish(out, profile, reels)
        tg.send_photos(
            [made[1]],
            f"{tag} 벌 순서를 {_shown(rec)} 으로 마저 바꿨어요."
            " 이 순서로 올리려면 /ok 를 보내 주세요",
        )
        _save_order(out, uid, rec.get("order", shown), reels is not None)
        return f"{tag} 멈췄던 벌 순서 바꾸기를 마침"
    if rec.get("order") == shown and not rec.get("error"):
        # 같은 순서를 다시 보낸 글: 이미 그 순서라 또 바꾸면 섞인다 (2026-10-28 run380 게시 실패)
        if not dry_run:
            _save_order(out, uid, shown, bool(rec.get("reel_stale")))
        return ""
    prev = next((p for p in chain(out) if load_order(p).get("update_id") == uid), None)
    if prev is not None:
        # 지난 미리보기에서 바꾼 순서. /fix 가 순서를 바꾸기 전 기획을 가져갔으면 같은 순서로 맞춘다
        # (벌 상품 구성으로 짝지음, 고친 벌이 있어 짝이 안 맞으면 그대로)
        want, have = _look_keys(prev), _look_keys(out)
        if dry_run or want == have or sorted(want) != sorted(have):
            if not dry_run:
                _save_order(out, uid, shown, False)
            return ""
        order = [have.index(k) + 1 for k in want]
        made = _reorder(out, order, profile)
        _save_order(out, uid, shown, notify.load_manifest(out).reels is not None)
        return f"{tag} 지난 미리보기에서 바꾼 벌 순서를 이어받음" if made else ""
    if dry_run:
        return f"{tag} 벌 순서를 {shown} 으로 바꿀 예정 (시험)"
    approved = notify.approval_now(out, record, profile, updates, tg.chat_id) == "ok"
    before = len(_look_keys(out))
    try:
        made = reorder(out, shown, profile)
    except (ValueError, FileNotFoundError) as e:
        tg.send(f"{tag} 벌 순서를 바꾸지 못했어요: {e}")
        _save_order(out, uid, shown, False, str(e))
        return f"{tag} 벌 순서 바꾸기 실패: {e}"
    order = " ".join(map(str, shown))
    if not made:
        tg.send(f"{tag} 벌 순서가 이미 {order} 이에요")
        _save_order(out, uid, shown, False)
        return f"{tag} 벌 순서 그대로"
    stale = notify.load_manifest(out).reels is not None
    tail = "이 순서로 게시해요" if approved else "이 순서로 올리려면 /ok 를 보내 주세요"
    left = [n for n in range(1, before + 1) if n not in shown]
    drop = (
        f" 적지 않은 LOOK {', '.join(map(str, left))} 은 뺐어요 ({len(shown)}벌)." if left else ""
    )
    tg.send_photos(
        [made[1]],
        f"{tag} 벌 순서를 {order} 으로 바꿨어요 (첫 벌이 표지, LOOK 번호는 새 순서로)."
        f"{drop} {tail}",
    )
    _save_order(out, uid, shown, stale)
    return f"{tag} 벌 순서를 {order} 으로 바꿈"


def check_title(out: Path, tg, updates: list[dict], dry_run: bool = False) -> str:
    """표지 제목만 바꾸기 (/제목 첫 줄/둘째 줄, 2026-10-28 사용자). 그림은 그대로, 코드만.
    같은 글은 한 번만."""
    record = notify.load_record(out)
    assert record is not None
    profile = load_profile(record.account)
    tag = f"[{record.account}] {record.date}"
    dl = notify.ok_deadline(profile, record.date)
    found = notify.find_title(updates, record, tg.chat_id, int(dl.timestamp()) if dl else None)
    if found is None or load_title(out).get("update_id") == found[0]:
        return ""
    uid, lines = found
    shown = " / ".join(lines)
    if dry_run:
        return f"{tag} 표지 제목을 '{shown}' 으로 바꿀 예정 (시험)"
    approved = notify.approval_now(out, record, profile, updates, tg.chat_id) == "ok"
    stale = bool(load_title(out).get("reel_stale"))
    before = notify.load_manifest(out).title_lines
    try:
        made = retitle(out, lines, profile)
    except (ValueError, FileNotFoundError) as e:
        tg.send(f"{tag} 표지 제목을 바꾸지 못했어요: {e}")
        _save_title(out, uid, lines, stale, str(e))
        return f"{tag} 표지 제목 바꾸기 실패: {e}"
    stale = stale or notify.load_manifest(out).reels is not None
    note_title(out, record, uid, before, lines)
    tail = "이 제목으로 게시해요" if approved else "이 제목으로 올리려면 /ok 를 보내 주세요"
    tg.send_photos([made[0]], f"{tag} 표지 제목을 '{shown}' 으로 바꿨어요. {tail}")
    _save_title(out, uid, lines, stale)
    return f"{tag} 표지 제목을 '{shown}' 으로 바꿈"


def store_of(account: str):
    from core.store import Store

    return Store(account)


def note_title(
    out: Path, record: PreviewRecord, uid: int, before: list[str], lines: list[str]
) -> None:
    """사용자가 /제목 으로 고친 제목을 피드백 기록에 남긴다. 게시 뒤 피드백 반영 단계가
    다른 /fix 와 함께 읽어 제목 쓰는 규칙에 반영한다 (2026-10-29 사용자)."""
    from core.models import FeedbackEntry

    old, new = " / ".join(before), " / ".join(lines)
    text = f"표지 제목을 사용자가 직접 바꿈: '{old}' → '{new}' (바꾼 제목처럼 쓰기)"
    entry = FeedbackEntry(
        account=record.account,
        date=record.date,
        run=out.name,
        slide=1,
        kind="cover",
        text=text,
        at=int(time.time()),
        update_id=uid,
        result="title",
    )
    store_of(record.account).add_feedback([entry])


def retitle(out: Path, lines: list[str], profile: Profile | None = None) -> list[Path]:
    """기획 제목을 바꾸고 슬라이드·미리보기·manifest 를 다시 만든다 (AI 없음).
    표지 장과 미리보기 경로."""
    from tools import plan as plan_tool

    plan = plan_tool.load_plan(out / "plan.yaml")
    profile = profile or load_profile(plan.account)
    if plan.text is None or not plan.looks:
        raise ValueError("여러 벌 게시물만 제목을 바로 바꿀 수 있어요 (/fix 1 제목을 … 로)")
    reels = notify.load_manifest(out).reels
    data = plan.model_dump(mode="json", exclude_none=True)
    data["text"]["title_lines"] = lines
    _write_yaml(data, out / "plan.yaml")
    return _refinish(out, profile, reels)


def _save_title(out: Path, uid: int, lines: list[str], stale: bool, error: str = "") -> None:
    data = {"update_id": uid, "title_lines": lines, "reel_stale": stale, "at": int(time.time())}
    if error:
        data["error"] = error
    (out / TITLE_RECORD).parent.mkdir(parents=True, exist_ok=True)
    (out / TITLE_RECORD).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _shown(rec: dict) -> str:
    return " ".join(map(str, rec.get("order", [])))


def _save_order(out: Path, uid: int, shown: list[int], stale: bool, error: str = "") -> None:
    data = {"update_id": uid, "order": shown, "reel_stale": stale, "at": int(time.time())}
    if error:
        data["error"] = error
    (out / ORDER_RECORD).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def check_cover(out: Path, tg, updates: list[dict], dry_run: bool = False) -> str:
    """표지 벌 고르기 (/ok 3, /cover 3). 벌 순서 바꾸기보다 앞에 온 글은 버린다."""
    record = notify.load_record(out)
    if record is None or record.sandbox or tg is None:
        return ""
    if (out / "published.json").exists():
        return ""
    profile = load_profile(record.account)
    tag = f"[{record.account}] {record.date}"
    found = requested(out, record, profile, updates, tg.chat_id)
    if found is None:
        return ""
    uid, shown = found
    ordered = requested_order(out, record, profile, updates, tg.chat_id)
    if ordered is not None and uid <= ordered[0]:
        return ""  # 순서 바꾸기의 첫 번호가 표지라 그 앞(같은 글 포함)의 표지 고르기는 쓰지 않는다
    try:
        n = look_number(out, profile, shown)
    except ValueError:
        n = shown  # 아래 apply 가 없는 번호라고 알린다
    done = load_record(out)
    if done.get("update_id") == uid:
        return ""
    if dry_run:
        return f"{tag} 표지를 LOOK {n} 으로 바꿀 예정 (시험)"
    approved = notify.approval_now(out, record, profile, updates, tg.chat_id) == "ok"
    stale = bool(done.get("reel_stale"))
    past = [r for p in chain(out) if (r := load_record(p)).get("update_id") == uid]
    seen = bool(past)
    if past and not past[0].get("error"):
        # 지난 미리보기의 LOOK 번호로 이미 고른 벌. 고친 미리보기는 표지가 바뀌어 보이는 번호가
        # 달라지므로 다시 읽지 않는다 (2026-10-16: /cover 2 가 다른 벌로 한 번 더 바뀜)
        n = past[0].get("look", n)
    if seen and (current(out, profile) == n or fix_changed_cover(out)):
        # 지난 미리보기에서 이미 처리한 글: 고친 미리보기가 그 표지를 이어받았거나,
        # /fix 가 표지를 따로 바꿨으면 /fix 를 따른다
        note = (
            f"{tag} 표지 LOOK {current(out, profile)} 그대로 (지난 미리보기에서 고른 표지 처리함)"
        )
    elif current(out, profile) == n:
        note = f"{tag} 표지는 이미 LOOK {n} 이에요"
        tg.send(note + (". 이대로 게시해요" if approved else ". /ok 를 보내 주시면 게시해요"))
    else:
        try:
            made = apply(out, n, profile)
        except (ValueError, FileNotFoundError) as e:
            tg.send(f"{tag} 표지를 바꾸지 못했어요: {e}")
            _save(out, uid, n, stale, str(e))
            return f"{tag} 표지 바꾸기 실패: {e}"
        stale = stale or notify.load_manifest(out).reels is not None
        tail = "이 표지로 게시해요" if approved else "이 표지로 올리려면 /ok 를 보내 주세요"
        tg.send_photos([made[0]], f"{tag} 표지를 LOOK {n} 으로 바꿨어요. {tail}")
        note = f"{tag} 표지를 LOOK {n} 으로 바꿈"
    _save(out, uid, n, stale)
    return note


def _save(out: Path, uid: int, n: int, stale: bool, error: str = "") -> None:
    data = {"update_id": uid, "look": n, "reel_stale": stale, "at": int(time.time())}
    if error:
        data["error"] = error
    (out / RECORD).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="결과 폴더의 표지를 n번 벌로 다시 만들기")
    a.add_argument("out", type=Path)
    a.add_argument("look", type=int)
    o = sub.add_parser("order", help="결과 폴더의 벌 순서를 LOOK 번호 순서로 (첫 번호가 표지)")
    o.add_argument("out", type=Path)
    o.add_argument("looks", type=int, nargs="+")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "order":
            made = reorder(args.out, args.looks)
        else:
            made = apply(args.out, args.look)
    except (ValueError, FileNotFoundError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    for p in made:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
