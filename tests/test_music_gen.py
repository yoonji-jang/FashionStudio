from datetime import date

import numpy as np

from core.profile import load_profile
from tools import music_gen


def fake_render(audio, path):
    assert audio.ndim == 2 and audio.shape[1] == 2
    path.write_bytes(b"m4a")


def women(tmp_path, days=14, count=3, keep=4):
    p = load_profile("women")
    p.reels.music_dir = "music"
    p.reels.music_refresh_days, p.reels.music_refresh_count, p.reels.music_keep = days, count, keep
    (tmp_path / "music").mkdir()
    (tmp_path / "music" / "mine.m4a").write_bytes(b"x")  # 사람이 넣은 곡
    readme = tmp_path / "music" / "README.md"
    readme.write_text("# 릴스 배경 음악\n\n| 파일 |\n", encoding="utf-8")
    return p


def test_compose_every_style_is_stereo_and_audible():
    for style in music_gen.STYLES:
        audio = music_gen.compose(style, 7)
        assert audio.shape[1] == 2 and len(audio) / music_gen.SR >= 12, style
        assert 0.01 < np.sqrt(np.mean(audio**2)) and np.max(np.abs(audio)) <= 0.9, style


def test_seed_changes_key_and_tempo():
    a, b = music_gen.compose("piano_ballad", 1), music_gen.compose("piano_ballad", 2)
    assert len(a) != len(b) or not np.allclose(a, b)


def test_refresh_every_days_keeps_recent_and_mine(tmp_path):
    p = women(tmp_path)
    run = lambda d, **kw: music_gen.refresh(p, d, root=tmp_path, render=fake_render, **kw)  # noqa: E731
    first = run(date(2026, 10, 12))
    assert [f.name.split("_")[1] for f in first] == ["20261012"] * 3
    assert run(date(2026, 10, 19)) == []  # 2주가 안 됨
    second = run(date(2026, 10, 26))
    assert len(second) == 3
    # 바로 전 묶음과 같은 분위기는 피한다
    old = {music_gen.GEN_RE.match(f.name)[2] for f in first}
    assert not old & {music_gen.GEN_RE.match(f.name)[2] for f in second}
    left = music_gen.generated(tmp_path / "music")
    assert len(left) == 4 and all(d == date(2026, 10, 26) for d, _, _ in left[1:])
    assert (tmp_path / "music" / "mine.m4a").exists()
    readme = (tmp_path / "music" / "README.md").read_text(encoding="utf-8")
    assert readme.count(music_gen.README_START) == 1 and readme.count("gen_") == 4
    assert run(date(2026, 10, 27), force=True)  # force 는 바로


def test_refresh_off_without_days(tmp_path):
    p = women(tmp_path, days=None)
    assert music_gen.refresh(p, date(2026, 10, 12), root=tmp_path, render=fake_render) == []
