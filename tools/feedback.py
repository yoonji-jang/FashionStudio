r"""최종 이미지 피드백 (15단계). 미리보기를 받은 뒤 텔레그램으로 고칠 점을 보내면 다시 만든다.

  /fix 1 신발 더 크게, 가방 오른쪽 위로     1 = 장 번호 (미리보기 사진 순서)
  /fix 캡션 도입 한 줄을 더 짧게            캡션
  /fix 전체 좀 더 밝은 분위기로             게시물 전체 (장 번호 없이 써도 같음)
  미리보기 사진 한 장에 답장으로 /fix 신발 더 크게  → 그 장

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

from core.models import FeedbackEntry, FeedbackResult, Manifest, PreviewRecord
from core.profile import Profile, load_profile, minutes
from core.store import Store
from tools import notify

CAPTION_WORDS = {"캡션", "본문", "문구", "caption"}
ALL_WORDS = {"전체", "all"}
KIND_NAMES = {
    "cover": "표지",
    "product": "상품 장",
    "summary": "요약 장",
    "comment": "댓글 안내 장",
    "slide": "장",
    "caption": "캡션",
    "all": "전체",
}
# 다시 만들기를 보낸 뒤 이만큼(분) 지나도 새 미리보기가 없으면 실패로 보고 다시 받는다
STALE_MINUTES = 120


# ---------- 장 번호 ----------


def slide_map(manifest: Manifest) -> list[dict]:
    """미리보기 사진 순서대로 장 종류. 상품 장은 기획 상품 순서와 같다 (tools.feed.render)."""
    out = []
    n_product = 0
    for i, img in enumerate(manifest.images, 1):
        stem = Path(img).stem.split("_", 1)[-1]
        entry: dict = {"no": i, "file": Path(img).name}
        if stem == "cover":
            entry["kind"] = "cover"
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
    for key in ("product", "category"):
        if key in s:
            found[key] = s[key]
    return found


def find_fixes(
    updates: list[dict], record: PreviewRecord, chat_id: str, slides: list[dict]
) -> list[dict]:
    """미리보기 뒤 등록된 채팅에서 온 이 계정·날짜의 /fix (update_id 순).
    계정·날짜 없이 봇의 다른 메시지(다른 계정·다른 날)에 답장한 글은 그쪽 것이라 뺀다."""
    found = []
    for u in sorted(updates, key=lambda u: u["update_id"]):
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        if msg.get("date", 0) < record.sent_at:
            continue
        cmd = notify.parse(msg.get("text") or "", record.account, record.date)
        if cmd is None or cmd.name != "fix":
            continue
        reply_to = (msg.get("reply_to_message") or {}).get("message_id")
        if not cmd.labeled and reply_to and reply_to not in record.message_ids:
            continue
        fix = target(cmd.args, reply_to, record, slides)
        if not fix["text"]:
            continue  # 고칠 내용이 없는 /fix
        found.append(fix | {"update_id": u["update_id"], "at": msg.get("date", 0)})
    return found


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
    """다시 만들기를 받는 마지막 시각: 게시 마지막 확인 시각 - render_minutes."""
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
    fixes = [
        f
        for f in find_fixes(tg.updates(), record, tg.chat_id, slides)
        if f["update_id"] not in seen
    ]
    if not fixes:
        return None
    result = decide(profile, store, run_dir, record, now, known)
    rnd = rounds_used(store, day) + 1 if result == "rerender" else 0
    entries = [
        FeedbackEntry(
            account=account,
            date=day,
            run=run_dir.name,
            slide=f.get("slide"),
            kind=f["kind"],
            product=f.get("product"),
            text=f["text"],
            at=f["at"],
            update_id=f["update_id"],
            result=result,
            round=rnd,
        )
        for f in fixes
    ]
    head = MESSAGES[result].format(round=rnd, max=profile.feedback.max_fixes)
    lines = [f"[{account}] {day} {head}"] + [f"- {label(f)}: {f['text']}" for f in fixes]
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
            {k: f[k] for k in ("slide", "kind", "product", "category", "text") if f.get(k)}
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
    data = {
        "run": pick["run"],
        "round": pick.get("round", 1),
        "cover": cover,
        "fixes": pick["fixes"],
        "slides": slide_map(manifest),
    }
    (out / "fix.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


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
        fix = {"slide": e.slide, "kind": e.kind}
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


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prepare", help="daily mode=fix: 지난 결과 가져오기")
    s.add_argument("pick", help="daily pick 값 (JSON: run, round, fixes)")
    s.add_argument("--prev", type=Path, required=True, help="daily-results 브랜치를 꺼낸 폴더")
    s.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("summary", help="피드백 모아 보기")
    s.add_argument("--account", required=True)
    s.add_argument("--days", type=int, default=30)
    s = sub.add_parser("pending", help="규칙 PR 에 아직 넣지 않은 피드백 (없으면 종료 코드 5)")
    s.add_argument("--account", required=True)
    s.add_argument("--out", type=Path, help="재료를 쓸 파일 (기본: 화면)")
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
