"""계정 프로필 로더와 검증.

사용법: python -m core.profile women
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from core.models import Approval, Category, Monetize

REPO_ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS_DIR = REPO_ROOT / "accounts"
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class Audience(BaseModel):
    gender: Literal["women", "men", "unisex"]
    age_range: tuple[int, int]
    description: str


class Style(BaseModel):
    keywords: list[str] = Field(min_length=1)
    palette: list[str] = []
    avoid: list[str] = []


class SlotRule(BaseModel):
    category: Category
    required: bool = True


class Outfit(BaseModel):
    items_min: int = Field(default=5, ge=1)
    items_max: int = Field(default=7, le=10)  # Gemini 참고 이미지 한도 안쪽
    slots: list[SlotRule] = Field(min_length=1)
    reuse_cooldown_days: int = Field(default=14, ge=0)
    max_per_shop: int = Field(default=2, ge=1)


class ProductSourceConfig(BaseModel):
    name: str  # sources/<name>.py
    min_rating: float = Field(default=4.5, ge=0, le=5)
    min_reviews: int = Field(default=20, ge=0)
    prefer_flat_shot: bool = True


class Visual(BaseModel):
    backdrop: str  # 배경 묘사 (프롬프트에 들어감)
    backdrop_ref: str | None = None  # accounts/<id>/ 기준 참고 이미지 파일명
    feed_ratio: Literal["4:5", "1:1"] = "4:5"
    font: str  # assets/fonts/ 기준 파일명
    title_font_size: int = Field(default=64, gt=0)
    title_line_spacing: float = Field(default=1.25, gt=0)
    gradient_height: float = Field(default=0.35, gt=0, le=1)
    gradient_opacity: float = Field(default=0.45, ge=0, le=1)
    logo_text: str


class Schedule(BaseModel):
    timezone: str = "Asia/Seoul"
    generate_at: str = Field(pattern=HHMM)
    publish_at: str = Field(pattern=HHMM)
    reels_at: str | None = Field(default=None, pattern=HHMM)


class Instagram(BaseModel):
    token_secret: str  # GitHub Secret 이름 (값이 아님)
    user_id_secret: str


class Profile(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    display_name: str
    language: str = "ko"
    audience: Audience
    style: Style
    outfit: Outfit
    product_source: ProductSourceConfig
    visual: Visual
    schedule: Schedule
    monetize: Monetize = "none"
    approval: Approval = "required"
    instagram: Instagram


class AccountIndex(BaseModel):
    accounts: list[str]


def account_dir(account: str, root: Path = ACCOUNTS_DIR) -> Path:
    return root / account


def load_profile(account: str, root: Path = ACCOUNTS_DIR) -> Profile:
    path = account_dir(account, root) / "profile.yaml"
    if not path.exists():
        raise FileNotFoundError(f"프로필이 없습니다: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profile = Profile.model_validate(data)
    if profile.id != account:
        raise ValueError(f"폴더 이름({account})과 profile.id({profile.id})가 다릅니다")
    if profile.outfit.items_min > profile.outfit.items_max:
        raise ValueError("outfit.items_min이 items_max보다 큽니다")
    return profile


def load_voice(account: str, root: Path = ACCOUNTS_DIR) -> str:
    path = account_dir(account, root) / "voice.md"
    if not path.exists():
        raise FileNotFoundError(f"말투 파일이 없습니다: {path}")
    return path.read_text(encoding="utf-8")


def load_index(root: Path = ACCOUNTS_DIR) -> AccountIndex:
    data = yaml.safe_load((root / "index.yaml").read_text(encoding="utf-8")) or {}
    return AccountIndex.model_validate(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="계정 프로필을 검증하고 출력합니다")
    parser.add_argument("account", nargs="?", help="계정 id (생략하면 index.yaml의 모든 계정)")
    args = parser.parse_args(argv)

    accounts = [args.account] if args.account else load_index().accounts
    ok = True
    for account in accounts:
        try:
            profile = load_profile(account)
            load_voice(account)
        except (FileNotFoundError, ValueError, ValidationError) as e:
            ok = False
            print(f"[실패] {account}\n{e}", file=sys.stderr)
            continue
        print(f"[통과] {account}")
        print(yaml.safe_dump(profile.model_dump(mode="json"), allow_unicode=True, sort_keys=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
