"""data/<계정>/ 아래 상태 파일 읽기·쓰기.

- products.json: 수집한 상품 후보
- themes.json:   주간 주제
- posts.json:    게시 기록 (중복 게시 방지, 댓글 DM 링크)
- dm.json:       댓글 DM 기록 (댓글 id 해시만)
- feedback.jsonl: 텔레그램 /fix 피드백 (15단계, 한 줄에 하나)
- cache/:        상품별 정리컷 이미지 (git 제외)
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from core.models import DmState, FeedbackEntry, PostRecord, Product, Theme

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

_products = TypeAdapter(list[Product])
_themes = TypeAdapter(list[Theme])
_posts = TypeAdapter(list[PostRecord])


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

    def posts(self) -> list[PostRecord]:
        return _posts.validate_python(self._read("posts.json"))

    def save_posts(self, posts: list[PostRecord]) -> None:
        self._write("posts.json", _posts.dump_json(posts, indent=2))

    def dm(self) -> DmState:
        path = self.dir / "dm.json"
        if not path.exists():
            return DmState()
        return DmState.model_validate_json(path.read_text(encoding="utf-8"))

    def save_dm(self, state: DmState) -> None:
        self._write("dm.json", state.model_dump_json(indent=2).encode())

    def feedback(self) -> list[FeedbackEntry]:
        path = self.dir / "feedback.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        return [FeedbackEntry.model_validate_json(line) for line in lines if line.strip()]

    def add_feedback(self, entries: list[FeedbackEntry]) -> None:
        """한 줄씩 덧붙인다 (지난 줄은 고치지 않음)."""
        if not entries:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "feedback.jsonl").open("a", encoding="utf-8") as f:
            for e in entries:
                f.write(e.model_dump_json() + "\n")

    def recent_posts(self, today: date, days: int) -> list[PostRecord]:
        since = today - timedelta(days=days)
        return [p for p in self.posts() if since <= p.date < today]

    def used_since(self, today: date, days: int) -> dict[str, date]:
        """최근 days 일 안에 게시(또는 last_used)된 상품 id → 마지막 날짜."""
        used: dict[str, date] = {}
        for post in self.recent_posts(today, days):
            for pid in post.products:
                used[pid] = max(used.get(pid, post.date), post.date)
        since = today - timedelta(days=days)
        for p in self.products():
            if p.last_used and since <= p.last_used < today:
                used[p.id] = max(used.get(p.id, p.last_used), p.last_used)
        return used
