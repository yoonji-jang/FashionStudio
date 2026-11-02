r"""코디 영상 만들기 (2026-11-02 사용자 "시작", 10/27 퍼 코디 영상으로 정한 방식).

코디 주제 하나당 영상 한 편. 고른 컨셉(data/<계정>/video_concepts.json)과 확정된 코디컷으로
벌마다 첫 장면을 그리고, 그 장면을 영상 AI 로 움직여 이어 붙인다. 영상은 사용자가 직접 올린다.
사람 확인은 텔레그램으로 세 번 (컨셉은 tools.video_concept):
  ② frames   얼굴 자르기(코드) + 빈 배경 + 벌마다 첫 장면(얼굴·코디컷·배경)
             + 얼굴만 고치기 → 사진 묶음
  ③ prompts  벌마다 영상 지시문 (카메라·인물 크기·걷는 속도·표정·동작) → 한국어 요약과 원문
  ④ clips    벌마다 영상(profile video.video_model) + 음악(Lyria) → 편집(edit)
             → 최종본 2개·표지·캡션
     edit    코드 편집만 다시 (속도·제목·순서, 무료)
     music   음악만 새로 ($0.04) → 편집
결과는 daily-results 의 <결과 폴더>/video/ (video.json 에 단계·비용·편집 값).
기준: profile `video`, accounts/<계정>/video.md, 지시문 prompts/video/,
편집 자리 config/layout.yaml video_edit.
OpenRouter 요금이 나가므로 video 워크플로(차례 흐름이 부름)에서만 돈다.

사용법:
  python -m tools.video frames  --account women --run /tmp/results/2026-10-27-run380 \
      --face /tmp/results/flow/women/face.jpg [--looks 3 5] [--note "3:얼굴이 달라"] [--background]
  python -m tools.video prompts --account women --run … [--note "3:가방 고쳐 들기"]
  python -m tools.video clips   --account women --run … [--looks 2] [--note "2:더 빨리"]
  python -m tools.video edit    --account women --run … [--speed 1.3] [--hook "…"]
      [--order 3 1 2 4 5 6]
  python -m tools.video music   --account women --run …
  --fake: API 없이 흐름만 (가짜 그림·영상), --no-send: 텔레그램으로 보내지 않음
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal

import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pydantic import BaseModel

from core.models import Manifest
from core.profile import REPO_ROOT, Profile, load_profile
from core.store import Store
from tools import video_test
from tools.video_concept import Concept
from tools.video_concept import load as load_concepts

PROMPTS = REPO_ROOT / "prompts" / "video"
LAYOUT = REPO_ROOT / "config" / "layout.yaml"
FONTS = REPO_ROOT / "assets" / "fonts"
FACE_PAD = 0.6  # 얼굴 상자 둘레에 더 넣는 비율 (머리·턱선까지, 모음 사진의 옆 컷은 덜 들어가게)
FACE_MIN_PX = 512

Step = Literal["frames", "prompts", "final"]


class VideoRecord(BaseModel):
    """한 게시물 영상의 지금 상태 (<결과 폴더>/video/video.json)."""

    account: str
    date: dt.date
    run: str  # 결과 폴더 이름
    step: Step = "frames"  # 마지막으로 보낸 확인 단계
    sent_at: int = 0  # 그 단계를 텔레그램으로 보낸 시각 (흐름이 이 뒤의 답을 읽는다)
    looks: int = 0
    costs: dict[str, float] = {}  # "frame_3", "clip_3", "background", "music" → 달러
    notes: dict[str, list[str]] = {}  # 벌마다 사용자 고칠 점 ("3" → […], "all" → 모든 벌)
    speed: float = 0  # 0 이면 profile video.speed
    order: list[int] = []  # 비면 LOOK 1, 2, …
    hook: str = ""  # 비면 컨셉 훅 문구
    flags: list[str] = []  # 자동 검사 표시 ("LOOK 3 다가옴")
    error: str = ""  # 마지막 실패 (차례 흐름이 /retry 를 안내)
    error_at: int = 0
    concept: int = 0  # 고른 컨셉 번호 (0 이면 video_concepts.json 의 chosen)

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


def notes_for(rec: VideoRecord, n: int) -> list[str]:
    return [*rec.notes.get("all", []), *rec.notes.get(str(n), [])]


def add_notes(rec: VideoRecord, raw: list[str]) -> list[int]:
    """'3:얼굴이 달라' (벌 번호:고칠 점) 또는 '고칠 점' (모든 벌). 고친 벌 번호를 돌려준다."""
    touched: list[int] = []
    for r in raw:
        head, sep, text = r.partition(":")
        if sep and head.strip().isdigit():
            rec.notes.setdefault(head.strip(), []).append(text.strip())
            touched.append(int(head))
        elif r.strip():
            rec.notes.setdefault("all", []).append(r.strip())
    return touched


def template(name: str, **kw: str) -> str:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    return text.format_map({k: (v or "").strip().rstrip(".") for k, v in kw.items()}).strip()


def common(profile: Profile, c: Concept) -> dict[str, str]:
    v = profile.video
    person = c.person_en or v.person_en
    return {
        "camera": c.camera_en or v.camera_en,
        "camera_short": "a fashion shot like a boyfriend filming his girlfriend",
        "pace": c.pace_en or v.pace_en,
        "person": person,
        "expression": person,
        "direction": c.direction_en or "along the path",
        "light": c.light_en or c.background_en,
        "size": "70 to 80 percent",
    }


# ---------- 이미지·영상 AI ----------


def _gen_client(profile: Profile, fake: bool):
    from tools.generate import FakeClient, OpenRouterClient

    cfg = video_test.load_config()
    gcfg = {**cfg["generate"], "aspect_ratio": "9:16"}
    client = FakeClient() if fake else OpenRouterClient(gcfg, os.environ["OPENROUTER_API_KEY"])
    return client, gcfg["models"]


def face_crop(face: Path) -> bytes:
    """얼굴 사진(여러 컷 모음일 수 있음)에서 가장 크게 나온 얼굴 하나를 넉넉히 자른다. AI 없음."""
    from tools.faces import find_faces

    im = Image.open(face).convert("RGB")
    boxes = find_faces(im)
    if boxes:
        x0, y0, x1, y1 = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        cx, cy, side = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0) * (1 + 2 * FACE_PAD)
        box = (
            max(0, round(cx - side / 2)),
            max(0, round(cy - side / 2)),
            min(im.width, round(cx + side / 2)),
            min(im.height, round(cy + side / 2)),
        )
        im = im.crop(box)
    if max(im.size) < FACE_MIN_PX:
        s = FACE_MIN_PX / max(im.size)
        im = im.resize((round(im.width * s), round(im.height * s)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def frames(
    profile: Profile,
    run: Path,
    face: Path,
    looks: list[int] | None = None,
    notes: list[str] | None = None,
    redo_background: bool = False,
    fake: bool = False,
    concept: int = 0,
) -> VideoRecord:
    """② 얼굴 자르기 + 빈 배경 + 벌마다 첫 장면 (+ 얼굴만 고치기)."""
    m = manifest_of(run)
    rec = load_record(run) or VideoRecord(account=profile.id, date=m.date, run=run.name)
    rec.concept = concept or rec.concept
    c = concept_of(profile, m.date, rec.concept)
    rec.looks = look_count(run)
    touched = add_notes(rec, notes or [])
    todo = looks or touched or list(range(1, rec.looks + 1))
    d = vdir(run)
    (d / "frames").mkdir(parents=True, exist_ok=True)
    (d / "face.jpg").write_bytes(face_crop(face))
    face_b = (d / "face.jpg").read_bytes()
    client, models = _gen_client(profile, fake)
    kw = common(profile, c)
    v = profile.video
    bg_path = d / "background.jpg"
    if redo_background or not bg_path.exists():
        direction = f"The walking path runs {c.direction_en}." if c.direction_en else ""
        prompt = template(
            "background", background_en=c.background_en, light=c.light, direction_line=direction
        )
        for note in rec.notes.get("background", []):
            prompt += f"\nUser request: {note}"
        data, cost = client.generate(models[v.background_model], prompt, [])
        bg_path.write_bytes(video_test.jpeg(data, 1920))
        rec.costs["background"] = round(cost or 0, 4)
        todo = list(range(1, rec.looks + 1))  # 배경이 바뀌면 첫 장면도 모두 새로
    bg = bg_path.read_bytes()
    shots = {s.look: s for s in c.shots}
    for n in todo:
        shot = shots.get(n)
        pose = (shot.motion_en or f"{shot.action}, {shot.pose}") if shot else "walking mid-stride"
        prompt = template("first_frame", **kw, items=items_en(run, n), pose=pose)
        for note in notes_for(rec, n):
            prompt += f"\nUser request (most important): {note}"
        outfit = video_test.jpeg(look_image(run, n).read_bytes())
        refs = [video_test.jpeg(face_b), outfit, video_test.jpeg(bg)]
        data, cost = client.generate(models[v.frame_model], prompt, refs)
        total = cost or 0
        if v.face_edit:
            edit = template("face_edit", expression=kw["expression"])
            refs = [video_test.jpeg(data, 1920), video_test.jpeg(face_b)]
            data, cost = client.generate(models[v.frame_model], edit, refs)
            total += cost or 0
        (d / "frames" / f"look{n}.jpg").write_bytes(video_test.jpeg(data, 1920))
        rec.costs[f"frame_{n}"] = round(total, 4)
    rec.step, rec.error = "frames", ""
    save_record(run, rec)
    return rec


def motion_prompt(profile: Profile, run: Path, c: Concept, rec: VideoRecord, n: int) -> str:
    shot = next((s for s in c.shots if s.look == n), None)
    motion = (shot.motion_en or f"{shot.action}, {shot.pose}") if shot else "She walks naturally"
    text = template("motion", **common(profile, c), items=items_en(run, n), motion=motion)
    for note in notes_for(rec, n):
        text += f"\nUser request (most important): {note}"
    return text


def prompts(profile: Profile, run: Path, notes: list[str] | None = None) -> VideoRecord:
    """③ 벌마다 영상 지시문 (AI 요금 없음)."""
    rec = load_record(run)
    if rec is None:
        raise ValueError("첫 장면(frames)을 먼저 만들어 주세요")
    c = concept_of(profile, rec.date, rec.concept)
    add_notes(rec, notes or [])
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
) -> VideoRecord:
    """④ 벌마다 첫 장면을 영상으로 (없는 벌, 또는 고칠 벌만) + 음악이 없으면 음악."""
    rec = load_record(run)
    if rec is None:
        raise ValueError("첫 장면(frames)을 먼저 만들어 주세요")
    c = concept_of(profile, rec.date, rec.concept)
    touched = add_notes(rec, notes or [])
    d = vdir(run)
    (d / "clips").mkdir(exist_ok=True)
    cfg = video_test.load_config()
    vcfg = cfg["video"]
    model = vcfg["models"][profile.video.video_model]
    redo = set(looks or touched)
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
        first = (d / "frames" / f"look{n}.jpg").read_bytes()
        body = video_test.payload(model, {"aspect_ratio": "9:16"}, texts[str(n)], [], first)
        video, cost, _ = client.make(body)
        clip.write_bytes(video if not fake else _fake_clip(first))
        rec.costs[f"clip_{n}"] = round(cost or 0, 4)
    (d / "prompts.json").write_text(json.dumps(texts, ensure_ascii=False, indent=2), "utf-8")
    if profile.video.music == "lyria" and not (d / "music.mp3").exists():
        make_music(profile, run, rec, c, fake)
    save_record(run, rec)
    return edit(profile, run)


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
    font = _font(profile.visual.logo_font, lay["look_size"])
    label = f"LOOK {n}"
    right, top = lay["look_margin"]
    draw.text((w - right - draw.textlength(label, font=font), top), label, font=font, fill=color)
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 110), mask=im.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(4))
    Image.alpha_composite(shadow, im).save(path)


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
    seconds = {s.look: s.seconds for s in c.shots}
    rec.flags = []
    total = 0.0
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        segs = []
        for i, n in enumerate(seq):
            clip = d / "clips" / f"look{n}.mp4"
            start = profile.video.clip_start
            src = min(seconds.get(n, profile.video.look_seconds) * sp, _duration(clip) - start)
            png = tmp / f"o{n}.png"
            overlay_png(profile, n, hook_text if i == 0 else "", png)
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
    m = manifest_of(run)
    (final / "caption.txt").write_text(m.caption + "\n", encoding="utf-8")
    rec.step, rec.error = "final", ""
    save_record(run, rec)
    return rec


# ---------- 텔레그램 ----------


def frames_guide(tag: str) -> str:
    return "\n".join(
        [
            f"{tag} 영상 첫 장면이에요 (첫 장은 빈 배경, 다음부터 LOOK 1~).\n"
            "영상은 이 장면에서 시작해요.",
            "• /ok → 영상 지시문을 보내 드려요 (요금 없음)",
            "• /fix 3 얼굴이 달라 → LOOK 3 첫 장면만 다시 그려요 (약 $0.28)",
            "• /fix 배경 눈이 더 많이 → 빈 배경과 첫 장면을 모두 다시 그려요 (약 $1.8)",
            "• /fix 표정을 더 도도하게 → 모든 벌 첫 장면을 다시 그려요",
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
        f"• 표정·머리: {' · '.join(x for x in (c.expression, c.hair) if x) or '살짝 도도하게'}",
        f"• 빛: {c.light}",
        "• 금지: 다가가기·줌·흔들림, 다른 사람, 글자, 로고",
        "",
        "벌마다",
    ]
    for s in c.shots:
        extra = "; ".join(notes_for(rec, s.look))
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


def final_guide(tag: str, rec: VideoRecord, profile: Profile) -> str:
    sp = rec.speed or profile.video.speed
    order = " ".join(map(str, rec.order or range(1, rec.looks + 1)))
    lines = [
        f"{tag} 영상 최종본이에요 (음악 있음·없음 원본 파일, 표지, 캡션).\n"
        "게시는 직접 올려 주세요.",
        f"지금: 속도 {sp:g}배 · 순서 {order} · 비용 ${rec.total():.2f}",
    ]
    lines += [f"⚠ {f}" for f in rec.flags]
    lines += [
        "• /speed 1.3 → 속도만 바꿔 다시 편집 (무료)",
        "• /제목 새 문구 → 첫 화면 문구 바꾸기 (무료)",
        "• /order 3 1 2 4 5 6 → 순서 바꾸기 (무료)",
        "• /fix 3 더 빨리 걷게 → LOOK 3 영상만 다시 (약 $0.65)",
        "• /frame 3 얼굴이 달라 → LOOK 3 첫 장면부터 다시 (약 $0.93)",
        "• /music → 음악만 새로 (약 $0.04)",
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
    if rec.step == "frames":
        pics = [
            d / "background.jpg",
            *[d / "frames" / f"look{n}.jpg" for n in range(1, rec.looks + 1)],
        ]
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
        main_video = (
            f / "video_music.mp4" if (f / "video_music.mp4").exists() else f / "video_silent.mp4"
        )
        tg.send_video(main_video, f"{tag} 영상 미리보기")
        for name, label in (("video_music.mp4", "음악 있음"), ("video_silent.mp4", "음악 없음")):
            if (f / name).exists():
                tg.send_document(f / name, f"{tag} 영상 원본 ({label})")
        tg.send_photos([f / "cover.jpg"], f"{tag} 표지 프레임")
        tg.send((f / "caption.txt").read_text(encoding="utf-8"))
        tg.send(final_guide(tag, rec, profile))
    rec.sent_at = int(time.time())
    save_record(run, rec)
    return rec


def guide(tag: str, rec: VideoRecord, profile: Profile) -> str:
    """지금 단계에서 쓸 수 있는 명령 (답을 못 알아들었을 때 다시 보낸다)."""
    if rec.step == "frames":
        return frames_guide(tag)
    if rec.step == "prompts":
        return prompts_text(tag, profile, concept_of(profile, rec.date, rec.concept), rec)
    return final_guide(tag, rec, profile)


def action_text(act: dict) -> str:
    """요청을 사용자에게 알리는 한 줄."""
    step = act.get("step")
    looks = f"LOOK {act['looks']} " if act.get("looks") else ""
    return {
        "frames": f"{'빈 배경과 ' if act.get('background') else ''}{looks or '모든 벌 '}첫 장면을 "
        "다시 그릴게요",
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


def action(rec: VideoRecord, reply: Reply) -> dict | None:
    """답 → video 워크플로 입력 (step, looks, note, …). ok·skip 는 흐름이 따로 다룬다."""
    note = f"{reply.look}:{reply.text}" if reply.look and reply.text else reply.text
    looks = str(reply.look) if reply.look else ""
    if rec.step == "frames":
        if reply.kind == "ok":
            return {"step": "prompts"}
        if reply.kind == "background":
            return {"step": "frames", "background": True, "note": f"배경:{reply.text}"}
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
        if reply.kind == "music":
            return {"step": "music"}
        if reply.kind == "fix" and reply.look:
            return {"step": "clips", "looks": looks, "note": note}
        if reply.kind == "frame" and reply.look:
            return {"step": "redo", "looks": looks, "note": note}
    return None


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("step", choices=["frames", "prompts", "clips", "redo", "edit", "music", "send"])
    ap.add_argument("--account", required=True)
    ap.add_argument(
        "--run", type=Path, required=True, help="결과 폴더 (daily-results 의 <날짜>-run<번호>)"
    )
    ap.add_argument("--face", type=Path, help="얼굴 사진 (frames)")
    ap.add_argument("--looks", type=int, nargs="*")
    ap.add_argument(
        "--note", action="append", default=[], help="'3:고칠 점' 또는 '고칠 점' (모든 벌)"
    )
    ap.add_argument("--background", action="store_true", help="빈 배경부터 다시")
    ap.add_argument("--speed", type=float)
    ap.add_argument("--hook")
    ap.add_argument("--order", type=int, nargs="*")
    ap.add_argument("--concept", type=int, default=0, help="고른 컨셉 번호 (차례 흐름이 넘김)")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args(argv)
    profile = load_profile(args.account)
    notes = [x.strip() for n in args.note for x in n.splitlines() if x.strip()]
    bg_notes = [n.partition(":")[2] for n in notes if n.startswith("배경:")]
    notes = [n for n in notes if not n.startswith("배경:")]
    try:
        if args.step == "send":
            pass
        elif args.step in ("frames", "redo"):
            if args.face is None:
                raise ValueError("--face 가 필요해요")
            rec = load_record(args.run)
            if rec is not None and bg_notes:
                rec.notes.setdefault("background", []).extend(bg_notes)
                save_record(args.run, rec)
            frames(profile, args.run, args.face, args.looks, notes, args.background, args.fake,
                   args.concept)  # fmt: skip
            if args.step == "redo":  # /frame N: 첫 장면부터 그 벌 영상까지
                clips(profile, args.run, args.looks, None, args.fake)
        elif args.step == "prompts":
            prompts(profile, args.run, notes)
        elif args.step == "clips":
            clips(profile, args.run, args.looks, notes, args.fake)
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
        if rec is None and (args.run / "manifest.json").exists():
            m = manifest_of(args.run)
            rec = VideoRecord(account=profile.id, date=m.date, run=args.run.name)
        if rec is not None:
            rec.error, rec.error_at = f"{type(e).__name__}: {e}"[:400], int(time.time())
            save_record(args.run, rec)
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
