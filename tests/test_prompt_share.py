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
