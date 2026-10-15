r"""코디 고르기 (9단계). 외부로 보내는 도구라 Claude 허용 목록에 없다.

이미지 AI 비용을 아끼려고, Claude 가 짠 코디 후보(profile pick.count 개)를 쇼핑몰 원본 사진 묶음으로
텔레그램에 보내고 사람이 고른 한 벌만 코디컷을 만든다.

- validate: 후보 기획(out/<날짜>/picks/p1.yaml …)을 검사해 문제 있는 후보를 빼고 번호를 다시 붙인다
- send: 후보마다 상품 사진 한 장짜리 모음(시트)을 만들어 보내고 picks/pick.json 을 남긴다
  버튼(/pick 1 … /again /skip)은 입력창 아래 키보드로 나온다
- check: schedule job 이 부른다. 고른 번호, "다시", 건너뛰기, 마감(on_timeout)을 판단해
  picks/picked.json 에 쓴다
- 여러 벌 게시물(20단계, profile looks): 후보는 p1.yaml 하나에 벌 여러 개(looks.count + spare).
  벌마다 시트를 보내고, 그대로 두면 앞의 looks.count 벌로 만든다. "1 2 3 5 6 7" 로 쓸 벌을 고르거나
  "4번 빼고"(/drop 4)로 그 벌만 빼면 뒤의 남는 벌이 채운다. 고른 기획은 picks/chosen.yaml
- 후보 고치기: 여러 벌 후보에 "/fix 1 이너를 나시로" 가 오면 check 가 그 벌만 고치라고
  (daily mode=edit) 요청한다.
  prepare-edit 이 지난 후보를 가져오고, Claude(outfit-candidates "후보 고치기")가 그 벌만 바꾼 뒤
  send-edit 이 고친 벌 시트만 다시 보낸다. 고르기는 그대로 이어진다 (고치기 전에 보낸 번호도 읽음)
- themes: 주간 주제를 보낸다 (review_before_use 면 "/themes again" 버튼)

봇 서버 없이 동작한다. 답장은 schedule job 이 돌 때 getUpdates 로 읽는다.

사용법 (환경 변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID):
  python -m tools.pick validate out/2026-09-28
  python -m tools.pick send out/2026-09-28 --run 2026-09-28-run31 [--round 2]
  python -m tools.pick prepare-edit '{"run": "...", "fixes": [...]}' --prev <결과 브랜치> \
      --out out/<날짜>
  python -m tools.pick send-edit out/2026-09-28 --run 2026-09-28-run33
  python -m tools.pick themes --account women --first 2026-09-29
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
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

AGAIN_MIN_LEFT = 60  # 마감까지 이만큼(분) 남아야 "다시"·후보 고치기를 받는다 (새 후보 만드는 시간)
EDIT_WAIT = 120  # 후보 고치기 실행이 이만큼(분) 지나도 끝나지 않으면 기다리지 않고 고르기를 받는다
EDITS = "edits.json"  # 고치라고 요청한 /fix (결과 폴더 picks/, 고친 후보가 올 때까지 고르기를 미룸)
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
        key = frozenset(it.product for it in plan.all_items())
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


def sheet(
    plan: DailyPlan,
    number: int,
    store: Store,
    out: Path,
    width: int = 1080,
    title: str | None = None,
) -> Path:
    """후보 한 벌을 한 장으로: 위에 번호와 주제, 아래에 상품 원본 사진 격자 (카테고리·색).
    title 이 있으면 주제 대신 그 글 (여러 벌 게시물은 벌 설명)."""
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
    head_font = load_font("Pretendard-Bold.otf", 34)
    body = load_font("Pretendard-Medium.otf", 24)
    draw.text((pad, 30), f"{number}번", font=bold, fill="#7A2E34")
    for k, line in enumerate(
        feed.wrap(feed.drawable(title or plan.theme.title_hint), head_font, width - 180, 2)
    ):
        draw.text((pad + 130, 34 + k * 44), line, font=head_font, fill="#222222")
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


def with_ref(path: Path, look, profile: Profile, out: Path, panel: int = 640) -> Path:
    """후보 시트 왼쪽에 따라 한 기준 코디 사진을 붙인다 (2026-10-13 사용자: 참고 코디도 보내 줘).
    사진을 못 받으면 "참고 사진 없음" 을 적는다.
    받은 사진은 picks/photos/ 에 둔다 (결과 브랜치에 올리지 않음)."""
    from tools import scout

    if not (look.ref or look.ref_photo):
        return path
    base = Image.open(path).convert("RGB")
    img = Image.new("RGB", (panel + base.width, base.height), "#F4F2EE")
    img.paste(base, (panel, 0))
    draw = ImageDraw.Draw(img)
    pad = 24
    draw.text(
        (pad, 34),
        f"참고 {look.ref or ''}".strip(),
        font=load_font("Pretendard-Bold.otf", 34),
        fill="#7A2E34",
    )
    box_w, box_h = panel - pad * 2, base.height - 120 - pad
    try:
        if not look.ref_photo:
            raise OSError("사진 주소 없음")
        sites = profile.themes.ref_search.scout_sites if profile.themes.ref_search else []
        name = re.sub(r"[^0-9A-Za-z]+", "_", look.ref_photo)[-80:]
        ph = Image.open(
            scout.ref_photo(look.ref_photo, sites, picks_dir(out) / "photos" / f"ref_{name}")
        )
        ph = ph.convert("RGB")
        ph.thumbnail((box_w, box_h))
        img.paste(ph, (pad + (box_w - ph.width) // 2, 120))
    except Exception as e:  # noqa: BLE001 - 참고 사진이 없어도 시트는 보낸다
        print(f"참고 사진을 받지 못함: {look.ref_photo} ({e})", file=sys.stderr)
        draw.text(
            (pad, 140),
            "참고 사진 없음",
            font=load_font("Pretendard-Medium.otf", 26),
            fill="#8C867E",
        )
    img.save(path, quality=88)
    return path


def look_sheet(plan: DailyPlan, n: int, store: Store, out: Path, profile: Profile) -> Path:
    """여러 벌 후보의 n 번째(1부터) 벌 시트: 상품 사진 격자 + 왼쪽에 기준 코디 사진."""
    look = plan.looks[n - 1]
    path = sheet(plan.look_plan(n - 1), n, store, out, title=look.reason.split(". ")[0] or None)
    return with_ref(path, look, profile, out)


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


def looks_keyboard(profile: Profile, day: date, can_again: bool) -> dict:
    lab = label(profile, day)
    last = [{"text": f"/skip {lab}"}]
    if can_again:
        last.insert(0, {"text": f"/again {lab}"})
    rows = [[{"text": f"/pick {lab}"}], last]
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}


def looks_text(
    plan: DailyPlan, profile: Profile, day: date, round_: int, count: int | None = None
) -> str:
    """여러 벌 후보 안내 (20단계). 앞의 looks.count 벌(count 가 있으면 그 수)이 기본,
    나머지는 바꿔 넣을 예비."""
    s, rules, lk = profile.schedule, profile.pick, profile.looks
    want = count or lk.count
    use = min(want, len(plan.looks))
    head = f"[{profile.id}] {day:%m-%d}({WEEKDAYS[day.weekday()]}) 코디 {len(plan.looks)}벌 후보"
    if round_ > 1:
        head += f" · 다시 {round_ - 1}번째"
    lines = [head, f"주제: {plan.theme.title_hint}", ""]
    for n, look in enumerate(plan.looks, 1):
        mark = "" if n <= use else " (예비)"
        top = f"{n}. " + (look.reason.split(". ")[0] if look.reason else "") + mark
        lines.append(top.rstrip("."))
        lines.append("   " + " · ".join(short(it.desc) for it in look.items))
    after = f"1~{use}번으로 만들어요" if rules.on_timeout == "first" else "오늘은 건너뛰어요"
    flow = profile.flow.enabled
    lines += ["", "[고르는 방법]"]
    if flow:
        # 차례 흐름 (tools.flow): 마감 없이 답을 기다린다
        lines.append(f"• 그대로 좋으면 /pick → 1~{use}번으로 만들어요 (마감 없이 기다려요)")
    else:
        lines += [
            f"• 그대로 좋으면 아무것도 안 해도 돼요. {s.pick_deadline}에 {after}",
            "  (바로 만들려면 /pick)",
        ]
    lines += [
        '• 빼고 싶은 벌이 있으면 "4번 빼고" → 예비 벌이 그 자리를 채워요',
        '• 직접 고르려면 "1 2 3 5 6 7" 처럼 번호를 보내요'
        + (f" ({lk.min}~{want}벌)" if lk.min != want else ""),
    ]
    lines.append('• 한 벌만 조금 바꾸려면 "/fix 1 이너를 나시로" → 그 벌만 고쳐 다시 보내요')
    if round_ <= rules.max_again:
        lines.append('• 다 별로면 "다시" → 새 후보를 보내요')
        lines.append(
            '• 몇 벌만 마음에 들면 "4번 남기고 다시 8벌" → 그 벌을 두고 나머지를 새로 채워요'
        )
    if flow:
        lines += [
            "• 이 주제를 쉬려면 /skip → 다음 주제를 제안해요",
            "",
            "[장면 사진]",
            "• 이 주제에 맞는 장면 사진을 이 채팅에 보내 주세요 "
            "(스트리트, 스튜디오, 거울 셀카 등).",
            "  한 장이면 그 무드로, 여러 장(앨범)이면 공통 무드로",
            "  벌마다 어울리는 사진의 자리·포즈로 그려요.",
            "  따로 다시 보내면 마지막으로 보낸 사진(묶음)을 써요",
            "→ 고른 벌과 장면 사진이 다 오면 한 시간 안에 확인해 만들고 미리보기를 보내요",
            "",
            "사진은 쇼핑몰 대표 사진이라 고른 색과 다를 수 있어요.",
        ]
        return "\n".join(lines)
    lines += [
        "• 오늘 쉬려면 /skip",
        f"  → {s.pick_check_every}분 안에 확인해 고른 벌만 코디컷을 만들고 미리보기를 보내요",
        "",
        "사진은 쇼핑몰 대표 사진이라 고른 색과 다를 수 있어요.",
    ]
    return "\n".join(lines)


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
    after = "1번으로 만들어요" if rules.on_timeout == "first" else "오늘은 건너뛰어요"
    lines += [
        "",
        "[고르는 방법]",
        '• 마음에 드는 번호를 "2번"처럼 보내거나 아래 버튼을 눌러요',
        f"  → {s.pick_check_every}분 안에 확인해 그 코디만 코디컷을 만들고,",
        "     다 되면 미리보기를 보내요",
    ]
    if round_ <= rules.max_again:
        lines.append('• 다 별로면 "다시" → 새 후보를 보내요')
    lines += [
        "• 오늘 쉬려면 /skip",
        f"• {s.pick_deadline}까지 고르지 않으면 {after}",
        "",
        "사진은 쇼핑몰 대표 사진이라 고른 색과 다를 수 있어요.",
    ]
    return "\n".join(lines)


def _wanted(out: Path, profile: Profile) -> int:
    """기본으로 쓸 벌 수.
    사용자가 벌 수를 정해 다시 짜게 했으면 (picks_request.json count) 그 수."""
    path = out / "picks_request.json"
    if path.exists():
        return int(json.loads(path.read_text(encoding="utf-8")).get("count") or profile.looks.count)
    return profile.looks.count


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
    can_again = round_ <= profile.pick.max_again
    looks = plans[0].looks
    if looks:
        # 여러 벌: 후보 기획 하나(p1) 안의 벌마다 시트 한 장
        plan = plans[0]
        sheets = [look_sheet(plan, n, store, out, profile) for n in range(1, len(looks) + 1)]
        text = looks_text(plan, profile, day, round_, _wanted(out, profile))
        markup = looks_keyboard(profile, day, can_again)
    else:
        sheets = [sheet(p, n, store, out) for n, p in enumerate(plans, 1)]
        text = control_text(plans, profile, day, round_)
        markup = keyboard(profile, day, len(plans), can_again)
    want = _wanted(out, profile)
    ids = tg.send_photos(sheets, f"[{profile.id}] {day} 코디 후보")
    sheet_ids = {mid: n for n, mid in enumerate(ids, 1)} if looks else {}
    ids.append(tg.send(text, reply_markup=markup))
    record = PickRecord(
        account=profile.id,
        date=day,
        run=run,
        round=round_,
        count=len(looks) if looks else len(plans),
        looks=min(want, len(looks)) if looks else 0,
        looks_min=min(profile.looks.min, len(looks)) if looks else 0,
        sent_at=int(time.time()),
        message_ids=ids,
        sheets=sheet_ids,
    )
    (picks_dir(out) / "pick.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return record


# ---------- 후보 고치기 (/fix N 내용) ----------


def find_edits(updates: list[dict], record: PickRecord, chat_id: str) -> list[dict]:
    """후보를 보낸 뒤 온 /fix (update_id 순): {update_id, looks: [벌 번호], text}.
    "/fix 1 이너를 나시로", "/fix 1,3 …", 벌 시트에 답장한 "/fix 이너를 나시로" 를 읽는다.
    번호를 못 읽으면 looks 가 비어 있다. 계정·날짜 없이 봇의 다른 메시지에 답장한 글은 뺀다."""
    found = []
    for u in sorted(updates, key=lambda u: u["update_id"]):
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        if msg.get("date", 0) < record.sent_at:
            continue
        reply_to = (msg.get("reply_to_message") or {}).get("message_id")
        # 한 메시지에 줄을 바꿔 /fix 를 여러 개 적으면 하나씩 따로 읽는다
        for part in re.split(r"\n(?=\s*/fix\b)", msg.get("text") or ""):
            cmd = notify.parse(part, record.account, record.date)
            if cmd is None or cmd.name != "fix":
                continue
            if not cmd.labeled and reply_to and reply_to not in record.message_ids:
                continue
            words, nums = list(cmd.args), []
            while words and re.fullmatch(r"\d{1,2}(?:,\d{1,2})*,?(?:번|번은|번에서)?,?", words[0]):
                nums += [int(x) for x in re.findall(r"\d+", words.pop(0))]
            if not nums and reply_to in record.sheets:
                nums = [record.sheets[reply_to]]
            text = " ".join(words).strip()
            if text:
                found.append(
                    {"update_id": u["update_id"], "looks": list(dict.fromkeys(nums)), "text": text}
                )
    return found


def load_edits(run_dir: Path) -> dict:
    f = run_dir / "picks" / EDITS
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def edit_request(
    run_dir: Path,
    record: PickRecord,
    updates: list[dict],
    chat_id: str,
    refused: set[int],
    final: bool,
    minutes_left: int,
    now: int,
) -> tuple[list[dict], list[dict], bool]:
    """(고칠 /fix, 받지 않은 /fix(+이유 why), 고치는 중이라 고르기를 미룰지)."""
    if not record.looks:
        return [], [], False
    pending = load_edits(run_dir)
    busy = bool(pending) and now - pending.get("at", 0) < EDIT_WAIT * 60 and not final
    seen = set(record.edits) | refused | set(pending.get("update_ids", []))
    new = [e for e in find_edits(updates, record, chat_id) if e["update_id"] not in seen]
    good, bad = [], []
    for e in new:
        wrong = [n for n in e["looks"] if not 1 <= n <= record.count]
        if not e["looks"]:
            bad.append(e | {"why": '고칠 벌 번호를 같이 보내 주세요 (예: "/fix 1 이너를 나시로").'})
        elif wrong:
            nums = ", ".join(map(str, wrong))
            bad.append(e | {"why": f"{nums}번 벌은 없어요 (1~{record.count}번)."})
        elif final or minutes_left < AGAIN_MIN_LEFT:
            why = "마감이 가까워 후보를 고칠 시간이 없어요. 지금 후보 중에서 골라 주세요."
            bad.append(e | {"why": why})
        else:
            good.append(e)
    if busy:
        return [], bad, True  # 앞서 요청한 고치기가 끝나면 그 후보에서 이어서 고친다
    return good, bad, False


def prepare_edit(spec: dict, prev: Path, out: Path) -> Path:
    """daily mode=edit 준비: 지난 후보(p1.yaml, pick.json)를 out/<날짜>/picks/ 로 가져오고
    고칠 점을 picks/edit.json 에 둔다. Claude 는 picks/p1.yaml 의 그 벌만 고친다."""
    src, dst = prev / spec["run"] / "picks", picks_dir(out)
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "p1.yaml", dst / "p1.yaml")
    shutil.copy(src / "p1.yaml", dst / "before.yaml")
    shutil.copy(src / "pick.json", dst / "prev_pick.json")
    if (src / "refused.txt").exists():
        shutil.copy(src / "refused.txt", dst / "refused.txt")
    (dst / "edit.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst / "edit.json"


def send_edit(
    out: Path, run: str, tg: notify.Telegram, profile: Profile, store: Store
) -> PickRecord:
    """고친 벌 시트만 다시 보내고 picks/pick.json 을 남긴다.
    고쳐 달라지 않은 벌은 지난 후보대로 되돌려 번호와 차례가 바뀌지 않게 한다."""
    d = picks_dir(out)
    spec = json.loads((d / "edit.json").read_text(encoding="utf-8"))
    prev = PickRecord.model_validate_json((d / "prev_pick.json").read_text(encoding="utf-8"))
    before = DailyPlan.model_validate(
        yaml.safe_load((d / "before.yaml").read_text(encoding="utf-8"))
    )
    edited = load_candidates(out)[0]
    asked = sorted({n for f in spec["fixes"] for n in f["looks"] if 1 <= n <= len(before.looks)})
    looks = list(before.looks)
    for n in asked:
        if n <= len(edited.looks):
            looks[n - 1] = edited.looks[n - 1]
    plan = before.model_copy(update={"looks": looks})
    (d / "p1.yaml").write_text(
        yaml.safe_dump(plan.model_dump(mode="json", exclude_none=True), allow_unicode=True),
        encoding="utf-8",
    )
    day = plan.date
    changed = [n for n in asked if looks[n - 1] != before.looks[n - 1]]
    lines = [f"[{profile.id}] {day:%m-%d}({WEEKDAYS[day.weekday()]}) 코디 후보 고침"]
    for f in spec["fixes"]:
        lines.append(f"• {', '.join(map(str, f['looks']))}번: {f['text']}")
    lines.append("")
    for n in asked:
        look = looks[n - 1]
        if n in changed:
            lines.append(f"{n}. " + (look.reason.split(". ")[0] if look.reason else "").rstrip("."))
            lines.append("   " + " · ".join(short(it.desc) for it in look.items))
        else:
            lines.append(f"{n}. 바꿀 상품을 찾지 못해 그대로예요")
    lines += [
        "",
        "고르는 방법은 그대로예요. 그대로 두거나 번호를 보내면 고친 벌로 만들어요.",
        f"더 고치려면 /fix 를 또 보내요. 마감은 {profile.schedule.pick_deadline}.",
    ]
    sheets = [look_sheet(plan, n, store, out, profile) for n in changed]
    ids = tg.send_photos(sheets, f"[{profile.id}] {day} 코디 후보 고침") if sheets else []
    new_sheets = dict(prev.sheets) | dict(zip(ids, changed, strict=True))
    ids.append(tg.send("\n".join(lines)))
    record = prev.model_copy(
        update={
            "run": run,
            "count": len(looks),
            "message_ids": prev.message_ids + ids,
            "sheets": new_sheets,
            "edits": prev.edits + [f["update_id"] for f in spec["fixes"]],
        }
    )
    (d / "pick.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return record


# ---------- 고른 번호 확인 ----------


@dataclass
class Action:
    # render(만들기) again(새 후보) edit(후보 고치기) skip wait(아직) done(이미 정함)
    # none(후보 없음)
    action: str
    run: str = ""
    choice: int | str = 0  # 고른 후보 번호. 여러 벌이면 "chosen" (picks/chosen.yaml)
    detail: str = ""
    fixes: list[dict] = field(default_factory=list)  # edit: 고칠 /fix [{update_id, looks, text}]
    keep: list[int] = field(default_factory=list)  # again: 남길 벌
    count: int | None = None  # again: 채울 벌 수


def latest(
    results: Path, account: str, day: date, since: int = 0
) -> tuple[Path, PickRecord] | None:
    """이 계정·날짜로 후보를 보낸 가장 최근 결과 폴더 (since 가 있으면 그 뒤에 보낸 것만)."""
    found = []
    for d in results.glob(f"{day}-run*"):
        m = re.fullmatch(rf"{day}-run(\d+)", d.name)
        f = d / "picks" / "pick.json"
        if not m or not f.exists():
            continue
        rec = PickRecord.model_validate_json(f.read_text(encoding="utf-8"))
        if rec.account == account and rec.sent_at >= since:
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
        {"pick", "again", "skip", "drop"},
        record.message_ids,
    )
    note, refused_id = "", None
    default = list(range(1, record.looks + 1))
    for uid, cmd in reversed(cmds):
        if record.looks and cmd.name in ("pick", "drop"):
            chosen, why = look_choice(cmd, record, default)
            if chosen:
                return PickDecision(choice=chosen, by="user", decided_at=now), "", None
            if why and uid not in (refused or set()):
                note, refused_id = why, uid
                break
            continue
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
        if cmd.name == "again" and (not cmd.args or record.looks):
            keep, count, why = again_request(cmd, record)
            if why:
                pass
            elif record.round > rules.max_again:
                why = f"새 후보는 {rules.max_again}번까지 받을 수 있어요"
            elif final or minutes_left < AGAIN_MIN_LEFT:
                why = "마감이 가까워 새 후보를 만들 시간이 없어요"
            else:
                decision = PickDecision(
                    choice="again", by="user", decided_at=now, keep=keep, count=count
                )
                return decision, "", None
            if uid not in (refused or set()):
                note, refused_id = f"{why}. 지금 후보 중에서 골라 주세요.", uid
            break
    if final:
        first: int | list[int] = default if record.looks else 1
        choice = first if rules.on_timeout == "first" else "skip"
        return PickDecision(choice=choice, by="timeout", decided_at=now), note, refused_id
    return None, note, refused_id


def again_request(cmd: notify.Command, record: PickRecord) -> tuple[list[int], int | None, str]:
    """ "4번 남기고 다시 8벌" → (남길 벌, 채울 벌 수, 받지 않은 이유)."""
    size = [int(a[:-1]) for a in cmd.args if re.fullmatch(r"\d+벌", a)]
    keep = [int(x) for a in cmd.args if not a.endswith("벌") for x in re.findall(r"\d+", a)]
    keep = list(dict.fromkeys(keep))
    bad = [n for n in keep if not 1 <= n <= record.count]
    if bad:
        return [], None, f"{', '.join(map(str, bad))}번 벌은 없어요 (1~{record.count}번)."
    count = size[0] if size else (record.count if keep else None)
    if count is not None and not len(keep) < count <= 12:
        return [], None, f"채울 벌 수는 남길 벌보다 많고 12벌까지예요 (지금 {count}벌)."
    return keep, count, ""


def again_pick(act: Action) -> str:
    """daily candidates 의 pick 값.
    남길 벌이 있으면 다시 짜기 요청 JSON (tools.flow use-theme --out)."""
    if not act.keep and act.count is None:
        return act.run
    return json.dumps({"again": act.run, "keep": act.keep, "count": act.count}, ensure_ascii=False)


def look_choice(cmd: notify.Command, record: PickRecord, default: list[int]) -> tuple[list, str]:
    """여러 벌 고르기 명령을 쓸 벌 번호 목록으로. (번호들, 받지 않은 이유)"""
    nums = [int(x) for a in cmd.args for x in re.findall(r"\d+", a)]
    if cmd.name == "pick" and not nums:
        return default, ""  # 그대로 바로 만들기
    bad = [n for n in nums if not 1 <= n <= record.count]
    if bad:
        return [], f"{', '.join(map(str, bad))}번 벌은 없어요 (1~{record.count}번)."
    if cmd.name == "drop":
        nums = [n for n in range(1, record.count + 1) if n not in nums][: record.looks]
    nums = list(dict.fromkeys(nums))
    lo, hi = record.looks_min or record.looks, record.looks
    if not lo <= len(nums) <= hi:
        span = f"{lo}~{hi}" if lo != hi else f"{hi}"
        return [], f"{span}벌을 골라 주세요 (지금 {len(nums)}벌)."
    return nums, ""


def write_chosen(run_dir: Path, nums: list[int]) -> Path:
    """여러 벌 후보(p1)에서 고른 벌만 그 순서대로 담은 기획 (picks/chosen.yaml)."""
    plan = load_candidates(run_dir)[0]
    chosen = plan.model_copy(update={"looks": [plan.looks[n - 1] for n in nums]})
    path = picks_dir(run_dir) / "chosen.yaml"
    path.write_text(
        yaml.safe_dump(chosen.model_dump(mode="json", exclude_none=True), allow_unicode=True),
        encoding="utf-8",
    )
    return path


def check(
    profile: Profile,
    day: date,
    results: Path,
    tg: notify.Telegram | None,
    final: bool,
    minutes_left: int,
    now: datetime | None = None,
    hold: str = "",
    since: int = 0,
) -> Action:
    """schedule job 의 고르기 확인 한 번. 결정은 picks/picked.json 에 쓴다 (커밋은 워크플로).
    hold 가 있으면 고른 뒤 바로 만들지 않고 그 말로 알린다
    (차례 흐름에서 장면 사진을 기다릴 때, tools.flow).
    since 가 있으면 그 뒤에 보낸 후보만 본다
    (같은 날짜로 주제를 다시 정했을 때 지난 후보를 보지 않게)."""
    ts = int((now or datetime.now()).timestamp())
    tag = f"[{profile.id}] {day:%m-%d}"
    found = latest(results, profile.id, day, since)
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
    edits, refused_edits, busy = edit_request(
        run_dir, record, updates, chat, refused, final, minutes_left, ts
    )
    if refused_edits:
        refused |= {e["update_id"] for e in refused_edits}
        refused_file.write_text(" ".join(map(str, sorted(refused))))
        if tg is not None:
            tg.send(f"{tag} " + " ".join(dict.fromkeys(e["why"] for e in refused_edits)))
    if edits:
        (run_dir / "picks" / EDITS).write_text(
            json.dumps({"update_ids": [e["update_id"] for e in edits], "at": ts}), encoding="utf-8"
        )
        if tg is not None:
            todo = ", ".join(dict.fromkeys(str(n) for e in edits for n in e["looks"]))
            tg.send(
                f"{tag} {todo}번 벌을 고치고 있어요. 다 되면 그 벌만 다시 보낼게요. "
                "고른 번호가 있으면 고친 뒤에 반영해요."
            )
        return Action("edit", run_dir.name, detail="후보 고치기", fixes=edits)
    if busy:
        return Action("wait", run_dir.name, detail="후보를 고치는 중")
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
        act = Action(
            "again", run_dir.name, detail="새 후보", keep=decision.keep, count=decision.count
        )
        if decision.keep:
            kept = ", ".join(map(str, decision.keep))
            msgs.append(
                f"{kept}번 벌은 남기고 {decision.count}벌을 채워 다시 짜고 있어요. "
                "다 되면 다시 보낼게요."
            )
        else:
            msgs.append("새 후보를 만들고 있어요. 다 되면 다시 보낼게요.")
    elif decision.choice == "skip":
        act = Action("skip", run_dir.name, detail=decision.by)
        msgs.append(f"{late}오늘은 건너뛸게요.")
    elif isinstance(decision.choice, list):
        write_chosen(run_dir, decision.choice)
        act = Action("render", run_dir.name, "chosen", decision.by)
        nums = ", ".join(map(str, decision.choice))
        if hold:
            msgs.append(f"{nums}번 벌로 정했어요. {hold}")
        else:
            msgs.append(f"{late}{nums}번 벌로 코디컷을 만들어요. 다 되면 미리보기를 보낼게요.")
    else:
        act = Action("render", run_dir.name, int(decision.choice), decision.by)
        msgs.append(
            f"{decision.choice}번으로 정했어요. {hold}"
            if hold
            else f"{late}{decision.choice}번으로 코디컷을 만들어요. 다 되면 미리보기를 보낼게요."
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
    s = sub.add_parser("prepare-edit", help="후보 고치기 준비 (daily mode=edit)")
    s.add_argument("spec", help='{"run": 지난 결과 폴더, "fixes": [{update_id, looks, text}]} JSON')
    s.add_argument("--prev", type=Path, required=True, help="daily-results 브랜치 폴더")
    s.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("send-edit", help="고친 벌 시트만 다시 보내기")
    s.add_argument("out", type=Path)
    s.add_argument("--run", required=True, help="결과 폴더 이름 (daily-results 브랜치)")
    s = sub.add_parser(
        "status", help="보낸 후보에 온 답장을 읽어 어떻게 정할지 보여 줌 (기록 안 함)"
    )
    s.add_argument(
        "run_dir", type=Path, help="후보를 보낸 결과 폴더 (daily-results/<날짜>-run<번호>)"
    )
    s = sub.add_parser("themes")
    s.add_argument("--account", required=True)
    s.add_argument("--first", required=True, help="첫 주제 날짜")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "prepare-edit":
            f = prepare_edit(json.loads(args.spec), args.prev, args.out)
            print(f"후보 고치기 준비 → {f}")
            return 0
        if args.cmd == "status":
            f = args.run_dir / "picks" / "pick.json"
            record = PickRecord.model_validate_json(f.read_text(encoding="utf-8"))
            tg = notify.Telegram.from_env()
            profile = load_profile(record.account)
            d, note, _ = decide(
                tg.updates(), record, tg.chat_id, profile, False, 24 * 60, int(time.time())
            )
            print(f"{record.run} ({record.account} {record.date}) 후보 {record.count}개")
            print(f"읽은 결정: {d.choice if d else '아직 없음'}" + (f" / {note}" if note else ""))
            return 0
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
        if args.cmd == "send-edit":
            rec = send_edit(args.out, args.run, notify.Telegram.from_env(), profile, store)
            print(f"고친 후보 보냄 → {picks_dir(args.out) / 'pick.json'}")
            return 0
        rec = send(args.out, args.run, notify.Telegram.from_env(), profile, store, args.round)
        print(f"후보 {rec.count}개 보냄 → {picks_dir(args.out) / 'pick.json'}")
        return 0
    except (ValueError, FileNotFoundError, RuntimeError, KeyError, httpx.HTTPError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
