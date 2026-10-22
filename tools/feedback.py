r"""최종 이미지 피드백 (15단계). 미리보기를 받은 뒤 텔레그램으로 고칠 점을 보내면 다시 만든다.

  /fix 1 신발 더 크게, 가방 오른쪽 위로     1 = 장 번호 (미리보기 사진 순서)
  /fix 캡션 도입 한 줄을 더 짧게            캡션
  /fix 전체 좀 더 밝은 분위기로             게시물 전체 (장 번호 없이 써도 같음)
  미리보기 사진 한 장에 답장으로 /fix 신발 더 크게  → 그 장
  사진에 /fix 를 설명으로 달거나, /fix 앞뒤 3분 안에 사진을 보내면 그 /fix 의 참고 사진
  (예: "코끝을 두 번째 사진처럼", 2026-10-22 사용자). 차례 흐름 계정만 받는다
  (flow/<계정>/<날짜>/fix/ 에 저장, schedule 이 커밋)

봇 서버 없이 동작한다. schedule job 이 깨어날 때 답장을 읽어(check) 결과를 정하고,
다시 만들 때는 daily 워크플로를 mode=fix 로 부른다. daily 는 지난 결과를 가져와(prepare)
Claude(fix-feed 스킬)가 피드백대로 기획을 고친 뒤 다시 만들고 검수해서 새 미리보기를
보낸다. 표지 코디컷을 고칠 일이 없으면 이미 검수한 코디컷을 그대로 쓴다
(이미지 AI 를 다시 부르지 않음).

받은 피드백은 처리 결과와 함께 모두 data/<계정>/feedback.jsonl 에 쌓는다
(core.models.FeedbackEntry). 최근 피드백은 기획(tools.plan context)과
검수(tools.review prep)에 보여 줘서 다음 게시물부터 참고한다.
횟수 상한·확인 간격은 profile feedback 에서 읽는다.

사용법:
  python -m tools.feedback prepare '<daily pick 값 JSON>' --prev <daily-results 폴더>
      --out out/<날짜>
  python -m tools.feedback summary --account women [--days 30]
      피드백 모아 보기 (규칙에 반영할 후보)
  python -m tools.feedback pending --account women [--out 파일]
      규칙 PR 에 아직 넣지 않은 피드백 (없으면 종료 코드 5, 마지막 줄 UPTO=<update_id>)
  python -m tools.feedback mark --account women --upto <update_id>
      규칙 PR 에서 다룬 피드백 기록 (data/<계정>/feedback_rules.json)
check 는 텔레그램으로 글을 보내서 schedule 만 부른다 (CLI 없음).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel

from core.models import FeedbackEntry, FeedbackResult, Manifest, PreviewRecord
from core.profile import Profile, load_profile, minutes
from core.store import Store
from tools import notify

CAPTION_WORDS = {"캡션", "본문", "문구", "caption"}
ALL_WORDS = {"전체", "all"}
KIND_NAMES = {
    "cover": "표지",
    "product": "상품 장",
    "look": "코디 장",
    "summary": "요약 장",
    "comment": "댓글 안내 장",
    "slide": "장",
    "caption": "캡션",
    "all": "전체",
}
# 다시 만들기를 보낸 뒤 이만큼(분) 지나도 새 미리보기가 없으면 실패로 보고 다시 받는다
STALE_MINUTES = 120
# /fix 앞뒤로 이만큼(초) 안에 따로 보낸 사진은 그 /fix 의 참고 사진
FIX_PHOTO_GAP = 180
FIX_PHOTO_MAX = 6


# ---------- 장 번호 ----------


def slide_map(manifest: Manifest) -> list[dict]:
    """미리보기 사진 순서대로 장 종류. 상품 장은 기획 상품 순서와 같다 (tools.feed.render)."""
    out = []
    n_product = 0
    for i, img in enumerate(manifest.images, 1):
        stem = Path(img).stem.split("_", 1)[-1]
        entry: dict = {"no": i, "file": Path(img).name}
        look = re.fullmatch(r"look(\d+)", stem)
        if stem == "cover":
            entry["kind"] = "cover"
        elif look:  # 여러 벌 게시물의 벌 장 (20단계)
            entry |= {"kind": "look", "look": int(look.group(1))}
        elif stem in ("summary", "comment"):
            entry["kind"] = stem
        elif (
            n_product < len(manifest.plan.items) and stem == manifest.plan.items[n_product].category
        ):
            p = manifest.plan.items[n_product]
            entry |= {"kind": "product", "item": n_product, "product": p.id, "category": stem}
            n_product += 1
        else:
            entry["kind"] = "slide"
        out.append(entry)
    return out


def label(fix: dict) -> str:
    """ "1장 표지", "3장 상의", "캡션" """
    if fix.get("slide"):
        what = fix.get("category") or KIND_NAMES.get(fix["kind"], "")
        if fix.get("look"):
            what = f"{fix['look']}번 코디"
        return f"{fix['slide']}장 {what}".strip()
    return KIND_NAMES.get(fix["kind"], "전체")


def target(
    args: list[str], reply_to: int | None, record: PreviewRecord, slides: list[dict]
) -> dict:
    """/fix 뒤 글에서 고칠 곳과 내용을 나눈다."""
    first = args[0] if args else ""
    rest = " ".join(args[1:]).strip()
    whole = " ".join(args).strip()
    n = int(re.sub(r"(장|번)$", "", first)) if re.fullmatch(r"\d{1,2}(장|번)?", first) else None
    if n is not None and 1 <= n <= len(slides):
        found = {"slide": n, "text": rest}
    elif first.lower() in CAPTION_WORDS:
        return {"slide": None, "kind": "caption", "text": rest}
    elif first.lower() in ALL_WORDS:
        return {"slide": None, "kind": "all", "text": rest}
    elif reply_to in record.message_ids and record.message_ids.index(reply_to) < len(slides):
        found = {"slide": record.message_ids.index(reply_to) + 1, "text": whole}
    elif reply_to in record.message_ids and record.message_ids.index(reply_to) == len(slides):
        return {"slide": None, "kind": "caption", "text": whole}  # 캡션 메시지에 답장
    else:
        return {"slide": None, "kind": "all", "text": whole}
    s = slides[found["slide"] - 1]
    found["kind"] = s["kind"]
    for key in ("product", "category", "look"):
        if key in s:
            found[key] = s[key]
    return found


def find_fixes(
    updates: list[dict], record: PreviewRecord, chat_id: str, slides: list[dict]
) -> list[dict]:
    """미리보기 뒤 등록된 채팅에서 온 이 계정·날짜의 /fix (update_id 순).
    계정·날짜 없이 봇의 다른 메시지(다른 계정·다른 날)에 답장한 글은 그쪽 것이라 뺀다."""
    found = []
    mine = [
        u
        for u in sorted(updates, key=lambda u: u["update_id"])
        if str(((u.get("message") or {}).get("chat") or {}).get("id")) == str(chat_id)
        and (u.get("message") or {}).get("date", 0) >= record.sent_at
    ]
    for u in mine:
        msg = u.get("message") or {}
        reply_to = (msg.get("reply_to_message") or {}).get("message_id")
        # 사진에 설명으로 단 /fix 도 읽는다 (2026-10-22 사용자: 참고 사진을 붙여 보냄)
        text = msg.get("text") or msg.get("caption") or ""
        # 한 메시지에 줄을 바꿔 /fix 를 여러 개 적으면 하나씩 따로 읽는다.
        # 줄을 바꿔 쓴 표지 벌 고르기("/cover 3", "/ok 3", "표지 3번")는 고칠 내용에 넣지 않는다
        parts = re.split(r"\n(?=\s*(?:/(?:fix|cover|ok)\b|표지\s*\d))", text)
        photos: list[str] | None = None
        for part in parts:
            cmd = notify.parse(part, record.account, record.date)
            if cmd is None or cmd.name != "fix":
                continue
            if not cmd.labeled and reply_to and reply_to not in record.message_ids:
                continue
            # "/fix 2 3 …", "/fix 2,3 …" 처럼 장 번호를 여러 개 적으면 장마다 같은 내용으로 나눈다
            words, nums = list(cmd.args), []
            while words and re.fullmatch(r"\d{1,2}(?:,\d{1,2})*,?(?:장|번)?,?", words[0]):
                nums += [int(x) for x in re.findall(r"\d+", words.pop(0))]
            asks = [[str(n), *words] for n in dict.fromkeys(nums)] if len(nums) > 1 else [cmd.args]
            for args in asks:
                fix = target(args, reply_to, record, slides)
                if not fix["text"]:
                    continue  # 고칠 내용이 없는 /fix
                if photos is None:
                    photos = fix_photos(mine, u)
                if photos:
                    fix["photos"] = photos
                found.append(fix | {"update_id": u["update_id"], "at": msg.get("date", 0)})
    return found


def fix_photos(updates: list[dict], fix_update: dict) -> list[str]:
    """이 /fix 글의 참고 사진 파일 번호 (보낸 순서): 글에 붙은 사진과 같은 앨범의 사진,
    그리고 앞뒤 FIX_PHOTO_GAP 초 안에 따로 보낸 (명령이 아닌) 사진."""
    msg = fix_update.get("message") or {}
    at, group = msg.get("date", 0), str(msg.get("media_group_id") or "")
    out = []
    for u in updates:
        m = u.get("message") or {}
        fid = notify.photo_file_id(m)
        if not fid:
            continue
        caption = (m.get("caption") or "").strip()
        same = u is fix_update or (group and str(m.get("media_group_id") or "") == group)
        near = abs(m.get("date", 0) - at) <= FIX_PHOTO_GAP and not caption.startswith("/")
        if same or near:
            out.append(fid)
    return list(dict.fromkeys(out))[:FIX_PHOTO_MAX]


def save_fix_photos(
    fixes: list[dict], results: Path, account: str, day: date, tg, dry_run: bool
) -> None:
    """참고 사진을 받아 daily-results flow/<계정>/<날짜>/fix/ 에 두고, fix 의 photos 를
    그 경로(daily-results 기준)로 바꾼다. 받지 못한 사진은 뺀다."""
    saved: dict[str, str] = {}
    for f in fixes:
        paths = []
        for i, fid in enumerate(f.get("photos") or [], 1):
            if fid not in saved:
                rel_path = f"flow/{account}/{day.isoformat()}/fix/{f['update_id']}_{i}.jpg"
                try:
                    if not dry_run:
                        tg.download(fid, results / rel_path)
                    saved[fid] = rel_path
                except Exception as e:  # noqa: BLE001 - 사진 없이도 고칠 수 있다
                    print(f"/fix 참고 사진을 받지 못함: {e}", file=sys.stderr)
                    continue
            paths.append(saved[fid])
        if paths:
            f["photos"] = paths
        else:
            f.pop("photos", None)


# ---------- 결과 폴더 ----------


def latest_preview(results: Path, account: str, day: date) -> Path | None:
    """이 계정·날짜로 미리보기를 보낸 가장 최근 결과 폴더."""
    found = []
    for d in results.glob(f"{day}-run*"):
        m = re.fullmatch(rf"{day}-run(\d+)", d.name)
        if not m or not (d / "telegram.json").exists() or not (d / "manifest.json").exists():
            continue
        rec = notify.load_record(d)
        if rec is not None and rec.account == account:
            found.append((int(m.group(1)), d))
    return max(found)[1] if found else None


def deadline(profile: Profile, day: date) -> datetime:
    """다시 만들기를 받는 마지막 시각: 승인 마감(없으면 게시 마지막 확인 시각) - render_minutes.
    고친 미리보기에 승인 마감 전에 /ok 할 수 있어야 해서. 차례 흐름(profile flow)은 마감이 없다."""
    if profile.flow.enabled:
        return datetime.max.replace(tzinfo=UTC)
    ok_by = notify.ok_deadline(profile, day)
    if ok_by is not None:
        return ok_by - timedelta(minutes=profile.feedback.render_minutes)
    s = profile.schedule
    last = minutes(s.publish_at) + s.publish_retries * s.publish_retry_every
    start = datetime.combine(day, datetime.min.time(), ZoneInfo(s.timezone))
    return start + timedelta(minutes=last - profile.feedback.render_minutes)


def rounds_used(store: Store, day: date) -> int:
    return max((e.round for e in store.feedback() if e.date == day), default=0)


def decide(
    profile: Profile,
    store: Store,
    run_dir: Path,
    record: PreviewRecord,
    now: datetime,
    known: list[FeedbackEntry] | None = None,
) -> FeedbackResult:
    """새 /fix 를 어떻게 처리할지."""
    day = record.date
    known = store.feedback() if known is None else known
    if any(p.date == day for p in store.posts()) or (run_dir / "published.json").exists():
        return "after_post"
    if record.sandbox:
        return "sandbox"
    since = int((now - timedelta(minutes=STALE_MINUTES)).timestamp())
    if any(e.run == run_dir.name and e.result == "rerender" and e.at >= since for e in known):
        return "busy"
    if max((e.round for e in known if e.date == day), default=0) >= profile.feedback.max_fixes:
        return "limit"
    if now > deadline(profile, day):
        return "late"
    return "rerender"


LATER = "피드백은 기록해 두고 다음 게시물부터 참고해요."
MESSAGES = {
    "rerender": (
        "고칠 점을 받았어요. 같은 코디로 다시 만들어 새 미리보기를 보내 드릴게요 ({round}/{max}회)"
    ),
    "limit": "오늘은 이미 {max}번 다시 만들어서 더 고치지 않았어요. " + LATER,
    "late": "게시 마감이 가까워 다시 만들지 않았어요. " + LATER,
    "busy": (
        "앞의 고칠 점으로 다시 만드는 중이에요. 새 미리보기가 오면 그때 다시 보내 주세요 "
        "(피드백은 기록했어요)."
    ),
    "after_post": "이미 게시해서 다시 만들지 않았어요. " + LATER,
    "sandbox": "시험 미리보기라 다시 만들지 않았어요 (피드백은 기록했어요).",
    "candidates": (
        "새 후보를 보낸 뒤에 온 /fix 라 지난 미리보기는 다시 만들지 않았어요. "
        "후보 고치기는 고르기 마감 전에만 돼요 (피드백은 기록했어요)."
    ),
}


def check(
    account: str,
    day: date,
    results: Path | None,
    tg,
    now: datetime | None = None,
    dry_run: bool = False,
    store: Store | None = None,
) -> dict | None:
    """schedule 이 부른다. 새 /fix 를 기록하고 알린다.
    다시 만들 때는 daily 워크플로 pick 값(dict)을 돌려준다."""
    profile = load_profile(account)
    if not profile.feedback.enabled or results is None or tg is None:
        return None
    now = now or datetime.now(UTC)
    store = store or Store(account)
    run_dir = latest_preview(results, account, day)
    if run_dir is None:
        return None
    record = notify.load_record(run_dir)
    assert record is not None
    known = store.feedback()
    seen = {e.update_id for e in known}
    slides = slide_map(notify.load_manifest(run_dir))
    updates = tg.updates()
    fixes = [
        f for f in find_fixes(updates, record, tg.chat_id, slides) if f["update_id"] not in seen
    ]
    if not fixes:
        print(f"[{account}] {day} 새 /fix 없음 ({run_dir.name}, 텔레그램 글 {len(updates)}개 확인)")
        # 왜 못 읽었는지 알 수 있게 받은 글을 짧게 남긴다 (등록된 채팅 여부, 미리보기 전후, 앞부분)
        for u in updates[-10:]:
            msg = u.get("message") or {}
            same = str((msg.get("chat") or {}).get("id")) == str(tg.chat_id)
            chat = "등록 채팅" if same else "다른 채팅"
            when = datetime.fromtimestamp(msg.get("date", 0), UTC).strftime("%m-%d %H:%M UTC")
            after = "미리보기 뒤" if msg.get("date", 0) >= record.sent_at else "미리보기 전"
            head = (msg.get("text") or "(글 아님)").replace("\n", " ")[:20]
            print(f"  - {u['update_id']} {when} {chat} {after}: {head}")
        return None
    # 미리보기 뒤에 같은 날 후보를 새로 보냈으면 그 뒤에 온 /fix 는 새 후보에 한 말이다.
    # 지난 미리보기를 다시 그리지 않고 기록만 한다 (후보 고치기는 tools.pick 이 맡는다, 2026-10-07)
    from tools import pick

    cand = pick.latest(results, account, day)
    if cand is not None and cand[1].sent_at > record.sent_at:
        newer = [f for f in fixes if f["at"] >= cand[1].sent_at]
        fixes = [f for f in fixes if f["at"] < cand[1].sent_at]
        if newer:
            if not dry_run:
                store.add_feedback(
                    [
                        FeedbackEntry(
                            account=account,
                            date=day,
                            run=cand[0].name,
                            slide=None,
                            kind="all",
                            text=f["text"],
                            at=f["at"],
                            update_id=f["update_id"],
                            result="candidates",
                            round=0,
                        )
                        for f in newer
                    ]
                )
            tg.send(f"[{account}] {day} " + MESSAGES["candidates"])
        if not fixes:
            return None
    result = decide(profile, store, run_dir, record, now, known)
    rnd = rounds_used(store, day) + 1 if result == "rerender" else 0
    if result == "rerender":
        if profile.flow.enabled:
            save_fix_photos(fixes, results, account, day, tg, dry_run)
        else:
            for f in fixes:
                f.pop("photos", None)
    entries = [
        FeedbackEntry(
            account=account,
            date=day,
            run=run_dir.name,
            slide=f.get("slide"),
            kind=f["kind"],
            product=f.get("product"),
            look=f.get("look"),
            text=f["text"],
            at=f["at"],
            update_id=f["update_id"],
            result=result,
            round=rnd,
        )
        for f in fixes
    ]
    head = MESSAGES[result].format(round=rnd, max=profile.feedback.max_fixes)
    lines = [f"[{account}] {day} {head}"] + [
        f"- {label(f)}: {f['text']}"
        + (f" (참고 사진 {len(f['photos'])}장)" if f.get("photos") else "")
        for f in fixes
    ]
    if result in ("limit", "late", "busy"):
        lines.append(f"지금 미리보기로 게시하려면 /ok {notify.command_label(profile, day)}")
    if not dry_run:
        store.add_feedback(entries)
    tg.send("\n".join(lines))
    if result != "rerender":
        return None
    return {
        "run": run_dir.name,
        "round": rnd,
        "fixes": [
            {
                k: f[k]
                for k in ("slide", "kind", "product", "category", "look", "text", "photos")
                if f.get(k)
            }
            for f in fixes
        ],
    }


def waiting(
    updates: list[dict],
    record: PreviewRecord,
    chat_id: str,
    run_dir: Path,
    profile: Profile,
    store: Store,
    now: datetime | None = None,
) -> str | None:
    """게시 job 이 부른다. 미리보기에 온 마지막 명령이 /fix 이고 다시 만들 것이면 기다린다 (이유).
    /fix 뒤에 /ok 를 보내면 지금 미리보기로 게시한다."""
    if not profile.feedback.enabled:
        return None
    last: tuple[int, str] | None = None
    for uid, cmd in notify.commands(
        updates,
        chat_id,
        record.sent_at,
        record.account,
        record.date,
        {"ok", "skip", "fix"},
        record.message_ids,
    ):
        if cmd.name != "fix" and cmd.args:
            continue
        if cmd.name == "fix" and not cmd.args:
            continue
        last = (uid, cmd.name)
    if last is None or last[1] != "fix":
        return None
    known = store.feedback()
    entry = next((e for e in known if e.update_id == last[0]), None)
    if entry is not None:
        result = entry.result
    else:
        result = decide(profile, store, run_dir, record, now or datetime.now(UTC), known)
    if result in ("rerender", "busy"):
        return "고칠 점을 반영한 새 미리보기를 기다립니다"
    return None


# ---------- daily mode=fix ----------


def load_fix(out: Path) -> dict | None:
    path = out / "fix.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def prepare(pick: dict, prev_root: Path, out: Path) -> dict:
    """지난 결과(prev_root/<run>)에서 기획·검수 기록·고른 코디컷을 가져와 out 에 둔다.
    기획의 cover 를 지난 표지 코디컷으로 정해 두어, 표지를 고칠 일이 없으면 이미지 AI 를 다시
    부르지 않는다. 지난 슬라이드·캡션은 out/before/ 에 둔다 (Claude 가 비교해 봄)."""
    from tools import review as review_tool
    from tools.plan import load_plan, rel

    src = prev_root / pick["run"]
    if not (src / "plan.yaml").exists():
        raise FileNotFoundError(f"지난 결과에 plan.yaml 이 없습니다: {src}")
    out.mkdir(parents=True, exist_ok=True)
    manifest = notify.load_manifest(src)
    plan = load_plan(src / "plan.yaml")
    if plan.shot == "model":
        # 모델컷(16단계)은 창고에서 빠진 전날 상품을 쓸 수 있어 지난 manifest 에서 다시 넣는다
        from tools import model_post

        model_post.restore_products(manifest, Store(plan.account))
    cover = None
    if review_tool.review_path(src).exists():
        review = review_tool.load_review(src)
        chosen = next((c for c in review.candidates if c.id == review.chosen), None)
        if chosen and not Path(chosen.file).is_absolute() and (src / chosen.file).exists():
            dest = out / chosen.file
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / chosen.file, dest)
            review_tool.save_review(out, review)  # 점수를 그대로 두고 캐러셀만 다시 검수
            cover = rel(dest)
    if plan.looks:
        # 여러 벌 (20단계): 벌마다 지난 코디컷과 검수 기록을 가져와 그 벌의 cover 로 둔다
        looks = []
        for n, look in enumerate(plan.looks, 1):
            looks.append(look.model_copy(update={"cover": _keep_chosen(src, out, f"looks/{n}")}))
            prompt = src / "looks" / str(n) / "outfit" / "prompt.txt"
            if prompt.exists():
                (out / "before").mkdir(parents=True, exist_ok=True)
                shutil.copy2(prompt, out / "before" / f"prompt_{n}.txt")
        plan = plan.model_copy(update={"looks": looks})
    else:
        plan = plan.model_copy(update={"cover": cover})
    (out / "plan.yaml").write_text(
        yaml.safe_dump(
            plan.model_dump(mode="json", exclude_none=True), allow_unicode=True, sort_keys=False
        ),
        encoding="utf-8",
    )
    before = out / "before"
    before.mkdir(exist_ok=True)
    for name in ("caption.txt", "preview.jpg", "report.md"):
        if (src / name).exists():
            shutil.copy2(src / name, before / name)
    if (src / "outfit" / "prompt.txt").exists():
        shutil.copy2(src / "outfit" / "prompt.txt", before / "prompt.txt")
    if (src / "slides").is_dir():
        shutil.copytree(src / "slides", before / "slides", dirs_exist_ok=True)
    fixes = [dict(f) for f in pick["fixes"]]
    for f in fixes:
        # 참고 사진 (daily-results 기준 경로) → out/<날짜>/fix_photos/
        paths = []
        for p in f.get("photos") or []:
            if (prev_root / p).exists():
                dest = out / "fix_photos" / Path(p).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(prev_root / p, dest)
                paths.append(rel(dest))
            else:
                print(f"/fix 참고 사진이 없음: {p}", file=sys.stderr)
        if paths:
            f["photos"] = paths
        else:
            f.pop("photos", None)
    data = {
        "run": pick["run"],
        "round": pick.get("round", 1),
        "cover": cover,
        "fixes": fixes,
        "slides": slide_map(manifest),
        "cover_look": plan.cover_look,  # 지난 표지 벌 (tools.cover_look 이 /fix 가 바꿨는지 봄)
    }
    cand = _candidates(src, prev_root, out)
    if cand:
        data["candidates"] = cand  # "코디 후보 3번 추가" 처럼 후보 벌을 더할 때 (2026-10-22 사용자)
    earlier = _earlier(src, prev_root, out)
    if earlier:
        data["earlier"] = earlier  # 고치기 전 미리보기 (그 벌을 되돌릴 때)
    (out / "fix.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _source_pick(src: Path) -> str:
    """결과 폴더를 만든 daily pick 값 (run.json). 없으면 빈칸."""
    run = src / "run.json"
    if not run.exists():
        return ""
    return str(json.loads(run.read_text(encoding="utf-8")).get("pick") or "")


def _earlier_run(src: Path, prev_root: Path) -> Path | None:
    """src 가 /fix 로 고친 미리보기면 고치기 전 결과 폴더 (pick 이 고칠 점 JSON)."""
    pick = _source_pick(src)
    if not pick.startswith("{"):
        return None
    try:
        name = str(json.loads(pick).get("run") or "")
    except json.JSONDecodeError:
        return None
    earlier = prev_root / name
    return earlier if name and earlier.is_dir() else None


def _candidates(src: Path, prev_root: Path, out: Path, depth: int = 0) -> str | None:
    """이 미리보기를 만든 코디 후보(여러 벌 p1.yaml)를 out/candidates.yaml 로 (저장소 기준 경로).
    후보 번호 N 은 이 파일 looks 의 N번째 벌이다 (텔레그램 후보 시트 번호).
    고친 미리보기를 또 고칠 때는 그 결과의 candidates.yaml, 없으면 고치기 전 결과를 따라간다."""
    from tools.plan import rel

    dest = out / "candidates.yaml"
    if (src / "candidates.yaml").exists():
        shutil.copy2(src / "candidates.yaml", dest)
        return rel(dest)
    earlier = _earlier_run(src, prev_root)
    if earlier is not None:
        return _candidates(earlier, prev_root, out, depth + 1) if depth < 5 else None
    pick = _source_pick(src)
    if "/" not in pick:
        return None
    p1 = prev_root / pick.split("/")[0] / "picks" / "p1.yaml"
    if not p1.exists():
        return None
    shutil.copy2(p1, dest)
    return rel(dest)


def _earlier(src: Path, prev_root: Path, out: Path) -> dict | None:
    """고친 미리보기를 또 고칠 때 고치기 전 결과(기획·벌마다 고른 코디컷)를 out/earlier/ 에 둔다
    (2026-10-22 사용자: "/fix 1 고치기 전 이미지를 사용해줘")."""
    earlier = _earlier_run(src, prev_root)
    if earlier is None or not (earlier / "plan.yaml").exists():
        return None
    dest = out / "earlier"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(earlier / "plan.yaml", dest / "plan.yaml")
    looks = {}
    for d in sorted((earlier / "looks").glob("*")) if (earlier / "looks").is_dir() else []:
        if d.name.isdigit() and (path := _keep_chosen(earlier, dest, f"looks/{d.name}")):
            looks[int(d.name)] = path
    if (earlier / "slides").is_dir():
        shutil.copytree(earlier / "slides", dest / "slides", dirs_exist_ok=True)
    return {"run": earlier.name, "looks": looks}


def _keep_chosen(src: Path, out: Path, sub: str) -> str | None:
    """src/sub 의 검수로 고른 코디컷을 out/sub 로 복사하고 검수 기록도 둔다. 저장소 기준 경로."""
    from tools import review as review_tool
    from tools.plan import rel

    s, o = src / sub, out / sub
    if not review_tool.review_path(s).exists():
        return None
    review = review_tool.load_review(s)
    chosen = next((c for c in review.candidates if c.id == review.chosen), None)
    if not chosen or Path(chosen.file).is_absolute() or not (s / chosen.file).exists():
        return None
    dest = o / chosen.file
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s / chosen.file, dest)
    # 고른 코디컷 파일만 옮기므로 검수 기록에도 그 후보만 남긴다 (없는 파일로 prep 이 멈추지 않게)
    review_tool.save_review(o, review.model_copy(update={"candidates": [chosen]}))
    return rel(dest)


def fix_changes(out: Path) -> list[str]:
    """Claude 가 fix_result.yaml 에 적은 고친 내용 (미리보기에 보임)."""
    path = out / "fix_result.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(x) for x in data.get("changes", [])]


# ---------- 모아 보기 ----------


def recent_lines(store: Store, today: date, days: int, kinds: set[str] | None = None) -> list[str]:
    """최근 피드백 한 줄씩 (오래된 것부터). 기획·검수 재료에 붙인다."""
    if days <= 0:
        return []
    since = today - timedelta(days=days)
    lines = []
    for e in store.feedback():
        if not since <= e.date <= today or (kinds and e.kind not in kinds):
            continue
        fix = {"slide": e.slide, "kind": e.kind, "look": e.look}
        lines.append(f"- {e.date:%m-%d} {label(fix)}: {e.text}")
    return lines


def summary(store: Store, today: date, days: int) -> str:
    entries = [e for e in store.feedback() if e.date >= today - timedelta(days=days)]
    if not entries:
        return f"최근 {days}일 피드백이 없습니다"
    kinds = Counter(e.kind for e in entries)
    out = [f"# 최근 {days}일 피드백 {len(entries)}개", ""]
    out.append(", ".join(f"{KIND_NAMES[k]} {n}" for k, n in kinds.most_common()))
    for kind, _ in kinds.most_common():
        out += ["", f"## {KIND_NAMES[kind]}"]
        out += [
            f"- {e.date} {label({'slide': e.slide, 'kind': e.kind})}: {e.text} ({e.result})"
            for e in entries
            if e.kind == kind
        ]
    return "\n".join(out)


# ---------- 규칙 반영 PR (feedback-rules 워크플로) ----------

RULES_STATE = "feedback_rules.json"  # data/<계정>/ 안. 규칙 PR 에 넣은 마지막 피드백 update_id
NO_NEW = 5  # pending 종료 코드: 새 피드백 없음


def rules_state(store: Store) -> int:
    path = store.dir / RULES_STATE
    if not path.exists():
        return 0
    return int(json.loads(path.read_text(encoding="utf-8")).get("last_update_id", 0))


def _numbered(args: list[str]) -> bool:
    """ "/fix 2 …" 처럼 장·벌 번호로 시작하는지 (게시물 /fix)."""
    return bool(args) and re.match(r"\d", args[0]) is not None


def rules_requests(
    updates: list[dict], chat_id: str, account: str, after: int, plain: bool = False
) -> list[dict]:
    """피드백 규칙을 더하거나 고쳐 달라는 글 (update_id 순): "/fix 규칙 …" 또는 규칙 반영 알림(📝)에
    답장한 "/fix …" (2026-10-22 사용자). plain 이면 (고칠 미리보기·후보가 없을 때)
    다른 글에 답장하지 않은 그냥 "/fix …" (번호로 시작하지 않는 것)도. {update_id, at, text}."""
    found = []
    for u in sorted(updates, key=lambda u: u["update_id"]):
        msg = u.get("message") or {}
        if u["update_id"] <= after or str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        cmd = notify.parse(msg.get("text") or msg.get("caption") or "", account, None)
        if cmd is None:
            continue
        replied = (msg.get("reply_to_message") or {}).get("text") or ""
        replying = bool(msg.get("reply_to_message"))
        if cmd.name == "fix" and notify.RULES_MARK in replied.split("\n", 1)[0]:
            cmd = notify.Command("rules", cmd.args, cmd.labeled)
        elif cmd.name == "fix" and plain and not replying and not _numbered(cmd.args):
            cmd = notify.Command("rules", cmd.args, cmd.labeled)
        text = " ".join(cmd.args).strip()
        if cmd.name == "rules" and text:
            found.append({"update_id": u["update_id"], "at": msg.get("date", 0), "text": text})
    return found


RULES_COMMIT = "피드백 규칙 반영"  # feedback-rules 워크플로가 만든 PR 제목·커밋 앞머리


def rules_request_report(requests: list[dict], last_change: str, tz: str = "Asia/Seoul") -> str:
    """규칙 고치기 요청 재료 (feedback-rules 스킬 "사용자가 직접 고치기"가 읽음)."""
    lines = ["# 규칙 고치기 요청", ""]
    for r in requests:
        at = datetime.fromtimestamp(r.get("at", 0), ZoneInfo(tz)).strftime("%m-%d %H:%M")
        lines.append(f"- {at} {r['text']}")
    lines += ["", "# 지난 규칙 반영 (가장 최근 커밋)", ""]
    lines += ["```diff", last_change.strip() or "(찾지 못함)", "```"] if last_change else ["(없음)"]
    return "\n".join(lines) + "\n"


def last_rules_change(max_lines: int = 400) -> str:
    """main 에 들어간 가장 최근 규칙 반영 커밋 (설명과 바뀐 줄). git 이 없으면 빈 문자열."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h %ad %s%n%n%b", "--date=short", "-p",
             f"--grep={RULES_COMMIT}", "--", ".", ":!data"],
            capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return ""
    lines = out.splitlines()
    more = len(lines) - max_lines
    return "\n".join(lines[:max_lines]) + (f"\n… ({more}줄 더)" if more > 0 else "")


def pending_entries(store: Store) -> list[FeedbackEntry]:
    """아직 규칙 PR 에서 다루지 않은 피드백."""
    last = rules_state(store)
    return [e for e in store.feedback() if e.update_id > last]


def pending_report(store: Store, today: date, days: int = 30) -> str:
    """규칙 반영 재료: 새 피드백과 최근 days 일 모아 보기 (같은 지적이 몇 번 나왔는지)."""
    new = pending_entries(store)
    out = [f"# 새 피드백 {len(new)}개 ({store.account})", ""]
    for e in new:
        where = label({"slide": e.slide, "kind": e.kind})
        extra = f", 상품 {e.product}" if e.product else ""
        out.append(f"- {e.date} {where}: {e.text} (처리: {e.result}, 미리보기 {e.run}{extra})")
    out += ["", summary(store, today, days)]
    return "\n".join(out) + "\n"


def mark_rules(store: Store, upto: int, now: datetime | None = None) -> None:
    """upto 까지의 피드백을 규칙 PR 에서 다뤘다고 적는다 (워크플로가 main 에 커밋)."""
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    store.dir.mkdir(parents=True, exist_ok=True)
    (store.dir / RULES_STATE).write_text(
        json.dumps({"last_update_id": max(upto, rules_state(store)), "at": stamp}) + "\n",
        encoding="utf-8",
    )


# ---------- 피드백 반영 단계 (2026-10-22 사용자) ----------
# 게시 확인 → 반영 계획 보내기 → /ok·/fix → PR 보내기 → /merge·/fix → 합치면 다음 주제.
# 차례 흐름(tools.flow)이 단계를 넘기고, feedback-rules 워크플로(step plan·apply·revise·merge)가
# Claude 로 계획·PR 을 만들어 텔레그램으로 보낸 뒤 이 기록
# (daily-results flow/<계정>/rules.json)을 남긴다.

RULES_RECORD = "rules.json"


class RulesRound(BaseModel):
    """이번 피드백 반영 한 번의 기록."""

    plan: str = ""  # 보낸 반영 계획 (Claude 가 쓴 글)
    plan_at: int = 0  # 계획을 텔레그램으로 보낸 시각
    plan_notes: list[str] = []  # 계획에 대한 사용자 /fix (차례대로)
    upto: int = 0  # 이 반영에서 다루는 마지막 피드백 update_id
    branch: str = ""  # PR 브랜치
    pr_url: str = ""
    pr_at: int = 0  # PR 을 (다시) 텔레그램으로 보낸 시각
    pr_notes: list[str] = []  # PR 에 대한 사용자 /fix (차례대로)
    merged_at: int = 0
    failed_at: int = 0  # 워크플로가 실패한 시각 (실패 알림은 워크플로가 보냄)


def rules_round_path(results: Path, account: str) -> Path:
    return results / "flow" / account / RULES_RECORD


def load_round(results: Path, account: str) -> RulesRound:
    path = rules_round_path(results, account)
    if not path.exists():
        return RulesRound()
    return RulesRound.model_validate_json(path.read_text(encoding="utf-8"))


def save_round(results: Path, account: str, rnd: RulesRound) -> None:
    path = rules_round_path(results, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rnd.model_dump_json(indent=2) + "\n", encoding="utf-8")


def round_material(store: Store, rnd: RulesRound, step: str, today: date) -> str:
    """워크플로 step 마다 Claude 가 읽을 재료."""
    out = [f"# 단계: {step}", ""]
    if step in ("plan", "apply"):
        out.append(pending_report(store, today))
    if rnd.plan and step != "plan":
        out += ["# 확정한 반영 계획", "", rnd.plan, ""]
    if step == "plan" and rnd.plan_notes:
        out += [
            "# 지난 계획",
            "",
            rnd.plan or "(없음)",
            "",
            "# 계획에 대한 사용자 요청 (차례대로)",
            "",
        ]
        out += [f"- {n}" for n in rnd.plan_notes] + [""]
    if step == "revise":
        out += ["# PR 에 대한 사용자 요청 (차례대로, 마지막 것이 새 요청)", ""]
        out += [f"- {n}" for n in rnd.pr_notes] + [""]
    return "\n".join(out) + "\n"


def round_replies(
    updates: list[dict], chat_id: str, account: str, since: int
) -> list[tuple[int, str, str]]:
    """반영 계획·PR 을 보낸 뒤 온 답 (update_id, 명령, 글): ok·fix·merge·skip.
    "/fix 규칙 …" 은 따로 다루므로 뺀다."""
    found = []
    for u in sorted(updates, key=lambda u: u["update_id"]):
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id) or msg.get("date", 0) < since:
            continue
        cmd = notify.parse(msg.get("text") or "", account, None)
        if cmd is None or cmd.name not in ("ok", "fix", "merge", "skip"):
            continue
        text = " ".join(cmd.args).strip()
        if cmd.name == "fix" and not text:
            continue
        found.append((u["update_id"], cmd.name, text))
    return found


# ---------- CLI ----------


def ref_photos(plan_path: Path, dest: Path) -> list[tuple[int, Path | None]]:
    """기획 벌마다 따라 한 기준 코디 사진(ref_photo)을 dest 에 받는다 (얼굴은 가림, 고쳐 그릴 때
    이미지 AI 가 받는 것과 같은 사진). fix-feed 가 "참고 코디컷처럼" 을 보고 고칠 점을 쓰도록
    (2026-10-22 사용자: 참고 코디컷은 다시 보내지 않아도 알아보게). dest 는 결과 폴더 밖에 둔다."""
    from tools import outfit_gen
    from tools.plan import load_plan

    plan = load_plan(plan_path)
    profile = load_profile(plan.account)
    out: list[tuple[int, Path | None]] = []
    for i in range(len(plan.looks)):
        lp = plan.look_plan(i)
        data = outfit_gen.worn_ref_panel(lp, profile, dest / "cache", edit=True)
        path = None
        if data:
            path = dest / f"look{i + 1}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        out.append((i + 1, path))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prepare", help="daily mode=fix: 지난 결과 가져오기")
    s.add_argument("pick", help="daily pick 값 (JSON: run, round, fixes)")
    s.add_argument("--prev", type=Path, required=True, help="daily-results 브랜치를 꺼낸 폴더")
    s.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("ref-photos", help="벌마다 기준 코디 사진 받기 (fix-feed 가 봄)")
    s.add_argument("plan", type=Path)
    s.add_argument("--dest", type=Path, required=True, help="받을 폴더 (결과 폴더 밖)")
    s = sub.add_parser("summary", help="피드백 모아 보기")
    s.add_argument("--account", required=True)
    s.add_argument("--days", type=int, default=30)
    s = sub.add_parser("pending", help="규칙 PR 에 아직 넣지 않은 피드백 (없으면 종료 코드 5)")
    s.add_argument("--account", required=True)
    s.add_argument("--out", type=Path, help="재료를 쓸 파일 (기본: 화면)")
    s = sub.add_parser("rules-request", help="규칙 고치기 요청 재료 쓰기 (/fix 규칙 …)")
    s.add_argument("--account", required=True)
    s.add_argument("--request", required=True, help='JSON {"requests": [{update_id, at, text}]}')
    s.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("round-material", help="피드백 반영 단계 재료 쓰기 (워크플로 step)")
    s.add_argument("--account", required=True)
    s.add_argument("--results", type=Path, required=True, help="daily-results 를 꺼낸 폴더")
    s.add_argument("--step", choices=["plan", "apply", "revise"], required=True)
    s.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("round-record", help="피드백 반영 단계 기록 (워크플로 step 끝)")
    s.add_argument("--account", required=True)
    s.add_argument("--results", type=Path, required=True)
    s.add_argument("--plan", type=Path, help="보낸 계획 파일 (plan 단계)")
    s.add_argument("--upto", type=int, help="다루는 마지막 피드백 update_id (plan 단계)")
    s.add_argument("--branch", default="")
    s.add_argument("--pr-url", default="")
    s.add_argument("--pr", action="store_true", help="PR 을 (다시) 보냄")
    s.add_argument("--merged", action="store_true")
    s.add_argument("--failed", action="store_true")
    s.add_argument("--get", help="이 칸 값만 출력 (branch, pr_url, upto)")
    s = sub.add_parser("mark", help="규칙 PR 에서 다룬 피드백 기록")
    s.add_argument("--account", required=True)
    s.add_argument("--upto", type=int, required=True, help="다룬 마지막 update_id")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "prepare":
            data = prepare(json.loads(args.pick), args.prev, args.out)
            for f in data["fixes"]:
                print(f"- {label(f)}: {f['text']}")
            print(f"표지 코디컷: {data['cover'] or '없음 (새로 만듦)'}")
        elif args.cmd == "ref-photos":
            for n, path in ref_photos(args.plan, args.dest):
                print(f"looks[{n - 1}]: {path or '기준 코디 사진 없음'}")
        elif args.cmd == "pending":
            store = Store(args.account)
            new = pending_entries(store)
            if not new:
                print("새 피드백 없음")
                return NO_NEW
            report = pending_report(store, date.today())
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(report, encoding="utf-8")
            print(report if not args.out else f"새 피드백 {len(new)}개 → {args.out}")
            print(f"UPTO={max(e.update_id for e in new)}")
        elif args.cmd == "rules-request":
            requests = json.loads(args.request)["requests"]
            args.out.parent.mkdir(parents=True, exist_ok=True)
            tz = load_profile(args.account).schedule.timezone
            report = rules_request_report(requests, last_rules_change(), tz)
            args.out.write_text(report, encoding="utf-8")
            print(f"규칙 고치기 요청 {len(requests)}개 → {args.out}")
        elif args.cmd == "round-material":
            rnd = load_round(args.results, args.account)
            text = round_material(Store(args.account), rnd, args.step, date.today())
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"재료 → {args.out}")
        elif args.cmd == "round-record":
            rnd = load_round(args.results, args.account)
            if args.get:
                print(getattr(rnd, args.get))
                return 0
            now = int(datetime.now(UTC).timestamp())
            if args.plan:
                rnd.plan, rnd.plan_at = args.plan.read_text(encoding="utf-8").strip(), now
                rnd.upto = args.upto or rnd.upto
            if args.branch:
                rnd.branch = args.branch
            if args.pr_url:
                rnd.pr_url = args.pr_url
            if args.pr:
                rnd.pr_at = now
            if args.merged:
                rnd.merged_at = now
            if args.failed:
                rnd.failed_at = now
            save_round(args.results, args.account, rnd)
            print(rnd.model_dump_json(indent=2))
        elif args.cmd == "mark":
            mark_rules(Store(args.account), args.upto)
            print(f"규칙 PR 기록: update_id {args.upto} 까지")
        else:
            print(summary(Store(args.account), date.today(), args.days))
        return 0
    except (ValueError, FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
