"""모델 얼굴 찾기와 가리기 (AI 이미지 생성 없음, OpenCV 얼굴 검출 모델 YuNet).

캐러셀에 쓰는 쇼핑몰 원본 사진에서 모델 얼굴이 보이지 않게 한다.
- 얼굴 없는 사진을 먼저 고르고 (tools.feed)
- 얼굴이 있는 사진만 남으면 얼굴 아래부터 잘라 쓴다.
모델 파일: assets/models/face_detection_yunet_2023mar.onnx (MIT, YUNET_LICENSE.txt)

사용법: python -m tools.faces a.jpg b.jpg   (사진마다 찾은 얼굴 수)
"""

from __future__ import annotations

import sys
from functools import cache
from pathlib import Path

from PIL import Image

from core.profile import REPO_ROOT

MODEL = REPO_ROOT / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
DETECT_SIDE = 640  # 작은 사진은 키워서 찾는다 (썸네일 240px 에서도 얼굴을 찾도록)
MIN_SCORE = 0.75  # 귀·손을 얼굴로 잘못 잡는 경우를 줄인다
CROP_MARGIN = 0.04  # 얼굴 아래 이만큼 (사진 높이 대비) 더 띄우고 자름
MIN_KEEP = 0.45  # 자르고 남는 높이가 이보다 작으면 쓸 수 없는 사진으로 봄

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 (원본 픽셀)


@cache
def _detector():
    import cv2

    if not MODEL.exists():
        raise FileNotFoundError(f"얼굴 검출 모델이 없습니다: {MODEL}")
    return cv2.FaceDetectorYN.create(str(MODEL), "", (320, 320), MIN_SCORE, 0.3, 5000)


def find_faces(img: Image.Image) -> list[Box]:
    import cv2
    import numpy as np

    rgb = img.convert("RGB")
    s = DETECT_SIDE / max(rgb.size)
    big = rgb.resize((max(1, round(rgb.width * s)), max(1, round(rgb.height * s))))
    bgr = cv2.cvtColor(np.asarray(big), cv2.COLOR_RGB2BGR)
    det = _detector()
    det.setInputSize((big.width, big.height))
    _, faces = det.detect(bgr)
    if faces is None:
        return []
    return [(f[0] / s, f[1] / s, (f[0] + f[2]) / s, (f[1] + f[3]) / s) for f in faces]


def crop_below_faces(img: Image.Image, faces: list[Box]) -> Image.Image | None:
    """얼굴(들) 아래부터 끝까지 잘라낸다. 남는 부분이 너무 작으면 None."""
    if not faces:
        return img
    top = max(y1 for _, _, _, y1 in faces) + img.height * CROP_MARGIN
    if img.height - top < img.height * MIN_KEEP:
        return None
    return img.crop((0, round(top), img.width, img.height))


def hide_faces(img: Image.Image) -> tuple[Image.Image, bool]:
    """(얼굴을 잘라낸 사진, 얼굴 없이 쓸 수 있는지). 못 자르면 원본과 False."""
    cropped = crop_below_faces(img, find_faces(img))
    return (cropped, True) if cropped is not None else (img, False)


def main(argv: list[str] | None = None) -> int:
    for path in argv if argv is not None else sys.argv[1:]:
        print(f"{path}\t{len(find_faces(Image.open(Path(path))))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
