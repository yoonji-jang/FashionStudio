"""코디컷 지시문·참고판 공유 (tools.prompt_share, 2026-10-14)."""

from pathlib import Path

import yaml

from tools import outfit_gen, prompt_share


def test_build_writes_panels_and_prompt_per_look(tmp_path: Path, monkeypatch):
    results = tmp_path / "res"
    plan = yaml.safe_load(Path("samples/women/plan_2026-09-30.yaml").read_text(encoding="utf-8"))
    for n in (1, 2):
        d = results / "looks" / str(n)
        (d / "outfit").mkdir(parents=True)
        (d / "plan.yaml").write_text(yaml.safe_dump(plan, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(outfit_gen, "reference_panels", lambda *a, **k: [b"a", b"b"])
    monkeypatch.setattr(outfit_gen, "worn_ref_panel", lambda *a, **k: b"worn")
    monkeypatch.setattr(outfit_gen, "build_prompt", lambda *a, **k: "PROMPT")
    made = prompt_share.build(results, "women", tmp_path / "out")
    assert len(made) == 2  # 지시문은 지금 코드로 새로 만든다
    n, files, prompt = made[0]
    assert n == 1 and [f.read_bytes() for f in files] == [b"a", b"b", b"worn"]
    assert prompt.read_text(encoding="utf-8") == f"PROMPT\n{outfit_gen.WORN_REF}\n"


def test_build_reads_candidate_picks_and_one_look(tmp_path: Path, monkeypatch):
    """후보만 있는 회차는 picks/p1.yaml 의 벌들, <폴더>/<번호> 는 그 벌만 이어지는 번호로."""
    plan = yaml.safe_load(Path("samples/women/plan_2026-09-30.yaml").read_text(encoding="utf-8"))
    look = {"items": plan["items"], "styling": "worn open"}
    picks = dict(plan, items=[], looks=[look, look])
    (tmp_path / "cand" / "picks").mkdir(parents=True)
    (tmp_path / "cand" / "picks" / "p1.yaml").write_text(
        yaml.safe_dump(picks, allow_unicode=True), encoding="utf-8"
    )
    for n in (1, 2):
        d = tmp_path / "old" / "looks" / str(n)
        d.mkdir(parents=True)
        (d / "plan.yaml").write_text(yaml.safe_dump(plan, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(outfit_gen, "reference_panels", lambda *a, **k: [b"a"])
    monkeypatch.setattr(outfit_gen, "worn_ref_panel", lambda *a, **k: None)
    monkeypatch.setattr(outfit_gen, "build_prompt", lambda *a, **k: "PROMPT")
    sent = []
    monkeypatch.setattr(prompt_share, "build", _record(prompt_share.build, sent))
    assert (
        prompt_share.main(
            [
                "--account",
                "women",
                "--root",
                str(tmp_path),
                "--results",
                "cand+old/2",
                "--out",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )
    assert [n for n, _, _ in sent] == [1, 2, 3]
    assert (tmp_path / "out" / "look3" / "LOOK3_prompt.txt").read_text(encoding="utf-8") == "PROMPT"


def _record(fn, sent):
    def wrapped(*a, **k):
        made = fn(*a, **k)
        sent.extend(made)
        return made

    return wrapped
