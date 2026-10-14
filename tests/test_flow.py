from datetime import UTC, date, datetime

import pytest
import yaml
from PIL import Image

from core.models import PickRecord, PostRecord, Theme
from core.store import Store
from tests.test_plan import CATS, daily, fake_photos, product  # noqa: F401 - fixture
from tools import flow, generate, notify

CHAT = "42"
NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)  # 서울 10/15 12:00
T0 = int(NOW.timestamp())


class FakeTg:
    def __init__(self):
        self.chat_id = CHAT
        self.ups: list[dict] = []
        self.sent: list[str] = []
        self.got: list[str] = []

    def updates(self):
        return self.ups

    def send(self, text, **kw):
        self.sent.append(text)
        return len(self.sent)

    def download(self, file_id, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jpg")
        self.got.append(file_id)
        return dest

    def text(self, uid, text, at):
        self.ups.append(
            {"update_id": uid, "message": {"date": at, "chat": {"id": 42}, "text": text}}
        )

    def photo(self, uid, file_id, at, caption=""):
        sizes = [{"file_id": "small", "width": 90}, {"file_id": file_id, "width": 1280}]
        self.ups.append(
            {
                "update_id": uid,
                "message": {"date": at, "chat": {"id": 42}, "photo": sizes, "caption": caption},
            }
        )


def theme(n: int) -> Theme:
    return Theme(
        date=date(2026, 10, 15),
        title_hint=f"주제 {n}",
        mood="가을",
        slots=[{"category": "상의", "desc": "니트"}],
        type="상황",
    )


@pytest.fixture
def env(tmp_path):
    store = Store("women", root=tmp_path / "data")
    results = tmp_path / "results"
    results.mkdir()
    sent: list[tuple[str, dict]] = []

    def send(workflow, **inputs):
        sent.append((workflow, inputs))

    return store, results, sent, send


def tick(env, tg, at):
    store, results, _, send = env
    return flow.tick("women", results, tg, send, datetime.fromtimestamp(at, UTC), store=store)


def test_theme_pick_scene_render(env):
    store, results, sent, _ = env
    tg = FakeTg()
    # 처음: 다음 게시 날짜로 주제 제안 요청
    tick(env, tg, T0)
    assert sent[-1] == ("theme-options.yml", {"account": "women", "date": "2026-10-15", "hint": ""})
    assert flow.load_state(results, "women").stage == "theme_wait"
    # 제안이 오기 전에는 기다림
    assert tick(env, tg, T0 + 600) == []
    profile = flow.load_profile("women")
    opts = flow.save_options(
        profile, store, date(2026, 10, 15), [theme(1), theme(2)], "", tg, T0 + 900
    )
    assert "1. [상황] 주제 1" in tg.sent[-1] and opts.message_ids == [1]
    tg.text(1, "2", opts.sent_at + 10)
    tick(env, tg, opts.sent_at + 100)
    st = flow.load_state(results, "women")
    assert st.stage == "pick_wait"
    assert [t.title_hint for t in store.themes()] == ["주제 2"]
    wf, inputs = sent[-1]
    assert wf == "daily.yml" and inputs["mode"] == "candidates" and inputs["collect"] is True
    assert '"title_hint": "주제 2"' in inputs["pick"]
    assert "장면 사진 1장" in tg.sent[-1]
    # 장면 사진 먼저
    tg.photo(2, "BIG", st.theme_at + 50, caption="슬릭번")
    tick(env, tg, st.theme_at + 100)
    st = flow.load_state(results, "women")
    assert st.scene == "flow/women/2026-10-15/scene.jpg" and tg.got == ["BIG"]
    assert (results / st.scene).exists() and st.stage == "pick_wait"
    assert (results / "flow/women/2026-10-15/scene.txt").read_text(encoding="utf-8") == "슬릭번\n"
    # 후보가 오고 고르면 만들기 요청 (지난 주제 때 후보는 보지 않음)
    old = results / "2026-10-15-run1"
    (old / "picks").mkdir(parents=True)
    (old / "picks" / "pick.json").write_text(
        PickRecord(account="women", date=date(2026, 10, 15), run=old.name, count=3,
                   sent_at=st.theme_at - 3600, message_ids=[]).model_dump_json()
    )  # fmt: skip
    run = results / "2026-10-15-run7"
    (run / "picks").mkdir(parents=True)
    (run / "picks" / "pick.json").write_text(
        PickRecord(account="women", date=date(2026, 10, 15), run=run.name, count=3,
                   sent_at=st.theme_at + 200, message_ids=[]).model_dump_json()
    )  # fmt: skip
    tg.text(3, "3", st.theme_at + 300)
    tick(env, tg, st.theme_at + 400)
    st = flow.load_state(results, "women")
    assert st.pick == "2026-10-15-run7/3" and st.stage == "rendering"
    assert sent[-1] == (
        "daily.yml",
        {"account": "women", "date": "2026-10-15", "mode": "render",
         "pick": "2026-10-15-run7/3", "sandbox": False},
    )  # fmt: skip
    # 3시간 넘게 미리보기가 없으면 한 번 알림
    n = len(tg.sent)
    tick(env, tg, st.asked_at + 4 * 3600)
    tick(env, tg, st.asked_at + 5 * 3600)
    assert len(tg.sent) == n + 1 and "미리보기" in tg.sent[-1]


def test_user_theme_and_again(env):
    store, results, sent, _ = env
    tg = FakeTg()
    tick(env, tg, T0)
    profile = flow.load_profile("women")
    opts = flow.save_options(profile, store, date(2026, 10, 15), [theme(1)], "", tg, T0 + 100)
    tg.text(1, "다시", opts.sent_at + 5)
    tick(env, tg, opts.sent_at + 60)
    assert sent[-1][1]["hint"] == "" and len(sent) == 2
    st = flow.load_state(results, "women")
    assert st.stage == "theme_wait"
    opts = flow.save_options(
        profile, store, date(2026, 10, 15), [theme(3)], "", tg, st.asked_at + 100
    )
    tg.text(2, "/theme 니트 레이어드", opts.sent_at + 5)
    tick(env, tg, opts.sent_at + 60)
    assert sent[-1] == (
        "theme-options.yml",
        {"account": "women", "date": "2026-10-15", "hint": "니트 레이어드"},
    )
    st = flow.load_state(results, "women")
    # 사용자 주제로 짠 제안은 묻지 않고 바로 진행
    opts = flow.save_options(
        profile, store, date(2026, 10, 15), [theme(9)], "니트 레이어드", tg, st.asked_at + 100
    )
    tick(env, tg, opts.sent_at + 60)
    assert flow.load_state(results, "women").stage == "pick_wait"
    assert store.themes()[0].title_hint == "주제 9"


def test_published_starts_next_theme(env):
    store, results, sent, _ = env
    st = flow.FlowState(account="women", stage="publishing", date=date(2026, 10, 15), asked_at=T0)
    flow.save_state(results, st)
    tg = FakeTg()
    assert tick(env, tg, T0 + 600) == []
    store.save_posts([PostRecord(date=date(2026, 10, 15), title="t")])
    lines = tick(env, tg, T0 + 1200)
    assert lines[0] == "게시 확인"
    st = flow.load_state(results, "women")
    assert st.stage == "theme_wait" and st.date == date(2026, 10, 16)
    assert sent[-1][1]["date"] == "2026-10-16"


def test_options_cli_and_use_theme(env, tmp_path, monkeypatch):
    store, *_ = env
    monkeypatch.setattr(flow, "Store", lambda account: store)
    f = tmp_path / "opts.yaml"
    raw = [theme(n).model_dump(mode="json") for n in range(1, 6)]
    f.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    assert flow.main(["options", "--date", "2026-10-15", "--file", str(f)]) == 0
    assert len(flow.load_options(store).options) == 3  # profile flow.theme_options
    assert flow.main(["options", "--date", "2026-10-15", "--file", str(f), "--hint", "x"]) == 0
    assert len(flow.load_options(store).options) == 1
    js = '{"theme": ' + theme(4).model_dump_json() + "}"
    assert flow.main(["use-theme", "--json", js]) == 0
    assert store.themes()[0].title_hint == "주제 4"


def test_photo_file_id_and_slim_update():
    msg = {
        "photo": [{"file_id": "a", "width": 90}, {"file_id": "b", "width": 1280}],
        "caption": "장면",
    }
    assert notify.photo_file_id(msg) == "b"
    assert notify.photo_file_id({"document": {"file_id": "d", "mime_type": "image/jpeg"}}) == "d"
    assert (
        notify.photo_file_id({"document": {"file_id": "d", "mime_type": "application/pdf"}}) is None
    )
    slim = notify.slim_update({"update_id": 1, "message": {"date": 1, "chat": {"id": 42}, **msg}})
    assert notify.photo_file_id(slim["message"]) == "b"
    assert slim["message"]["caption"] == "장면"


def test_trim_border():
    im = Image.new("RGB", (400, 500), (0, 0, 0))
    im.paste(Image.new("RGB", (360, 460), (180, 160, 140)), (20, 20))
    out = generate.trim_border(im)
    assert out.size[0] <= 370 and out.size[1] <= 470
    assert out.getpixel((2, 2)) != (0, 0, 0)
    plain = Image.new("RGB", (400, 500), (180, 160, 140))
    assert generate.trim_border(plain).size == (400, 500)


def test_scene_prompt(tmp_path):
    from tools import outfit_gen

    data = Store("women", root=tmp_path / "data")
    data.save_products([product(i, c) for i, c in enumerate(CATS, 1)])
    plan = daily().model_copy(
        update={"shot": "model", "scene_image": "x.jpg", "mood": "a mirror selfie", "pose": "P1"}
    )
    profile = flow.load_profile("women")
    products = {p.id: p for p in data.products()}
    text = outfit_gen.build_prompt(plan, products, profile, generate.load_config())
    assert "scene photo" in text and "a mirror selfie" in text and "P1" in text
    assert "$" not in text


def test_preview_ok_publishes_now(env, monkeypatch, tmp_path):
    from tools import feedback
    from tools import schedule as sched

    store, results, sent, _ = env
    out = results / "2026-10-15-run9"
    out.mkdir()
    rec = type("Rec", (), {"sent_at": T0 + 50})()
    monkeypatch.setattr(feedback, "latest_preview", lambda *a: out)
    monkeypatch.setattr(notify, "load_record", lambda o: rec)
    monkeypatch.setattr(sched, "cover_change", lambda *a: "")
    fixes = []
    monkeypatch.setattr(feedback, "check", lambda *a: fixes.pop() if fixes else None)
    monkeypatch.setattr(feedback, "waiting", lambda *a: False)
    decided = [None]
    monkeypatch.setattr(notify, "approval_now", lambda *a: decided[0])
    monkeypatch.setattr(notify, "remember", lambda *a: True)
    st = flow.FlowState(account="women", stage="rendering", date=date(2026, 10, 15), asked_at=T0)
    flow.save_state(results, st)
    tg = FakeTg()
    tick(env, tg, T0 + 100)
    assert flow.load_state(results, "women").stage == "preview_wait" and sent == []
    # /fix 면 고쳐 만들기 요청 후 다시 미리보기 기다림
    fixes.append({"run": out.name, "round": 1, "fixes": []})
    tick(env, tg, T0 + 200)
    assert sent[-1][1]["mode"] == "fix" and flow.load_state(results, "women").stage == "rendering"
    rec.sent_at = T0 + 300
    decided[0] = "ok"
    tick(env, tg, T0 + 400)
    wf, inputs = sent[-1]
    assert wf == "publish.yml" and inputs["run"] == out.name and inputs["force"] is True
    assert flow.load_state(results, "women").stage == "publishing"
    assert "지금 게시" in tg.sent[-1]


def test_redo_request(tmp_path):
    from tools import pick

    js = '{"again": "2026-10-16-run263", "keep": [4], "count": 8, "note": "더 채워"}'
    assert flow.main(["use-theme", "--json", js, "--out", str(tmp_path)]) == 0
    req = (tmp_path / "picks_request.json").read_text(encoding="utf-8")
    assert '"keep": [\n    4\n  ]' in req and '"count": 8' in req
    profile = flow.load_profile("women")
    assert pick._wanted(tmp_path, profile) == 8
    assert pick._wanted(tmp_path / "none", profile) == profile.looks.count
