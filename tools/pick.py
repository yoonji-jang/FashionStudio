r"""코디 고르기 (9단계). 외부로 보내는 도구라 Claude 허용 목록에 없다.

Photoroom 을 아끼려고, Claude 가 짠 코디 후보(profile pick.count 개)를 쇼핑몰 원본 사진 묶음으로
텔레그램에 보내고 사람이 고른 한 벌만 코디컷을 만든다.

- validate: 후보 기획(out/<날짜>/picks/p1.yaml …)을 검사해 문제 있는 후보를 빼고 번호를 다시 붙인다
- send: 후보마다 상품 사진 한 장짜리 모음(시트)을 만들어 보내고 picks/pick.json 을 남긴다
  버튼(/pick 1 … /again /skip)은 입력창 아래 키보드로 나온다
- check: schedule job 이 부른다. 고른 번호, "다시", 건너뛰기, 마감(on_timeout)을 판단해
  picks/picked.json 에 쓴다
- themes: 주간 주제를 보낸다 (review_before_use 면 "/themes again" 버튼)

봇 서버 없이 동작한다. 답장은 schedule job 이 돌 때 getUpdates 로 읽는다.

사용법 (환경 변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID):
  python -m tools.pick validate out/2026-09-28
  python -m tools.pick send out/2026-09-28 --run 2026-09-28-run31 [--round 2]
  python -m tools.pick themes --account women --first 2026-09-29
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx
import yaml
from PIL import Image, ImageDraw

from core.models import DailyPlan, PickDecision, PickRecord, ThemesRecord
from core.profile import Profile, load_profile
from core.store import Store
from tools import feed, notify
from tools.overlay import load_font

AGAIN_MIN_LEFT = 60  # 마감까지 이만큼(분) 남아야 "다시"를 받는다 (새 후보 만드는 시간)
WEEKDAYS = "월화수목금토일"


def picks_dir(out: Path) -> Path:
    return out / "picks"


def candidate_paths(out: Path) -> list[Path]:
    found = []
    for p in picks_dir(out).glob("p*.yaml"):
        m = re.fullmatch(r"p(\d+)\.yaml", p.name)
        if m:
            found.append((int(m.group(1)), p))
    return [p for _, p in sorted(found)]


def load_candidates(out: Path) -> list[DailyPlan]:
    from tools.plan import load_plan

    return [load_plan(p) for p in candidate_paths(out)]


def short(desc: str) -> str:
    """ "블랙 레더자켓 (black leather jacket)" → "블랙 레더자켓" """
    return desc.split(" (")[0].strip()


# ---------- 검사 ----------


def validate(out: Path, profile: Profile, store: Store) -> list[str]:
    """후보를 검사해 오류가 있거나 다른 후보와 상품 구성이 같은 후보를 빼고
    p1 부터 다시 번호를 붙인다.
    남긴 후보가 없으면 ValueError. 뺀 이유 목록을 돌려준다."""
    from tools.plan import check

    notes: list[str] = []
    kept: list[DailyPlan] = []
    seen: set[frozenset[str]] = set()
    paths = candidate_paths(out)
    for path in paths:
        try:
            plan = DailyPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (ValueError, yaml.YAMLError) as e:
            notes.append(f"{path.name}: 읽을 수 없음 ({str(e).splitlines()[0]})")
            continue
        errors, _ = check(plan, profile, store, draft=True)
        if errors:
            notes.append(f"{path.name}: " + "; ".join(errors))
            continue
        key = frozenset(it.product for it in plan.items)
        if key in seen:
            notes.append(f"{path.name}: 앞 후보와 상품 구성이 같음")
            continue
        seen.add(key)
        kept.append(plan)
    kept = kept[: profile.pick.count]
    if not kept:
        raise ValueError("쓸 수 있는 코디 후보가 없습니다:\n" + "\n".join(f"- {n}" for n in notes))
    for path in paths:
        path.unlink()
    for i, plan in enumerate(kept, 1):
        (picks_dir(out) / f"p{i}.yaml").write_text(
            yaml.safe_dump(plan.model_dump(mode="json", exclude_none=True), allow_unicode=True),
            encoding="utf-8",
        )
    return notes


# ---------- 후보 시트 ----------


def sheet(plan: DailyPlan, number: int, store: Store, out: Path, width: int = 1080) -> Path:
    """후보 한 벌을 한 장으로: 위에 번호와 주제, 아래에 상품 원본 사진 격자 (카테고리·색)."""
    products = {p.id: p for p in store.products()}
    pad = 24
    cols = 4 if len(plan.items) > 6 else 3  # 7개 이상이면 두 줄에 담는다
    tile = (width - pad * (cols + 1)) // cols
    photo_h = int(tile * 1.25)
    label_h = 70
    rows = (len(plan.items) + cols - 1) // cols
    head_h = 120
    img = Image.new("RGB", (width, head_h + rows * (photo_h + label_h + pad) + pad), "#F4F2EE")
    draw = ImageDraw.Draw(img)
    bold = load_font("Pretendard-Bold.otf", 44)
    title = load_font("Pretendard-Bold.otf", 34)
    body = load_font("Pretendard-Medium.otf", 24)
    draw.text((pad, 30), f"{number}번", font=bold, fill="#7A2E34")
    for k, line in enumerate(
        feed.wrap(feed.drawable(plan.theme.title_hint), title, width - 180, 2)
    ):
        draw.text((pad + 130, 34 + k * 44), line, font=title, fill="#222222")
    photo_dir = picks_dir(out) / "photos"
    for i, it in enumerate(plan.items):
        x = pad + (i % cols) * (tile + pad)
        y = head_h + (i // cols) * (photo_h + label_h + pad)
        draw.rectangle((x, y, x + tile, y + photo_h), fill="#FFFFFF")
        p = products.get(it.product)
        index = it.photo if it.photo is not None else it.cutout_photo
        try:
            if p is None:
                raise OSError("상품 창고에 없음")
            url = str(p.image_urls[min(index, len(p.image_urls) - 1)])
            path = feed.fetch_photo(url, photo_dir / f"{p.id.split(':')[-1]}_{index}.jpg")
            ph = Image.open(path).convert("RGB")
            ph.thumbnail((tile, photo_h))
            img.paste(ph, (x + (tile - ph.width) // 2, y + (photo_h - ph.height) // 2))
        except Exception as e:  # noqa: BLE001 - 사진 한 장이 없어도 시트는 보낸다
            print(f"사진을 받지 못함: {it.product} ({e})", file=sys.stderr)
            draw.text((x + 20, y + photo_h // 2), "사진 없음", font=body, fill="#8C867E")
        cat = p.category if p else "?"
        label = f"{cat} · {it.color}" if it.color else cat
        draw.text((x, y + photo_h + 8), label, font=body, fill="#8C867E")
        name = feed.wrap(feed.drawable(short(it.desc)), body, tile, 1)
        draw.text((x, y + photo_h + 38), name[0] if name else "", font=body, fill="#222222")
    dest = picks_dir(out) / "sheets" / f"p{number}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, quality=88)
    return dest


# ---------- 보내기 ----------


def label(profile: Profile, day: date) -> str:
    return notify.command_label(profile, day)


def keyboard(profile: Profile, day: date, count: int, can_again: bool) -> dict:
    lab = label(profile, day)
    nums = [{"text": f"/pick {n} {lab}"} for n in range(1, count + 1)]
    rows = [nums[i : i + 3] for i in range(0, len(nums), 3)]
    last = [{"text": f"/skip {lab}"}]
    if can_again:
        last.insert(0, {"text": f"/again {lab}"})
    rows.append(last)
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}


def control_text(plans: list[DailyPlan], profile: Profile, day: date, round_: int) -> str:
    s, rules = profile.schedule, profile.pick
    head = f"[{profile.id}] {day:%m-%d}({WEEKDAYS[day.weekday()]}) 코디 후보 {len(plans)}개"
    if round_ > 1:
        head += f" · 다시 {round_ - 1}번째"
    lines = [head]
    themes = {p.theme.title_hint for p in plans}
    if len(themes) == 1:
        lines.append(f"주제: {plans[0].theme.title_hint}")
    lines.append("")
    for n, p in enumerate(plans, 1):
        top = f"{n}. " + (p.theme.title_hint if len(themes) > 1 else p.reason.split(". ")[0])
        lines.append(top.rstrip("."))
        lines.append("   " + " · ".join(short(it.desc) for it in p.items))
    lines.append("")
    lines.append("번호를 누르면 그 코디만 코디컷을 만들어요.")
    after = "1번으로 만들어요" if rules.on_timeout == "first" else "오늘은 건너뛰어요"
    lines.append(f"{s.pick_deadline}까지 고르지 않으면 {after}.")
    if round_ <= rules.max_again:
        lines.append("마음에 드는 게 없으면 /again 으로 새 후보를 받아요.")
    lines.append("사진은 쇼핑몰 대표 사진이라 고른 색과 다를 수 있어요.")
    return "\n".join(lines)


def send(
    out: Path,
    run: str,
    tg: notify.Telegram,
    profile: Profile,
    store: Store,
    round_: int = 1,
) -> PickRecord:
    plans = load_candidates(out)
    if not plans:
        raise FileNotFoundError(f"코디 후보가 없습니다: {picks_dir(out)}")
    day = plans[0].date
    sheets = [sheet(p, n, store, out) for n, p in enumerate(plans, 1)]
    ids = tg.send_photos(sheets, f"[{profile.id}] {day} 코디 후보")
    markup = keyboard(profile, day, len(plans), round_ <= profile.pick.max_again)
    ids.append(tg.send(control_text(plans, profile, day, round_), reply_markup=markup))
    record = PickRecord(
        account=profile.id,
        date=day,
        run=run,
        round=round_,
        count=len(plans),
        sent_at=int(time.time()),
        message_ids=ids,
    )
    (picks_dir(out) / "pick.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return record


# ---------- 고른 번호 확인 ----------


@dataclass
class Action:
    action: str  # render(만들기) again(새 후보) skip wait(아직) done(이미 정함) none(후보 없음)
    run: str = ""
    choice: int = 0
    detail: str = ""


def latest(results: Path, account: str, day: date) -> tuple[Path, PickRecord] | None:
    """이 계정·날짜로 후보를 보낸 가장 최근 결과 폴더."""
    found = []
    for d in results.glob(f"{day}-run*"):
        m = re.fullmatch(rf"{day}-run(\d+)", d.name)
        f = d / "picks" / "pick.json"
        if not m or not f.exists():
            continue
        rec = PickRecord.model_validate_json(f.read_text(encoding="utf-8"))
        if rec.account == account:
            found.append((int(m.group(1)), d, rec))
    if not found:
        return None
    _, d, rec = max(found, key=lambda x: x[0])
    return d, rec


def load_decision(run_dir: Path) -> PickDecision | None:
    f = run_dir / "picks" / "picked.json"
    return PickDecision.model_validate_json(f.read_text(encoding="utf-8")) if f.exists() else None


def decide(
    updates: list[dict],
    record: PickRecord,
    chat_id: str,
    profile: Profile,
    final: bool,
    minutes_left: int,
    now: int,
    refused: set[int] | None = None,
) -> tuple[PickDecision | None, str, int | None]:
    """(결정, 알릴 말, 받지 않은 "다시" 답장의 update_id).
    가장 마지막 명령이 이긴다. 마감(final)인데 결정이 없으면 on_timeout 대로 정한다."""
    rules = profile.pick
    cmds = notify.commands(
        updates,
        chat_id,
        record.sent_at,
        record.account,
        record.date,
        {"pick", "again", "skip"},
        record.message_ids,
    )
    note, refused_id = "", None
    for uid, cmd in reversed(cmds):
        if cmd.name == "pick":
            if (
                len(cmd.args) == 1
                and cmd.args[0].isdigit()
                and 1 <= int(cmd.args[0]) <= record.count
            ):
                return PickDecision(choice=int(cmd.args[0]), by="user", decided_at=now), "", None
            continue
        if cmd.name == "skip" and not cmd.args:
            return PickDecision(choice="skip", by="user", decided_at=now), "", None
        if cmd.name == "again" and not cmd.args:
            if record.round > rules.max_again:
                why = f"새 후보는 {rules.max_again}번까지 받을 수 있어요"
            elif final or minutes_left < AGAIN_MIN_LEFT:
                why = "마감이 가까워 새 후보를 만들 시간이 없어요"
            else:
                return PickDecision(choice="again", by="user", decided_at=now), "", None
            if uid not in (refused or set()):
                note, refused_id = f"{why}. 지금 후보 중에서 골라 주세요.", uid
            break
    if final:
        choice = 1 if rules.on_timeout == "first" else "skip"
        return PickDecision(choice=choice, by="timeout", decided_at=now), note, refused_id
    return None, note, refused_id


def check(
    profile: Profile,
    day: date,
    results: Path,
    tg: notify.Telegram | None,
    final: bool,
    minutes_left: int,
    now: datetime | None = None,
) -> Action:
    """schedule job 의 고르기 확인 한 번. 결정은 picks/picked.json 에 쓴다 (커밋은 워크플로)."""
    ts = int((now or datetime.now()).timestamp())
    tag = f"[{profile.id}] {day:%m-%d}"
    found = latest(results, profile.id, day)
    if found is None:
        if final and tg is not None:
            tg.send(f"{tag} 코디 후보가 없어 오늘 게시물을 만들지 못했어요")
        return Action("none", detail="후보를 보낸 기록이 없음")
    run_dir, record = found
    decided = load_decision(run_dir)
    if decided is not None:
        if decided.choice == "again":
            return Action("wait", run_dir.name, detail="새 후보를 만드는 중")
        return Action("done", run_dir.name, detail=f"이미 정함 ({decided.choice})")

    refused_file = run_dir / "picks" / "refused.txt"
    refused = set()
    if refused_file.exists():
        refused = {int(x) for x in refused_file.read_text().split() if x.strip()}
    updates = tg.updates() if tg is not None else []
    chat = tg.chat_id if tg is not None else ""
    decision, note, refused_id = decide(
        updates, record, chat, profile, final, minutes_left, ts, refused
    )
    if refused_id is not None:
        refused_file.write_text(" ".join(map(str, sorted(refused | {refused_id}))))
    msgs = [note] if note else []
    if decision is None:
        if tg is not None and msgs:
            tg.send(f"{tag} " + " ".join(msgs))
        return Action("wait", run_dir.name, detail="아직 고르지 않음")

    (run_dir / "picks" / "picked.json").write_text(
        decision.model_dump_json(indent=2), encoding="utf-8"
    )
    late = f"{profile.schedule.pick_deadline}까지 고르지 않아 " if decision.by == "timeout" else ""
    if decision.choice == "again":
        act = Action("again", run_dir.name, detail="새 후보")
        msgs.append("새 후보를 만들고 있어요. 다 되면 다시 보낼게요.")
    elif decision.choice == "skip":
        act = Action("skip", run_dir.name, detail=decision.by)
        msgs.append(f"{late}오늘은 건너뛸게요.")
    else:
        act = Action("render", run_dir.name, int(decision.choice), decision.by)
        msgs.append(
            f"{late}{decision.choice}번으로 코디컷을 만들어요. 다 되면 미리보기를 보낼게요."
        )
    if tg is not None:
        tg.send(f"{tag} " + " ".join(msgs))
    return act


# ---------- 주간 주제 ----------


def themes_path(store: Store) -> Path:
    return store.dir / "themes_review.json"


def send_themes(profile: Profile, store: Store, first: date, tg: notify.Telegram) -> ThemesRecord:
    themes = [t for t in store.themes() if t.date >= first][: profile.themes.per_week]
    if not themes:
        raise ValueError(f"{first} 부터의 주제가 없습니다 (data/{profile.id}/themes.json)")
    lines = [f"[{profile.id}] 다음 주 주제 {len(themes)}개"]
    for t in themes:
        lines.append(
            f"{t.date:%m-%d}({WEEKDAYS[t.date.weekday()]}) [{t.type or '-'}] {t.title_hint}"
        )
    markup = None
    if profile.themes.review_before_use:
        lines += ["", "그대로 두면 이 주제로 진행해요. 다시 정하려면 /themes again"]
        cmd = f"/themes again {profile.id}"
        markup = {"keyboard": [[{"text": cmd}]], "resize_keyboard": True, "one_time_keyboard": True}
    mid = tg.send("\n".join(lines), reply_markup=markup)
    record = ThemesRecord(
        account=profile.id,
        first=themes[0].date,
        last=themes[-1].date,
        sent_at=int(time.time()),
        message_ids=[mid],
    )
    themes_path(store).write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return record


def themes_again(profile: Profile, store: Store, tg: notify.Telegram) -> ThemesRecord | None:
    """주간 주제에 "/themes again" 답장이 왔으면 기록을 처리됨으로 바꾸고 돌려준다 (한 번만)."""
    if not profile.themes.review_before_use or not themes_path(store).exists():
        return None
    rec = ThemesRecord.model_validate_json(themes_path(store).read_text(encoding="utf-8"))
    if rec.handled:
        return None
    cmds = notify.commands(
        tg.updates(), tg.chat_id, rec.sent_at, profile.id, None, {"themes"}, rec.message_ids
    )
    if not any(c.args == ["again"] for _, c in cmds):
        return None
    rec.handled = True
    themes_path(store).write_text(rec.model_dump_json(indent=2), encoding="utf-8")
    tg.send(f"[{profile.id}] 주제를 다시 정하고 있어요. 다 되면 보낼게요.")
    return rec


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("validate")
    s.add_argument("out", type=Path)
    s = sub.add_parser("send")
    s.add_argument("out", type=Path)
    s.add_argument("--run", required=True, help="결과 폴더 이름 (daily-results 브랜치)")
    s.add_argument("--round", type=int, default=1)
    s = sub.add_parser("themes")
    s.add_argument("--account", required=True)
    s.add_argument("--first", required=True, help="첫 주제 날짜")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "themes":
            profile, store = load_profile(args.account), Store(args.account)
            rec = send_themes(
                profile, store, date.fromisoformat(args.first), notify.Telegram.from_env()
            )
            print(f"주제 보냄: {rec.first} ~ {rec.last}")
            return 0
        plans = load_candidates(args.out)
        if not plans:
            raise FileNotFoundError(f"코디 후보가 없습니다: {picks_dir(args.out)}")
        profile, store = load_profile(plans[0].account), Store(plans[0].account)
        if args.cmd == "validate":
            for n in validate(args.out, profile, store):
                print(f"뺀 후보: {n}")
            print(f"후보 {len(candidate_paths(args.out))}개")
            return 0
        rec = send(args.out, args.run, notify.Telegram.from_env(), profile, store, args.round)
        print(f"후보 {rec.count}개 보냄 → {picks_dir(args.out) / 'pick.json'}")
        return 0
    except (ValueError, FileNotFoundError, RuntimeError, httpx.HTTPError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
