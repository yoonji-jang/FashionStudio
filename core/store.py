"""data/<계정>/ 아래 상태 파일 읽기·쓰기.

- products.json: 수집한 상품 후보
- themes.json:   주간 주제
- posts.json:    게시 기록 (중복 게시 방지)
- cache/:        상품별 정리컷 이미지 (git 제외)
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from core.models import Product, Theme

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

_products = TypeAdapter(list[Product])
_themes = TypeAdapter(list[Theme])


class Store:
    def __init__(self, account: str, root: Path = DATA_DIR):
        self.account = account
        self.dir = root / account

    @property
    def cache_dir(self) -> Path:
        return self.dir / "cache"

    def _read(self, name: str) -> list:
        path = self.dir / name
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, name: str, data: bytes) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{name}.tmp"
        tmp.write_bytes(data)
        tmp.replace(self.dir / name)

    def products(self) -> list[Product]:
        return _products.validate_python(self._read("products.json"))

    def save_products(self, products: list[Product]) -> None:
        self._write("products.json", _products.dump_json(products, indent=2))

    def themes(self) -> list[Theme]:
        return _themes.validate_python(self._read("themes.json"))

    def save_themes(self, themes: list[Theme]) -> None:
        self._write("themes.json", _themes.dump_json(themes, indent=2))

    def posts(self) -> list[dict]:
        return self._read("posts.json")
