import json

from tools import progress


class FakeTg:
    def __init__(self):
        self.sent = []

    def send(self, text, **kw):
        self.sent.append(text)
        return 1


def test_note_keeps_start_for_same_stage(tmp_path, monkeypatch):
    f = tmp_path / "p.json"
    monkeypatch.setattr(progress.time, "time", lambda: 1000.0)
    progress.note("참고 코디 따라 짜기", 1, 10, f)
    monkeypatch.setattr(progress.time, "time", lambda: 1600.0)
    progress.note("참고 코디 따라 짜기", 3, 10, f)
    data = json.loads(f.read_text())
    assert data["started"] == 1000.0 and data["done"] == 3 and data["at"] == 1600.0
    progress.note("검사", path=f)
    assert progress.read(f)["started"] == 1600.0


def test_eta_and_message():
    info = {"text": "참고 코디 따라 짜기", "done": 3, "total": 11, "at": 3500.0, "started": 1700.0}
    # 3벌에 30분 → 남은 8벌 80분
    assert progress.eta(info, 3500.0) == 4800.0
    text = progress.message(
        "[women] 코디 후보 만들기", progress.Snapshot(3600, "Claude 코디 후보", info, 9000), 3500.0
    )
    assert "1시간째 진행 중" in text
    assert "지금 단계: Claude 코디 후보" in text
    assert "(3/11)" in text and "약 1시간 20분 더" in text
    unknown = progress.message("x", progress.Snapshot(3600, "", {}, 9000), 3600.0)
    assert "1시간 30분 뒤 시간 제한" in unknown


def test_watch_sends_every_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRESS_FILE", str(tmp_path / "p.json"))
    monkeypatch.setattr(progress, "current_step", lambda: "수집")
    tg, calls = FakeTg(), []

    def sleep(sec):
        calls.append(sec)
        if len(calls) > 2:
            raise KeyboardInterrupt

    try:
        progress.watch("[women] 상품 수집", 3600, None, tg, sleep)
    except KeyboardInterrupt:
        pass
    assert calls == [3600, 3600, 3600] and len(tg.sent) == 2
    assert "지금 단계: 수집" in tg.sent[0]


def test_collect_env_defaults(monkeypatch):
    from tools import collect

    seen = {}
    monkeypatch.setattr(
        collect, "collect", lambda *a, **k: seen.update(k) or collect.Report("women", None)
    )
    monkeypatch.setenv("COLLECT_MINUTES", "10")
    monkeypatch.setenv("COLLECT_NO_RECHECK", "1")
    try:
        collect.main(["--account", "women", "--category", "상의", "--query", "캐미"])
    except Exception:  # noqa: BLE001 - 보고 출력은 이 시험의 관심이 아님
        pass
    assert seen["minutes"] == 10.0 and seen["recheck"] is False
