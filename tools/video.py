r"""코디 영상 만들기 (2026-11-02 사용자 "시작", 10/27 퍼 코디 영상으로 정한 방식).

코디 주제 하나당 영상 한 편. 고른 컨셉(data/<계정>/video_concepts.json)과 확정된 코디컷으로
벌마다 첫 장면을 그리고, 그 장면을 영상 AI 로 움직여 이어 붙인다. 영상은 사용자가 직접 올린다.
사람 확인은 텔레그램으로 단계마다 (컨셉은 tools.video_concept).
그림은 그리기 전에 무엇을 그릴지 확인받고, 그린 뒤 /ok 를 받아야 다음으로 (2026-11-02 사용자):
  ① plan       그릴 그림 계획 (빈 배경, 벌마다 첫 장면의 코디·동작·크기) → 글 (요금 없음)
  ② background 빈 배경 한 장 → 사진
  ③ frames     얼굴 자르기(코드) + 벌마다 첫 장면(얼굴·코디컷·배경) + 얼굴만 고치기 → 사진 묶음
  ④ prompts    벌마다 영상 지시문 (카메라·인물 크기·걷는 속도·동작) → 한국어 요약과 원문
  ⑤ clips      벌마다 영상(profile video.video_model) (+ 음악, video.music 이 lyria 일 때만)
               → 편집(edit) → 최종본·표지·캡션
     edit      코드 편집만 다시 (속도·제목·순서, 무료)
     music     음악만 새로 ($0.04) → 편집 (video.music 이 lyria 일 때만)
결과는 daily-results 의 <결과 폴더>/video/ (video.json 에 단계·비용·편집 값).
기준: profile `video`, accounts/<계정>/video.md, 지시문 prompts/video/,
편집 자리 config/layout.yaml video_edit.
OpenRouter 요금이 나가므로 video 워크플로(차례 흐름이 부름)에서만 돈다.

사용법:
  python -m tools.video plan --account women --run … [--note "배경:눈이 더 많이"] [--concept 2]
  python -m tools.video background --account women --run … [--note "배경:…"]
  python -m tools.video frames  --account women --run /tmp/results/2026-10-27-run380 \
      [--looks 3 5] [--note "3:얼굴이 달라"] [--background]
  python -m tools.video scene_frames --account women --run … --face face.jpg [--looks 1]
      [--note "1:가방을 오른손에"]  코디컷 없이 첫 장면을 상품 사진에서 바로 (2026-11-07)
  python -m tools.video prompts --account women --run … [--note "3:가방 고쳐 들기"]
  python -m tools.video clips   --account women --run … [--looks 2] [--note "2:더 빨리"]
  python -m tools.video edit    --account women --run … [--speed 1.3] [--hook "…"]
      [--order 3 1 2 4 5 6]
  python -m tools.video music   --account women --run …
  python -m tools.video subset  --account women --run … --looks 1 2 3 5 6 7
      영상에 넣을 벌만 골라 새 결과 폴더(<결과 폴더>-l123567)로 (무료, 2026-11-05 클로드 스레드)
      코디컷 없는 후보 실행(picks/p1.yaml 만 있음)도 된다 (--pick 2 면 p2.yaml, 2026-11-07)
  --fake: API 없이 흐름만 (가짜 그림·영상), --no-send: 텔레그램으로 보내지 않음
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal

import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pydantic import BaseModel

from core.models import DailyPlan, Manifest
from core.profile import REPO_ROOT, Profile, load_profile
from core.store import Store
from tools import video_test
from tools.video_concept import Concept
from tools.video_concept import load as load_concepts

PROMPTS = REPO_ROOT / "prompts" / "video"
LAYOUT = REPO_ROOT / "config" / "layout.yaml"
FONTS = REPO_ROOT / "assets" / "fonts"

Step = Literal["plan", "background", "frames", "prompts", "final"]


class VideoRecord(BaseModel):
    """한 게시물 영상의 지금 상태 (<결과 폴더>/video/video.json)."""

    account: str
    date: dt.date
    run: str  # 결과 폴더 이름
    step: Step = "plan"  # 마지막으로 보낸 확인 단계
    sent_at: int = 0  # 그 단계를 텔레그램으로 보낸 시각 (흐름이 이 뒤의 답을 읽는다)
    looks: int = 0
    costs: dict[str, float] = {}  # "frame_3", "clip_3", "background", "music" → 달러
    notes: dict[str, list[str]] = {}  # 벌마다 사용자 고칠 점 ("3" → […], "all" → 모든 벌)
    # 영상 지시문 단계 이후의 고칠 점. 그림 고칠 점(notes)은 첫 장면에 이미 반영되어
    # 영상 지시문엔 넣지 않는다
    motion_notes: dict[str, list[str]] = {}
    speed: float = 0  # 0 이면 profile video.speed
    order: list[int] = []  # 비면 LOOK 1, 2, …
    hook: str = ""  # 비면 컨셉 훅 문구
    flags: list[str] = []  # 자동 검사 표시 ("LOOK 3 다가옴")
    error: str = ""  # 마지막 실패 (차례 흐름이 /retry 를 안내)
    error_at: int = 0
    concept: int = 0  # 고른 컨셉 번호 (0 이면 video_concepts.json 의 chosen)
    bg_versions: int = 0  # 그린 빈 배경 수 (video/backgrounds/<번호>.jpg, 마지막이 지금 배경)
    bg_base: int = 0  # 지금 배경이 몇 번째 배경을 고친 것인지 (0 이면 새로 그림)
    stale: list[int] = []  # 영상을 만든 뒤 첫 장면을 다시 그린 벌 (다음 clips 때 영상도 다시)
    # 첫 장면을 상품 사진에서 바로 그린 벌 (scene_frames, 2026-11-07). 있으면 clips 는
    # profile video.scene_video_model 로 그 첫 장면을 first_frame 으로 넣는다
    scene: list[int] = []

    def total(self) -> float:
        return round(sum(self.costs.values()), 2)


# ---------- 경로·재료 ----------


def vdir(run: Path) -> Path:
    return run / "video"


def load_record(run: Path) -> VideoRecord | None:
    p = vdir(run) / "video.json"
    return VideoRecord.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def save_record(run: Path, rec: VideoRecord) -> None:
    vdir(run).mkdir(parents=True, exist_ok=True)
    (vdir(run) / "video.json").write_text(rec.model_dump_json(indent=2), encoding="utf-8")


def manifest_of(run: Path) -> Manifest:
    return Manifest.model_validate_json((run / "manifest.json").read_text(encoding="utf-8"))


def look_count(run: Path) -> int:
    return len(sorted(p for p in (run / "looks").iterdir() if p.name.isdigit()))


def look_image(run: Path, n: int) -> Path:
    """그 벌의 확정 코디컷: 검수에서 고른 후보(review.json chosen), 없으면 마지막 회차의 첫 장."""
    look = run / "looks" / str(n)
    rv = look / "review" / "review.json"
    if rv.exists():
        data = json.loads(rv.read_text(encoding="utf-8"))
        cand = next((c for c in data.get("candidates", []) if c["id"] == data.get("chosen")), None)
        if cand and (look / cand["file"]).exists():
            return look / cand["file"]
    dirs = sorted(look.glob("outfit*/results"), key=lambda d: (len(d.parent.name), d.parent.name))
    for d in reversed(dirs):
        files = sorted(d.glob("*.jpg"))
        if files:
            return files[0]
    frame = vdir(run) / "frames" / f"look{n}.jpg"  # 코디컷 없이 그린 첫 장면 (scene_frames)
    if frame.exists():
        return frame
    raise FileNotFoundError(f"LOOK {n} 코디컷이 없어요: {look}")


def items_en(run: Path, n: int) -> str:
    """그 벌 상품을 영어로 (기획 desc 의 괄호 속 영어, 없으면 desc)."""
    plan = yaml.safe_load((run / "looks" / str(n) / "plan.yaml").read_text(encoding="utf-8")) or {}
    out = []
    for item in plan.get("items") or []:
        desc = item.get("desc") or item.get("name", "")
        m = re.findall(r"\(([^()]*[A-Za-z][^()]*)\)", desc)
        out.append(m[-1] if m else desc)
    return "; ".join(x for x in out if x)


def items_ko(run: Path, n: int) -> str:
    """그 벌 상품을 한국어로 (기획 desc 의 괄호 앞)."""
    plan = yaml.safe_load((run / "looks" / str(n) / "plan.yaml").read_text(encoding="utf-8")) or {}
    out = []
    for item in plan.get("items") or []:
        desc = item.get("desc") or item.get("name", "")
        out.append(re.sub(r"\s*\([^()]*\)\s*$", "", desc).strip() or desc)
    return ", ".join(x for x in out if x)


def concept_of(profile: Profile, day: dt.date, n: int = 0) -> Concept:
    """고른 컨셉. n 은 차례 흐름이 아는 고른 번호 (video_concepts.json 의 chosen 은
    schedule 이 커밋하지 않아 저장소에 없을 수 있다)."""
    cs = load_concepts(Store(profile.id))
    if cs is None or cs.date != day:
        raise ValueError(f"{day} 영상 컨셉 후보가 없어요 (video_concepts.json)")
    n = n or cs.chosen
    if not 1 <= n <= len(cs.concepts):
        raise ValueError(f"{day} 에 고른 영상 컨셉이 없어요 (번호 {n})")
    return cs.concepts[n - 1]


def notes_for(rec: VideoRecord, n: int, motion: bool = False) -> list[str]:
    d = rec.motion_notes if motion else rec.notes
    return [*d.get("all", []), *d.get(str(n), [])]


def add_notes(rec: VideoRecord, raw: list[str], motion: bool = False) -> list[int]:
    """'3:얼굴이 달라' (벌 번호:고칠 점) 또는 '고칠 점' (모든 벌). 고친 벌 번호를 돌려준다.

    motion 이면 영상 지시문 고칠 점 (첫 장면 그림 고칠 점과 따로 둔다)."""
    d = rec.motion_notes if motion else rec.notes
    touched: list[int] = []
    for r in raw:
        head, sep, text = r.partition(":")
        if sep and head.strip().isdigit():
            d.setdefault(head.strip(), []).append(text.strip())
            touched.append(int(head))
        elif r.strip():
            d.setdefault("all", []).append(r.strip())
    return touched


def template(name: str, **kw: str) -> str:
    """지시문 틀 채우기. 빈 칸의 줄("Label: ." 처럼 내용 없는 줄)은 지운다."""
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    text = text.format_map({k: (v or "").strip().rstrip(".") for k, v in kw.items()})
    empty = re.compile(r"([A-Za-z ()]+:)?\s*\.\s*|[A-Za-z ()]+:\s*")  # "Light: ." / "."
    out = [ln for ln in text.splitlines() if not ln or not empty.fullmatch(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def common(profile: Profile, c: Concept) -> dict[str, str]:
    # 고정값 없음 (2026-11-03 사용자): 크기·카메라·걸음·자세·동작 순서·표정·머리는 영상마다
    # 컨셉 지시문에만 적는다. 틀에는 이미지 역할과 9:16·글자 금지만.
    return {
        "camera": c.camera_en,
        "pace": c.pace_en,
        "direction": c.direction_en,
        "light": c.light_en or c.background_en,
    }


# ---------- 이미지·영상 AI ----------


def _gen_client(profile: Profile, fake: bool):
    from tools.generate import FakeClient, OpenRouterClient

    cfg = video_test.load_config()
    gcfg = {**cfg["generate"], "aspect_ratio": "9:16"}
    client = FakeClient() if fake else OpenRouterClient(gcfg, os.environ["OPENROUTER_API_KEY"])
    return client, gcfg["models"]


def run_date(run: Path) -> dt.date:
    """게시 날짜: manifest, 없으면(후보 실행에서 고른 폴더, subset_picks) plan.yaml."""
    if (run / "manifest.json").exists():
        return manifest_of(run).date
    plan = yaml.safe_load((run / "plan.yaml").read_text(encoding="utf-8")) or {}
    return dt.date.fromisoformat(str(plan["date"]))


def start_record(profile: Profile, run: Path, concept: int = 0) -> VideoRecord:
    """video.json 을 읽거나 새로 만든다 (고른 컨셉 번호·벌 수를 채움)."""
    rec = load_record(run) or VideoRecord(account=profile.id, date=run_date(run), run=run.name)
    rec.concept = concept or rec.concept
    rec.looks = look_count(run)
    return rec


def plan(
    profile: Profile, run: Path, notes: list[str] | None = None, concept: int = 0
) -> VideoRecord:
    """① 그릴 그림 계획 (요금 없음). 확인을 받아야 그린다 (2026-11-02 사용자)."""
    rec = start_record(profile, run, concept)
    concept_of(profile, rec.date, rec.concept)  # 고른 컨셉이 있는지 먼저 확인
    add_notes(rec, notes or [])
    rec.step, rec.error = "plan", ""
    save_record(run, rec)
    return rec


def bg_path(run: Path, n: int) -> Path:
    return vdir(run) / "backgrounds" / f"{n}.jpg"


def backfill_backgrounds(run: Path, rec: VideoRecord) -> None:
    """번호 붙은 배경이 없던 때(2026-11-03 전) 그린 배경을 git 기록에서 꺼내 1, 2, … 로 둔다."""
    cur = vdir(run) / "background.jpg"
    if rec.bg_versions or not cur.exists():
        return
    rel = cur.resolve()
    blobs: list[bytes] = []
    try:
        top = subprocess.run(["git", "-C", str(run), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
        path = str(rel.relative_to(Path(top).resolve()))
        log = subprocess.run(["git", "-C", top, "log", "--reverse", "--format=%H", "--", path],
                             capture_output=True, text=True, check=True).stdout.split()  # fmt: skip
        for h in log:
            out = subprocess.run(["git", "-C", top, "show", f"{h}:{path}"], capture_output=True)
            if out.returncode == 0 and out.stdout and (not blobs or blobs[-1] != out.stdout):
                blobs.append(out.stdout)
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        blobs = []
    if not blobs or blobs[-1] != cur.read_bytes():
        blobs.append(cur.read_bytes())
    for i, b in enumerate(blobs, 1):
        bg_path(run, i).parent.mkdir(parents=True, exist_ok=True)
        bg_path(run, i).write_bytes(b)
    rec.bg_versions = len(blobs)


def make_background(
    profile: Profile, run: Path, rec: VideoRecord, c: Concept, client, models,
    base: int = 0, request: str = "",
) -> None:  # fmt: skip
    """빈 배경. base 가 있으면 그 번째 배경을 넣고 말한 곳만 고친다 (2026-11-03 사용자:
    "첫 번째 이미지에 문 위치만 앞쪽으로"). 없으면 처음부터 새로 그린다."""
    backfill_backgrounds(run, rec)
    model = models[profile.video.background_model]
    if base < 0:  # 지금 배경
        base = rec.bg_versions
    if base and request and bg_path(run, base).exists():
        prompt = template("background_edit", request=request)
        src = video_test.jpeg(bg_path(run, base).read_bytes())
        data, cost = client.generate(model, prompt, [src])
    else:
        base = 0
        prompt = template("background", background_en=c.background_en, light=c.light_en or c.light)
        for note in rec.notes.get("background", []):
            prompt += f"\nUser request: {note}"
        data, cost = client.generate(model, prompt, [])
    vdir(run).mkdir(parents=True, exist_ok=True)
    img = video_test.jpeg(data, 1920)
    (vdir(run) / "background.jpg").write_bytes(img)
    rec.bg_versions += 1
    bg_path(run, rec.bg_versions).parent.mkdir(parents=True, exist_ok=True)
    bg_path(run, rec.bg_versions).write_bytes(img)
    rec.bg_base = base
    rec.costs[f"background_{rec.bg_versions}"] = round(cost or 0, 4)


def background(
    profile: Profile, run: Path, notes: list[str] | None = None, fake: bool = False,
    concept: int = 0, base: int = 0, request: str = "",
) -> VideoRecord:  # fmt: skip
    """② 빈 배경 한 장. /ok 를 받아야 첫 장면을 그린다. base·request: 그 번째 배경을 고치기."""
    rec = start_record(profile, run, concept)
    c = concept_of(profile, rec.date, rec.concept)
    add_notes(rec, notes or [])
    client, models = _gen_client(profile, fake)
    make_background(profile, run, rec, c, client, models, base, request)
    rec.step, rec.error = "background", ""
    save_record(run, rec)
    return rec


def frames(
    profile: Profile,
    run: Path,
    looks: list[int] | None = None,
    notes: list[str] | None = None,
    redo_background: bool = False,
    fake: bool = False,
    concept: int = 0,
    face: Path | None = None,
) -> VideoRecord:
    """③ 벌마다 첫 장면: 얼굴 사진의 얼굴 + 확정 코디컷의 머리·코디로, 빈 배경에서 걷는 중인 모습.

    2026-11-03 사용자: 코디컷 그대로 포즈만 바꿔 배경에 그린다 (잘라 붙이기 없음),
    얼굴은 사용자가 만든 얼굴(flow/<계정>/face.jpg)로 모든 벌 통일.
    첫 장면은 걷는 중일 뿐 포즈 동작(모자 잡기 등)은 하지 않는다: 포즈는 영상에서 멈춘 뒤에.
    빈 배경이 없을 때만 배경도 그린다."""
    rec = start_record(profile, run, concept)
    c = concept_of(profile, rec.date, rec.concept)
    touched = add_notes(rec, notes or [])
    todo = looks or touched or list(range(1, rec.looks + 1))
    d = vdir(run)
    (d / "frames").mkdir(parents=True, exist_ok=True)
    client, models = _gen_client(profile, fake)
    kw = common(profile, c)
    v = profile.video
    bg_path = d / "background.jpg"
    if redo_background or not bg_path.exists():
        make_background(profile, run, rec, c, client, models)
        todo = list(range(1, rec.looks + 1))  # 배경이 바뀌면 첫 장면도 모두 새로
    bg = bg_path.read_bytes()
    shots = {s.look: s for s in c.shots}
    face_b = video_test.jpeg(face.read_bytes()) if face and face.exists() else None
    name = "first_frame_face" if face_b else "first_frame"
    for n in todo:
        shot = shots.get(n)
        prompt = template(name, **kw, items=items_en(run, n), start=shot.start_en if shot else "")
        for note in notes_for(rec, n):
            prompt += f"\nUser request (most important): {note}"
        outfit = video_test.jpeg(look_image(run, n).read_bytes())
        refs = [face_b, outfit, video_test.jpeg(bg)] if face_b else [outfit, video_test.jpeg(bg)]
        data, cost = client.generate(models[v.frame_model], prompt, refs)
        total = cost or 0
        (d / "frames" / f"look{n}.jpg").write_bytes(video_test.jpeg(data, 1920))
        rec.costs[f"frame_{n}"] = round(total, 4)
        if (d / "clips" / f"look{n}.mp4").exists() and n not in rec.stale:
            rec.stale.append(n)
    rec.step, rec.error = "frames", ""
    save_record(run, rec)
    return rec


def scene_dir(run: Path, n: int) -> Path:
    """n 번 벌 첫 장면의 그림·검수 폴더 (tools.review prep/apply 를 그대로 쓴다).
    outfit/originals/ 는 쇼핑몰 사진이라 결과 브랜치에 올리지 않는다 (video 워크플로가 뺌)."""
    return vdir(run) / "scene" / str(n)


def _panel(data: bytes, px: int = 1536) -> bytes:
    return video_test.jpeg(data, px)


def scene_items(plan: DailyPlan, products: dict, cfg: dict) -> str:
    """상품 참고판 설명 (tools.outfit_gen 장면 모델컷과 같은 말).
    옷 기장은 cm 대신 착용컷을 따른다."""
    from tools import outfit_gen

    lines = []
    for i, it in enumerate(plan.items, 1):
        p = products[it.product]
        size = outfit_gen.item_size(it, p, cfg, worn=True, ref=True)
        line = (
            f"The {outfit_gen.sheet_name(i)}: the {outfit_gen.item_label(it, p)},"
            f" {outfit_gen.english(it.desc)}."
        )
        lines.append(line + (f" Real size: {size}." if size else ""))
    return "\n".join(lines)


# 첫 장면: 얼굴 사진이 1번 그림이라 outfit_gen 의 "첫 그림" 문구를 순서에 맞게 (2026-11-10)
SHEETS_AFTER_FACE = (
    "Then come, in order, reference sheets of the exact items. Each sheet shows shop photos of"
    " one item: worn photos show how long it is and how it sits on a body, close-ups show its"
    " fabric"
)


def scene_frames(
    profile: Profile,
    run: Path,
    looks: list[int] | None = None,
    notes: list[str] | None = None,
    fake: bool = False,
    concept: int = 0,
    face: Path | None = None,
    client=None,
) -> VideoRecord:
    """③' 벌마다 영상 첫 장면을 상품 사진에서 바로 그린다 (2026-11-07 사용자: 스튜디오 코디컷 없이).

    한 번에: 고른 빈 배경 + 얼굴 사진(flow/<계정>/face.jpg) + 상품 참고판(tools.outfit_gen 과 같은
    쇼핑몰 사진, 기준 코디 사진)으로 컨셉의 시작 자세·위치·크기대로 사람을 배경 속에 그린다.
    그린 그림은 검수 폴더(video/scene/<n>/)에 두고 video 워크플로가 image-reviewer 로 상품을 본다.
    고칠 점("3:가방을 오른손에")이 오면 그 벌의 지금 첫 장면을 고친다
    (말한 곳만 바꿈, 10/29 사용자).
    "3:새로 …" 면 처음부터 다시 그린다. 길이 문구는 지시하지 않는다.
    얼굴 사진을 1번 그림으로 보내고 profile video.face_en(미모·화장·빛·머리)을 붙인다
    (2026-11-10 사용자). 새로 그릴 때 video.scene_candidates 장을 그리고,
    "3:판2" 처럼 고칠 말 없이 오면 2번째 그림을 첫 장면으로 고른다."""
    from tools import generate, outfit_gen
    from tools import looks as looks_tool
    from tools import review as review_tool

    rec = start_record(profile, run, concept)
    c = concept_of(profile, rec.date, rec.concept)
    asked: dict[int, list[str]] = {}
    for r in notes or []:
        head, sep, text = r.partition(":")
        if sep and head.strip().isdigit():
            asked.setdefault(int(head), []).append(text.strip())
    add_notes(rec, notes or [])
    todo = looks or sorted(asked) or list(range(1, rec.looks + 1))
    d = vdir(run)
    bg_file = d / "background.jpg"
    if not bg_file.exists():
        raise ValueError("빈 배경을 먼저 골라 주세요 (video.yml step=background)")
    if not (face and face.exists()) and not fake:
        raise ValueError(f"얼굴 사진이 없어요 (모든 벌 같은 얼굴): {face}")
    face_b = _panel(face.read_bytes()) if face and face.exists() else None
    bg = _panel(bg_file.read_bytes())
    gcfg = generate.load_config()
    if client is None:
        client, models = _gen_client(profile, fake)
    else:
        models = gcfg["models"]
    model = models[profile.video.frame_model]
    products = {p.id: p for p in Store(profile.id).products()}
    cache = generate.CACHE_DIR / profile.id
    shots = {s.look: s for s in c.shots}
    kw = common(profile, c)
    (d / "frames").mkdir(parents=True, exist_ok=True)
    drawn = []
    for n in todo:
        lp = DailyPlan.model_validate(
            yaml.safe_load((run / "looks" / str(n) / "plan.yaml").read_text(encoding="utf-8"))
        )
        sd = scene_dir(run, n)
        work = sd / "outfit"
        if (work / "originals").exists():
            shutil.rmtree(work / "originals")
        sheets = outfit_gen.reference_panels(lp, products, work, gcfg, cache)
        worn = outfit_gen.worn_ref_panel(lp, profile, cache)
        if lp.ref_photo and not worn:
            # 참고 코디(입는 모양)가 가장 중요하다 (PR #168). 없이 그리면 비용만 든다
            raise RuntimeError(f"LOOK {n} 참고 코디 사진을 받지 못해 그리지 않습니다")
        orig = work / "originals"
        if worn:
            (orig / "worn_ref.jpg").write_bytes(worn)
        if face_b:
            (orig / "face.jpg").write_bytes(face_b)
        (orig / "background.jpg").write_bytes(bg)
        shot = shots.get(n)
        start = (shot.start_en if shot else "") or profile.video.scene_start_en
        (work / "start.txt").write_text(
            ((shot.start if shot else "") or start) + "\n", encoding="utf-8"
        )
        items = scene_items(lp, products, gcfg)
        current = d / "frames" / f"look{n}.jpg"
        fix = [x for x in asked.get(n, []) if not x.startswith("새로")]
        # "3:판1 …" 이면 지금 첫 장면 대신 그 벌의 1번째 그림(results/S_1.jpg)을 고친다
        # (2026-11-07 사용자: "얼굴은 첫번째 사진을 쓰고 …")
        base = current
        for i, x in enumerate(fix):
            m = re.match(r"판\s*(\d+)\s*", x)
            if m:
                base = work / "results" / f"S_{m.group(1)}.jpg"
                if not base.exists():
                    raise ValueError(f"LOOK {n} {m.group(1)}번째 그림이 없어요: {base.name}")
                fix[i] = x[m.end() :]
        fix = [x for x in fix if x.strip()]
        if not fix and base != current and n in rec.scene:
            # "3:판2" (고칠 말 없음): 그 그림을 첫 장면으로 고르기만 한다 (그리지 않음)
            shutil.copy2(base, current)
            if (d / "clips" / f"look{n}.mp4").exists() and n not in rec.stale:
                rec.stale.append(n)
            drawn.append(n)
            continue
        count = 1
        if fix and base.exists() and n in rec.scene:
            prompt = template("scene_frame_edit", request="; ".join(fix), items=items)
            refs = [_panel(base.read_bytes()), *([face_b] if face_b else []), *sheets]
        else:
            lp = lp.model_copy(
                update={
                    "requests": [
                        *lp.requests,
                        *[x.removeprefix("새로").strip() for x in notes_for(rec, n)],
                    ][:6]
                }
            )
            prompt = template(
                "scene_frame",
                must=outfit_gen.must_text(lp, bool(worn)),
                # 얼굴 사진이 1번 그림이라 참고 코디 사진은 2번째, 상품판은 그 뒤 (2026-11-10)
                worn=outfit_gen.WORN_REF_MODEL.replace(
                    "The first attached image", "The second attached image"
                )
                if worn
                else "",
                sheets=outfit_gen.SHEETS_AFTER_REF if worn else SHEETS_AFTER_FACE,
                items=items,
                start=start,
                lens=profile.video.frame_camera_en,
                face=profile.video.face_en or "Do not change her face",
                light=kw["light"],
            )
            refs = [*([face_b] if face_b else []), *([worn] if worn else []), *sheets, bg]
            count = profile.video.scene_candidates
        (work / "results").mkdir(parents=True, exist_ok=True)
        made = []
        for _ in range(count):
            data, cost = client.generate(model, prompt, refs)
            k = len(list((work / "results").glob("S_*.jpg"))) + 1
            out = work / "results" / f"S_{k}.jpg"
            out.write_bytes(video_test.jpeg(data, 1920))
            (work / f"prompt_{k}.txt").write_text(prompt, encoding="utf-8")
            rec.costs[f"scene_{n}_{k}"] = round(cost or 0, 4)
            made.append(out)
        (work / "prompt.txt").write_text(prompt, encoding="utf-8")
        looks_tool.write_plan(lp.model_copy(update={"shot": "model"}), sd / "plan.yaml")
        if review_tool.review_path(sd).exists():
            review_tool.review_path(sd).unlink()
        review_tool.start(sd, made)
        shutil.copy2(made[0], current)
        if n not in rec.scene:
            rec.scene = sorted([*rec.scene, n])
        if (d / "clips" / f"look{n}.mp4").exists() and n not in rec.stale:
            rec.stale.append(n)
        drawn.append(n)
    (d / "scene").mkdir(exist_ok=True)
    (d / "scene" / "drawn.txt").write_text(" ".join(map(str, drawn)) + "\n", encoding="utf-8")
    rec.step, rec.error = "frames", ""
    save_record(run, rec)
    return rec


def motion_prompt(profile: Profile, run: Path, c: Concept, rec: VideoRecord, n: int) -> str:
    shot = next((s for s in c.shots if s.look == n), None)
    motion = (shot.motion_en or f"{shot.action}, {shot.pose}") if shot else "She walks naturally"
    text = template(
        "motion", **common(profile, c), items=items_en(run, n), motion=motion,
        face=profile.video.motion_face_en,
    )  # fmt: skip
    notes = notes_for(rec, n, motion=True)
    if not (uses_frames(profile) or rec.scene):  # 첫 장면이 없으니 그림 고칠 점도 영상 지시문에
        notes = notes_for(rec, n) + notes
    for note in notes:
        text += f"\nUser request (most important): {note}"
    return text


def prompts(profile: Profile, run: Path, notes: list[str] | None = None) -> VideoRecord:
    """③ 벌마다 영상 지시문 (AI 요금 없음)."""
    rec = load_record(run)
    if rec is None:
        raise ValueError("첫 장면(frames)을 먼저 만들어 주세요")
    c = concept_of(profile, rec.date, rec.concept)
    add_notes(rec, notes or [], motion=True)
    out = {str(n): motion_prompt(profile, run, c, rec, n) for n in range(1, rec.looks + 1)}
    (vdir(run) / "prompts.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    rec.step, rec.error = "prompts", ""
    save_record(run, rec)
    return rec


def clips(
    profile: Profile,
    run: Path,
    looks: list[int] | None = None,
    notes: list[str] | None = None,
    fake: bool = False,
    model_key: str = "",
    face: Path | None = None,
) -> VideoRecord:
    """④ 벌마다 첫 장면을 영상으로 (없는 벌, 또는 고칠 벌만) + 음악이 없으면 음악.

    model_key 를 주면 모델 비교 시험: clips_<모델>/ 에 그 벌들만 만들고 편집·단계는 그대로."""
    rec = load_record(run)
    if rec is None:
        raise ValueError("첫 장면(frames)을 먼저 만들어 주세요")
    c = concept_of(profile, rec.date, rec.concept)
    touched = add_notes(rec, notes or [], motion=True)
    d = vdir(run)
    (d / "clips").mkdir(exist_ok=True)
    cfg = video_test.load_config()
    vcfg = cfg["video"]
    # 첫 장면을 상품 사진에서 바로 그렸으면(scene_frames) 그 그림을 first_frame 으로 (2026-11-07)
    key = model_key or (profile.video.scene_video_model if rec.scene else profile.video.video_model)
    model = vcfg["models"][key]
    if model_key:
        return compare_clips(profile, run, rec, c, model, model_key, looks or [1], fake, face)
    if rec.scene and model.get("inputs") != "first_frame":
        raise ValueError(f"video.scene_video_model '{key}' 은 첫 장면(first_frame) 모델이어야 해요")
    redo = set(looks or touched) | set(rec.stale)
    client = (
        video_test.FakeVideoClient()
        if fake
        else video_test.VideoClient(vcfg, os.environ["OPENROUTER_API_KEY"])
    )
    texts = {}
    for n in range(1, rec.looks + 1):
        clip = d / "clips" / f"look{n}.mp4"
        texts[str(n)] = motion_prompt(profile, run, c, rec, n)
        if clip.exists() and n not in redo:
            continue
        if rec.scene and (
            (looks and n not in looks) or not (d / "frames" / f"look{n}.jpg").exists()
        ):  # 첫 장면 방식: 한 벌씩 시험할 수 있게 고른 벌·첫 장면이 있는 벌만
            continue
        body, texts[str(n)] = clip_body(run, c, n, model, texts[str(n)])
        video, cost, _ = client.make(body)
        clip.write_bytes(video if not fake else _fake_clip(look_image(run, n).read_bytes()))
        rec.costs[f"clip_{n}"] = round(cost or 0, 4)
        if n in rec.stale:
            rec.stale.remove(n)
    (d / "prompts.json").write_text(json.dumps(texts, ensure_ascii=False, indent=2), "utf-8")
    if profile.video.music == "lyria" and not (d / "music.mp3").exists():
        make_music(profile, run, rec, c, fake)
    save_record(run, rec)
    missing = [n for n in range(1, rec.looks + 1) if not (d / "clips" / f"look{n}.mp4").exists()]
    if missing:  # 한 벌 시험 등: 모든 벌 영상이 생긴 뒤에 편집
        print(f"아직 영상이 없는 벌: {' '.join(map(str, missing))} (편집은 모두 만든 뒤에)")
        return rec
    return edit(profile, run)


REF_ROLES = (
    "PERSON AND OUTFIT (most important): the woman in image 1 is the only person, exactly as "
    "she is in image 1: the same face, hair, and the same outfit copied item by item: the same "
    "garments, colors, patterns, knit textures, collars, necklines, lengths, fit, layering, "
    "shoes, bag and accessories, worn the same way. Do not redesign, simplify, recolor or add "
    "anything. Her face and outfit stay exactly like image 1 for the whole video, "
    "nothing changes or morphs. Ignore the plain backdrop and the standing pose of image 1.\n"
    "PLACE: image 2, the same place, light and camera angle (it has no people)."
)


def ref_motion(text: str) -> str:
    """첫 장면 기준 지시문을 참고 사진 방식으로 (첫 장면 → image 1)."""
    text = text.replace("Start exactly from the first frame.", "")
    return text.replace("as in the first frame", "as in image 1")


def clip_body(run: Path, c: Concept, n: int, model: dict, text: str) -> tuple[dict, str]:
    """영상 AI 요청: 첫 장면 모델은 첫 장면 한 장, 참고 사진 모델은 코디컷·빈 배경 두 장.

    2026-11-03 사용자: 중간 그림(첫 장면) 없이 코디컷 인물 그대로 배경에서 동작만 지시."""
    if model.get("inputs") == "first_frame":
        first = (vdir(run) / "frames" / f"look{n}.jpg").read_bytes()
        return video_test.payload(model, {"aspect_ratio": "9:16"}, text, [], first), text
    shot = next((s for s in c.shots if s.look == n), None)
    start = f"Start: {shot.start_en.rstrip('.')}." if shot and shot.start_en else ""
    text = "\n".join([REF_ROLES, start, ref_motion(text)])
    refs = [
        video_test.jpeg(look_image(run, n).read_bytes()),
        video_test.jpeg((vdir(run) / "background.jpg").read_bytes()),
    ]
    return video_test.payload(model, {"aspect_ratio": "9:16"}, text, refs), text


def compare_clips(profile, run, rec, c, model: dict, key: str, looks: list[int], fake: bool,
                  face: Path | None = None):  # fmt: skip
    """같은 지시문으로 다른 영상 모델 시험 (clips_<모델>/lookN.mp4).

    첫 장면 모델은 첫 장면 한 장, 참고 사진 모델은 코디컷·빈 배경 두 장
    (2026-11-03 사용자: 중간 그림 없이 코디컷 인물 그대로 배경에서 움직이게)."""
    vcfg = video_test.load_config()["video"]
    client = (
        video_test.FakeVideoClient()
        if fake
        else video_test.VideoClient(vcfg, os.environ["OPENROUTER_API_KEY"])
    )
    out = vdir(run) / f"clips_{key}"
    out.mkdir(exist_ok=True)
    for n in looks:
        body, text = clip_body(run, c, n, model, motion_prompt(profile, run, c, rec, n))
        video, cost, _ = client.make(body)
        fake_src = look_image(run, n).read_bytes()
        (out / f"look{n}.mp4").write_bytes(video if not fake else _fake_clip(fake_src))
        (out / f"look{n}.txt").write_text(text, encoding="utf-8")
        rec.costs[f"clip_{key}_{n}"] = round(cost or 0, 4)
    save_record(run, rec)
    return rec


def make_music(profile: Profile, run: Path, rec: VideoRecord, c: Concept, fake: bool) -> None:
    cfg = video_test.load_config()
    entry = video_test.music({"music": {"prompt": c.music}}, vdir(run), cfg, fake)
    if fake:
        _fake_audio(vdir(run) / "music.mp3")
    rec.costs["music"] = round(entry.get("cost") or 0, 4)


def music(profile: Profile, run: Path, fake: bool = False) -> VideoRecord:
    rec = load_record(run)
    if rec is None:
        raise ValueError("영상을 먼저 만들어 주세요")
    make_music(profile, run, rec, concept_of(profile, rec.date, rec.concept), fake)
    save_record(run, rec)
    return edit(profile, run)


# ---------- 편집 (코드, 무료) ----------


def _ffmpeg(*args: str) -> None:
    from tools.reels import ffmpeg_exe

    res = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", *args], capture_output=True)
    if res.returncode:
        raise RuntimeError(f"ffmpeg 실패: {res.stderr.decode(errors='replace')[-400:]}")


def _fake_clip(first: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as t:
        img, out = Path(t) / "f.jpg", Path(t) / "c.mp4"
        img.write_bytes(first)
        _ffmpeg(
            "-loop",
            "1",
            "-i",
            str(img),
            "-t",
            "5",
            "-vf",
            "scale=720:1280,fps=24",
            "-pix_fmt",
            "yuv420p",
            str(out),
        )
        return out.read_bytes()


def _fake_audio(path: Path) -> None:
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=30", str(path))


def layout() -> dict:
    return yaml.safe_load(LAYOUT.read_text(encoding="utf-8"))["video_edit"]


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def overlay_png(profile: Profile, n: int, hook: str, path: Path) -> None:
    """한 벌 화면 글자 (첫 벌 훅 문구 + 오른쪽 위 LOOK 번호). AI 가 그리지 않는다."""
    lay = layout()
    w, h = lay["size"]
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    color = profile.visual.title_color
    if hook:
        size = lay["hook_size"]
        font = _font(profile.visual.font, size)
        while draw.textlength(hook, font=font) > w * 0.86 and size > 30:
            size -= 2
            font = _font(profile.visual.font, size)
        tw = draw.textlength(hook, font=font)
        draw.text(((w - tw) / 2, h * lay["hook_y"] - size / 2), hook, font=font, fill=color)
    bar = None
    if lay.get("look_place") == "carousel":
        # 캐러셀 릴스 판 벌 장과 같은 자리·글꼴·세로선 (2026-11-05 사용자 "캐러셀 글자 위치에")
        from tools.feed import tall_profile

        tp = tall_profile(profile)
        lk, v, k = tp.looks, tp.visual, w / 1080
        font = _font(v.font, round(lk.label_size * k))
        x, y = round(w * tp.feed.margin_x), h * (1 - lk.bottom)
        draw.text((x, y), (lk.label or "LOOK {n}").format(n=n), font=font, fill=lk.text_color,
                  anchor="ls")  # fmt: skip
        if v.accent_color:
            x1 = x - round(v.accent_gap * k)
            x0 = x1 - max(1, round(v.accent_width * k))
            bar = ((x0, round(y - font.size * 0.8), x1 - 1, round(y + 2 * k)), v.accent_color)
    else:
        font = _font(profile.visual.logo_font, lay["look_size"])
        label = f"LOOK {n}"
        right, top = lay["look_margin"]
        draw.text((w - right - draw.textlength(label, font=font), top), label, font=font,
                  fill=color)  # fmt: skip
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 110), mask=im.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(4))
    out = Image.alpha_composite(shadow, im)
    if bar:
        ImageDraw.Draw(out).rectangle(bar[0], fill=bar[1])
    out.save(path)


def _duration(path: Path) -> float:
    from tools.reels import ffmpeg_exe

    probe = Path(ffmpeg_exe()).with_name("ffprobe")
    exe = str(probe) if probe.exists() else "ffprobe"
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
        )
        return float(out.stdout.strip())
    except (ValueError, FileNotFoundError):
        return 5.0


def _face_height(clip: Path, at: float) -> float:
    from tools.faces import find_faces

    with tempfile.TemporaryDirectory() as t:
        img = Path(t) / "f.jpg"
        try:
            _ffmpeg("-ss", f"{at:.2f}", "-i", str(clip), "-frames:v", "1", str(img))
            boxes = find_faces(Image.open(img))
        except RuntimeError:
            return 0
    return max((b[3] - b[1] for b in boxes), default=0)


def subset_picks(run: Path, keep: list[int], pick: int = 1) -> Path:
    """코디컷을 그리기 전의 후보 실행(picks/p<번호>.yaml)에서 영상에 넣을 벌만 골라 새 결과 폴더로
    (2026-11-07 사용자: 스튜디오 코디컷 없이 영상 첫 장면을 상품 사진에서 바로 그림).
    벌마다 looks/<n>/plan.yaml (한 벌 기획) 과 plan.yaml (고른 벌만, 컨셉 재료)을 둔다.
    manifest·코디컷은 없다. 컨셉(video-concept run "<새 폴더>/plan")·빈 배경·첫 장면이
    이 폴더를 쓴다."""
    src = run / "picks" / f"p{pick}.yaml"
    if not src.exists():
        raise FileNotFoundError(f"후보 기획이 없어요: {src}")
    plan = DailyPlan.model_validate(yaml.safe_load(src.read_text(encoding="utf-8")))
    total = len(plan.looks)
    if not keep or len(set(keep)) != len(keep) or not all(1 <= n <= total for n in keep):
        raise ValueError(f"벌 번호는 1~{total} 를 겹치지 않게: {keep}")
    tag = "" if pick == 1 else f"-p{pick}"
    dest = run.parent / f"{run.name}{tag}-l{''.join(map(str, keep))}"
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "looks").mkdir(parents=True)
    for new, old in enumerate(keep, 1):
        d = dest / "looks" / str(new)
        d.mkdir()
        sub = plan.look_plan(old - 1)
        (d / "plan.yaml").write_text(
            yaml.safe_dump(sub.model_dump(mode="json", exclude_none=True), allow_unicode=True),
            encoding="utf-8",
        )
        prev = run / "picks" / "preview" / f"p{old}.jpg"  # 후보 시트 (이미 결과 브랜치에 있음)
        if prev.exists():
            shutil.copy2(prev, d / "preview.jpg")
    if (run / "SOURCE.txt").exists():
        shutil.copy2(run / "SOURCE.txt", dest / "SOURCE.txt")
    picked = plan.model_copy(update={"looks": [plan.looks[n - 1] for n in keep]})
    (dest / "plan.yaml").write_text(
        yaml.safe_dump(picked.model_dump(mode="json", exclude_none=True), allow_unicode=True),
        encoding="utf-8",
    )
    (dest / "source.json").write_text(
        json.dumps({"run": run.name, "pick": pick, "looks": keep}, ensure_ascii=False), "utf-8"
    )
    return dest


def subset(run: Path, keep: list[int], pick: int = 0) -> Path:
    """영상에 넣을 벌만 골라 옆에 새 결과 폴더를 만든다 (2026-11-05 사용자 "1,2,3,5,6,7").
    벌 번호는 고른 순서대로 1부터 다시 매기고, 기획(plan.yaml·manifest)의 벌도 같이 줄인다.
    코디컷이 없는 후보 실행(picks/ 만 있음)이거나 pick 을 주면 subset_picks."""
    if pick or not (run / "looks").is_dir():
        return subset_picks(run, keep, pick or 1)
    total = look_count(run)
    if not keep or len(set(keep)) != len(keep) or not all(1 <= n <= total for n in keep):
        raise ValueError(f"벌 번호는 1~{total} 를 겹치지 않게: {keep}")
    dest = run.parent / f"{run.name}-l{''.join(map(str, keep))}"
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "looks").mkdir(parents=True)
    for new, old in enumerate(keep, 1):
        shutil.copytree(run / "looks" / str(old), dest / "looks" / str(new))
    for name in ("SOURCE.txt", "caption.txt"):
        if (run / name).exists():
            shutil.copy2(run / name, dest / name)
    plan = yaml.safe_load((run / "plan.yaml").read_text(encoding="utf-8")) or {}
    if plan.get("looks"):
        plan["looks"] = [plan["looks"][n - 1] for n in keep]
        plan.pop("cover_look", None)
    (dest / "plan.yaml").write_text(
        yaml.safe_dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    m = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    looks = m.get("plan", {}).get("looks") or []
    if looks:
        m["plan"]["looks"] = [looks[n - 1] for n in keep]
        ids = {i for look in m["plan"]["looks"] for i in look}
        m["plan"]["items"] = [p for p in m["plan"]["items"] if p.get("id") in ids]
    Manifest.model_validate(m)
    (dest / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), "utf-8")
    return dest


def edit(
    profile: Profile,
    run: Path,
    speed: float | None = None,
    hook: str | None = None,
    order: list[int] | None = None,
) -> VideoRecord:
    """벌 영상을 잘라 이어 붙이고 글자·음악을 얹는다. 음악 있음/없음 두 판과 표지 한 장."""
    rec = load_record(run)
    if rec is None:
        raise ValueError("영상을 먼저 만들어 주세요")
    c = concept_of(profile, rec.date, rec.concept)
    if speed:
        rec.speed = speed
    if hook is not None:
        rec.hook = hook
    if order:
        if sorted(order) != list(range(1, rec.looks + 1)):
            raise ValueError(f"순서는 1~{rec.looks} 를 한 번씩: {order}")
        rec.order = order
    sp = rec.speed or profile.video.speed
    seq = rec.order or list(range(1, rec.looks + 1))
    hook_text = rec.hook or c.hook
    lay = layout()
    w, h = lay["size"]
    d = vdir(run)
    final = d / "final"
    final.mkdir(exist_ok=True)
    rec.flags = []
    total = 0.0
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        segs = []
        for i, n in enumerate(seq):
            clip = d / "clips" / f"look{n}.mp4"
            start = profile.video.clip_start
            # 영상 전부 (2026-11-03 사용자: 퇴장 장면이 잘리지 않게. 컨셉 seconds 는 지시문용)
            want = (_duration(clip) - start) / sp
            src = min(want * sp, _duration(clip) - start)
            png = tmp / f"o{n}.png"
            overlay_png(profile, i + 1, hook_text if i == 0 else "", png)  # 보이는 순서 번호
            seg = tmp / f"s{i}.mp4"
            vf = (
                f"[0:v]setpts=(PTS-STARTPTS)/{sp},"
                f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"fps={lay['fps']}[v];[v][1:v]overlay=0:0,format=yuv420p"
            )
            _ffmpeg(
                "-ss",
                f"{start:.2f}",
                "-t",
                f"{src:.2f}",
                "-i",
                str(clip),
                "-i",
                str(png),
                "-filter_complex",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "medium",
                str(seg),
            )
            segs.append(seg)
            total += src / sp
            a, b = _face_height(clip, start + 0.1), _face_height(clip, start + src - 0.1)
            if a and b and b / a >= lay["zoom_flag"]:
                rec.flags.append(f"LOOK {n} 카메라가 다가와요 (얼굴 {b / a:.1f}배)")
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")
        silent = final / "video_silent.mp4"
        _ffmpeg(
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(silent),
        )
    mp3 = d / "music.mp3"
    if mp3.exists():
        fo = lay["music_fade_out"]
        af = (
            f"[1:a]atrim=0:{total:.2f},afade=t=in:d={lay['music_fade_in']},"
            f"afade=t=out:st={max(0, total - fo):.2f}:d={fo}[a]"
        )
        _ffmpeg(
            "-i",
            str(silent),
            "-i",
            str(mp3),
            "-filter_complex",
            af,
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final / "video_music.mp4"),
        )
    _ffmpeg(
        "-ss", "0.5", "-i", str(silent), "-frames:v", "1", "-q:v", "2", str(final / "cover.jpg")
    )
    if (run / "manifest.json").exists():  # 후보 실행에서 고른 폴더는 캡션이 없다 (따로 씀)
        m = manifest_of(run)
        # 영상이므로 AI 고지를 "영상은 …" 으로 (2026-11-03 사용자)
        notice = profile.copy_rules.ai_notice
        caption = m.caption.replace(notice, video_notice(notice))
        if m.hashtags:  # 바로 붙여 넣을 수 있게 해시태그까지
            caption += "\n\n" + " ".join(m.hashtags)
        (final / "caption.txt").write_text(caption + "\n", encoding="utf-8")
    rec.step, rec.error = "final", ""
    save_record(run, rec)
    return rec


# ---------- 텔레그램 ----------


BG_COST, FRAME_COST = 0.14, 0.28  # 안내용 어림값 (빈 배경 한 장, 첫 장면 한 장 + 얼굴 고치기)


def plan_text(tag: str, profile: Profile, c: Concept, rec: VideoRecord, run: Path) -> str:
    """① 그리기 전에 확인받는 그림 계획 (2026-11-02 사용자)."""
    bg_req = "; ".join(rec.notes.get("background", []))
    lines = [
        f"{tag} 영상에 쓸 그림 계획이에요. 확인해 주시면 그릴게요 (아직 요금 없음).",
        "",
        f"① 빈 배경 1장 (약 ${BG_COST:.2f}): 사람 없이 배경만",
        f"• 장소: {c.background}",
        f"• 빛·색감: {c.light}",
    ]
    if bg_req:
        lines.append(f"• 요청: {bg_req}")
    lines += [
        "",
        f"② 벌마다 첫 장면 {rec.looks}장 (장당 약 ${FRAME_COST:.2f}): "
        "확정 코디컷의 인물(얼굴·머리·코디) 그대로 포즈만 바꿔 빈 배경에 그려요"
        if uses_frames(profile)
        else "② 벌마다 영상: 확정 코디컷과 빈 배경을 영상 AI 에 바로 넣어요 (첫 장면 그림 없음)",
        f"• 구도: {c.framing}"
        + ("" if any(s.start for s in c.shots) else " (인물은 화면 세로의 70~80%)"),
    ]
    shots = {s.look: s for s in c.shots}
    for n in range(1, rec.looks + 1):
        s = shots.get(n)
        try:
            items = items_ko(run, n)
        except FileNotFoundError:
            items = ""
        move = f"{s.action} → {s.pose}" if s else "걷는 중"
        if s and s.start:
            move = f"시작: {s.start} / {move}"
        extra = "; ".join(notes_for(rec, n))
        req = f" (요청: {extra})" if extra else ""
        lines.append(f"• LOOK {n}: {items or '확정 코디컷'} / {move}{req}")
    lines += [
        "",
        "빈 배경을 먼저 그려 보내 드리고, 배경에 /ok 를 받으면 "
        + ("첫 장면을 그려요." if uses_frames(profile) else "영상 지시문을 보내 드려요."),
        "• /ok → 빈 배경 그리기",
        "• /fix 배경 바다가 보이게 → 배경 계획 고치기 (요금 없음)",
        "• /fix 3 가방을 앞으로 들게 → LOOK 3 계획 고치기",
        "• /fix 모두 가방을 앞으로 들게 → 모든 벌 계획 고치기",
        "• /skip → 이 영상은 만들지 않고 다음 주제로",
        "답을 받을 때까지 기다려요",
    ]
    return "\n".join(lines)


def bg_label(rec: VideoRecord) -> str:
    n = rec.bg_versions or 1
    return f"{n}번째" + (f" ({rec.bg_base}번째를 고침)" if rec.bg_base else "")


def background_guide(tag: str, rec: VideoRecord, frames: bool = True) -> str:
    n = rec.bg_versions or 1
    lines = [
        f"{tag} 영상 빈 배경 {bg_label(rec)}이에요. 영상은 모두 이 배경에서 찍어요.",
        f"• /ok → 벌마다 첫 장면 {rec.looks}장 그리기 (약 ${FRAME_COST * rec.looks:.2f})"
        if frames
        else "• /ok → 영상 지시문을 보내 드려요 (요금 없음)",
        f"• /fix 문을 앞쪽으로 → 지금({n}번째) 배경에서 그 부분만 고쳐요 (약 ${BG_COST:.2f})",
    ]
    if n > 1:
        lines.append("• /fix 1 문을 앞쪽으로 → 1번째 배경에서 그 부분만 고쳐요")
    lines += [
        "• /fix 새로 해변으로 → 처음부터 새로 그려요",
        "• /skip → 이 영상은 만들지 않고 다음 주제로",
        "답을 받을 때까지 기다려요",
    ]
    return "\n".join(lines)


def frames_guide(tag: str) -> str:
    return "\n".join(
        [
            f"{tag} 영상 첫 장면이에요 (LOOK 1~ 순서). 영상은 이 장면에서 시작해요.",
            "• /ok → 영상 지시문을 보내 드려요 (요금 없음)",
            f"• /fix 3 얼굴이 달라 → LOOK 3 첫 장면만 다시 그려요 (약 ${FRAME_COST:.2f})",
            "• /fix 모두 조금 더 작게 → 모든 벌 첫 장면을 다시 그려요",
            f"• /fix 배경 눈이 더 많이 → 빈 배경부터 다시 그려 보내 드려요 (약 ${BG_COST:.2f}, "
            "배경에 /ok 하면 첫 장면도 모두 다시)",
            "• /skip → 이 영상은 만들지 않고 다음 주제로",
            "답을 받을 때까지 기다려요",
        ]
    )


def prompts_text(tag: str, profile: Profile, c: Concept, rec: VideoRecord) -> str:
    v = profile.video
    sp = rec.speed or v.speed
    lines = [
        f"{tag} 영상 지시문이에요. /ok 면 벌마다 영상을 만들어요 "
        f"({v.video_model}, 벌마다 약 $0.65).",
        "",
        "공통",
        f"• 카메라: {c.framing}",
        f"• 걷는 속도: {c.pace or '1초에 약 2걸음, 보폭 살짝 크게'} (편집에서 {sp:g}배 빠르게)",
        f"• 빛: {c.light}",
        "• 금지: 다가가기·줌·흔들림, 다른 사람, 글자, 로고",
        "",
        "벌마다",
    ]
    for s in c.shots:
        extra = "; ".join(notes_for(rec, s.look, motion=True))
        lines.append(
            f"• LOOK {s.look} ({s.seconds:g}초): {s.action} → {s.pose}"
            + (f" (요청: {extra})" if extra else "")
        )
    lines += [
        "",
        "영어 원문은 첨부 파일에 있어요.",
        "• /ok → 영상 만들기",
        "• /fix 3 가방을 고쳐 들게 → LOOK 3 지시문에 더해요 (요금 없음)",
        "• /fix 더 빨리 걷게 → 모든 벌에 더해요",
        "• /skip → 이 영상은 만들지 않고 다음 주제로",
    ]
    return "\n".join(lines)


def uses_frames(profile: Profile) -> bool:
    """영상 모델이 첫 장면 한 장으로 움직이는지. 아니면 코디컷·빈 배경을 바로 넣는다
    (첫 장면 단계 없음, 2026-11-03 사용자: 중간 그림을 거치면 옷·얼굴이 틀어진다)."""
    model = video_test.load_config()["video"]["models"].get(profile.video.video_model, {})
    return model.get("inputs") == "first_frame"


def video_notice(notice: str) -> str:
    """AI 합성 고지를 영상용으로 ("이미지는 …" → "영상은 …")."""
    return "영상은" + notice[len("이미지는") :] if notice.startswith("이미지는") else notice


def has_music(profile: Profile) -> bool:
    return profile.video.music == "lyria"


def final_guide(tag: str, rec: VideoRecord, profile: Profile) -> str:
    sp = rec.speed or profile.video.speed
    order = " ".join(map(str, rec.order or range(1, rec.looks + 1)))
    files = "음악 있음·없음 원본 파일" if has_music(profile) else "원본 파일"
    lines = [
        f"{tag} 영상 최종본이에요 ({files}, 표지, 캡션).\n게시는 직접 올려 주세요.",
        f"지금: 속도 {sp:g}배 · 순서 {order} · 비용 ${rec.total():.2f}",
    ]
    lines += [f"⚠ {f}" for f in rec.flags]
    lines += [
        "• /speed 1.3 → 속도만 바꿔 다시 편집 (무료)",
        "• /제목 새 문구 → 첫 화면 문구 바꾸기 (무료)",
        "• /order 3 1 2 4 5 6 → 순서 바꾸기 (무료)",
        "• /fix 3 더 빨리 걷게 → LOOK 3 영상만 다시 (약 $0.65)",
    ]
    if uses_frames(profile):
        lines.append(
            "• /frame 3 얼굴이 달라 → LOOK 3 첫 장면을 다시 그려 보내 드려요 "
            f"(약 ${FRAME_COST:.2f}, /ok 하면 그 벌 영상도 다시 약 $0.65)"
        )
    if has_music(profile):
        lines.append("• /music → 음악만 새로 (약 $0.04)")
    lines += [
        "• /skip → 그만두고 다음 주제로",
        "• /ok → 끝 (직접 올린 것으로 보고 다음 주제로)",
    ]
    return "\n".join(lines)


def tag_of(profile: Profile, rec: VideoRecord) -> str:
    return f"[{profile.id}] {rec.date:%m-%d}"


def send(profile: Profile, run: Path, tg) -> VideoRecord:
    """지금 단계(video.json step)의 결과를 텔레그램으로 보낸다."""
    rec = load_record(run)
    assert rec is not None
    d = vdir(run)
    tag = tag_of(profile, rec)
    if rec.step == "plan":
        c = concept_of(profile, rec.date, rec.concept)
        tg.send(plan_text(tag, profile, c, rec, run))
    elif rec.step == "background":
        tg.send_photos([d / "background.jpg"], f"{tag} 영상 빈 배경 {bg_label(rec)}")
        tg.send(background_guide(tag, rec, uses_frames(profile)))
    elif rec.step == "frames":
        pics = [d / "frames" / f"look{n}.jpg" for n in range(1, rec.looks + 1)]
        tg.send_photos(pics, f"{tag} 영상 첫 장면")
        tg.send(frames_guide(tag))
    elif rec.step == "prompts":
        c = concept_of(profile, rec.date, rec.concept)
        data = json.loads((d / "prompts.json").read_text(encoding="utf-8"))
        txt = d / "prompts.txt"
        txt.write_text("\n\n".join(f"LOOK {n}\n{t}" for n, t in data.items()) + "\n", "utf-8")
        tg.send(prompts_text(tag, profile, c, rec))
        tg.send_document(txt, f"{tag} 영상 지시문 (영어 원문)")
    else:
        f = d / "final"
        music = has_music(profile) and (f / "video_music.mp4").exists()
        main_video = f / "video_music.mp4" if music else f / "video_silent.mp4"
        tg.send_video(main_video, f"{tag} 영상 미리보기")
        if music:
            for name, label in (("video_music.mp4", "음악 있음"),
                                ("video_silent.mp4", "음악 없음")):  # fmt: skip
                tg.send_document(f / name, f"{tag} 영상 원본 ({label})")
        else:  # 결과 영상 하나 (2026-11-02 사용자 "음악은 없애줘")
            tg.send_document(main_video, f"{tag} 영상 원본")
        tg.send_photos([f / "cover.jpg"], f"{tag} 표지 프레임")
        tg.send((f / "caption.txt").read_text(encoding="utf-8"))
        tg.send(final_guide(tag, rec, profile))
    rec.sent_at = int(time.time())
    save_record(run, rec)
    return rec


def guide(tag: str, rec: VideoRecord, profile: Profile, run: Path | None = None) -> str:
    """지금 단계에서 쓸 수 있는 명령 (답을 못 알아들었을 때 다시 보낸다)."""
    if rec.step == "plan":
        c = concept_of(profile, rec.date, rec.concept)
        return plan_text(tag, profile, c, rec, run or Path(rec.run))
    if rec.step == "background":
        return background_guide(tag, rec, uses_frames(profile))
    if rec.step == "frames":
        return frames_guide(tag)
    if rec.step == "prompts":
        return prompts_text(tag, profile, concept_of(profile, rec.date, rec.concept), rec)
    return final_guide(tag, rec, profile)


def action_text(act: dict) -> str:
    """요청을 사용자에게 알리는 한 줄."""
    step = act.get("step")
    looks = f"LOOK {act['looks']} " if act.get("looks") else ""
    again = "다시 " if act.get("note") or act.get("looks") else ""
    return {
        "plan": "그림 계획을 고쳐 다시 보낼게요",
        "background": f"빈 배경을 {again}그려 보낼게요",
        "frames": f"{looks or '모든 벌 '}첫 장면을 {again}그려 보낼게요",
        "prompts": "영상 지시문을 고쳐 다시 보낼게요"
        if act.get("note")
        else "영상 지시문을 보낼게요",
        "clips": f"{looks}영상을 다시 만들게요"
        if looks
        else "벌마다 영상을 만들게요 (20분쯤 걸려요)",
        "redo": f"{looks}첫 장면부터 영상까지 다시 만들게요",
        "edit": "다시 편집해서 최종본을 보낼게요",
        "music": "음악을 새로 만들어 다시 편집할게요",
    }.get(step or "", "영상 요청을 보냈어요")


def record_post(store: Store, run: Path) -> None:
    """영상이 캐러셀을 대신할 때: 사용자가 직접 올린 것으로 보고 게시 기록을 남긴다
    (다음 게시 날짜와 상품 중복 피하기에 쓰인다)."""
    from core.models import PostRecord
    from tools.publish import post_links

    m = manifest_of(run)
    posts = [p for p in store.posts() if p.date != m.date]
    posts.append(
        PostRecord(
            date=m.date,
            title=" ".join(m.title_lines),
            theme=m.plan.theme.title_hint,
            products=[p.id for p in m.plan.items],
            links=post_links(m),
        )
    )
    store.save_posts(sorted(posts, key=lambda p: p.date))


# ---------- 답 읽기 (차례 흐름이 부름) ----------


class Reply(BaseModel):
    kind: Literal["ok", "skip", "fix", "frame", "background", "speed", "title", "order", "music"]
    look: int = 0
    text: str = ""
    number: float = 0
    order: list[int] = []


def parse(text: str, looks: int) -> Reply | None:
    """영상 확인 단계에 온 답. 모르는 글이면 None."""
    t = text.strip()
    head, _, rest = t.partition(" ")
    name, rest = head.lower().split("@")[0], rest.strip()
    if name in ("/ok", "ok", "좋아"):
        return Reply(kind="ok")
    if name == "/skip":
        return Reply(kind="skip")
    if name in ("/music", "/음악"):
        return Reply(kind="music")
    if name == "/speed":
        try:
            return Reply(kind="speed", number=float(rest.rstrip("배x")))
        except ValueError:
            return None
    if name == "/제목" and rest:
        return Reply(kind="title", text=rest)
    if name == "/order":
        nums = [int(x) for x in re.findall(r"\d+", rest)]
        return (
            Reply(kind="order", order=nums) if sorted(nums) == list(range(1, looks + 1)) else None
        )
    if name in ("/fix", "/frame") and rest:
        first, _, more = rest.partition(" ")
        if first == "배경":
            return Reply(kind="background", text=more.strip())
        if first.isdigit() and 1 <= int(first) <= looks:
            return Reply(
                kind="fix" if name == "/fix" else "frame", look=int(first), text=more.strip()
            )
        return Reply(kind="fix" if name == "/fix" else "frame", text=rest)
    return None


def replies(updates: list[dict], chat_id: str, since: int) -> list[tuple[int, str]]:
    """since 뒤 등록된 채팅에서 온 글 (보낸 시각, 글)."""
    found = []
    for u in sorted(updates, key=lambda u: u.get("update_id", 0)):
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        at, text = msg.get("date", 0), (msg.get("text") or "").strip()
        if at > since and text:
            found.append((at, text))
    return found


NEW_BG = ("새로", "처음부터")


def bg_fix(rec: VideoRecord, text: str, base: int) -> dict:
    """빈 배경 고치기 요청 → 워크플로 입력. 기본은 지금 배경에서 말한 곳만 고치기."""
    t = text.strip()
    for w in NEW_BG:
        if t.startswith(w):
            rest = t[len(w) :].strip(" ,.")
            return {"step": "background", "note": f"배경:{rest}" if rest else ""}
    return {"step": "background", "note": f"배경:{t}", "base": str(base or -1)}  # -1 = 지금 배경


def action(rec: VideoRecord, reply: Reply, music: bool = True, frames: bool = True) -> dict | None:
    """답 → video 워크플로 입력 (step, looks, note, …). ok·skip 는 흐름이 따로 다룬다.
    그림은 그리기 전(plan)과 그린 뒤(background, frames) 모두 /ok 를 받아야 다음으로 (2026-11-02).
    music: 음악을 쓰는지 (profile video.music 이 none 이면 /music 은 없음).
    frames: 첫 장면 단계가 있는지 (uses_frames). 없으면 배경 /ok 다음은 지시문."""
    note = f"{reply.look}:{reply.text}" if reply.look and reply.text else reply.text
    looks = str(reply.look) if reply.look else ""
    if rec.step == "plan":
        if reply.kind == "ok":
            return {"step": "background"}
        if reply.kind == "background":
            return {"step": "plan", "note": f"배경:{reply.text}"} if reply.text else None
        if reply.kind in ("fix", "frame") and reply.text:
            return {"step": "plan", "note": note}
    elif rec.step == "background":
        if reply.kind == "ok":
            return {"step": "frames" if frames else "prompts"}
        if reply.kind in ("background", "fix") and reply.text:
            # 말한 곳만 고친다: 번호가 있으면 그 번째 배경, 없으면 지금 배경 ("새로 …" 면 새로 그림)
            return bg_fix(rec, reply.text, reply.look)
    elif rec.step == "frames":
        if reply.kind == "ok":
            return {"step": "prompts"}
        if reply.kind == "background" and reply.text:
            return bg_fix(rec, reply.text, 0)
        if reply.kind in ("fix", "frame"):
            return {"step": "frames", "looks": looks, "note": note}
    elif rec.step == "prompts":
        if reply.kind == "ok":
            return {"step": "clips"}
        if reply.kind in ("fix", "frame"):
            return {"step": "prompts", "note": note}
    else:
        if reply.kind == "speed" and 0.5 <= reply.number <= 2:
            return {"step": "edit", "speed": f"{reply.number:g}"}
        if reply.kind == "title":
            return {"step": "edit", "hook": reply.text}
        if reply.kind == "order":
            return {"step": "edit", "order": " ".join(map(str, reply.order))}
        if reply.kind == "music" and music:
            return {"step": "music"}
        if reply.kind == "fix" and reply.look:
            return {"step": "clips", "looks": looks, "note": note}
        if reply.kind == "frame" and reply.look:
            # 새 첫 장면도 보고 허락받은 뒤 영상으로 (2026-11-02). 첫 장면이 없으면 영상만 다시
            return {"step": "frames" if frames else "clips", "looks": looks, "note": note}
    return None


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "step",
        choices=["plan", "background", "frames", "scene_frames", "prompts", "clips", "redo",
                 "edit", "music", "send", "subset"],
    )  # fmt: skip
    ap.add_argument("--account", required=True)
    ap.add_argument(
        "--run", type=Path, required=True, help="결과 폴더 (daily-results 의 <날짜>-run<번호>)"
    )
    ap.add_argument("--face", type=Path, help="얼굴 사진 (frames, 모든 벌 같은 얼굴)")
    ap.add_argument("--model", default="", help="영상 모델 비교 시험 (clips, video.models 키)")
    ap.add_argument("--looks", type=int, nargs="*")
    ap.add_argument(
        "--note", action="append", default=[], help="'3:고칠 점' 또는 '고칠 점' (모든 벌)"
    )
    ap.add_argument("--background", action="store_true", help="빈 배경부터 다시")
    ap.add_argument(
        "--base", type=int, default=0, help="이 번째 빈 배경에서 말한 곳만 고치기 (-1 = 지금 배경)"
    )
    ap.add_argument("--speed", type=float)
    ap.add_argument("--hook")
    ap.add_argument("--order", type=int, nargs="*")
    ap.add_argument("--concept", type=int, default=0, help="고른 컨셉 번호 (차례 흐름이 넘김)")
    ap.add_argument(
        "--pick", type=int, default=0, help="후보 실행의 후보 번호 (subset, picks/p<번호>.yaml)"
    )
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args(argv)
    profile = load_profile(args.account)
    notes = [x.strip() for n in args.note for x in n.splitlines() if x.strip()]
    bg_notes = [n.partition(":")[2] for n in notes if n.startswith("배경:")]
    notes = [n for n in notes if not n.startswith("배경:")]
    try:
        if bg_notes and args.step in ("plan", "background", "frames", "redo"):
            rec = start_record(profile, args.run, args.concept)
            rec.notes.setdefault("background", []).extend(bg_notes)
            save_record(args.run, rec)
        if args.step == "subset":  # 기록·텔레그램 없이 새 폴더 이름만 출력
            print(subset(args.run, args.looks or [], args.pick).name)
            return 0
        if args.step == "send":
            pass
        elif args.step == "plan":
            plan(profile, args.run, notes, args.concept)
        elif args.step == "background":
            background(profile, args.run, notes, args.fake, args.concept, args.base,
                       "; ".join(bg_notes))  # fmt: skip
        elif args.step == "redo" and not uses_frames(profile):  # 첫 장면이 없으면 영상만
            clips(profile, args.run, args.looks, notes, args.fake)
        elif args.step in ("frames", "redo"):
            frames(profile, args.run, args.looks, notes, args.background, args.fake, args.concept,
                   args.face)  # fmt: skip
            if args.step == "redo":  # /frame N: 첫 장면부터 그 벌 영상까지
                clips(profile, args.run, args.looks, None, args.fake)
        elif args.step == "scene_frames":
            scene_frames(profile, args.run, args.looks, notes, args.fake, args.concept, args.face)
        elif args.step == "prompts":
            prompts(profile, args.run, notes)
        elif args.step == "clips":
            if not args.model:
                clips(profile, args.run, args.looks, notes, args.fake, "", args.face)
            for key in args.model.split():  # 비교 시험은 여러 모델을 차례로, 하나가 실패해도 계속
                try:
                    clips(profile, args.run, args.looks, notes, args.fake, key, args.face)
                except Exception as e:  # noqa: BLE001
                    print(f"{key} 실패: {e}", file=sys.stderr)
                    out = vdir(args.run) / f"clips_{key}"
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "error.txt").write_text(f"{type(e).__name__}: {e}"[:1000], "utf-8")
            if args.model:
                args.no_send = True  # 비교 시험은 텔레그램 확인 단계가 아님
        elif args.step == "edit":
            edit(profile, args.run, args.speed, args.hook, args.order)
        elif args.step == "music":
            music(profile, args.run, args.fake)
        rec = load_record(args.run)
        if rec is not None:
            print(
                json.dumps(
                    {"step": rec.step, "cost": rec.total(), "flags": rec.flags}, ensure_ascii=False
                )
            )
        if not args.no_send:
            from tools import notify

            send(profile, args.run, notify.Telegram.from_env())
        return 0
    except Exception as e:  # 어떤 실패든 기록해 두면 차례 흐름이 /retry 를 안내한다
        rec = load_record(args.run)
        if rec is None and (
            (args.run / "manifest.json").exists() or (args.run / "plan.yaml").exists()
        ):
            try:
                rec = VideoRecord(account=profile.id, date=run_date(args.run), run=args.run.name)
            except (KeyError, ValueError, OSError):
                rec = None
        if rec is not None:
            rec.error, rec.error_at = f"{type(e).__name__}: {e}"[:400], int(time.time())
            save_record(args.run, rec)
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
