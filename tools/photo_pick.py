"""상품 사진 고르기. 누끼 따기 좋은 사진(상품만, 한 개, 단색 배경)을 앞으로 보낸다.

AI 없이 간단한 규칙으로 점수를 매긴다.
- plain: 테두리가 한 가지 색에 가까울수록 높음 (단색 배경 단독컷)
- skin:  피부색 비율 (모델 착용컷일수록 높음)
- parts: 배경과 다른 덩어리 수 (여러 상품이 같이 찍히면 많음)
한계: 베이지·카멜 색 옷은 피부색으로 잡힐 수 있다. 최종 확인은 이미지 검수(6단계)가 한다.

사용법: python -m tools.photo_pick a.jpg b.jpg
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

GRID = 48  # 계산용으로 줄이는 크기
BG_TOLERANCE = 38  # 배경색과 이만큼 가까우면 배경으로 봄 (RGB 거리)
MIN_PART = 0.03  # 이보다 작은 덩어리는 무시 (전체 대비)


@dataclass(frozen=True)
class PhotoScore:
    plain: float
    skin: float
    parts: int

    @property
    def flat(self) -> bool:
        """사람 없이 상품 하나만 단색 배경에 찍힌 사진으로 보이는지."""
        return self.plain >= 0.6 and self.skin < 0.012 and self.parts == 1

    @property
    def score(self) -> float:
        return (
            0.5 * self.plain
            + 0.5 * (1 - min(1.0, self.skin * 8))
            - 0.25 * max(0, self.parts - 1)
            - (0.3 if self.parts == 0 else 0)
        )


def _dist(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=False)) ** 0.5


def _is_skin(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return 77 <= cb <= 127 and 137 <= cr <= 173 and r > g > b and r > 90


def _count_parts(mask: list[list[bool]]) -> int:
    n = len(mask)
    seen = [[False] * n for _ in range(n)]
    parts = 0
    for y in range(n):
        for x in range(n):
            if not mask[y][x] or seen[y][x]:
                continue
            stack, area = [(y, x)], 0
            seen[y][x] = True
            while stack:
                cy, cx = stack.pop()
                area += 1
                for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                    if 0 <= ny < n and 0 <= nx < n and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((ny, nx))
            if area >= MIN_PART * n * n:
                parts += 1
    return parts


def score_image(img: Image.Image) -> PhotoScore:
    small = img.convert("RGB").resize((GRID, GRID), Image.BILINEAR)
    px = small.load()
    border = [px[x, y] for x in range(GRID) for y in (0, 1, GRID - 2, GRID - 1)]
    border += [px[x, y] for y in range(2, GRID - 2) for x in (0, 1, GRID - 2, GRID - 1)]
    bg = tuple(sorted(c[i] for c in border)[len(border) // 2] for i in range(3))
    plain = sum(_dist(c, bg) < BG_TOLERANCE for c in border) / len(border)

    mask = [[_dist(px[x, y], bg) >= BG_TOLERANCE for x in range(GRID)] for y in range(GRID)]
    fg = [px[x, y] for y in range(GRID) for x in range(GRID) if mask[y][x]]
    skin = sum(_is_skin(c) for c in fg) / max(1, len(fg))
    return PhotoScore(plain=round(plain, 3), skin=round(skin, 3), parts=_count_parts(mask))


def score_bytes(data: bytes) -> PhotoScore:
    return score_image(Image.open(BytesIO(data)))


def rank(scores: dict[str, PhotoScore]) -> list[str]:
    """점수 높은 순서의 키 목록. 동점이면 원래 순서 유지."""
    order = list(scores)
    return sorted(order, key=lambda k: (-scores[k].score, order.index(k)))


def main(argv: list[str] | None = None) -> int:
    paths = [Path(a) for a in (argv if argv is not None else sys.argv[1:])]
    for p in paths:
        s = score_image(Image.open(p))
        print(
            f"{p}\tscore={s.score:.2f}\tplain={s.plain}\tskin={s.skin}\tparts={s.parts}\tflat={s.flat}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
