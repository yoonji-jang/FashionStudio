"""릴스 배경 음악을 코드로 작곡·합성한다 (19단계).
외부 음원·샘플을 쓰지 않아 상업 이용에 문제가 없다.

profile `reels.music_refresh_days` 마다 새 곡 `music_refresh_count` 개를 `reels.music_dir` 에
gen_<날짜>_<분위기>.m4a 로 넣고, 자동으로 만든 곡은 최근 `music_keep` 개만 남긴다.
사람이 넣은 곡(gen_ 로 시작하지 않는 파일)은 건드리지 않는다. music 워크플로가 매주 부른다.
곡은 분위기(STYLES)마다 화음·악기·리듬 틀이 있고,
날짜로 정한 난수로 조·빠르기·화음 순서·멜로디를 바꾼다.

사용법:
  python -m tools.music_gen women            # 때가 됐으면 새 곡을 만든다
  python -m tools.music_gen women --force    # 바로 만든다
  python -m tools.music_gen women --style bossa_cafe --out /tmp/a.wav   # 한 곡만 시험
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import wave
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from core.profile import REPO_ROOT, Profile, load_index, load_profile

SR = 44100
GEN_RE = re.compile(r"^gen_(\d{8})_([a-z_]+)\.m4a$")
README_START = "<!-- music_gen 자동 목록 -->"
README_END = "<!-- /music_gen -->"


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def env(n: int, a=0.01, d=0.3, s=0.6, r=0.2) -> np.ndarray:
    """어택 a, 감쇠 d, 지속 s, 끝 r 초."""
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(a, 1e-4)) * (s + (1 - s) * np.exp(-t / d))
    rel = min(n, int(r * SR))
    if rel:
        e[-rel:] *= np.linspace(1, 0, rel)
    return e


def _t(dur: float) -> tuple[int, np.ndarray]:
    n = max(1, int(dur * SR))
    return n, np.arange(n) / SR


# --- 악기 ---
def rhodes(f, dur, v=0.2):
    n, t = _t(dur)
    mod = 1.2 * np.exp(-t * 4) * np.sin(2 * np.pi * f * t)
    x = np.sin(2 * np.pi * f * t + mod) + 0.15 * np.sin(2 * np.pi * 4 * f * t) * np.exp(-t * 8)
    return v * x * env(n, 0.004, 0.9, 0.2, 0.3)


def piano(f, dur, v=0.22):
    n, t = _t(dur)
    parts = ((1, 1), (2, 0.5), (3, 0.25), (4, 0.12), (5, 0.06))
    x = sum(
        a * np.sin(2 * np.pi * k * f * (1 + 0.0004 * k * k) * t) * np.exp(-t * (1.2 + k * 0.9))
        for k, a in parts
    )
    return v * x * env(n, 0.002, 2.0, 0.0, 0.25)


def epiano(f, dur, v=0.2):
    n, t = _t(dur)
    x = (
        np.sin(2 * np.pi * f * t)
        + 0.35 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 3)
        + 0.12 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t * 6)
    )
    return v * x * env(n, 0.005, 0.8, 0.25, 0.4)


def guitar(f, dur, v=0.3, rng=None):
    """Karplus-Strong 뜯는 줄 (나일론 기타 느낌)."""
    n, p = int(dur * SR), max(2, int(SR / f))
    buf = (rng or np.random.default_rng(1)).uniform(-1, 1, p)
    out = np.empty(n)
    for i in range(n):
        j = i % p
        out[i] = buf[j]
        buf[j] = 0.498 * (buf[j] + buf[(i + 1) % p])
    return v * out * env(n, 0.002, 1.5, 0.3, 0.1)


def marimba(f, dur, v=0.3):
    n, t = _t(dur)
    x = (
        np.sin(2 * np.pi * f * t) * np.exp(-t * 5)
        + 0.4 * np.sin(2 * np.pi * 4 * f * t) * np.exp(-t * 20)
        + 0.1 * np.sin(2 * np.pi * 10 * f * t) * np.exp(-t * 40)
    )
    return v * x * env(n, 0.001, 9, 1, 0.05)


def bell(f, dur, v=0.12):
    n, t = _t(dur)
    x = np.sin(2 * np.pi * f * t + 2 * np.exp(-t * 2) * np.sin(2 * np.pi * 3.5 * f * t))
    return v * x * env(n, 0.002, 0.7, 0.1, 0.3)


def pad(f, dur, v=0.05):
    n, t = _t(dur)
    x = sum(
        np.sin(2 * np.pi * k * f * (1 + d) * t) / k for k in range(1, 7) for d in (-0.004, 0.004)
    )
    return v * x / 2 * env(n, 0.5, 9, 1, 0.6)


def synth_pluck(f, dur, v=0.15):
    n, t = _t(dur)
    x = sum(np.sin(2 * np.pi * k * f * t) * np.exp(-t * (6 + 3 * k)) / k for k in range(1, 8))
    return v * x * env(n, 0.002, 9, 1, 0.05)


def bass(f, dur, v=0.32, slap=False):
    n, t = _t(dur)
    x = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 2 * f * t)
    if slap:
        x += 0.5 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t * 25)
    return v * np.tanh(1.3 * x) * env(n, 0.005, 0.5, 0.5, 0.06)


def sub808(f, dur, v=0.45):
    n, t = _t(dur)
    ff = f * (1 + 1.5 * np.exp(-t * 40))
    return v * np.sin(2 * np.pi * np.cumsum(ff) / SR) * env(n, 0.002, 0.8, 0.4, 0.1)


# --- 타악 ---
class Drums:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng

    def noise(self, n):
        return self.rng.standard_normal(n)

    def kick(self, v=0.6, punch=70):
        n, t = _t(0.4)
        f = 48 + punch * np.exp(-t * 30)
        return v * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 8)

    def snare(self, v=0.18):
        n, t = _t(0.22)
        tone = 0.6 * np.sin(2 * np.pi * 185 * t) * np.exp(-t * 25)
        return v * (self.noise(n) * np.exp(-t * 18) + tone)

    def clap(self, v=0.15):
        n, t = _t(0.25)
        e = sum(np.exp(-np.maximum(0, t - d) * 60) * (t >= d) for d in (0, 0.01, 0.02))
        return v * self.noise(n) * e * np.exp(-t * 8)

    def rim(self, v=0.12):
        n, t = _t(0.05)
        return v * (np.sin(2 * np.pi * 1700 * t) + 0.5 * self.noise(n)) * np.exp(-t * 90)

    def hat(self, v=0.05, open_=False):
        n, t = _t(0.25 if open_ else 0.05)
        return v * np.diff(self.noise(n), prepend=0) * np.exp(-t * (12 if open_ else 70))

    def shaker(self, v=0.035):
        n, t = _t(0.09)
        return v * np.diff(self.noise(n), prepend=0) * np.sin(np.pi * np.minimum(1, t / 0.09))


def lowpass(x: np.ndarray, cut: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * cut / SR)
    y = np.empty_like(x)
    p = 0.0
    for i in range(len(x)):
        p = (1 - a) * x[i] + a * p
        y[i] = p
    return y


class Mix:
    def __init__(self, sec: float):
        self.sec = sec
        self.L = np.zeros(int((sec + 2) * SR))
        self.R = np.zeros_like(self.L)

    def add(self, x, at, pan=0.0):
        i = int(at * SR)
        j = min(len(self.L), i + len(x))
        if i >= j:
            return
        self.L[i:j] += x[: j - i] * (1 - pan) * 0.9
        self.R[i:j] += x[: j - i] * (1 + pan) * 0.9

    def done(self, cut=7000, vinyl=0.0, rng=None) -> np.ndarray:
        """(샘플, 2) 배열, 최대 -1dB 로 맞춤."""
        m = np.stack([self.L, self.R], 1)
        if vinyl and rng is not None:
            m += vinyl * np.repeat(lowpass(rng.standard_normal(len(m)), 3000)[:, None], 2, 1)
            m[rng.integers(0, len(m), int(len(m) / SR * 3))] += vinyl * 20
        for c in range(2):
            m[:, c] = lowpass(m[:, c], cut)
        m = m[: int(self.sec * SR)]
        fade = int(0.05 * SR)
        m[:fade] *= np.linspace(0, 1, fade)[:, None]
        return m / (np.max(np.abs(m)) * 1.12)


def melody(chords, beats_per_chord, rng, low=67, high=81, density=0.6):
    """화음음 위주로 걷는 멜로디 [(박, 음, 길이)]."""
    out, cur = [], None
    for ci, ch in enumerate(chords):
        tones = sorted(
            {p for c in ch for p in range(c % 12, high + 1, 12) if low <= p <= high}
        ) or [low]
        b = 0.0
        while b < beats_per_chord:
            pool = [0.5, 1, 1, 1.5, 2] if rng.random() < density else [1, 2]
            step = min(float(rng.choice(pool)), beats_per_chord - b)
            if rng.random() < 0.8:
                cur = tones[len(tones) // 2] if cur is None else cur
                near = [p for p in tones if abs(p - cur) <= 5] or tones
                cur = int(rng.choice(near))
                out.append((ci * beats_per_chord + b, cur, step))
            b += step
    return out


@dataclass
class Plan:
    """한 곡의 바뀌는 값: 화음(조 옮김·순서 돌림 적용), 빠르기, 난수."""

    chords: list[list[int]]
    bpm: float
    rng: np.random.Generator

    @property
    def beat(self) -> float:
        return 60 / self.bpm


def plan(base: list[list[int]], bpm: float, rng: np.random.Generator) -> Plan:
    shift = int(rng.integers(-3, 4))
    turn = int(rng.integers(0, len(base))) if rng.random() < 0.5 else 0
    chords = [[m + shift for m in c] for c in base[turn:] + base[:turn]]
    return Plan(chords, bpm * float(rng.uniform(0.94, 1.06)), rng)


# --- 분위기 (곡 틀) ---
def autumn_lofi(p: Plan) -> np.ndarray:
    """가을 로파이: 스윙 드럼, 일렉피아노, 바이닐 잡음."""
    ch, beat, dr = p.chords, p.beat, Drums(p.rng)
    mx = Mix(len(ch) * 2 * 4 * beat)
    for rep in range(2):
        for ci, c in enumerate(ch):
            b0 = (rep * len(ch) + ci) * 4 * beat
            for k, m in enumerate(c):
                mx.add(epiano(hz(m), 4 * beat * 0.95, 0.1), b0 + k * 0.02, -0.15)
                mx.add(pad(hz(m + 12), 4 * beat, 0.015), b0, 0.2)
            for at in (0, 1.5, 2.5):
                mx.add(bass(hz(c[0] - 12), beat * 0.9, 0.3), b0 + at * beat)
            mx.add(dr.kick(0.5), b0)
            mx.add(dr.kick(0.4), b0 + 2.5 * beat)
            for at in (1, 3):
                mx.add(dr.snare(0.12), b0 + at * beat)
            for e in range(8):
                mx.add(dr.hat(0.05), b0 + (e * 0.5 + (0.09 if e % 2 else 0)) * beat, 0.3)
    for b, m, d in melody(ch * 2, 4, p.rng, 69, 81, 0.5):
        mx.add(epiano(hz(m), d * beat, 0.14), b * beat, 0.1)
    return mx.done(5200, vinyl=0.003, rng=p.rng)


def bright_walk(p: Plan) -> np.ndarray:
    """산뜻한 미디엄 템포: 일렉피아노, 가벼운 드럼."""
    ch, beat, dr = p.chords, p.beat, Drums(p.rng)
    mx = Mix(len(ch) * 2 * 4 * beat)
    for rep in range(2):
        for ci, c in enumerate(ch):
            b0 = (rep * len(ch) + ci) * 4 * beat
            for at in (0, 2, 3.5):
                for k, m in enumerate(c):
                    mx.add(epiano(hz(m), beat * 1.5, 0.07), b0 + at * beat + k * 0.012, -0.2)
            for at in (0, 1, 2, 3):
                root = c[0] - 12 + (7 if at == 3 else 0)
                mx.add(bass(hz(root), beat * 0.8, 0.28), b0 + at * beat)
            for q in range(4):
                mx.add(dr.kick(0.5) if q in (0, 2) else dr.snare(0.14), b0 + q * beat)
            for e in range(8):
                mx.add(dr.hat(0.04), b0 + e * beat / 2, 0.3)
    for b, m, d in melody(ch * 2, 4, p.rng, 72, 86, 0.7):
        mx.add(epiano(hz(m), d * beat, 0.15), b * beat, 0.15)
    return mx.done(7500)


def calm_ambient(p: Plan) -> np.ndarray:
    """잔잔한 앰비언트: 패드와 느린 종소리, 드럼 없음."""
    ch, beat = p.chords, p.beat
    mx = Mix(len(ch) * 2 * 4 * beat)
    for rep in range(2):
        for ci, c in enumerate(ch):
            b0 = (rep * len(ch) + ci) * 4 * beat
            for m in c:
                mx.add(pad(hz(m), 4.5 * beat, 0.03), b0, -0.3)
            mx.add(bass(hz(c[0] - 12), 4 * beat, 0.18), b0)
    for b, m, d in melody(ch * 2, 4, p.rng, 72, 84, 0.3):
        mx.add(bell(hz(m), d * beat * 2, 0.1), b * beat, 0.25)
    return mx.done(6000)


def bossa_cafe(p: Plan) -> np.ndarray:
    """보사노바: 나일론 기타, 림샷·셰이커."""
    ch, beat, dr = p.chords, p.beat, Drums(p.rng)
    mx = Mix(len(ch) * 2 * 2 * 4 * beat)
    for rep in range(2):
        for ci, c in enumerate(ch):
            for bar in range(2):
                b0 = ((rep * len(ch) + ci) * 2 + bar) * 4 * beat
                for at in (0, 1.5, 3) if bar == 0 else (0, 1, 2.5):
                    for k, m in enumerate(c[1:]):
                        mx.add(guitar(hz(m), 1.2, 0.12, p.rng), b0 + at * beat + k * 0.008, -0.2)
                for at in (0, 1.5, 2, 3.5):
                    root = c[0] - 12 if at in (0, 2) else c[0] - 5
                    mx.add(bass(hz(root), beat * 0.9, 0.28), b0 + at * beat)
                for e in range(8):
                    mx.add(dr.shaker(), b0 + e * beat / 2, 0.4)
                for at in (0, 1.5, 3) if bar == 0 else (1, 2.5):
                    mx.add(dr.rim(), b0 + at * beat, 0.2)
    for b, m, d in melody(ch * 2, 8, p.rng, 69, 81, 0.5):
        mx.add(rhodes(hz(m), d * beat * 1.1, 0.14), b * beat, 0.1)
    return mx.done(6500)


def city_pop(p: Plan) -> np.ndarray:
    """시티팝: 슬랩 베이스, 일렉피아노, 밝은 템포."""
    ch, beat, dr = p.chords, p.beat, Drums(p.rng)
    mx = Mix(len(ch) * 2 * 4 * beat)
    for rep in range(2):
        for ci, c in enumerate(ch):
            b0 = (rep * len(ch) + ci) * 4 * beat
            for at in (0, 1.5, 2.5, 3.5):
                for k, m in enumerate(c):
                    mx.add(rhodes(hz(m), beat * 0.8, 0.07), b0 + at * beat + k * 0.01, -0.3)
            for at, oc in ((0, 0), (0.75, 12), (1.5, 0), (2, 0), (2.75, 12), (3.5, 7)):
                mx.add(bass(hz(c[0] - 12 + oc), beat * 0.4, 0.3, True), b0 + at * beat)
            for q in range(4):
                mx.add(dr.kick(0.5) if q in (0, 2) else dr.snare(0.16), b0 + q * beat)
            for e in range(8):
                mx.add(dr.hat(0.05 if e % 2 else 0.035), b0 + e * beat / 2, 0.3)
            mx.add(dr.hat(0.04, True), b0 + 3.5 * beat, 0.3)
    for b, m, d in melody(ch * 2, 4, p.rng, 72, 86, 0.75):
        mx.add(bell(hz(m), d * beat, 0.1), b * beat, 0.2)
    return mx.done(8000)


def piano_ballad(p: Plan) -> np.ndarray:
    """피아노 독주: 펼친 화음과 멜로디, 드럼 없음."""
    ch, beat = p.chords, p.beat
    mx = Mix(len(ch) * 2 * 4 * beat)
    for rep in range(2):
        for ci, c in enumerate(ch):
            b0 = (rep * len(ch) + ci) * 4 * beat
            mx.add(piano(hz(c[0] - 12), 4 * beat, 0.2), b0, -0.2)
            for e, k in enumerate([1, 2, 3, 2, 1, 2, 3, 2]):
                mx.add(piano(hz(c[k % len(c)]), beat * 1.2, 0.09), b0 + e * beat / 2, -0.1)
    for b, m, d in melody(ch * 2, 4, p.rng, 69, 84, 0.4):
        mx.add(piano(hz(m), d * beat * 1.5, 0.17), b * beat, 0.15)
    return mx.done(9000)


def chill_house(p: Plan) -> np.ndarray:
    """칠 하우스: 4박 킥, 엇박 하이햇, 플럭 화음."""
    ch, beat, dr = p.chords, p.beat, Drums(p.rng)
    mx = Mix(len(ch) * 2 * 2 * 4 * beat)
    for rep in range(2):
        for ci, c in enumerate(ch):
            for bar in range(2):
                b0 = ((rep * len(ch) + ci) * 2 + bar) * 4 * beat
                for q in range(4):
                    mx.add(dr.kick(0.55, 90), b0 + q * beat)
                    mx.add(dr.hat(0.06, True), b0 + (q + 0.5) * beat, 0.3)
                    if q in (1, 3):
                        mx.add(dr.clap(0.12), b0 + q * beat)
                for at in (0.5, 1.5, 2.25, 3, 3.5):
                    for m in c:
                        mx.add(synth_pluck(hz(m), beat * 0.6, 0.05), b0 + at * beat, -0.2)
                for at in (0.5, 1.5, 2.5, 3.5):
                    mx.add(bass(hz(c[0] - 12), beat * 0.45, 0.3), b0 + at * beat)
                mx.add(pad(hz(c[2]), 4 * beat, 0.025), b0, 0.3)
    return mx.done(7500)


def jazz_hop(p: Plan) -> np.ndarray:
    """재즈 힙합: 붐뱁 드럼, 로즈, 바이닐 잡음."""
    ch, beat, dr, sw = p.chords, p.beat, Drums(p.rng), 0.2
    mx = Mix(len(ch) * 2 * 4 * beat)
    for rep in range(2):
        for ci, c in enumerate(ch):
            b0 = (rep * len(ch) + ci) * 4 * beat
            for k, m in enumerate(c[1:]):
                mx.add(rhodes(hz(m), 3 * beat, 0.09), b0 + k * 0.03, -0.25)
            mx.add(rhodes(hz(c[2]), beat, 0.07), b0 + (2.5 + sw / 2) * beat, -0.25)
            for at in (0, 0.75 + sw / 4, 2.5):
                mx.add(bass(hz(c[0] - 12), beat * 0.8, 0.32), b0 + at * beat)
                mx.add(dr.kick(0.6, 60), b0 + at * beat)
            for at in (1, 3):
                mx.add(dr.snare(0.2), b0 + at * beat)
            for e in range(8):
                at = e * 0.5 + (sw / 2 if e % 2 else 0)
                mx.add(dr.hat(0.045 if e % 2 else 0.06), b0 + at * beat, 0.35)
    for b, m, d in melody(ch * 2, 4, p.rng, 70, 84, 0.55):
        mx.add(bell(hz(m), d * beat, 0.07), b * beat, 0.25)
    return mx.done(5000, vinyl=0.004, rng=p.rng)


def marimba_morning(p: Plan) -> np.ndarray:
    """마림바: 밝은 아르페지오, 가벼운 손뼉."""
    ch, beat, dr = p.chords, p.beat, Drums(p.rng)
    mx = Mix(len(ch) * 2 * 4 * beat)
    for rep in range(2):
        for ci, c in enumerate(ch):
            b0 = (rep * len(ch) + ci) * 4 * beat
            for e, k in enumerate([0, 2, 1, 3, 2, 1, 3, 2]):
                mx.add(marimba(hz(c[k % len(c)] + 12), beat, 0.16), b0 + e * beat / 2, -0.2)
            for at in (0, 2):
                mx.add(bass(hz(c[0] - 12), beat * 1.5, 0.26), b0 + at * beat)
                mx.add(dr.kick(0.4), b0 + at * beat)
            for at in (1, 3):
                mx.add(dr.clap(0.1), b0 + at * beat, 0.1)
            for e in range(8):
                mx.add(dr.shaker(0.03), b0 + e * beat / 2, 0.4)
    for b, m, d in melody(ch * 2, 4, p.rng, 72, 88, 0.6):
        mx.add(bell(hz(m), d * beat, 0.08), b * beat, 0.2)
    return mx.done(9000)


def rnb_night(p: Plan) -> np.ndarray:
    """느린 알앤비: 패드, 808 베이스, 잘게 쪼갠 하이햇."""
    ch, beat, dr = p.chords, p.beat, Drums(p.rng)
    mx = Mix(len(ch) * 2 * 4 * beat)
    for rep in range(2):
        for ci, c in enumerate(ch):
            b0 = (rep * len(ch) + ci) * 4 * beat
            for k, m in enumerate(c):
                mx.add(pad(hz(m), 4 * beat, 0.035), b0, -0.2)
                mx.add(rhodes(hz(m + 12), 2 * beat, 0.05), b0 + 0.5 * beat + k * 0.05, 0.2)
            for at in (0, 1.75, 2.5):
                mx.add(sub808(hz(c[0] - 12), beat * 1.2), b0 + at * beat)
                mx.add(dr.kick(0.45), b0 + at * beat)
            mx.add(dr.clap(0.14), b0 + 2 * beat)
            for s in range(16):
                if s not in (6, 14):
                    mx.add(dr.hat(0.04 if s % 2 else 0.055), b0 + s * beat / 4, 0.3)
    for b, m, d in melody(ch * 2, 4, p.rng, 67, 79, 0.45):
        mx.add(rhodes(hz(m), d * beat * 1.2, 0.11), b * beat, 0.1)
    return mx.done(6000)


# 분위기: (곡 틀, 기본 화음, 기본 빠르기)
STYLES = {
    "autumn_lofi": (
        autumn_lofi,
        [[53, 57, 60, 64], [52, 55, 59, 62], [50, 53, 57, 60], [48, 52, 55, 59]],
        84,
    ),
    "bright_walk": (
        bright_walk,
        [[48, 52, 55, 59], [45, 48, 52, 55], [50, 53, 57, 60], [43, 47, 50, 53]],
        100,
    ),
    "calm_ambient": (
        calm_ambient,
        [[45, 52, 55, 59], [41, 48, 52, 57], [48, 52, 55, 59], [43, 50, 55, 57]],
        72,
    ),
    "bossa_cafe": (
        bossa_cafe,
        [[50, 53, 57, 60, 64], [43, 47, 50, 53, 57], [48, 52, 55, 59, 62], [45, 49, 52, 55, 58]],
        132,
    ),
    "city_pop": (
        city_pop,
        [[53, 57, 60, 64], [52, 55, 59, 62], [57, 60, 64, 67], [50, 53, 57, 60]],
        112,
    ),
    "piano_ballad": (
        piano_ballad,
        [[48, 55, 64, 67], [47, 55, 62, 67], [45, 52, 60, 64], [41, 48, 57, 64]],
        70,
    ),
    "chill_house": (
        chill_house,
        [[57, 60, 64, 67], [53, 57, 60, 64], [48, 52, 55, 59], [55, 59, 62, 65]],
        120,
    ),
    "jazz_hop": (
        jazz_hop,
        [[50, 53, 57, 60, 64], [43, 47, 53, 57], [48, 52, 55, 59, 62], [45, 48, 52, 55, 59]],
        86,
    ),
    "marimba_morning": (
        marimba_morning,
        [[60, 64, 67, 71], [57, 60, 64, 67], [53, 57, 60, 64], [55, 59, 62, 67]],
        104,
    ),
    "rnb_night": (
        rnb_night,
        [[51, 55, 58, 62], [50, 53, 57, 60], [48, 51, 55, 58], [46, 50, 53, 57]],
        72,
    ),
}


def compose(style: str, seed: int) -> np.ndarray:
    """분위기 하나로 곡을 만든다. (샘플, 2) float 배열."""
    fn, base, bpm = STYLES[style]
    return fn(plan(base, bpm, np.random.default_rng(seed)))


def write_wav(audio: np.ndarray, path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio * 32767).astype("<i2").tobytes())


def write_m4a(audio: np.ndarray, path: Path) -> None:
    from tools.reels import ffmpeg_exe

    wav = path.with_suffix(".wav")
    write_wav(audio, wav)
    try:
        cmd = [ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(wav)]
        subprocess.run(cmd + ["-c:a", "aac", "-b:a", "192k", str(path)], check=True)
    finally:
        wav.unlink(missing_ok=True)


def generated(folder: Path) -> list[tuple[date, str, Path]]:
    """자동으로 만든 곡 (날짜, 분위기, 경로), 오래된 것부터."""
    out = []
    for p in folder.glob("gen_*.m4a"):
        m = GEN_RE.match(p.name)
        if m:
            day = date(int(m[1][:4]), int(m[1][4:6]), int(m[1][6:]))
            out.append((day, m[2], p))
    return sorted(out)


def due(profile: Profile, folder: Path, today: date) -> bool:
    days = profile.reels.music_refresh_days
    if not days:
        return False
    gens = generated(folder)
    return not gens or (today - gens[-1][0]).days >= days


def pick_styles(count: int, recent: set[str], rng: np.random.Generator) -> list[str]:
    """바로 전에 만든 분위기는 피해서 count 개 (모자라면 다시 씀)."""
    fresh = [s for s in STYLES if s not in recent] or list(STYLES)
    order = list(rng.permutation(fresh)) + list(rng.permutation(list(STYLES)))
    return list(dict.fromkeys(order))[:count]


def update_readme(folder: Path) -> None:
    readme = folder / "README.md"
    text = readme.read_text(encoding="utf-8") if readme.exists() else "# 릴스 배경 음악\n"
    rows = [
        f"| {p.name} | 코드로 직접 만든 곡 ({d}, {style}, tools.music_gen)"
        " | 이 계정 소유, 상업 이용 가능 |"
        for d, style, p in generated(folder)
    ]
    block = "\n".join(
        [
            README_START,
            "자동으로 만든 곡 (music 워크플로가 고침, 직접 고치지 않음)",
            "",
            "| 파일 | 출처 | 라이선스 |",
            "|---|---|---|",
            *rows,
            README_END,
        ]
    )
    if README_START in text and README_END in text:
        head, rest = text.split(README_START, 1)
        text = head + block + rest.split(README_END, 1)[1]
    else:
        text = text.rstrip("\n") + "\n\n" + block + "\n"
    readme.write_text(text, encoding="utf-8")


def refresh(
    profile: Profile,
    today: date,
    force: bool = False,
    root: Path = REPO_ROOT,
    render=write_m4a,
) -> list[Path]:
    """때가 됐으면 새 곡을 만들고 오래된 자동 곡을 지운다. 새로 만든 파일 목록."""
    cfg = profile.reels
    if not cfg.music_dir:
        return []
    folder = root / cfg.music_dir
    folder.mkdir(parents=True, exist_ok=True)
    if not force and not due(profile, folder, today):
        return []
    rng = np.random.default_rng(today.toordinal())
    gens = generated(folder)
    last_day = gens[-1][0] if gens else None
    recent = {s for d, s, _ in gens if d == last_day}
    made = []
    for i, style in enumerate(pick_styles(cfg.music_refresh_count, recent, rng)):
        path = folder / f"gen_{today:%Y%m%d}_{style}.m4a"
        render(compose(style, today.toordinal() * 100 + i), path)
        made.append(path)
    for _, _, old in generated(folder)[: -cfg.music_keep or None]:
        old.unlink()
    update_readme(folder)
    return made


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("account", nargs="?", help="계정 (생략하면 index.yaml 의 모든 계정)")
    ap.add_argument("--force", action="store_true", help="때가 안 됐어도 바로 만든다")
    ap.add_argument("--date", help="오늘 날짜 대신 (YYYY-MM-DD)")
    ap.add_argument(
        "--style", choices=sorted(STYLES), help="이 분위기로 한 곡만 만들어 --out 에 쓴다"
    )
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    if args.style:
        if not args.out:
            ap.error("--style 에는 --out 이 필요합니다")
        audio = compose(args.style, args.seed)
        (write_wav if args.out.suffix == ".wav" else write_m4a)(audio, args.out)
        print(f"{args.out} ({len(audio) / SR:.1f}초)")
        return 0
    today = date.fromisoformat(args.date) if args.date else date.today()
    for account in [args.account] if args.account else load_index().accounts:
        made = refresh(load_profile(account), today, args.force)
        names = ", ".join(p.name for p in made) or "없음 (아직 때가 아니거나 꺼져 있음)"
        print(f"[{account}] 새 곡: {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
