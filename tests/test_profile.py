import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from core.profile import ACCOUNTS_DIR, load_index, load_profile, load_voice, main


def test_index_accounts_all_load():
    index = load_index()
    assert "women" in index.accounts
    for account in index.accounts:
        profile = load_profile(account)
        assert profile.id == account
        assert load_voice(account).strip()


def test_women_profile_values():
    profile = load_profile("women")
    assert profile.audience.gender == "women"
    assert profile.monetize == "none"  # 공유리워드 링크는 사용자가 직접 달기로 함 (2026-09-30)
    assert profile.comment_tags.enabled and profile.link_page.send_links
    assert profile.approval == "required"
    assert profile.outfit.items_min <= profile.outfit.items_max
    assert profile.instagram.token_secret == "IG_WOMEN_TOKEN"


def test_template_is_valid():
    assert load_profile("_template").id == "_template"


def _copy_template(tmp_path: Path, account: str) -> dict:
    dest = tmp_path / account
    shutil.copytree(ACCOUNTS_DIR / "_template", dest)
    data = yaml.safe_load((dest / "profile.yaml").read_text(encoding="utf-8"))
    data["id"] = account
    return data


def _write(tmp_path: Path, account: str, data: dict) -> None:
    path = tmp_path / account / "profile.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_new_account_from_template(tmp_path):
    data = _copy_template(tmp_path, "men")
    data["audience"]["gender"] = "men"
    _write(tmp_path, "men", data)
    assert load_profile("men", root=tmp_path).audience.gender == "men"


def test_id_must_match_folder(tmp_path):
    data = _copy_template(tmp_path, "men")
    data["id"] = "women"
    _write(tmp_path, "men", data)
    with pytest.raises(ValueError, match="폴더 이름"):
        load_profile("men", root=tmp_path)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("monetize",), "affiliate"),
        (("schedule", "publish_at"), "7:23"),
        (("outfit", "items_max"), 11),
        (("audience", "gender"), "kids"),
        (("themes", "type_mix"), {"뉴스": 7}),
    ],
)
def test_invalid_values_rejected(tmp_path, path, value):
    data = _copy_template(tmp_path, "bad")
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    _write(tmp_path, "bad", data)
    with pytest.raises(ValidationError):
        load_profile("bad", root=tmp_path)


def test_theme_mix_must_sum_to_per_week(tmp_path):
    data = _copy_template(tmp_path, "bad")
    data["themes"]["per_week"] = 5
    _write(tmp_path, "bad", data)
    with pytest.raises(ValueError, match="type_mix"):
        load_profile("bad", root=tmp_path)


def test_missing_profile(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_profile("nobody", root=tmp_path)


def test_cli(capsys):
    assert main(["women"]) == 0
    assert "[통과] women" in capsys.readouterr().out
