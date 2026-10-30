r"""움직이는 코디 영상의 컨셉 (2026-10-29 사용자). Claude 는 부르지 않는다.

코디 주제 하나당 영상 한 편: 같은 배경·고정 카메라에서 같은 얼굴의 모델이 고른 벌들을 차례로 입고
걷거나 가볍게 춤춘다. 코디를 고르면 차례 흐름(tools.flow)이 video-concept 워크플로를 부르고,
Claude(video-concept 스킬)가 컨셉 후보를 쓰면 이 도구가 검사해 data/<계정>/video_concepts.json 에
저장하고 텔레그램으로 보낸다. 사용자가 번호로 고르거나 고칠 점·원하는 배경을 적으면
흐름이 정하거나 다시 짜기를 요청한다. 기준은 accounts/<계정>/video.md, profile `video`.

사용법:
  python -m tools.video_concept context --account women --date 2026-10-28 \
      --plan out/video/looks.yaml [--feedback "…"]
      Claude 가 읽을 재료 (주제, 고른 벌, 영상 기준, 앞서 보낸 후보와 고칠 점)
  python -m tools.video_concept save --account women --date 2026-10-28 \
      --file out/video_concepts.yaml --run 2026-10-28-run398/chosen --looks 6 \
      [--feedback …] [--send]
      Claude 가 쓴 후보를 검사·저장하고 (--send 면) 텔레그램으로 보낸다
  python -m tools.video_concept show --account women
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from core.models import Theme
from core.profile import WEEKDAYS, Profile, load_profile, load_video_rules
from core.store import Store


class Shot(BaseModel):
    """한 벌이 나오는 장면. 카메라·배경은 컨셉 공통이고 벌마다 동작만 바뀐다."""

    look: int = Field(ge=1)  # 벌 번호 (고른 순서, 1부터)
    seconds: float = Field(gt=0.5, le=10)
    action: str  # 동작 시작 → 끝 (한국어, 컨셉의 동작과 같은 종류)
    pose: str  # 옷이 가장 잘 보이는 순간의 포즈
    text: str = ""  # 화면 글자 (첫 벌의 훅 문구 등, 코드가 얹는다)


class Concept(BaseModel):
    title: str  # 한 줄 컨셉 ("남친이 찍어 준 눈 오는 여행지")
    background: str  # 배경 (한국어, 모든 벌 공통: 장소·자리)
    background_en: str  # 장면 사진에 쓸 같은 배경 (영어 한두 문장)
    framing: str  # 고정 구도 (세로, 눈높이, 전신이 화면 높이의 약 75% …)
    light: str  # 시간·빛·색감
    motion: str  # 동작 컨셉 (모든 벌 통일, 예: "걷기")
    music: str  # 음악 AI 지시 (영어: 장르, 분위기, BPM, instrumental)
    music_mood: str  # 음악 분위기 (한국어, 텔레그램에 보임)
    hook: str  # 첫 벌에 얹을 훅 문구
    why: str = ""  # 이 주제·코디에 이 컨셉인 이유
    shots: list[Shot] = Field(min_length=1)

    @model_validator(mode="after")
    def _looks_once(self) -> Concept:
        nums = [s.look for s in self.shots]
        if sorted(nums) != list(range(1, len(nums) + 1)):
            raise ValueError(
                f"'{self.title}' 장면의 벌 번호가 1부터 빠짐없이 한 번씩이어야 합니다: {nums}"
            )
        return self


class ConceptSet(BaseModel):
    """한 게시물의 컨셉 후보 (data/<계정>/video_concepts.json)."""

    account: str
    date: dt.date
    run: str = ""  # 고른 코디 (daily-results 기준 "<결과 폴더>/chosen" 또는 "/<번호>")
    concepts: list[Concept] = Field(min_length=1)
    feedback: str = ""  # 이 후보를 짤 때 반영한 사용자 고칠 점
    sent_at: int = 0
    message_ids: list[int] = []
    chosen: int = 0  # 고른 번호 (0 이면 아직). 고칠 점에 번호까지 적었으면 Claude 가 채움
    chosen_at: int = 0

    def picked(self) -> Concept | None:
        return self.concepts[self.chosen - 1] if self.chosen else None


def path(store: Store) -> Path:
    return store.dir / "video_concepts.json"


def load(store: Store) -> ConceptSet | None:
    p = path(store)
    if not p.exists():
        return None
    return ConceptSet.model_validate_json(p.read_text(encoding="utf-8"))


def write(store: Store, cs: ConceptSet) -> None:
    store.dir.mkdir(parents=True, exist_ok=True)
    path(store).write_text(cs.model_dump_json(indent=2), encoding="utf-8")


def plan_file(results: Path, pick: str) -> Path:
    """고른 코디("<결과 폴더>/chosen" 또는 "/<번호>")의 기획 파일."""
    run, _, choice = pick.partition("/")
    name = "chosen.yaml" if choice in ("", "chosen") else f"p{choice}.yaml"
    return results / run / "picks" / name


def looks_of(plan: dict) -> list[dict]:
    """기획(picks/*.yaml)의 벌들. 한 벌 기획이면 그 하나."""
    looks = plan.get("looks") or []
    return looks or [{"items": plan.get("items") or [], "reason": plan.get("reason", "")}]


def looks_text(plan: dict) -> str:
    lines = []
    for n, look in enumerate(looks_of(plan), 1):
        names = ", ".join(i.get("desc") or i.get("name", "") for i in look.get("items") or [])
        lines.append(f"LOOK {n}: {names}")
        for r in look.get("requests") or []:
            lines.append(f"  요청: {r}")
    return "\n".join(lines)


def concept_text(profile: Profile, cs: ConceptSet) -> str:
    d = cs.date
    lines = [f"[{profile.id}] {d:%m-%d}({WEEKDAYS[d.weekday()]}) 영상 컨셉 후보", ""]
    for n, c in enumerate(cs.concepts, 1):
        lines += [
            f"{n}. {c.title}",
            f"   배경: {c.background}",
            f"   구도: {c.framing} · {c.light}",
            f"   동작: {c.motion}",
        ]
        lines += [f"     LOOK {s.look} ({s.seconds:g}초) {s.action} → {s.pose}" for s in c.shots]
        lines += [f"   음악: {c.music_mood}", f'   훅 문구: "{c.hook}"']
        if c.why:
            lines.append(f"   {c.why}")
        lines.append("")
    lines += [
        '• 마음에 드는 번호를 "2" 처럼 보내 주세요',
        '• 고칠 점이나 원하는 배경·동작은 편하게 적어 주세요: "2번인데 배경은 겨울 바다로", '
        '"춤 컨셉으로" → 고친 후보를 다시 보내요 (번호를 같이 적으면 고친 뒤 바로 정해요)',
        '• 다 별로면 "다시" → 새로 짜요',
        "답을 받을 때까지 기다려요 (한 시간 안에 확인)",
    ]
    return "\n".join(lines)


NEW_WORDS = {"다시", "새로", "새로 짜줘", "다시 짜줘", "새 컨셉"}


def reply(text: str, count: int) -> int | str | None:
    """컨셉 후보에 온 답: 번호(고름), "new"(새로), None(고칠 점·원하는 컨셉)."""
    t = re.sub(r"[\s!.~^]+$", "", text.strip())
    if t in NEW_WORDS:
        return "new"
    m = re.fullmatch(r"(\d{1,2})\s*번?(?:\s*(?:으로|로|요|이요|할게|좋아))?", t)
    if m and 1 <= int(m.group(1)) <= count:
        return int(m.group(1))
    return None


def save(
    profile: Profile,
    store: Store,
    day: dt.date,
    concepts: list[Concept],
    run: str = "",
    looks: int = 0,
    feedback: str = "",
    chosen: int = 0,
    tg=None,
    sent_at: int | None = None,
) -> ConceptSet:
    """Claude 가 쓴 후보를 검사해 저장하고 (보낼 수 있으면) 텔레그램으로 보낸다.
    고칠 점에 번호까지 있어 고른 번호(chosen)가 정해졌으면 보내지 않는다 (흐름이 정하고 알린다)."""
    concepts = concepts[: profile.video.concepts] if not chosen else concepts
    for c in concepts:
        if looks and len(c.shots) != looks:
            raise ValueError(
                f"'{c.title}' 장면이 {len(c.shots)}개예요. 고른 벌 수({looks})와 같아야 합니다"
            )
    if chosen and not 1 <= chosen <= len(concepts):
        raise ValueError(f"고른 번호가 잘못됐습니다: {chosen} (1~{len(concepts)})")
    cs = ConceptSet(
        account=profile.id,
        date=day,
        run=run,
        concepts=concepts,
        feedback=feedback,
        sent_at=sent_at or int(time.time()),
        chosen=chosen,
    )
    if tg is not None and not chosen:
        cs.message_ids = [tg.send(concept_text(profile, cs))]
    write(store, cs)
    return cs


def context(account: str, day: dt.date, plan: dict, feedback: str, store: Store) -> str:
    """Claude 가 컨셉을 짤 때 읽을 재료."""
    profile = load_profile(account)
    theme: Theme | None = next((t for t in store.themes() if t.date == day), None)
    v = profile.video
    lines = [f"# {account} {day} 영상 컨셉 재료", ""]
    if theme is not None:
        lines += [f"주제: {theme.title_hint}", f"무드: {theme.mood}"]
        if theme.why:
            lines.append(f"이유: {theme.why}")
    lines += [
        "",
        f"후보 수: {v.concepts}, 벌마다 {v.look_seconds:g}초, 음악: {v.music}",
        "",
        "## 고른 벌",
        looks_text(plan),
        "",
        "## 영상 기준 (video.md)",
        load_video_rules(account).strip() or "(없음)",
    ]
    prev = load(store)
    if prev is not None and prev.date == day:
        lines += ["", "## 앞서 보낸 후보 (이것을 고친다)"]
        lines.append(yaml.safe_dump(
            [c.model_dump(mode="json") for c in prev.concepts], allow_unicode=True, sort_keys=False
        ))  # fmt: skip
    if feedback:
        lines += ["", "## 사용자 고칠 점 (그대로 반영)", feedback]
    return "\n".join(lines)


def _read_concepts(file: Path) -> tuple[list[Concept], int]:
    raw = yaml.safe_load(file.read_text(encoding="utf-8")) or []
    chosen = 0
    if isinstance(raw, dict):  # {concepts: [...], chosen: 2} (고칠 점에 번호까지 있었을 때)
        chosen = int(raw.get("chosen") or 0)
        raw = raw.get("concepts") or []
    concepts = [Concept.model_validate(c) for c in raw]
    if not concepts:
        raise ValueError("컨셉 후보가 없습니다")
    return concepts, chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="영상 컨셉 후보 (tools.video_concept)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("context", help="Claude 가 읽을 재료")
    c.add_argument("--account", required=True)
    c.add_argument("--date", required=True)
    c.add_argument("--plan", type=Path, required=True, help="고른 코디 기획 (picks/*.yaml)")
    c.add_argument("--feedback", default="")
    s = sub.add_parser("save", help="후보 검사·저장·보내기")
    s.add_argument("--account", required=True)
    s.add_argument("--date", required=True)
    s.add_argument("--file", type=Path, required=True)
    s.add_argument("--run", default="")
    s.add_argument("--looks", type=int, default=0, help="고른 벌 수 (장면 수 검사)")
    s.add_argument("--feedback", default="")
    s.add_argument("--send", action="store_true")
    w = sub.add_parser("show", help="저장된 후보")
    w.add_argument("--account", required=True)
    args = parser.parse_args(argv)
    try:
        store = Store(args.account)
        if args.cmd == "context":
            plan = yaml.safe_load(args.plan.read_text(encoding="utf-8")) or {}
            day = dt.date.fromisoformat(args.date)
            print(context(args.account, day, plan, args.feedback, store))
        elif args.cmd == "save":
            profile = load_profile(args.account)
            concepts, chosen = _read_concepts(args.file)
            tg = None
            if args.send:
                from tools import notify

                tg = notify.Telegram.from_env()
            cs = save(
                profile, store, dt.date.fromisoformat(args.date), concepts, args.run,
                args.looks, args.feedback, chosen, tg,
            )  # fmt: skip
            for n, con in enumerate(cs.concepts, 1):
                print(f"{n}. {con.title}" + (" (고름)" if n == cs.chosen else ""))
        else:
            cs = load(store)
            if cs is None:
                print("저장된 컨셉 후보가 없습니다")
            else:
                print(concept_text(load_profile(args.account), cs))
        return 0
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
