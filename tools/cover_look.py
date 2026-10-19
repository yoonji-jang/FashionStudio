r"""표지 벌 바꾸기 (2026-10-12 사용자 "6벌 콜라주가 아닌 코디 중 하나로").
Claude·이미지 AI 없이 코드만.

여러 벌 게시물 표지는 한 벌 코디컷 + 제목이다 (profile looks.cover_collage.enabled false).
어느 벌로 할지는 daily-feed 가 plan.yaml `cover_look` 에 고르고,
미리보기를 보낸 뒤 텔레그램으로 바꾼다.
- "/ok 3", "/ok 표지 3"   3번 벌을 표지로 하고 승인
- "/cover 3", "표지 3번"  승인 없이 표지만 3번 벌로 (새 표지 사진을 받아 보고 /ok)
번호는 미리보기의 LOOK 번호. 승인 마감(schedule.ok_deadline)까지 온 마지막 글을 쓴다.

바꿀 때는 결과 폴더의 표지 장(slides/01_cover.jpg, 릴스용 slides/tall/01_cover.jpg)과 미리보기
(preview.jpg)를 그 벌 코디컷으로 다시 만들고 plan.yaml·feed.yaml 에 남긴 뒤,
새 표지 사진을 텔레그램으로 보낸다. 릴스 영상은 첫 장이 바뀌므로 cover.json 의 reel_stale 을 보고
릴스 게시 때 다시 만든다.
같은 글은 한 번만 처리한다 (cover.json 의 update_id).

schedule 매시 확인(links 칸)과 게시(tools.publish) 직전에 부른다.

사용법 (결과 폴더에서 바로 바꿔 보기, 텔레그램 없이):
  python -m tools.cover_look apply out/2026-10-13 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

from core.models import DailyPlan, FeedSpec, PreviewRecord
from core.profile import Profile, load_profile
from tools import feed, looks, notify

RECORD = "cover.json"  # 처리한 표지 바꾸기 (결과 폴더)


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


def reel_stale(out: Path) -> bool:
    """표지를 바꾼 뒤 릴스 영상을 아직 다시 만들지 않았는지."""
    return bool(load_record(out).get("reel_stale"))


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


def check(out: Path, tg, updates: list[dict] | None = None, dry_run: bool = False) -> str:
    """텔레그램에 표지 벌 고르기가 왔으면 표지를 다시 만들고 새 표지를 보낸다. 로그 한 줄."""
    record = notify.load_record(out)
    if record is None or record.sandbox or tg is None:
        return ""
    if (out / "published.json").exists():
        return ""
    profile = load_profile(record.account)
    tag = f"[{record.account}] {record.date}"
    updates = tg.updates() if updates is None else updates
    found = requested(out, record, profile, updates, tg.chat_id)
    if found is None:
        return ""
    uid, shown = found
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
    args = ap.parse_args(argv)
    try:
        made = apply(args.out, args.look)
    except (ValueError, FileNotFoundError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    for p in made:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
