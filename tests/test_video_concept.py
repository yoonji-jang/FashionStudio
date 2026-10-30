from datetime import date
from pathlib import Path

import pytest
import yaml

from core.profile import load_profile
from core.store import Store
from tests.test_flow import FakeTg, _concepts
from tools import video_concept as vc


def test_reply():
    assert vc.reply("2", 3) == 2
    assert vc.reply("3번", 3) == 3
    assert vc.reply("1번으로", 3) == 1
    assert vc.reply("4", 3) is None  # 없는 번호는 고칠 점으로
    assert vc.reply("다시", 3) == "new"
    assert vc.reply("2번인데 배경은 바다로", 3) is None


def test_plan_file():
    root = Path("r")
    assert (
        vc.plan_file(root, "2026-10-28-run398/chosen")
        == root / "2026-10-28-run398/picks/chosen.yaml"
    )
    assert vc.plan_file(root, "2026-10-15-run7/3") == root / "2026-10-15-run7/picks/p3.yaml"


def test_save_checks_shots(tmp_path):
    profile = load_profile("women")
    store = Store("women", root=tmp_path)
    with pytest.raises(ValueError, match="고른 벌 수"):
        vc.save(profile, store, date(2026, 10, 15), _concepts(looks=2), looks=6)
    with pytest.raises(ValueError, match="빠짐없이"):
        bad = _concepts(1)[0].model_dump()
        bad["shots"][1]["look"] = 5
        vc.Concept.model_validate(bad)
    cs = vc.save(profile, store, date(2026, 10, 15), _concepts(5), looks=2, tg=FakeTg())
    assert len(cs.concepts) == profile.video.concepts and vc.load(store) == cs


def test_context_and_cli(tmp_path, monkeypatch):
    plan = {"looks": [
        {"items": [{"name": "퍼 자켓", "desc": "그레이 퍼 자켓 (grey fur jacket)"}],
         "requests": ["시스루 스타킹"]},
        {"items": [{"name": "니트", "desc": "블랙 니트 (black knit)"}]},
    ]}  # fmt: skip
    store = Store("women", root=tmp_path)
    text = vc.context("women", date(2026, 10, 15), plan, "춤 컨셉으로", store)
    assert "LOOK 1: 그레이 퍼 자켓" in text and "요청: 시스루 스타킹" in text
    assert "카메라는 멈춰" in text and "춤 컨셉으로" in text
    assert len(vc.looks_of(plan)) == 2 and len(vc.looks_of({"items": [{"name": "a"}]})) == 1
    # save CLI: {concepts, chosen} 형식
    f = tmp_path / "c.yaml"
    f.write_text(yaml.safe_dump(
        {"concepts": [c.model_dump(mode="json") for c in _concepts()], "chosen": 2},
        allow_unicode=True,
    ), encoding="utf-8")  # fmt: skip
    monkeypatch.setattr(vc, "Store", lambda account: store)
    assert vc.main(["save", "--account", "women", "--date", "2026-10-15", "--file", str(f),
                    "--looks", "2"]) == 0  # fmt: skip
    assert vc.load(store).chosen == 2
