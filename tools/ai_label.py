"""이미지 파일 안에 "AI로 합성한 이미지" 표시를 넣고 읽는다 (13단계).

인스타 게시 API 에는 AI 레이블 값이 없다. 대신 Meta 는 이미지 안의 업계 표준 표시
(IPTC `DigitalSourceType`, XMP 에 기록)를 읽어 게시물에 "AI 정보" 레이블을 붙인다.
계정마다 `profile.yaml` 의 `ai_label` 로 켜고 끈다.

  composite  compositeWithTrainedAlgorithmicMedia
             실제 사진에 AI 편집을 섞은 이미지 (누끼·AI 그림자 코디컷)
  generated  trainedAlgorithmicMedia
             AI 가 통째로 만든 이미지 (모델컷 표지 등)

사용법 (파일에 표시가 있는지 확인):
  python -m tools.ai_label out/2026-09-30/slides/*.jpg
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image

from core.profile import AiLabel

IPTC_CV = "http://cv.iptc.org/newscodes/digitalsourcetype/"
SOURCE_TYPES = {
    "composite": "compositeWithTrainedAlgorithmicMedia",
    "generated": "trainedAlgorithmicMedia",
}
_TYPE_RE = re.compile(rb"DigitalSourceType>\s*([^<\s]+)\s*<")


def xmp_packet(source_type: str) -> bytes:
    """IPTC DigitalSourceType 한 줄만 담은 XMP 패킷."""
    value = IPTC_CV + SOURCE_TYPES[source_type]
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/">\n'
        f"   <Iptc4xmpExt:DigitalSourceType>{value}</Iptc4xmpExt:DigitalSourceType>\n"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    ).encode()


def slide_kind(path: Path) -> str:
    """슬라이드 파일 이름으로 종류를 안다.
    tools.feed 가 01_cover, 02_아우터 … 09_summary, 10_comment 로 저장한다.
    여러 벌 게시물(20단계)의 벌 장은 02_look1, 03_look2 …"""
    stem = path.stem
    if stem.endswith("_cover"):
        return "cover"
    if re.search(r"_look\d+$", stem):
        return "looks"
    if stem.endswith("_summary"):
        return "summary"
    if stem.endswith("_comment"):
        return "comment"
    return "products"


def labeled(label: AiLabel, kind: str) -> bool:
    return label.mode == "metadata" and kind in label.slides


def save_options(label: AiLabel, kind: str) -> dict:
    """Pillow JPEG 저장에 넘길 값. 표시할 슬라이드면 {"xmp": …}, 아니면 {}."""
    return {"xmp": xmp_packet(label.source_type)} if labeled(label, kind) else {}


def read(path: Path) -> str | None:
    """파일에 든 DigitalSourceType 값 (예: compositeWithTrainedAlgorithmicMedia). 없으면 None."""
    with Image.open(path) as im:
        xmp = im.info.get("xmp") or b""
    if isinstance(xmp, str):
        xmp = xmp.encode()
    m = _TYPE_RE.search(xmp)
    return m.group(1).decode().removeprefix(IPTC_CV) if m else None


XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"


def ensure(label: AiLabel, images: list[Path]) -> list[str]:
    """표시가 있어야 하는데 없는 슬라이드에 표시를 넣는다 (게시 직전, 다시 압축하지 않음).
    ai_label 을 켜기 전에 만든 슬라이드도 게시할 때 표시가 붙는다. 표시를 넣은 파일 이름 목록."""
    want = SOURCE_TYPES[label.source_type]
    done = []
    for p in images:
        if labeled(label, slide_kind(p)) and read(p) != want:
            p.write_bytes(insert_xmp(p.read_bytes(), xmp_packet(label.source_type)))
            done.append(p.name)
    return done


# MP4 안의 XMP 는 이 UUID 를 단 맨 위 uuid 상자에 둔다 (Adobe XMP 규격 3부)
MP4_XMP_UUID = bytes.fromhex("BE7ACFCB97A942E89C71999491E3AFAC")


def mp4_boxes(data: bytes) -> list[tuple[int, int, bytes]]:
    """맨 위 상자 목록 (시작, 끝, 종류)."""
    boxes, pos = [], 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        if size == 1:
            size = int.from_bytes(data[pos + 8 : pos + 16], "big")
        elif size == 0:
            size = len(data) - pos
        if size < 8 or pos + size > len(data):
            raise ValueError("MP4 상자 구조를 읽지 못했습니다")
        boxes.append((pos, pos + size, kind))
        pos += size
    return boxes


def _is_xmp_box(data: bytes, start: int, kind: bytes) -> bool:
    return kind == b"uuid" and data[start + 8 : start + 24] == MP4_XMP_UUID


def read_video(path: Path) -> str | None:
    """MP4 에 든 DigitalSourceType 값. 없으면 None."""
    data = path.read_bytes()
    for start, end, kind in mp4_boxes(data):
        if _is_xmp_box(data, start, kind):
            m = _TYPE_RE.search(data[start:end])
            return m.group(1).decode().removeprefix(IPTC_CV) if m else None
    return None


def insert_mp4_xmp(data: bytes, packet: bytes) -> bytes:
    """MP4 맨 끝에 XMP uuid 상자를 붙인다 (원래 XMP 상자는 뺀다). 영상·소리는 건드리지 않는다.
    상자 위치(오프셋)를 가리키는 moov 안의 값이 바뀌지 않도록 앞쪽 상자는 그대로 둔다."""
    boxes = mp4_boxes(data)
    if not boxes or boxes[0][2] != b"ftyp":
        raise ValueError("MP4 가 아닙니다")
    if boxes[-1][2] == b"uuid" and _is_xmp_box(data, boxes[-1][0], boxes[-1][2]):
        data = data[: boxes[-1][0]]  # 앞서 붙인 표시 (맨 끝) 만 바꾼다
    body = MP4_XMP_UUID + packet
    return data + (len(body) + 8).to_bytes(4, "big") + b"uuid" + body


def ensure_video(label: AiLabel, video: Path) -> bool:
    """릴스 영상에 AI 표시가 없으면 넣는다. 릴스에 AI 가 그린 장(표지·벌 장)이 들어가므로
    ai_label 이 켜져 있으면 영상 전체에 표시한다. 넣었으면 True."""
    if label.mode != "metadata" or read_video(video) == SOURCE_TYPES[label.source_type]:
        return False
    video.write_bytes(insert_mp4_xmp(video.read_bytes(), xmp_packet(label.source_type)))
    return True


def insert_xmp(jpeg: bytes, packet: bytes) -> bytes:
    """JPEG 바이트에 XMP(APP1) 조각을 넣는다. 원래 XMP 조각은 빼고, 픽셀은 건드리지 않는다."""
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("JPEG 가 아닙니다")
    body = XMP_HEADER + packet
    segment = b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body
    head, pos = [jpeg[:2]], 2
    at = None  # APP0(JFIF) 뒤에 넣는다
    while pos + 4 <= len(jpeg) and jpeg[pos] == 0xFF and 0xE0 <= jpeg[pos + 1] <= 0xEF:
        size = int.from_bytes(jpeg[pos + 2 : pos + 4], "big")
        seg = jpeg[pos : pos + 2 + size]
        if not (jpeg[pos + 1] == 0xE1 and seg[4:].startswith(XMP_HEADER)):
            head.append(seg)
        if jpeg[pos + 1] == 0xE0:
            at = len(head)
        pos += 2 + size
    head.insert(at or 1, segment)
    return b"".join(head) + jpeg[pos:]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="이미지 안의 AI 합성 표시(IPTC) 확인")
    ap.add_argument("images", nargs="+", type=Path)
    args = ap.parse_args(argv)
    for p in args.images:
        print(f"{p.name}: {read(p) or '없음'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
