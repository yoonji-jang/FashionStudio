"""사용자가 클로드 스레드에서 보낸 참고 사진을 잠가서 결과 브랜치에 두기 (2026-11-03).

저장소가 공개라 참고 사진(남의 사진)을 그대로 올리지 않는다. 텔레그램 없이 진행할 때는
사진을 이 공개 키로 잠가(`seal`) data/<계정>/user_refs/<이름>.sealed 에 두고,
워크플로가 비밀값에서 같은 열쇠를 만들어 러너 안에서만 푼다(`open`).
푼 사진은 저장소에 올리지 않는다.
참고 코디(refs.json)의 사진 주소는 "sealed:<이름>".

열쇠: 비밀값(REF_SEAL_SEED, 워크플로에서는 OPENROUTER_API_KEY) → SHA-256 → X25519 개인 키.
공개 키는 config/ref_seal.pub (공개해도 됨).
비밀값을 바꾸면 예전 사진은 못 푼다 (그때는 다시 보내면 됨).

  python -m tools.sealed pubkey                       # 비밀값이 있는 곳(워크플로)에서 공개 키 출력
  python -m tools.sealed seal 사진.jpg 이름.sealed      # 공개 키로 잠금 (클로드 스레드)
  python -m tools.sealed open <폴더> [--dest <폴더>]    # *.sealed 를 모두 풀어 <이름>.jpg 로
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from pathlib import Path

from nacl.public import PrivateKey, PublicKey, SealedBox

PREFIX = "sealed:"
PUBKEY_FILE = Path("config/ref_seal.pub")
SEED_ENV = "REF_SEAL_SEED"
CONTEXT = b"fashionstudio-user-refs-v1:"


def cache_dir() -> Path:
    """푼 사진을 두는 곳 (out/ 밖, 저장소에 올리지 않음)."""
    return Path(os.environ.get("USER_REFS_DIR") or Path.home() / ".cache/fashionstudio/user_refs")


def private_key(seed: str) -> PrivateKey:
    return PrivateKey(hashlib.sha256(CONTEXT + seed.encode()).digest())


def public_key() -> PublicKey:
    return PublicKey(base64.b64decode(PUBKEY_FILE.read_text().strip()))


def seal(data: bytes, pub: PublicKey | None = None) -> bytes:
    return SealedBox(pub or public_key()).encrypt(data)


def unseal(data: bytes, seed: str) -> bytes:
    return SealedBox(private_key(seed)).decrypt(data)


def open_dir(src: Path, dest: Path, seed: str) -> int:
    """src 의 *.sealed 를 모두 풀어 dest/<이름>.jpg 로. 푼 개수."""
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.glob("*.sealed")):
        try:
            (dest / f"{f.stem}.jpg").write_bytes(unseal(f.read_bytes(), seed))
            n += 1
        except Exception as e:  # noqa: BLE001 - 한 장이 안 풀려도 나머지는 쓴다
            print(f"참고 사진을 풀지 못함: {f.name} ({type(e).__name__})", file=sys.stderr)
    return n


def photo_path(url: str) -> Path:
    """ "sealed:<이름>" 사진의 푼 파일. 없으면 FileNotFoundError."""
    path = cache_dir() / f"{url[len(PREFIX) :]}.jpg"
    if not path.exists():
        raise FileNotFoundError(f"풀어 둔 참고 사진이 없음: {url}")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.sealed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pubkey")
    s = sub.add_parser("seal")
    s.add_argument("src", type=Path)
    s.add_argument("dest", type=Path)
    o = sub.add_parser("open")
    o.add_argument("src", type=Path)
    o.add_argument("--dest", type=Path, default=None)
    args = ap.parse_args(argv)

    if args.cmd == "seal":
        args.dest.parent.mkdir(parents=True, exist_ok=True)
        args.dest.write_bytes(seal(args.src.read_bytes()))
        return 0
    seed = os.environ.get(SEED_ENV, "")
    if not seed:
        print(f"{SEED_ENV} 가 없어요", file=sys.stderr)
        return 0 if args.cmd == "open" else 1
    if args.cmd == "pubkey":
        print(base64.b64encode(bytes(private_key(seed).public_key)).decode())
        return 0
    n = open_dir(args.src, args.dest or cache_dir(), seed) if args.src.is_dir() else 0
    print(f"참고 사진 {n}장 풂")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
