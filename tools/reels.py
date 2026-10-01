r"""하루 게시물로 릴스 영상 만들기 (19단계). Claude·영상 AI 는 부르지 않는다.

그날 캐러셀 슬라이드(manifest.images)를 세로 영상(1080×1920, 9:16)으로 이어 붙인다.
- 순서·장마다 시간·넘기는 효과·확대 정도는 profile.yaml `reels` 에서 읽는다.
- 슬라이드(4:5)는 위쪽에 두고(reels.top) 남는 곳은 상품 장 배경색(feed.background)으로 채운다.
  아래쪽은 인스타 캡션·버튼이 덮는 자리라 비워 둔다.
- 표지는 천천히 커지고(reels.cover_zoom), 장 사이는 캐러셀을 넘기듯 옆으로 밀린다(transition).
- 배경 음악: reels.music_dir 의 음원 하나를 날짜마다 돌려 쓴다 (상업 이용이 허락된 파일만).
  폴더가 비어 있으면 소리 없는 오디오 트랙을 넣는다.
- 결과: <결과 폴더>/reels/reel.mp4 (H.264 + AAC, 30fps), reels/cover.jpg (첫 화면),
  manifest.json 의 reels 를 채운다. 게시는 tools.publish 가 한다 (schedule.reels_at).

ffmpeg 가 필요하다 (GitHub Actions ubuntu 에는 기본으로 있음).

사용법:
  python -m tools.reels build out/2026-10-01 [--no-music] [--seconds 2]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image

from core.models import Manifest, ReelsSpec
from core.profile import REPO_ROOT, Profile, Reels, load_profile
from tools import ai_label, notify

SIZE = (1080, 1920)
MUSIC_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg"}
VIDEO = "reels/reel.mp4"
COVER = "reels/cover.jpg"


@dataclass
class Clip:
    path: Path
    kind: str  # cover, products, summary, comment
    seconds: float  # 다음 장으로 넘어가는 시간 포함
    zoom: float


def timeline(slides: list[Path], cfg: Reels) -> list[Clip]:
    """릴스에 넣을 장과 시간. 슬라이드 종류는 파일 이름으로 안다 (01_cover, 02_아우터 …)."""
    clips = []
    for kind in cfg.slides:
        for p in slides:
            if ai_label.slide_kind(p) != kind:
                continue
            zoom = cfg.cover_zoom if kind == "cover" else cfg.zoom
            clips.append(Clip(p, kind, cfg.seconds.get(kind, 1.6), zoom))
    if not clips:
        raise ValueError("릴스에 넣을 슬라이드가 없습니다 (profile reels.slides 확인)")
    return clips


def starts(clips: list[Clip], overlap: float) -> list[float]:
    """장마다 시작 시각. 다음 장은 앞 장 끝나기 overlap 초 전에 들어오기 시작한다."""
    out, t = [], 0.0
    for c in clips:
        out.append(t)
        t += c.seconds - overlap
    return out


def total_seconds(clips: list[Clip], overlap: float) -> float:
    return sum(c.seconds for c in clips) - overlap * (len(clips) - 1)


def ease(p: float) -> float:
    """천천히 시작해 천천히 멈춤 (cubic in-out)."""
    p = min(max(p, 0.0), 1.0)
    return 4 * p**3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def pick_music(cfg: Reels, day: date, root: Path = REPO_ROOT) -> Path | None:
    """음원 폴더에서 날짜마다 한 곡 (같은 날은 같은 곡). 없으면 None."""
    if not cfg.music_dir:
        return None
    folder = root / cfg.music_dir
    if not folder.is_dir():
        return None
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in MUSIC_EXT)
    return files[day.toordinal() % len(files)] if files else None


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:  # 선택: pip 패키지에 든 ffmpeg
        import imageio_ffmpeg  # type: ignore[import-not-found]

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as e:
        raise RuntimeError("ffmpeg 가 없습니다 (apt install ffmpeg, brew install ffmpeg)") from e


class Frames:
    """시각 t 의 화면을 그린다."""

    def __init__(self, clips: list[Clip], cfg: Reels, bg: str, size: tuple[int, int] = SIZE):
        self.clips, self.cfg, self.size = clips, cfg, size
        w, h = size
        self.overlap = cfg.transition_sec if cfg.transition != "cut" else 0.0
        self.starts = starts(clips, self.overlap)
        self.base = Image.new("RGB", size, bg)
        self.cards: list[Image.Image] = []
        for c in clips:
            with Image.open(c.path) as im:
                im = im.convert("RGB")
                ch = round(w * im.height / im.width)
                self.cards.append(im.resize((w, ch), Image.Resampling.LANCZOS))
        tallest = max(c.height for c in self.cards)
        # 슬라이드가 아래로 넘치지 않게 (1:1 피드 등)
        self.top = min(round(cfg.top * h), max(0, h - tallest))

    def card(self, i: int, u: float) -> Image.Image:
        """i 번째 장을 그 장 시간 u 초에. 시간이 지나면서 가운데 기준으로 조금씩 커진다."""
        img, clip = self.cards[i], self.clips[i]
        if clip.zoom <= 0:
            return img
        z = 1 + clip.zoom * min(max(u / clip.seconds, 0.0), 1.0)
        w, h = img.size
        cw, ch = w / z, h / z
        box = ((w - cw) / 2, (h - ch) / 2, (w + cw) / 2, (h + ch) / 2)
        return img.resize((w, h), Image.Resampling.BILINEAR, box=box)

    def at(self, t: float) -> Image.Image:
        n = len(self.clips)
        i = max(k for k in range(n) if self.starts[k] <= t + 1e-9)
        frame = self.base.copy()
        cur = self.card(i, t - self.starts[i])
        # 앞 장이 아직 끝나지 않았으면 넘기는 중
        if i > 0 and self.overlap > 0 and t < self.starts[i] + self.overlap:
            p = ease((t - self.starts[i]) / self.overlap)
            prev = self.card(i - 1, t - self.starts[i - 1])
            if self.cfg.transition == "fade":
                a, b = frame.copy(), frame.copy()
                a.paste(prev, (0, self.top))
                b.paste(cur, (0, self.top))
                return Image.blend(a, b, p)
            w = self.size[0]
            frame.paste(prev, (round(-p * w), self.top))
            frame.paste(cur, (round((1 - p) * w), self.top))
            return frame
        frame.paste(cur, (0, self.top))
        return frame


def render(
    clips: list[Clip],
    dest: Path,
    cfg: Reels,
    bg: str,
    music: Path | None = None,
    size: tuple[int, int] = SIZE,
) -> float:
    """영상을 dest 에 쓰고 길이(초)를 돌려준다."""
    frames = Frames(clips, cfg, bg, size)
    fps = cfg.fps
    duration = total_seconds(clips, frames.overlap)
    count = round(duration * fps)
    dest.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if music is not None:
        fade_out = max(0.0, duration - 1.2)
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        audio = f"volume={cfg.music_volume},afade=t=in:d=0.4,afade=t=out:st={fade_out:.2f}:d=1.2"
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = "anull"
    # 인스타 릴스 권장: H.264 High, yuv420p, AAC 48kHz, faststart
    cmd += ["-filter:a", audio, "-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high"]
    cmd += ["-pix_fmt", "yuv420p", "-maxrate", "8M", "-bufsize", "16M", "-g", str(fps * 2)]
    cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    cmd += ["-t", f"{count / fps:.3f}", "-movflags", "+faststart", str(dest)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for k in range(count):
            proc.stdin.write(frames.at(k / fps).tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg 가 영상을 만들지 못했습니다: {err.strip()[-400:]}")
    return count / fps


def build(
    out: Path,
    profile: Profile | None = None,
    music: bool = True,
    size: tuple[int, int] = SIZE,
    root: Path = REPO_ROOT,
) -> ReelsSpec:
    """결과 폴더의 슬라이드로 릴스를 만들고 manifest.reels 를 채운다."""
    manifest = notify.load_manifest(out)
    profile = profile or load_profile(manifest.account)
    cfg = profile.reels
    clips = timeline(notify.slide_paths(manifest, out), cfg)
    bg = cfg.background or profile.feed.background
    song = pick_music(cfg, manifest.date, root) if music else None
    seconds = render(clips, out / VIDEO, cfg, bg, song, size)
    Frames(clips, cfg, bg, size).at(0).save(out / COVER, "JPEG", quality=90)
    spec = ReelsSpec(
        video=VIDEO, cover=COVER, audio=song.name if song else None, duration_sec=round(seconds, 2)
    )
    manifest.reels = spec
    write_manifest(out, manifest)
    return spec


def write_manifest(out: Path, manifest: Manifest) -> None:
    (out / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def video_path(manifest: Manifest, out: Path) -> Path | None:
    """manifest 에 적힌 릴스 영상 (파일이 있을 때만)."""
    if manifest.reels is None or not manifest.reels.video:
        return None
    path = out / manifest.reels.video
    return path if path.exists() else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="결과 폴더의 슬라이드로 릴스 만들기")
    b.add_argument("out", type=Path, help="결과 폴더 (out/<날짜>)")
    b.add_argument("--no-music", action="store_true", help="음원 없이 (소리 없는 트랙)")
    b.add_argument("--force", action="store_true", help="프로필 reels.enabled 가 꺼져 있어도 만듦")
    args = ap.parse_args(argv)
    try:
        manifest = notify.load_manifest(args.out)
        profile = load_profile(manifest.account)
        if not profile.reels.enabled and not args.force:
            print(f"[{profile.id}] 릴스가 꺼져 있습니다 (profile reels.enabled)")
            return 0
        spec = build(args.out, profile, music=not args.no_music)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    sound = spec.audio or "소리 없음"
    print(f"릴스: {args.out / VIDEO} ({spec.duration_sec:g}초, 음원 {sound})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
