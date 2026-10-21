r"""차례 흐름 (2026-10-14 사용자). 시각표 대신 한 게시물씩 차례로 간다.
Claude 는 부르지 않는다.

profile `flow.enabled` 인 계정은 schedule 이 깨어날 때마다(매시, cron=auto) tick 을 한 번 돈다.
사람의 답이 필요한 단계는 마감 없이 답이 올 때까지 기다린다.
한 게시물이 게시되면 다음 주제를 시작한다.

  theme         다음 게시 날짜를 정하고 주제 제안을 요청
                (theme-options 워크플로, Claude plan-themes "제안")
                profile flow.week_options 면 고른 주간 주제가 남아 있으면 다음 것을 바로 쓰고,
                다 썼으면 주간 후보(보통 10개)를 요청한다 (2026-10-19 사용자)
                flow.theme_review 면 남은 주제로 시작하기 전에 theme_review 로
  theme_review  남은 주간 주제 목록을 보내고 답을 기다림 (2026-10-21 사용자):
                "좋아" = 1번으로 시작, "3 1 2" = 이 순서로 (빠진 번호는 뺌) 바로 시작,
                "새로" = 주간 후보를 새로 받음,
                그 밖의 글 = 고칠 점: Claude 가 남은 목록을 고쳐
                주간 후보처럼 다시 보낸다 (theme_wait)
  theme_wait    제안 주제에 답 기다림: "2" / "/theme 2" = 그 주제,
                "/theme 니트 레이어드" = 이 말로 주제를 새로 짜서 바로 진행, "다시" = 새 제안.
                정하면 주제를 themes.json 에 넣고 코디 후보 요청
                (daily mode=candidates, 주제 검색어로 수집)
                주간 후보면: "1 3 4 5 7 8 10" = 그 주제들을 이 순서로 쓴다,
                그 밖의 글("3번은 데이트룩으로", "다시")은 고칠 점:
                Claude 가 후보를 고쳐 다시 보낸다
                (고를 번호까지 적었으면 고친 뒤 바로 정한다)
  pick_wait     코디 고르기(tools.pick)와 장면 사진(한 장 또는 여러 장)을 기다림.
                둘 다 오면 만들기 요청 (daily mode=render)
  rendering     미리보기를 기다림
  preview_wait  /fix 면 고쳐 만들기 (daily mode=fix), /cover 면 표지 바꾸기,
                /ok 면 바로 게시 요청 (publish), /skip 이면 이 주제를 버리고 theme 으로
  publishing    게시 기록이 생기면 theme 으로 (다음 주제).
                flow.rules_after_post 면 그동안 받은 /fix 피드백을 규칙에 반영하라고
                feedback-rules 워크플로를 요청한다 (새 피드백이 있을 때만, 2026-10-21 사용자)

상태는 daily-results 의 flow/<계정>/state.json, 장면 사진은 flow/<계정>/<날짜>/scene.jpg
(여러 장이면 scene_2.jpg, scene_3.jpg …: 공통 무드는 함께 읽고
 벌마다 어울리는 사진의 자리·포즈로, 2026-10-15)
(사진에 붙인 말은 같은 폴더 scene.txt: 모든 벌에 반영할 요청, 예 "슬릭번")
(커밋은 schedule 워크플로가 한다).
제안 주제는 data/<계정>/theme_options.json (theme-options 워크플로가 커밋).

사용법:
  python -m tools.flow status --account women --results <daily-results 폴더>
  python -m tools.flow options --account women --date 2026-10-16 \
      --file out/theme_options.yaml [--hint …] [--week] [--send]
      Claude 가 쓴 제안 주제를 검사해 data/<계정>/theme_options.json 에 저장하고
      텔레그램으로 보낸다 (--week: 주간 후보)
  python -m tools.flow reset --account women --results <폴더>
      상태를 지우고 다음 tick 에 주제부터
  python -m tools.flow use-theme --account women --json '{"theme": {...}}'
      고른 주제를 themes.json 에 (daily candidates 가 부름)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal, NamedTuple
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field

from core.models import Theme
from core.profile import WEEKDAYS, Profile, load_profile
from core.store import Store

Stage = Literal[
    "theme",
    "theme_wait",
    "theme_review",
    "refs_wait",
    "ref_pick_wait",
    "pick_wait",
    "rendering",
    "preview_wait",
    "publishing",
]
WAIT_ALERT = timedelta(hours=3)  # 워크플로를 요청하고 이만큼 지나도 결과가 없으면 한 번 알림
NO_DEADLINE = 10**6  # tools.pick 에 넘기는 "마감까지 남은 분" (마감 없음)


class FlowState(BaseModel):
    """한 계정의 지금 단계 (daily-results flow/<계정>/state.json)."""

    account: str
    stage: Stage = "theme"
    date: dt.date | None = None  # 지금 만드는 게시물 날짜
    asked_at: int = 0  # 마지막으로 워크플로를 요청한 시각 (유닉스 초)
    hint: str = ""  # 사용자가 직접 적은 주제 (있으면 제안 1개를 바로 씀)
    theme_at: int = 0  # 주제를 정한 시각 (이 뒤에 온 사진을 장면으로 본다)
    pick: str = ""  # 고른 후보 (<결과 폴더>/chosen 또는 /<번호>)
    scene: str = ""  # 장면 사진 (daily-results 기준 경로, 여러 장이면 첫 장)
    scenes: list[str] = []  # 장면 사진 전부 (scene.jpg, scene_2.jpg …)
    scene_update: int = 0  # 장면 사진을 받은 텔레그램 update_id (마지막 장)
    scene_at: int = 0  # 마지막 장면 사진을 보낸 시각 (이어 보낸 사진을 한 묶음으로)
    alerted: bool = False  # 이 단계에서 늦음 알림을 보냈는지
    week: list[Theme] = []  # 고른 주간 주제 중 아직 안 쓴 것 (차례대로, flow.week_options)
    read_upto: int = 0  # 주간 후보에 온 답을 여기(보낸 시각)까지 읽었다
    revising: bool = False  # 주간 후보 고치기를 요청했고 고친 후보가 아직 안 옴
    ref_pick_at: int = 0  # 이 주제의 참고 코디 묶음을 보낸 시각 (flow.ref_pick, 0 이면 아직)
    log: list[str] = []  # 단계 기록 (최근 30줄)

    def note(self, now: datetime, text: str) -> None:
        self.log = [*self.log, f"{now:%m-%d %H:%M} {text}"][-30:]


class ThemeOptions(BaseModel):
    """제안 주제 (data/<계정>/theme_options.json). theme-options 워크플로가 쓰고 보낸다."""

    account: str
    date: dt.date
    options: list[Theme] = Field(min_length=1)
    hint: str = ""
    sent_at: int = 0
    message_ids: list[int] = []
    week: bool = False  # 주간 후보 (flow.week_options)
    chosen: list[int] = []  # 주간 후보에서 쓸 번호 (고칠 점에 번호까지 적었으면 Claude 가 채움)


# 주간 주제를 정하면 그 주제로 참고 코디를 모은 뒤(themes refs_only → collect from_refs)
# 첫 후보를 만든다. 수집이 끝나면 collect 가 data/<계정>/refs_done.json 을 남긴다.
# 이만큼 지나도 안 끝나면 있는 참고 코디로 간다
REFS_WAIT = timedelta(hours=8)


def refs_done_at(store: Store) -> int:
    """참고 코디 수집(collect from_refs)이 마지막으로 끝난 시각 (없으면 0)."""
    path = store.dir / "refs_done.json"
    if not path.exists():
        return 0
    return int(json.loads(path.read_text(encoding="utf-8")).get("done_at", 0))


def state_path(results: Path, account: str) -> Path:
    return results / "flow" / account / "state.json"


def load_state(results: Path, account: str) -> FlowState:
    path = state_path(results, account)
    if not path.exists():
        return FlowState(account=account)
    return FlowState.model_validate_json(path.read_text(encoding="utf-8"))


def save_state(results: Path, st: FlowState) -> None:
    path = state_path(results, st.account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(st.model_dump_json(indent=2), encoding="utf-8")


def options_path(store: Store) -> Path:
    return store.dir / "theme_options.json"


def load_options(store: Store) -> ThemeOptions | None:
    path = options_path(store)
    if not path.exists():
        return None
    return ThemeOptions.model_validate_json(path.read_text(encoding="utf-8"))


def next_date(profile: Profile, store: Store, now: datetime) -> date:
    """다음 게시물 날짜: 오늘(계정 시간대)과 마지막 게시 다음 날 중 늦은 날."""
    today = now.astimezone(ZoneInfo(profile.schedule.timezone)).date()
    last = max((p.date for p in store.posts()), default=None)
    return max(today, last + timedelta(days=1)) if last else today


def options_text(profile: Profile, opts: ThemeOptions) -> str:
    d = opts.date
    lines = [f"[{profile.id}] {d:%m-%d}({WEEKDAYS[d.weekday()]}) 게시물 주제 제안", ""]
    for n, t in enumerate(opts.options, 1):
        lines.append(f"{n}. [{t.type or '-'}] {t.title_hint}")
        if t.why:
            lines.append(f"   {t.why}")
    lines += [
        "",
        '• 마음에 드는 번호를 "2" 처럼 보내 주세요 → 그 주제로 코디 후보를 만들어요',
        "• 다른 주제가 좋으면 /theme 니트 레이어드 코디 처럼 적어 주세요 → 그 주제로 바로 진행해요",
        '• 다 별로면 "다시" → 새로 제안해요',
        "답을 받을 때까지 기다려요 (한 시간 안에 확인)",
    ]
    return "\n".join(lines)


def week_text(profile: Profile, opts: ThemeOptions) -> str:
    d = opts.date
    lines = [f"[{profile.id}] 주간 주제 후보 {len(opts.options)}개 ({d:%m-%d}부터)", ""]
    for n, t in enumerate(opts.options, 1):
        lines.append(f"{n}. [{t.type or '-'}] {t.title_hint}")
        if t.why:
            lines.append(f"   {t.why}")
    lines += [
        "",
        f'• 쓸 주제 번호를 쓸 순서대로 보내 주세요: "1 3 4 5 7 8 10" → 이 순서로 하나씩 만들어요 '
        f"(한 주 {profile.themes.per_week}개)",
        '• 고칠 점은 편하게 적어 주세요: "3번은 데이트룩으로", "5번 빼고 롱스커트 코디 하나 더" '
        "→ 고친 후보를 다시 보내요 (쓸 번호를 같이 적으면 고친 뒤 바로 정해요)",
        '• 다 별로면 "다시" → 새로 제안해요',
        "답을 받을 때까지 기다려요 (한 시간 안에 확인)",
    ]
    return "\n".join(lines)


def save_options(
    profile: Profile,
    store: Store,
    day: date,
    themes: list[Theme],
    hint: str,
    tg=None,
    sent_at: int | None = None,
    week: bool = False,
    chosen: list[int] | None = None,
) -> ThemeOptions:
    """Claude 가 쓴 제안 주제를 저장하고(보낼 수 있으면) 텔레그램으로 보낸다.
    주간 후보에 쓸 번호(chosen)까지 정해졌으면 보내지 않는다 (흐름이 바로 정하고 알린다)."""
    chosen = chosen or []
    bad = [n for n in chosen if not 1 <= n <= len(themes)]
    if bad or len(set(chosen)) != len(chosen):
        raise ValueError(f"고른 번호가 잘못됐습니다: {chosen} (1~{len(themes)}, 겹치지 않게)")
    opts = ThemeOptions(
        account=profile.id,
        date=day,
        options=[t.model_copy(update={"date": day}) for t in themes],
        hint=hint,
        sent_at=sent_at or int(time.time()),
        week=week,
        chosen=chosen,
    )
    if tg is not None and week and not chosen:
        opts.message_ids = [tg.send(week_text(profile, opts))]
    elif tg is not None and not hint and not week:
        opts.message_ids = [tg.send(options_text(profile, opts))]
    store.dir.mkdir(parents=True, exist_ok=True)
    options_path(store).write_text(opts.model_dump_json(indent=2), encoding="utf-8")
    return opts


def week_replies(
    updates: list[dict], chat_id: str, since: int, ok: bool = False
) -> list[tuple[int, str]]:
    """since 뒤 등록된 채팅에서 온 글 (보낸 시각, 글).
    주간 후보를 기다리는 동안 온 글은 모두 답으로 본다.
    /theme·/themes 는 명령 이름을 떼고, 다른 / 명령(/ok, /fix …)은 뺀다.
    ok 면 /ok 는 "좋아" 로 본다 (남은 주제 확인)."""
    found = []
    for u in sorted(updates, key=lambda u: u.get("update_id", 0)):
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        at, text = msg.get("date", 0), (msg.get("text") or "").strip()
        if at <= since or not text:
            continue
        if text.startswith("/"):
            head, _, rest = text.partition(" ")
            name = head.lower().split("@")[0]
            if ok and name == "/ok":
                found.append((at, "좋아"))
                continue
            if name not in ("/theme", "/themes"):
                continue
            text = rest.strip()
            if not text:
                continue
        found.append((at, text))
    return found


def week_numbers(text: str) -> list[int] | None:
    """ "1 3 4 5 7 8 10", "1,3,4번" 처럼 번호만 적은 글이면 번호들 (적은 순서), 아니면 None."""
    if not re.fullmatch(r"\d{1,2}(?:\s*(?:,|\s)\s*\d{1,2})*\s*번?", text):
        return None
    return [int(n) for n in re.findall(r"\d+", text)]


GO_WORDS = {"좋아", "좋아요", "좋음", "그대로", "시작", "진행", "고", "ok", "okay", "go", "ㅇㅋ",
            "오케이", "네", "응", "ㅇㅇ"}  # fmt: skip
NEW_WORDS = {"새로", "새로 받기", "새 주제", "새후보", "새 후보", "다시"}


def review_reply(text: str) -> Literal["go", "new"] | list[int] | None:
    """남은 주제 확인에 온 답: "go" (그대로 시작), "new" (주간 후보 새로), 번호 (이 순서로),
    None (고칠 점)."""
    word = re.sub(r"[\s!.~^]+$", "", text.strip().lower())
    if word in GO_WORDS:
        return "go"
    if word in NEW_WORDS:
        return "new"
    return week_numbers(text)


def review_text(profile: Profile, st: FlowState) -> str:
    d = st.date
    head = f"[{profile.id}] {d:%m-%d}({WEEKDAYS[d.weekday()]}) " if d else f"[{profile.id}] "
    lines = [head + f"다음 주제를 시작하기 전에 남은 주제 {len(st.week)}개를 확인해 주세요", ""]
    lines += [f"{n}. [{t.type or '-'}] {t.title_hint}" for n, t in enumerate(st.week, 1)]
    lines += [
        "",
        '• 이대로면 "좋아" → 1번 주제로 시작해요',
        '• 순서를 바꾸거나 빼려면 번호를 쓸 순서대로: "3 1 2" → 빠진 번호는 빼고 3번부터 시작해요',
        '• 고칠 점은 편하게 적어 주세요: "2번은 겨울 니트 레이어드로", "하나 더 넣어줘" '
        "→ 고친 목록을 다시 보내요",
        '• 남은 주제를 버리고 주간 후보를 새로 받으려면 "새로"',
        "답을 받을 때까지 기다려요 (한 시간 안에 확인)",
    ]
    return "\n".join(lines)


def choose_theme(store: Store, theme: Theme) -> None:
    """정한 주제를 themes.json 에 넣는다 (같은 날짜 주제는 바꿈). 코디 후보가 이 주제를 쓴다."""
    themes = [t for t in store.themes() if t.date != theme.date]
    store.save_themes(sorted([*themes, theme], key=lambda t: t.date))


SCENE_GAP = 180  # 이 간격(초) 안에 이어 보낸 사진은 한 묶음 (한 장씩 여러 장 보낼 때)
SCENE_MAX = 8  # 장면 사진은 이만큼까지


class ScenePhoto(NamedTuple):
    update_id: int
    file_id: str
    caption: str
    at: int  # 보낸 시각
    group: str  # 앨범 번호 (한 장씩 보냈으면 "")


def scene_photos(
    updates: list[dict], chat_id: str, since: int, after_update: int
) -> list[ScenePhoto]:
    """since 뒤 등록된 채팅에서 온 사진 중 마지막 묶음 (after_update 이하는 이미 받은 것).
    묶음 = 앨범으로 한 번에 보낸 사진들, 또는 SCENE_GAP 초 안에 이어 보낸 사진들.
    다시 보낸 사진(앞 묶음과 떨어져 보낸 것)은 앞 묶음을 바꾼다."""
    from tools import notify

    photos: list[ScenePhoto] = []
    for u in updates:
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        if msg.get("date", 0) < since or u["update_id"] <= after_update:
            continue
        fid = notify.photo_file_id(msg)
        if fid:
            caption = (msg.get("caption") or "").strip()
            group = str(msg.get("media_group_id") or "")
            photos.append(ScenePhoto(u["update_id"], fid, caption, msg.get("date", 0), group))
    if not photos:
        return []
    batch = [photos[-1]]
    for p in reversed(photos[:-1]):
        last = batch[0]
        if (last.group and p.group == last.group) or last.at - p.at <= SCENE_GAP:
            batch.insert(0, p)
        else:
            break
    return batch[-SCENE_MAX:]


def tick(
    account: str,
    results: Path,
    tg,
    send,
    now: datetime | None = None,
    dry_run: bool = False,
    store: Store | None = None,
) -> list[str]:
    """한 번 확인하고 할 일을 한다. send(workflow, **inputs) 로 워크플로를 요청한다.
    단계가 바뀌면 같은 tick 에서 다음 단계도 이어서 본다 (예: 게시 확인 → 다음 주제 요청)."""
    profile = load_profile(account)
    store = store or Store(account)
    now = now or datetime.now(UTC)
    st = load_state(results, account)
    done: list[str] = []
    try:
        for _ in range(4):
            before = st.stage
            line = _step(profile, store, st, results, tg, send, now, dry_run)
            if line:
                done.append(line)
                st.note(now, line)
            if st.stage == before:
                break
            st.alerted = False
    finally:
        # 도중에 실패해도 이미 요청한 워크플로가 다시 요청되지 않게 지금까지 바뀐 단계를 남긴다
        if not dry_run:
            save_state(results, st)
    return done


def _late(st: FlowState, now: datetime, tg, text: str, after: timedelta = WAIT_ALERT) -> None:
    if st.alerted or not st.asked_at or tg is None:
        return
    if now.timestamp() - st.asked_at >= after.total_seconds():
        tg.send(text)
        st.alerted = True


def _use_theme(
    store: Store,
    st: FlowState,
    tg,
    send,
    chosen: Theme,
    ts: int,
    dry_run: bool,
    tag: str,
    note: str = "",
    profile: Profile | None = None,
) -> str:
    """정한 주제를 themes.json 에 넣고 코디 후보를 요청한다 (tg 가 있으면 알림).
    profile flow.ref_pick 이면 후보 대신 참고 코디 묶음을 먼저 보내 고르게 한다."""
    assert st.date is not None
    chosen = chosen.model_copy(update={"date": st.date})
    if not dry_run:
        choose_theme(store, chosen)
    st.ref_pick_at = (
        ts  # 참고 고르기를 하지 않는 주제도 "처리함"으로 (pick_wait 에서 다시 보내지 않게)
    )
    if profile is not None and profile.flow.ref_pick and tg is not None:
        st.hint, st.theme_at, st.asked_at = "", ts, ts
        tg.send(
            f"{tag} '{chosen.title_hint}' 주제로 할게요{note}. "
            "이 주제에 맞는 참고 코디를 보낼게요.\n"
            "이 주제에 맞는 장면 사진도 이 채팅에 보내 주세요 "
            "(스트리트, 스튜디오, 거울 셀카 등. 한 장도, 여러 장도 돼요)"
        )
        done = f"주제 정함: {chosen.title_hint}"
        return _send_ref_album(profile, store, st, tg, send, chosen, ts, dry_run, done)
    # daily 는 요청한 때의 커밋을 꺼내서 방금 고친 themes.json 을 못 본다: 주제를 같이 넘긴다
    theme = json.dumps({"theme": chosen.model_dump(mode="json")}, ensure_ascii=False)
    send(
        "daily.yml", account=st.account, date=st.date.isoformat(), mode="candidates", pick=theme,
        sandbox=False, collect=True,
    )  # fmt: skip
    st.hint, st.theme_at, st.asked_at, st.stage = "", ts, ts, "pick_wait"
    if tg is not None:
        tg.send(
            f"{tag} '{chosen.title_hint}' 주제로 할게요{note}. 코디 후보를 만들고 있어요.\n"
            "이 주제에 맞는 장면 사진도 이 채팅에 보내 주세요 "
            "(스트리트, 스튜디오, 거울 셀카 등. 한 장도, 여러 장도 돼요)"
        )
    return f"주제 정함: {chosen.title_hint}"


def _send_ref_album(
    profile: Profile, store: Store, st: FlowState, tg, send, theme: Theme, ts: int,
    dry_run: bool, done: str,
) -> str:  # fmt: skip
    """주제에 맞는 참고 코디 묶음을 보내고 고르기를 기다린다 (없으면 바로 후보)."""
    from tools import refs

    sent = [] if dry_run else refs.send_theme_album(profile, store, theme, tg, Path("out/ref_pick"))
    st.ref_pick_at = ts
    if not sent and not dry_run:
        _request_candidates(profile, st, send, theme, ts, [])
        return done + ", 맞는 참고 코디가 없어 바로 후보 요청"
    st.stage = "ref_pick_wait"
    return f"{done}, 참고 코디 {len(sent)}벌 보냄"


def _request_candidates(
    profile: Profile, st: FlowState, send, theme: Theme, ts: int, refs: list[int]
) -> None:
    """고른 참고 코디(없으면 Claude 가 고름)로 코디 후보를 요청한다."""
    req: dict = {"theme": theme.model_dump(mode="json")}
    if refs:
        base = profile.looks.count + profile.looks.spare if profile.looks.enabled else 0
        req["refs"] = refs
        req["count"] = min(12, max(base, len(refs)))
    send(
        "daily.yml", account=st.account, date=theme.date.isoformat(), mode="candidates",
        pick=json.dumps(req, ensure_ascii=False), sandbox=False, collect=True,
    )  # fmt: skip
    st.asked_at, st.stage = ts, "pick_wait"


def _week_wait(
    profile: Profile,
    store: Store,
    st: FlowState,
    opts: ThemeOptions,
    tg,
    send,
    ts: int,
    dry_run: bool,
    tag: str,
) -> str:
    """주간 후보에 온 답을 본다.
    번호만 = 그 주제들로 정함, 그 밖의 글 = 고칠 점 (Claude 가 고쳐 다시 보냄)."""
    if st.revising:
        if opts.sent_at <= st.asked_at:
            return ""  # 고친 후보를 기다림 (그 사이 온 글은 고친 후보에 대한 답으로 읽는다)
        st.revising = False
    nums = opts.chosen
    if not nums:
        if tg is None:
            return ""
        # 고치는 동안 온 글도 읽는다 (앞서 읽은 글 다음부터, 처음이면 후보를 보낸 뒤부터)
        since = st.read_upto or opts.sent_at - 1
        replies = week_replies(tg.updates(), tg.chat_id, since)
        if not replies:
            return ""
        st.read_upto = replies[-1][0]
        picks = [week_numbers(text) for _, text in replies]
        if all(p is not None for p in picks):
            nums = picks[-1] or []
            bad = [n for n in nums if not 1 <= n <= len(opts.options)]
            if bad or len(set(nums)) != len(nums) or not nums:
                tg.send(f"{tag} 번호는 1~{len(opts.options)} 중에서 겹치지 않게 보내 주세요")
                return f"주간 주제 번호 잘못됨: {nums}"
        else:
            feedback = "\n".join(text for _, text in replies)
            send("theme-options.yml", account=st.account, date=opts.date.isoformat(), hint="",
                 week=True, feedback=feedback)  # fmt: skip
            st.asked_at, st.revising = ts, True
            tg.send(f"{tag} 말씀하신 대로 주제 후보를 고치고 있어요. 다 되면 다시 보낼게요")
            return f"주간 주제 고치기 요청: {feedback[:60]}"
    picked = [opts.options[n - 1] for n in nums]
    refs = profile.themes.ref_search is not None
    if tg is not None:
        lines = [
            f"[{profile.id}] 주간 주제 {len(picked)}개를 정했어요 (이 순서로 하나씩 만들어요)",
            "",
        ]
        lines += [f"{i}. {t.title_hint}" for i, t in enumerate(picked, 1)]
        if refs:
            lines += [
                "",
                "이 주제들로 쇼핑몰 참고 코디를 모으고 있어요. "
                "다 모으면 첫 주제의 코디 후보를 보낼게요",
            ]
        tg.send("\n".join(lines))
    if refs:
        # 2026-10-19 사용자: 한 주 주제에 쓸 만한 코디를 쇼핑몰에서 모으기 (주제 맞춤은 느슨하게)
        send("themes.yml", account=st.account, date=opts.date.isoformat(), refs_only=True,
             week_themes="\n".join(t.title_hint for t in picked))  # fmt: skip
        st.week, st.asked_at, st.stage, st.alerted = picked, ts, "refs_wait", False
        return f"주간 주제 {len(picked)}개 정함, 참고 코디 수집 요청"
    st.week = picked[1:]
    left = f" (주간 주제 1/{len(picked)})"
    return _use_theme(store, st, tg, send, picked[0], ts, dry_run, tag, left, profile)


def _step(
    profile: Profile,
    store: Store,
    st: FlowState,
    results: Path,
    tg,
    send,
    now: datetime,
    dry_run: bool,
) -> str:
    from tools import feedback, notify, pick, publish
    from tools import schedule as sched

    acc = profile.id
    ts = int(now.timestamp())
    tag = f"[{acc}] {st.date:%m-%d}" if st.date else f"[{acc}]"

    if st.stage == "theme":
        st.date = next_date(profile, store, now)
        st.pick, st.scene, st.scene_update, st.theme_at = "", "", 0, 0
        st.scenes, st.scene_at = [], 0
        if profile.flow.week_options:
            if st.week and profile.flow.theme_review and tg is not None:
                tg.send(review_text(profile, st))
                st.asked_at = st.read_upto = ts
                st.stage = "theme_review"
                return f"남은 주제 {len(st.week)}개 확인 요청 ({st.date})"
            if st.week:
                chosen, st.week = st.week[0], st.week[1:]
                left = f" (주간 주제, 남은 주제 {len(st.week)}개)"
                return _use_theme(store, st, tg, send, chosen, ts, dry_run, tag, left, profile)
            st.read_upto, st.revising = 0, False
            send("theme-options.yml", account=acc, date=st.date.isoformat(), hint="",
                 week=True, feedback="")  # fmt: skip
            st.asked_at, st.stage = ts, "theme_wait"
            return f"주간 주제 후보 요청 ({st.date})"
        send("theme-options.yml", account=acc, date=st.date.isoformat(), hint=st.hint)
        st.asked_at, st.stage = ts, "theme_wait"
        return f"주제 제안 요청 ({st.date})"

    if st.stage == "theme_wait":
        opts = load_options(store)
        if opts is None or opts.date != st.date or opts.sent_at < st.asked_at - 60:
            _late(
                st,
                now,
                tg,
                f"{tag} 주제 제안이 아직 오지 않았어요 (주제 정하기 실행을 확인해 주세요)",
            )
            return ""
        if opts.week:
            return _week_wait(profile, store, st, opts, tg, send, ts, dry_run, tag)
        chosen = None
        if opts.hint:
            chosen = opts.options[0]  # 사용자가 적은 주제로 짠 것: 바로 진행
        elif tg is not None:
            cmds = notify.commands(
                tg.updates(), tg.chat_id, opts.sent_at, acc, st.date,
                {"theme", "pick", "again"}, opts.message_ids,
            )  # fmt: skip
            for _, cmd in reversed(cmds):
                arg = " ".join(cmd.args).strip()
                if cmd.name == "again" or (cmd.name == "theme" and arg in ("다시", "again")):
                    st.hint = ""
                    send("theme-options.yml", account=acc, date=st.date.isoformat(), hint="")
                    st.asked_at = ts
                    tg.send(f"{tag} 주제를 새로 제안할게요")
                    return "주제 다시 제안 요청"
                n = arg.rstrip("번").strip()
                if n.isdigit() and 1 <= int(n) <= len(opts.options):
                    chosen = opts.options[int(n) - 1]
                    break
                if cmd.name == "theme" and arg and not n.isdigit():
                    st.hint = arg
                    send("theme-options.yml", account=acc, date=st.date.isoformat(), hint=arg)
                    st.asked_at = ts
                    tg.send(f"{tag} '{arg}' 주제로 짜고 있어요. 다 되면 코디 후보를 보낼게요")
                    return f"사용자 주제로 다시 요청: {arg}"
        if chosen is None:
            return ""
        return _use_theme(
            store,
            st,
            tg if not opts.hint else None,
            send,
            chosen,
            ts,
            dry_run,
            tag,
            profile=profile,
        )

    if st.stage == "theme_review":
        return _review_wait(profile, store, st, tg, send, ts, dry_run, tag)

    if st.stage == "refs_wait":
        done = refs_done_at(store) >= st.asked_at
        late = ts - st.asked_at >= REFS_WAIT.total_seconds()
        if not (done or late):
            return ""
        if not st.week:  # 정한 주제가 없어졌으면 새로 받는다
            st.stage = "theme"
            return "참고 코디 수집 뒤 남은 주간 주제가 없음"
        chosen, st.week = st.week[0], st.week[1:]
        note = f" (주간 주제, 남은 주제 {len(st.week)}개)"
        if not done and tg is not None:
            tg.send(f"{tag} 참고 코디 수집이 늦어져 지금 있는 참고 코디로 후보를 만들게요")
        return _use_theme(store, st, tg, send, chosen, ts, dry_run, tag, note, profile)

    assert st.date is not None
    day = st.date

    if st.stage == "ref_pick_wait":
        if tg is None:
            return ""
        theme = next((t for t in store.themes() if t.date == day), None)
        if theme is None:
            st.stage = "theme"
            return "주제가 없어져 처음부터"
        from tools import refs as refs_tool

        have = {r.n for r in store.refs()}
        for _, text in reversed(week_replies(tg.updates(), tg.chat_id, st.ref_pick_at)):
            got = refs_tool.ref_numbers(text)
            if got is None:
                continue
            nums = [] if got == "auto" else got
            bad = [n for n in nums if n not in have]
            if bad:
                tg.send(
                    f"{tag} 참고 {', '.join(map(str, bad))}번은 없어요. "
                    "묶음의 번호로 다시 보내 주세요"
                )
                st.ref_pick_at = ts
                return f"참고 번호 잘못됨: {bad}"
            _request_candidates(profile, st, send, theme, ts, nums)
            if nums:
                tg.send(f"{tag} 참고 {len(nums)}벌로 코디 후보를 만들고 있어요")
            else:
                tg.send(f"{tag} 참고 코디는 제가 골라서 코디 후보를 만들고 있어요")
            return f"참고 고름 ({', '.join(map(str, nums)) or '알아서'}), 후보 요청"
        return ""

    if st.stage == "pick_wait":
        if profile.flow.ref_pick and not st.ref_pick_at and not st.pick and tg is not None:
            # 참고 고르기를 켜기 전에 정한 주제: 묶음을 먼저 보낸다
            theme = next((t for t in store.themes() if t.date == day), None)
            if theme is not None:
                return _send_ref_album(
                    profile, store, st, tg, send, theme, ts, dry_run, "참고 고르기"
                )
        lines = []
        if tg is not None:
            batch = scene_photos(tg.updates(), tg.chat_id, st.theme_at, st.scene_update)
            if batch:
                folder = f"flow/{acc}/{day}"
                # 앞 묶음에 이어 보낸 사진(간격이 짧음)은 더하고, 따로 보낸 묶음은 바꾼다
                joined = bool(st.scenes) and batch[0].at - st.scene_at <= SCENE_GAP
                keep = st.scenes if joined else []
                n0 = len(keep)
                new = [
                    f"{folder}/scene.jpg" if n0 + i == 0 else f"{folder}/scene_{n0 + i + 1}.jpg"
                    for i in range(len(batch))
                ][: max(0, SCENE_MAX - n0)]
                note = next((p.caption for p in batch if p.caption), "")
                if not dry_run:
                    if not joined:
                        for old in (results / folder).glob("scene_*.jpg"):
                            old.unlink()
                    for p, rel in zip(batch, new, strict=False):
                        tg.download(p.file_id, results / rel)
                    # 사진에 붙인 말(예: "슬릭번")은 모든 벌에 반영할 요청 (daily-feed 가 읽음)
                    note_path = results / folder / "scene.txt"
                    if note:
                        note_path.write_text(note + "\n", encoding="utf-8")
                    elif not joined:
                        note_path.unlink(missing_ok=True)
                st.scenes = [*keep, *new]
                st.scene, st.scene_update, st.scene_at = (
                    st.scenes[0],
                    batch[-1].update_id,
                    batch[-1].at,
                )
                more = "바로 만들게요" if st.pick else "코디를 골라 주시면 만들게요"
                count = f" {len(st.scenes)}장" if len(st.scenes) > 1 else ""
                tg.send(f"{tag} 장면 사진{count}을 받았어요. {more}")
                lines.append(f"장면 사진 받음 ({len(st.scenes)}장)")
        if not st.pick:
            hold = "" if st.scene else "장면 사진을 보내 주시면 바로 만들게요."
            act = pick.check(
                profile,
                day,
                results,
                tg,
                False,
                NO_DEADLINE,
                now,
                hold=hold,
                since=st.theme_at - 60,
            )
            if act.action == "render":
                st.pick = f"{act.run}/{act.choice}"
                lines.append(f"코디 고름 ({st.pick})")
            elif act.action == "again":
                send("daily.yml", account=acc, date=day.isoformat(), mode="candidates",
                     pick=pick.again_pick(act), sandbox=False)  # fmt: skip
                lines.append("새 후보 요청")
            elif act.action == "edit":
                spec = {"run": act.run, "fixes": act.fixes}
                send("daily.yml", account=acc, date=day.isoformat(), mode="edit",
                     pick=json.dumps(spec, ensure_ascii=False), sandbox=False)  # fmt: skip
                lines.append("후보 고치기 요청")
            elif act.action == "skip":
                st.stage = "theme"
                return "이 주제 건너뜀 (/skip)"
        if st.pick and st.scene:
            send("daily.yml", account=acc, date=day.isoformat(), mode="render", pick=st.pick,
                 sandbox=False)  # fmt: skip
            st.asked_at, st.stage = ts, "rendering"
            lines.append("만들기 요청")
        return ", ".join(lines)

    if st.stage == "rendering":
        out = feedback.latest_preview(results, acc, day)
        rec = notify.load_record(out) if out is not None else None
        if rec is None or rec.sent_at < st.asked_at:
            _late(
                st,
                now,
                tg,
                f"{tag} 게시물 미리보기가 아직 오지 않았어요 (만들기 실행을 확인해 주세요)",
            )
            return ""
        st.stage = "preview_wait"
        return f"미리보기 받음 ({out.name})"

    if st.stage == "preview_wait":
        out = feedback.latest_preview(results, acc, day)
        if out is None or tg is None:
            return ""
        rec = notify.load_record(out)
        assert rec is not None
        print(sched.cover_change(acc, day.isoformat(), results, tg, dry_run))
        fix = feedback.check(acc, day, results, tg, now, dry_run, store)
        if fix is not None:
            send("daily.yml", account=acc, date=day.isoformat(), mode="fix",
                 pick=json.dumps(fix, ensure_ascii=False), sandbox=False)  # fmt: skip
            st.asked_at, st.stage = ts, "rendering"
            return f"고쳐 만들기 요청 ({fix.get('round')}회)"
        updates = tg.updates()
        decided = notify.approval_now(out, rec, profile, updates, tg.chat_id)
        if decided and not dry_run:
            notify.remember(out, decided)
        if decided == "skip":
            st.stage = "theme"
            tg.send(f"{tag} 이 게시물은 건너뛰고 다음 주제를 제안할게요")
            return "미리보기 건너뜀 (/skip)"
        if decided != "ok":
            return ""
        if feedback.waiting(updates, rec, tg.chat_id, out, profile, store):
            return ""  # /fix 로 고치는 중: 고친 미리보기에 /ok 가 오면 그때
        send("publish.yml", account=acc, date=day.isoformat(), run=out.name, dry_run=False,
             force=True, scheduled=True)  # fmt: skip
        st.asked_at, st.stage = ts, "publishing"
        tg.send(f"{tag} /ok 확인했어요. 지금 게시할게요")
        return f"게시 요청 ({out.name})"

    if st.stage == "publishing":
        posted = any(p.date == day for p in store.posts())
        try:
            posted = posted or (publish.find_run(results, acc, day) / "published.json").exists()
        except FileNotFoundError:
            pass
        if not posted:
            mins = timedelta(minutes=profile.flow.publish_alert_minutes)
            _late(st, now, tg, f"{tag} 게시 기록이 아직 없어요 (게시 실행을 확인해 주세요)", mins)
            return ""
        st.stage = "theme"
        if profile.flow.rules_after_post and profile.feedback.enabled:
            new = feedback.pending_entries(store)
            if new:
                send("feedback-rules.yml", account=acc)
                if tg is not None:
                    tg.send(f"{tag} 게시를 확인했어요. 그동안 받은 /fix 피드백 {len(new)}개를 "
                            "규칙에 반영하고 있어요. 다 되면 바뀐 점을 알려 드릴게요")  # fmt: skip
                return f"게시 확인, 피드백 규칙 반영 요청 ({len(new)}개)"
        return "게시 확인"
    return ""


def _review_wait(
    profile: Profile,
    store: Store,
    st: FlowState,
    tg,
    send,
    ts: int,
    dry_run: bool,
    tag: str,
) -> str:
    """남은 주간 주제 확인에 온 답을 본다 (마지막 답이 정한다)."""
    if not st.week:
        st.stage = "theme"
        return "확인할 남은 주제가 없음"
    if tg is None:
        return ""
    replies = week_replies(tg.updates(), tg.chat_id, st.read_upto, ok=True)
    if not replies:
        return ""
    st.read_upto = replies[-1][0]
    answer = review_reply(replies[-1][1])
    if answer == "new":
        st.week, st.stage = [], "theme"
        tg.send(f"{tag} 남은 주제는 버리고 주간 주제 후보를 새로 받을게요")
        return "남은 주제 버림, 주간 후보 새로"
    if isinstance(answer, list):
        bad = [n for n in answer if not 1 <= n <= len(st.week)]
        if bad or len(set(answer)) != len(answer) or not answer:
            tg.send(f"{tag} 번호는 1~{len(st.week)} 중에서 겹치지 않게 보내 주세요")
            return f"남은 주제 번호 잘못됨: {answer}"
        st.week = [st.week[n - 1] for n in answer]
        lines = [f"{tag} 이 순서로 할게요", ""]
        lines += [f"{i}. {t.title_hint}" for i, t in enumerate(st.week, 1)]
        tg.send("\n".join(lines))
    if answer is None:
        feedback = "\n".join(text for _, text in replies)
        base = json.dumps([t.model_dump(mode="json") for t in st.week], ensure_ascii=False)
        send("theme-options.yml", account=st.account, date=st.date.isoformat(), hint="",
             week=True, feedback=feedback, base=base)  # fmt: skip
        # 고친 목록은 주간 후보처럼 와서(theme_wait) 쓸 번호를 고르면 그 주제들로 간다
        st.asked_at, st.read_upto, st.revising, st.stage = ts, 0, False, "theme_wait"
        tg.send(f"{tag} 말씀하신 대로 남은 주제를 고치고 있어요. 고친 목록을 보내면 "
                "쓸 번호를 순서대로 보내 주세요")  # fmt: skip
        return f"남은 주제 고치기 요청: {feedback[:60]}"
    chosen, st.week = st.week[0], st.week[1:]
    left = f" (주간 주제, 남은 주제 {len(st.week)}개)"
    return _use_theme(store, st, tg, send, chosen, ts, dry_run, tag, left, profile)


def status(results: Path, account: str) -> str:
    st = load_state(results, account)
    lines = [f"[{account}] 단계 {st.stage}, 날짜 {st.date or '-'}"]
    if st.pick:
        lines.append(f"고른 후보 {st.pick}")
    if st.scene:
        lines.append(f"장면 사진 {', '.join(st.scenes or [st.scene])}")
    lines += st.log[-10:]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--account", default="women")
    s.add_argument("--results", type=Path, required=True)
    s = sub.add_parser("reset")
    s.add_argument("--account", default="women")
    s.add_argument("--results", type=Path, required=True)
    s = sub.add_parser(
        "use-theme", help="고른 주제를 themes.json 에 넣음 (daily candidates 가 부름)"
    )
    s.add_argument("--account", default="women")
    s.add_argument("--json", required=True, help='{"theme": {...}}')
    s.add_argument(
        "--out", type=Path, help="다시 짜기 요청(keep·count·note)을 picks_request.json 으로"
    )
    s = sub.add_parser("options")
    s.add_argument("--account", default="women")
    s.add_argument("--date", required=True)
    s.add_argument(
        "--file", type=Path, required=True, help="Claude 가 쓴 제안 주제 YAML (Theme 목록)"
    )
    s.add_argument("--hint", default="")
    s.add_argument("--week", action="store_true", help="주간 후보 (flow.week_options 개)")
    s.add_argument("--send", action="store_true", help="텔레그램으로 보냄")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "status":
            print(status(args.results, args.account))
        elif args.cmd == "use-theme":
            req = json.loads(args.json)
            if req.get("theme"):
                theme = Theme.model_validate(req["theme"])
                choose_theme(Store(args.account), theme)
                print(f"{theme.date} 주제: {theme.title_hint}")
            ask = {k: req[k] for k in ("again", "keep", "count", "note", "refs") if req.get(k)}
            if ask and args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                path = args.out / "picks_request.json"
                path.write_text(json.dumps(ask, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"다시 짜기 요청: {ask}")
        elif args.cmd == "reset":
            save_state(args.results, FlowState(account=args.account))
            print(f"[{args.account}] 차례 흐름을 처음(주제)부터 다시 해요")
        else:
            profile = load_profile(args.account)
            day = date.fromisoformat(args.date)
            raw = yaml.safe_load(args.file.read_text(encoding="utf-8")) or []
            # 주간 후보는 {options: [...], chosen: [1, 3, …]} 로도 쓴다
            # (고칠 점에 쓸 번호까지 있었을 때)
            chosen: list[int] = []
            if isinstance(raw, dict):
                chosen = [int(n) for n in raw.get("chosen") or []]
                raw = raw.get("options") or []
            themes = [Theme.model_validate({**t, "date": day}) for t in raw]
            if not themes:
                raise ValueError("제안 주제가 없습니다")
            if args.week:
                limit = profile.flow.week_options or profile.flow.theme_options
            else:
                limit = 1 if args.hint else profile.flow.theme_options
            tg = None
            if args.send:
                from tools import notify

                tg = notify.Telegram.from_env()
            opts = save_options(
                profile, Store(args.account), day, themes[:limit], args.hint, tg,
                week=args.week, chosen=chosen,
            )  # fmt: skip
            for n, t in enumerate(opts.options, 1):
                print(f"{n}. {t.title_hint}")
        return 0
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
