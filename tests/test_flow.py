import json
from datetime import UTC, date, datetime

import pytest
import yaml
from PIL import Image

from core.models import FeedbackEntry, PickRecord, PostRecord, Theme
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


def _profile(
    week: int, ref_pick: int = 0, review: bool = False, rules: bool = False, search: bool = False
):
    real = flow.load_profile

    def load(account):
        p = real(account)
        f = p.flow.model_copy(
            update={"week_options": week, "ref_pick": ref_pick, "theme_review": review,
                    "rules_after_post": rules}
        )  # fmt: skip
        # 주제별 이미지 검색은 search 일 때만 (그 밖의 시험은 예전 쇼핑몰 수집 흐름)
        rs = p.themes.ref_search.model_copy(
            update={"image_search": ["daum"] if search else [], "weekly_crawl": True}
        )
        themes = p.themes.model_copy(update={"ref_search": rs})
        return p.model_copy(update={"flow": f, "themes": themes})

    return load


@pytest.fixture(autouse=True)
def day_mode(monkeypatch):
    """게시물마다 주제를 묻는 흐름으로 시험한다 (주간 후보 시험은 week_mode 로 바꿈)."""
    monkeypatch.setattr(flow, "load_profile", _profile(0))


@pytest.fixture
def week_mode(monkeypatch):
    monkeypatch.setattr(flow, "load_profile", _profile(10))


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
    assert "장면 사진도" in tg.sent[-1]
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
    assert "barely visible" in text
    # 2026-10-26 사용자: 얼굴 참고를 받았으면 그 특징의 얼굴을 보이게
    face = plan.model_copy(update={"face": "big double-lidded eyes, bold makeup, full lips."})
    text = outfit_gen.build_prompt(face, products, profile, generate.load_config())
    assert "full lips. Her face is small and slim" in text and "barely visible" not in text
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


def test_preview_published_by_hand(env, monkeypatch):
    """2026-10-28: /ok 없이 publish 를 직접 돌려 게시했으면 게시 확인 단계로 넘어간다."""
    from tools import feedback

    store, results, sent, _ = env
    out = results / "2026-10-27-run380"
    out.mkdir()
    (out / "published.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(feedback, "latest_preview", lambda *a: out)
    monkeypatch.setattr(notify, "load_record", lambda o: object())
    st = flow.FlowState(account="women", stage="preview_wait", date=date(2026, 10, 27), asked_at=T0)
    flow.save_state(results, st)
    tick(env, FakeTg(), T0 + 100)
    assert flow.load_state(results, "women").stage == "publishing" and sent == []


def test_redo_request(tmp_path):
    from tools import pick

    js = '{"again": "2026-10-16-run263", "keep": [4], "count": 8, "note": "더 채워"}'
    assert flow.main(["use-theme", "--json", js, "--out", str(tmp_path)]) == 0
    req = (tmp_path / "picks_request.json").read_text(encoding="utf-8")
    assert '"keep": [\n    4\n  ]' in req and '"count": 8' in req
    profile = flow.load_profile("women")
    assert pick._wanted(tmp_path, profile) == 8
    assert pick._wanted(tmp_path / "none", profile) == profile.looks.count


def test_scene_photos_batches():
    """2026-10-15 사용자: 장면 사진 여러 장. 앨범이나 3분 안에 이어 보낸 사진은 한 묶음,
    따로 다시 보낸 사진은 앞 묶음을 바꾼다."""
    tg = FakeTg()
    tg.photo(1, "OLD", T0 + 10)
    tg.photo(2, "A", T0 + 1000)
    tg.photo(3, "B", T0 + 1100, caption="슬릭번")
    tg.photo(4, "C", T0 + 1200)
    batch = flow.scene_photos(tg.ups, CHAT, T0, 0)
    assert [p.file_id for p in batch] == ["A", "B", "C"]
    assert flow.scene_photos(tg.ups, CHAT, T0, 4) == []
    # 앨범은 간격과 상관없이 한 묶음
    tg = FakeTg()
    tg.photo(1, "X", T0)
    tg.photo(2, "Y", T0 + 500)
    tg.ups[0]["message"]["media_group_id"] = tg.ups[1]["message"]["media_group_id"] = "g1"
    assert [p.file_id for p in flow.scene_photos(tg.ups, CHAT, T0, 0)] == ["X", "Y"]


def test_scene_photos_saved_and_joined(env):
    store, results, sent, _ = env
    tg = FakeTg()
    st = flow.FlowState(account="women", stage="pick_wait", date=date(2026, 10, 15), theme_at=T0)
    flow.save_state(results, st)
    folder = results / "flow/women/2026-10-15"
    tg.photo(1, "A", T0 + 10, caption="슬릭번")
    tg.photo(2, "B", T0 + 20)
    tick(env, tg, T0 + 30)
    st = flow.load_state(results, "women")
    assert st.scenes == ["flow/women/2026-10-15/scene.jpg", "flow/women/2026-10-15/scene_2.jpg"]
    assert st.scene == st.scenes[0] and "2장" in tg.sent[-1]
    assert (folder / "scene.txt").read_text(encoding="utf-8") == "슬릭번\n"
    # 확인 사이에 이어 보낸 사진은 더한다 (말은 그대로)
    tg.photo(3, "C", T0 + 100)
    tick(env, tg, T0 + 200)
    st = flow.load_state(results, "women")
    assert len(st.scenes) == 3 and st.scenes[-1].endswith("scene_3.jpg")
    assert (folder / "scene.txt").exists()
    # 한참 뒤 따로 보낸 한 장은 묶음을 바꾼다
    tg.photo(4, "D", T0 + 5000)
    tick(env, tg, T0 + 5100)
    st = flow.load_state(results, "women")
    assert st.scenes == ["flow/women/2026-10-15/scene.jpg"] and tg.got[-1] == "D"
    # 설명 없이 다시 보낸 사진은 앞서 보낸 설명을 그대로 쓴다 (2026-10-27)
    assert not list(folder.glob("scene_*.jpg")) and (folder / "scene.txt").exists()


def test_look_scene_image_override():
    """벌마다 고른 장면 사진(Look.scene_image)이 없으면 기획의 첫 장면 사진."""
    from core.models import DailyPlan, Look

    base = daily().model_dump(mode="json", exclude_none=True)
    items = base.pop("items")
    looks = [Look(items=items, scene_image="s2.jpg").model_dump(), Look(items=items).model_dump()]
    plan = DailyPlan.model_validate(
        {**base, "shot": "model", "scene_image": "s1.jpg", "looks": looks}
    )
    assert plan.look_plan(0).scene_image == "s2.jpg"
    assert plan.look_plan(1).scene_image == "s1.jpg"


def test_week_options_feedback_pick_and_queue(env, week_mode):
    store, results, sent, _ = env
    tg = FakeTg()
    tick(env, tg, T0)
    assert sent[-1] == (
        "theme-options.yml",
        {"account": "women", "date": "2026-10-15", "hint": "", "week": True, "feedback": ""},
    )
    profile = flow.load_profile("women")
    themes = [theme(n) for n in range(1, 11)]
    opts = flow.save_options(
        profile, store, date(2026, 10, 15), themes, "", tg, T0 + 100, week=True
    )
    assert "주간 주제 후보 10개" in tg.sent[-1] and "10. [상황] 주제 10" in tg.sent[-1]
    # 다른 명령(/ok)은 읽지 않고, 고칠 점은 여러 글을 모아 Claude 에게
    tg.text(1, "/ok", opts.sent_at + 5)
    tg.text(2, "3번은 데이트룩으로", opts.sent_at + 10)
    tg.text(3, "/theme 5번 빼고 롱스커트 코디 하나 더", opts.sent_at + 20)
    tick(env, tg, opts.sent_at + 60)
    wf, inputs = sent[-1]
    assert wf == "theme-options.yml" and inputs["week"] is True
    assert inputs["feedback"] == "3번은 데이트룩으로\n5번 빼고 롱스커트 코디 하나 더"
    st = flow.load_state(results, "women")
    assert st.stage == "theme_wait" and st.read_upto == opts.sent_at + 20
    # 고친 후보가 오기 전에는 기다리고, 그 사이 보낸 번호는 고친 후보에 쓴다
    tg.text(4, "1 3 4 5 7 8 10", st.asked_at + 30)
    assert tick(env, tg, st.asked_at + 40) == []
    opts = flow.save_options(
        profile, store, date(2026, 10, 15), [theme(n + 10) for n in range(1, 11)], "", tg,
        st.asked_at + 100, week=True,
    )  # fmt: skip
    tick(env, tg, opts.sent_at + 60)
    st = flow.load_state(results, "women")
    # 정한 주제로 참고 코디를 먼저 모은다 (2026-10-19)
    assert st.stage == "refs_wait" and "참고 코디를 모으고" in tg.sent[-1]
    assert "주간 주제 7개를 정했어요" in tg.sent[-1]
    wf, inputs = sent[-1]
    assert wf == "themes.yml" and inputs["refs_only"] is True
    assert inputs["week_themes"].splitlines() == [
        f"주제 {n} — 무드: 가을" for n in (11, 13, 14, 15, 17, 18, 20)
    ]
    assert tick(env, tg, opts.sent_at + 3600) == []
    # 수집이 끝나면(refs_done.json) 첫 주제 후보
    (store.dir / "refs_done.json").write_text(f'{{"done_at": {opts.sent_at + 4000}}}')
    tick(env, tg, opts.sent_at + 4000)
    st = flow.load_state(results, "women")
    assert st.stage == "pick_wait"
    assert [t.title_hint for t in st.week] == [f"주제 {n}" for n in (13, 14, 15, 17, 18, 20)]
    assert store.themes()[0].title_hint == "주제 11" and "남은 주제 6개" in tg.sent[-1]
    assert sent[-1][0] == "daily.yml" and '"주제 11"' in sent[-1][1]["pick"]
    # 게시되면 다음 주간 주제를 묻지 않고 바로 쓴다
    st.stage = "publishing"
    flow.save_state(results, st)
    store.save_posts([PostRecord(date=date(2026, 10, 15), title="t")])
    tick(env, tg, opts.sent_at + 7200)
    st = flow.load_state(results, "women")
    assert st.stage == "pick_wait" and st.date == date(2026, 10, 16) and len(st.week) == 5
    assert store.themes()[-1].title_hint == "주제 13" and "남은 주제 5개" in tg.sent[-1]


def test_week_options_bad_numbers_and_chosen(env, week_mode, tmp_path, monkeypatch):
    store, results, sent, _ = env
    tg = FakeTg()
    tick(env, tg, T0)
    profile = flow.load_profile("women")
    themes = [theme(n) for n in range(1, 11)]
    opts = flow.save_options(
        profile, store, date(2026, 10, 15), themes, "", tg, T0 + 100, week=True
    )
    tg.text(1, "1 2 11", opts.sent_at + 5)
    tick(env, tg, opts.sent_at + 60)
    assert "1~10" in tg.sent[-1] and flow.load_state(results, "women").stage == "theme_wait"
    # Claude 가 고치면서 쓸 번호까지 정한 후보 (--week, chosen): 보내지 않고 바로 정한다
    monkeypatch.setattr(flow, "Store", lambda account: store)
    f = tmp_path / "opts.yaml"
    raw = {"options": [t.model_dump(mode="json") for t in themes], "chosen": [2, 1]}
    f.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    n = len(tg.sent)
    assert flow.main(["options", "--date", "2026-10-15", "--file", str(f), "--week"]) == 0
    assert flow.load_options(store).chosen == [2, 1] and len(tg.sent) == n
    now = int(datetime.now(UTC).timestamp())
    tick(env, tg, now + 60)
    assert flow.load_state(results, "women").stage == "refs_wait"
    # 수집이 끝나지 않아도 REFS_WAIT 가 지나면 있는 참고 코디로 간다
    tick(env, tg, now + 60 + int(flow.REFS_WAIT.total_seconds()))
    st = flow.load_state(results, "women")
    assert st.stage == "pick_wait" and [t.title_hint for t in st.week] == ["주제 1"]
    assert "늦어져" in tg.sent[-2] and store.themes()[-1].title_hint == "주제 2"
    raw["chosen"] = [3, 3]
    f.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    assert flow.main(["options", "--date", "2026-10-15", "--file", str(f), "--week"]) == 1


@pytest.mark.looks
def test_ref_pick_album_then_candidates_from_chosen_refs(env, monkeypatch):
    # 2026-10-20 사용자: 주제마다 참고 코디 묶음을 먼저 보내고, 고른 번호로 후보를 만든다
    from tools import refs

    store, results, sent, _ = env
    monkeypatch.setattr(flow, "load_profile", _profile(10, ref_pick=48))
    albums = []

    def album(profile, store_, theme_, tg_, out):
        albums.append(theme_.title_hint)
        return [141, 203]

    monkeypatch.setattr(refs, "send_theme_album", album)
    monkeypatch.setattr(Store, "refs", lambda self: [type("R", (), {"n": n})() for n in (141, 203)])
    tg = FakeTg()
    st = flow.FlowState(account="women", stage="refs_wait", date=date(2026, 10, 15),
                        week=[theme(1), theme(2)], asked_at=T0)  # fmt: skip
    flow.save_state(results, st)
    (store.dir).mkdir(parents=True, exist_ok=True)
    (store.dir / "refs_done.json").write_text(f'{{"done_at": {T0 + 10}}}')
    tick(env, tg, T0 + 100)
    st = flow.load_state(results, "women")
    assert st.stage == "ref_pick_wait" and albums == ["주제 1"] and not sent
    assert "참고 코디를 보낼게요" in tg.sent[-1]
    # 번호가 아닌 글, 없는 번호
    tg.text(1, "음 고민중", T0 + 200)
    assert tick(env, tg, T0 + 250) == []
    tg.text(2, "141 999", T0 + 300)
    tick(env, tg, T0 + 350)
    assert "999번은 없어요" in tg.sent[-1] and not sent
    tg.text(3, "참고 203, 141번", T0 + 400)
    tick(env, tg, T0 + 450)
    st = flow.load_state(results, "women")
    assert st.stage == "pick_wait"
    wf, inputs = sent[-1]
    assert wf == "daily.yml" and inputs["mode"] == "candidates"
    req = __import__("json").loads(inputs["pick"])
    assert (
        req["refs"] == [203, 141] and req["count"] == 8 and req["theme"]["title_hint"] == "주제 1"
    )
    # 후보를 기다리는 동안 묶음을 다시 보내지 않는다
    tick(env, tg, T0 + 500)
    assert albums == ["주제 1"]


def test_ref_pick_more_refs(env, monkeypatch):
    # 2026-10-21 사용자: 묶음이 주제와 안 맞으면 "더 모아줘 화사하게" → 이 주제로 더 모아 다시 보냄
    from tools import refs

    store, results, sent, _ = env
    monkeypatch.setattr(flow, "load_profile", _profile(10, ref_pick=48))
    albums = []
    monkeypatch.setattr(
        refs, "send_theme_album", lambda p, s, t, tg, out: albums.append(t.title_hint) or [141]
    )
    flow.choose_theme(store, theme(1))
    st = flow.FlowState(account="women", stage="ref_pick_wait", date=date(2026, 10, 15),
                        week=[theme(2)], ref_pick_at=T0)  # fmt: skip
    flow.save_state(results, st)
    tg = FakeTg()
    tg.text(1, "더 모아줘 화사하게", T0 + 10)
    tick(env, tg, T0 + 100)
    st = flow.load_state(results, "women")
    wf, inputs = sent[-1]
    assert wf == "themes.yml" and inputs["refs_only"] is True and inputs["date"] == "2026-10-15"
    assert inputs["week_themes"] == "주제 1 — 무드: 가을 (요청: 화사하게)"
    assert st.stage == "refs_wait" and [t.title_hint for t in st.week] == ["주제 1", "주제 2"]
    assert "더 모을게요 (화사하게)" in tg.sent[-1]
    # 다 모으면 같은 주제로 묶음을 다시 보낸다
    (store.dir / "refs_done.json").write_text(f'{{"done_at": {T0 + 200}}}')
    tick(env, tg, T0 + 300)
    st = flow.load_state(results, "women")
    assert st.stage == "ref_pick_wait" and albums == ["주제 1"]
    assert [t.title_hint for t in st.week] == ["주제 2"]
    assert flow.MORE_REFS.match("참고 코디 다시 모아요") and not flow.MORE_REFS.match("141 203")


def test_ref_search_before_theme_then_album(env, monkeypatch):
    # 2026-10-26 사용자: 주제 직전에 그 주제로 참고 코디를 검색하고, 다 모으면 묶음을 보낸다
    from tools import refs

    store, results, sent, _ = env
    monkeypatch.setattr(flow, "load_profile", _profile(10, ref_pick=48, search=True))
    albums = []
    monkeypatch.setattr(
        refs, "send_theme_album", lambda p, s, t, tg, out: albums.append(t.title_hint) or [141]
    )
    st = flow.FlowState(account="women", stage="theme", week=[theme(1), theme(2)])
    flow.save_state(results, st)
    tg = FakeTg()
    tick(env, tg, T0)
    st = flow.load_state(results, "women")
    wf, inputs = sent[-1]
    assert wf == "themes.yml" and inputs["refs_only"] is True
    assert inputs["week_themes"] == "주제 1 — 무드: 가을" and "user_photos" not in inputs
    assert st.stage == "refs_wait" and [t.title_hint for t in st.week] == ["주제 1", "주제 2"]
    assert "검색해서 모으고" in tg.sent[-1] and not albums
    # 수집이 끝나면 다시 검색하지 않고 묶음을 보낸다
    store.dir.mkdir(parents=True, exist_ok=True)
    (store.dir / "refs_done.json").write_text(f'{{"done_at": {T0 + 200}}}')
    n = len(sent)
    tick(env, tg, T0 + 300)
    st = flow.load_state(results, "women")
    assert st.stage == "ref_pick_wait" and albums == ["주제 1"] and len(sent) == n
    assert [t.title_hint for t in st.week] == ["주제 2"]


def test_ref_pick_user_photos(env, monkeypatch):
    # 2026-10-26 사용자: 묶음이 마음에 안 들면 참고 사진을 직접 보낸다
    # (설명 "참고", 앨범은 한 장에만)
    from core.models import RefLook
    from tools import refs

    store, results, sent, _ = env
    monkeypatch.setattr(flow, "load_profile", _profile(10, ref_pick=48, search=True))
    monkeypatch.setattr(refs, "send_theme_album", lambda *a: [141])
    flow.choose_theme(store, theme(1))
    key = "2026-10-15|주제 1"
    flow.save_state(results, flow.FlowState(
        account="women", stage="ref_pick_wait", date=date(2026, 10, 15), week=[theme(2)],
        ref_pick_at=T0, theme_at=T0, searched=key,
    ))  # fmt: skip
    tg = FakeTg()
    tg.photo(1, "scene_a", T0 + 5)  # 장면 사진 (설명 없음)
    tg.photo(2, "ref_a", T0 + 10, caption="참고")
    tg.ups.append({"update_id": 3, "message": {
        "date": T0 + 10, "chat": {"id": 42}, "media_group_id": "g1",
        "photo": [{"file_id": "ref_b", "width": 1280}]}})  # fmt: skip
    tg.ups[1]["message"]["media_group_id"] = "g1"
    assert flow.ref_photos(tg.ups, CHAT, T0) == [(2, "ref_a"), (3, "ref_b")]
    assert [p.file_id for p in flow.scene_photos(tg.ups, CHAT, T0, 0)] == ["scene_a"]
    # 후보를 보낸 뒤 "참고" 로 보낸 사진은 장면 사진 (바로 앞 사진과 한 묶음)
    late = flow.scene_photos(tg.ups, CHAT, T0, 0, refs_until=T0 + 6)
    assert [p.file_id for p in late] == ["scene_a", "ref_a", "ref_b"]
    tick(env, tg, T0 + 100)
    st = flow.load_state(results, "women")
    wf, inputs = sent[-1]
    assert wf == "themes.yml" and json.loads(inputs["user_photos"]) == ["ref_a", "ref_b"]
    assert st.stage == "refs_wait" and st.user_refs == ["ref_a", "ref_b"] and st.searched == key
    # 옮겨 적고 수집이 끝나면 그 코디들로 바로 후보
    looks = [
        RefLook(n=n, name="x", source="사용자 사진", photo=f"tg:{fid}", added=date(2026, 10, 15),
                slots=[{"category": "상의", "color": "블랙", "desc": "니트", "query": "니트"},
                       {"category": "하의", "color": "블랙", "desc": "치마", "query": "치마"}])
        for n, fid in ((901, "ref_a"), (902, "ref_b"))
    ]  # fmt: skip
    monkeypatch.setattr(Store, "refs", lambda self: looks)
    (store.dir / "refs_done.json").write_text(f'{{"done_at": {T0 + 200}}}')
    tick(env, tg, T0 + 300)
    st = flow.load_state(results, "women")
    wf, inputs = sent[-1]
    req = json.loads(inputs["pick"])
    assert wf == "daily.yml" and req["refs"] == [901, 902] and st.stage == "pick_wait"
    assert st.user_refs == [] and any("보내 주신 참고 사진 2벌" in t for t in tg.sent)
    assert st.scene  # 장면 사진은 따로 받는다


def test_ref_pick_auto_and_existing_pick_wait(env, monkeypatch):
    from tools import refs

    store, results, sent, _ = env
    monkeypatch.setattr(flow, "load_profile", _profile(10, ref_pick=48))
    monkeypatch.setattr(refs, "send_theme_album", lambda *a: [141])
    monkeypatch.setattr(Store, "refs", lambda self: [])
    flow.choose_theme(store, theme(1))
    # 참고 고르기를 켜기 전에 후보를 기다리던 주제: 묶음을 먼저 보낸다
    flow.save_state(results, flow.FlowState(account="women", stage="pick_wait",
                                            date=date(2026, 10, 15), theme_at=T0))  # fmt: skip
    tg = FakeTg()
    tick(env, tg, T0 + 100)
    assert flow.load_state(results, "women").stage == "ref_pick_wait"
    tg.text(1, "알아서", T0 + 200)
    tick(env, tg, T0 + 300)
    req = __import__("json").loads(sent[-1][1]["pick"])
    assert "refs" not in req and "제가 골라서" in tg.sent[-1]


def _review_state(env, n: int = 4) -> flow.FlowState:
    store, results, _, _ = env
    st = flow.FlowState(
        account="women", stage="publishing", date=date(2026, 10, 15), asked_at=T0,
        week=[theme(i) for i in range(1, n + 1)],
    )  # fmt: skip
    flow.save_state(results, st)
    store.save_posts([PostRecord(date=date(2026, 10, 15), title="t")])
    return st


def test_theme_review_go_and_reorder(env, monkeypatch):
    # 2026-10-21 사용자: 주제 시작 전에 남은 주제 목록을 보여 주고 고칠 수 있게
    monkeypatch.setattr(flow, "load_profile", _profile(10, review=True))
    store, results, sent, _ = env
    _review_state(env)
    tg = FakeTg()
    lines = tick(env, tg, T0 + 600)
    st = flow.load_state(results, "women")
    assert lines == ["게시 확인", "남은 주제 4개 확인 요청 (2026-10-16)"]
    assert st.stage == "theme_review" and "4. [상황] 주제 4" in tg.sent[-1] and sent == []
    # 보내기 전 글과 다른 명령은 읽지 않는다
    tg.text(1, "좋아", T0 + 500)
    tg.text(2, "/fix 1 신발", T0 + 610)
    assert tick(env, tg, T0 + 700) == []
    tg.text(3, "3 1", T0 + 710)
    tick(env, tg, T0 + 800)
    st = flow.load_state(results, "women")
    assert "1. 주제 3\n2. 주제 1" in tg.sent[-2]
    assert st.stage == "pick_wait" and [t.title_hint for t in st.week] == ["주제 1"]
    assert store.themes()[-1].title_hint == "주제 3" and sent[-1][0] == "daily.yml"
    # 다음 게시 뒤 다시 묻고, /ok 도 "좋아"
    st.stage = "publishing"
    flow.save_state(results, st)
    store.save_posts([PostRecord(date=date(2026, 10, 16), title="t")])
    tick(env, tg, T0 + 900)
    assert flow.load_state(results, "women").stage == "theme_review"
    tg.text(4, "/ok", T0 + 910)
    tick(env, tg, T0 + 1000)
    st = flow.load_state(results, "women")
    assert st.stage == "pick_wait" and st.week == [] and store.themes()[-1].title_hint == "주제 1"
    # 남은 주제가 없으면 묻지 않고 주간 후보
    st.stage = "publishing"
    flow.save_state(results, st)
    store.save_posts([PostRecord(date=date(2026, 10, 17), title="t")])
    tick(env, tg, T0 + 1100)
    st = flow.load_state(results, "women")
    assert st.stage == "theme_wait" and sent[-1][1]["week"] is True


def test_theme_review_edit_new_and_bad(env, monkeypatch):
    monkeypatch.setattr(flow, "load_profile", _profile(10, review=True))
    store, results, sent, _ = env
    _review_state(env, 3)
    tg = FakeTg()
    tick(env, tg, T0 + 600)
    tg.text(1, "1 4", T0 + 610)
    tick(env, tg, T0 + 700)
    assert "1~3" in tg.sent[-1] and flow.load_state(results, "women").stage == "theme_review"
    # 고칠 점: 남은 목록을 base 로 Claude 에게, 고친 목록은 주간 후보처럼 와서 번호로 고른다
    tg.text(2, "2번은 겨울 니트 레이어드로", T0 + 710)
    tick(env, tg, T0 + 800)
    wf, inputs = sent[-1]
    assert wf == "theme-options.yml" and inputs["week"] is True
    assert inputs["feedback"] == "2번은 겨울 니트 레이어드로" and inputs["date"] == "2026-10-16"
    assert [t["title_hint"] for t in json.loads(inputs["base"])] == ["주제 1", "주제 2", "주제 3"]
    st = flow.load_state(results, "women")
    assert st.stage == "theme_wait" and len(st.week) == 3
    profile = flow.load_profile("women")
    opts = flow.save_options(
        profile, store, date(2026, 10, 16), [theme(1), theme(9), theme(3)], "", tg, T0 + 900,
        week=True,
    )  # fmt: skip
    tg.text(3, "1 2 3", opts.sent_at + 10)
    tick(env, tg, opts.sent_at + 60)
    st = flow.load_state(results, "women")
    assert st.stage == "refs_wait" and [t.title_hint for t in st.week] == [
        "주제 1",
        "주제 9",
        "주제 3",
    ]
    # "새로": 남은 주제를 버리고 주간 후보를 새로
    _review_state(env, 2)
    tick(env, tg, T0 + 2000)
    tg.text(4, "새로", T0 + 2010)
    tick(env, tg, T0 + 2100)
    st = flow.load_state(results, "women")
    assert st.stage == "theme_wait" and st.week == [] and sent[-1][1]["feedback"] == ""
    assert flow.review_reply("좋아요!") == "go" and flow.review_reply("2, 1번") == [2, 1]


def test_published_requests_feedback_rules(env, monkeypatch):
    # 2026-10-21 사용자: 게시가 끝나면 받은 /fix 를 규칙에 반영
    monkeypatch.setattr(flow, "load_profile", _profile(0, rules=True))
    store, results, sent, _ = env
    st = flow.FlowState(account="women", stage="publishing", date=date(2026, 10, 15), asked_at=T0)
    flow.save_state(results, st)
    store.save_posts([PostRecord(date=date(2026, 10, 15), title="t")])
    tg = FakeTg()
    assert tick(env, tg, T0 + 600)[0] == "게시 확인"  # 새 피드백이 없으면 부르지 않음
    assert all(wf != "feedback-rules.yml" for wf, _ in sent)
    store.add_feedback([
        FeedbackEntry(account="women", date=date(2026, 10, 16), text=f"신발 {i}", at=T0,
                      update_id=100 + i, result="rerender")
        for i in range(2)
    ])  # fmt: skip
    st = flow.load_state(results, "women")
    st.stage, st.date = "publishing", date(2026, 10, 16)
    flow.save_state(results, st)
    store.save_posts([PostRecord(date=date(2026, 10, 16), title="t")])
    lines = tick(env, tg, T0 + 1200)
    assert lines == ["게시 확인, 피드백 반영 계획 요청 (2개)"]
    assert ("feedback-round.yml", {"account": "women", "step": "plan"}) in sent
    assert "2개" in tg.sent[0]
    assert flow.load_state(results, "women").stage == "rules_plan"


def _round_sent(results, **kw):
    from tools import feedback

    rnd = feedback.load_round(results, "women")
    feedback.save_round(results, "women", rnd.model_copy(update=kw))


def test_feedback_round_plan_pr_merge_then_theme(env, monkeypatch):
    """2026-10-22 사용자: 반영 계획 → /fix·/ok → PR → /fix·/merge → 합치면 다음 주제."""
    monkeypatch.setattr(flow, "load_profile", _profile(0, rules=True))
    store, results, sent, _ = env
    st = flow.FlowState(account="women", stage="rules_plan", date=date(2026, 10, 16), asked_at=T0)
    flow.save_state(results, st)
    tg = FakeTg()
    rounds = lambda: [i["step"] for wf, i in sent if wf == "feedback-round.yml"]  # noqa: E731
    assert tick(env, tg, T0 + 60) == []  # 계획이 아직 안 옴
    _round_sent(results, plan="1. 신발", plan_at=T0 + 100)
    tg.text(10, "/fix 2번은 빼줘", T0 + 200)
    assert tick(env, tg, T0 + 300) == ["피드백 반영 계획 고치기 (1개)"]
    assert rounds() == ["plan"]
    from tools import feedback

    assert feedback.load_round(results, "women").plan_notes == ["2번은 빼줘"]
    assert tick(env, tg, T0 + 400) == []  # 고친 계획을 기다림 (그 전 답은 다시 읽지 않음)
    _round_sent(results, plan_at=T0 + 500)
    tg.text(11, "/ok", T0 + 600)
    assert tick(env, tg, T0 + 700) == ["피드백 반영 계획 확정, PR 요청"]
    assert flow.load_state(results, "women").stage == "rules_pr"
    _round_sent(results, pr_at=T0 + 800, branch="b", pr_url="u")
    tg.text(12, "/fix 문장을 더 짧게", T0 + 900)
    assert tick(env, tg, T0 + 1000) == ["규칙 PR 고치기 (1개)"]
    _round_sent(results, pr_at=T0 + 1100)
    tg.text(13, "/merge", T0 + 1200)
    assert tick(env, tg, T0 + 1300) == ["규칙 PR 합치기 요청 (/merge)"]
    assert rounds() == ["plan", "apply", "revise", "merge"]
    assert tick(env, tg, T0 + 1400) == []  # 합치기를 기다림, 주제는 아직
    assert all(wf != "theme-options.yml" for wf, _ in sent)
    _round_sent(results, merged_at=T0 + 1500)
    lines = tick(env, tg, T0 + 1600)
    assert lines[0] == "피드백 반영 마침 (PR 합침)" and lines[1].startswith("주제 제안 요청")
    # 단계 안에서 보낸 /fix 는 "/fix 규칙" 요청으로 다시 읽지 않는다
    assert all(wf != "feedback-rules.yml" for wf, _ in sent)


def test_feedback_round_skip_goes_to_theme(env, monkeypatch):
    monkeypatch.setattr(flow, "load_profile", _profile(0, rules=True))
    store, results, sent, _ = env
    st = flow.FlowState(account="women", stage="rules_plan", date=date(2026, 10, 16), asked_at=T0)
    flow.save_state(results, st)
    _round_sent(results, plan="1. 신발", plan_at=T0 + 100)
    tg = FakeTg()
    tg.text(10, "/skip", T0 + 200)
    lines = tick(env, tg, T0 + 300)
    assert lines[0] == "피드백 반영 건너뜀 (/skip)" and lines[1].startswith("주제 제안 요청")


def test_rules_fix_request_goes_to_feedback_rules(env):
    """2026-10-22 사용자: 규칙 반영 PR 에 더하거나 고칠 점도 /fix 로 보낸다.
    "/fix 규칙 …" 이나 규칙 반영 알림(📝)에 답장한 "/fix …" 는 단계와 상관없이
    규칙 고치기로 넘긴다."""
    store, results, sent, _ = env
    st = flow.FlowState(
        account="women", stage="preview_wait", date=date(2026, 10, 16), asked_at=T0, rules_upto=1
    )
    flow.save_state(results, st)
    tg = FakeTg()
    tg.text(500, "/fix 규칙 신발은 앞코 뾰족한 구두도 괜찮다고 넣어줘", T0 + 10)
    tg.ups.append(
        {
            "update_id": 501,
            "message": {
                "date": T0 + 20,
                "chat": {"id": 42},
                "text": "/fix 치마 기장 문장은 되돌려줘",
                "reply_to_message": {"message_id": 9, "text": "[women] 📝 /fix 피드백을 규칙에"},
            },
        }
    )
    tg.text(502, "/fix 2 신발 바꿔줘", T0 + 30)  # 게시물 /fix 는 규칙 고치기가 아님
    lines = tick(env, tg, T0 + 60)
    assert lines[0] == "규칙 고치기 요청 (2개)"
    wf, inputs = next((wf, i) for wf, i in sent if wf == "feedback-rules.yml")
    texts = [r["text"] for r in json.loads(inputs["request"])["requests"]]
    assert texts == ["신발은 앞코 뾰족한 구두도 괜찮다고 넣어줘", "치마 기장 문장은 되돌려줘"]
    assert flow.load_state(results, "women").rules_upto == 501
    assert any("규칙 고치기 요청을 받았어요" in t for t in tg.sent)
    # 같은 글은 한 번만
    n = len(sent)
    tick(env, tg, T0 + 120)
    assert sum(1 for wf, _ in sent[n:] if wf == "feedback-rules.yml") == 0


def test_rules_fix_ignores_old_messages_first_time(env):
    store, results, sent, _ = env
    flow.save_state(results, flow.FlowState(account="women", stage="theme_wait", asked_at=T0))
    tg = FakeTg()
    tg.text(400, "/fix 규칙 기능을 켜기 전 요청", T0 - 60)
    tick(env, tg, T0)
    assert all(wf != "feedback-rules.yml" for wf, _ in sent)
    assert flow.load_state(results, "women").rules_upto == 400
    # 주제 단계(미리보기 없음)의 그냥 /fix 도 규칙 고치기 (2026-10-22 사용자가 게시 뒤 보냄)
    tg.text(401, "/fix 바지는 발등을 덮게", T0 + 60)
    assert tick(env, tg, T0 + 120)[0] == "규칙 고치기 요청 (1개)"


def test_rules_word_is_not_a_post_fix():
    cmd = notify.parse("/fix 규칙 신발 문장 고쳐줘", "women", date(2026, 10, 15))
    assert cmd == notify.Command("rules", ["신발", "문장", "고쳐줘"], False)
    assert notify.parse("/fix 2 신발", "women", None).name == "fix"


def test_round_record_and_material_cli(tmp_path, monkeypatch):
    from tools import feedback

    results = tmp_path / "results"
    plan = tmp_path / "plan.md"
    plan.write_text("1. 신발\n")
    args = ["round-record", "--account", "women", "--results", str(results)]
    assert feedback.main([*args, "--plan", str(plan), "--upto", "7"]) == 0
    assert feedback.main([*args, "--branch", "feedback-rules/x", "--pr-url", "u", "--pr"]) == 0
    rnd = feedback.load_round(results, "women")
    assert rnd.plan == "1. 신발" and rnd.upto == 7 and rnd.branch == "feedback-rules/x"
    assert rnd.plan_at and rnd.pr_at
    rnd.pr_notes = ["더 짧게"]
    feedback.save_round(results, "women", rnd)
    text = feedback.round_material(Store("women", root=tmp_path / "d"), rnd, "revise", date.today())
    assert "# 확정한 반영 계획" in text and "더 짧게" in text


def test_resend_theme_review(env, monkeypatch):
    """2026-10-22 사용자: 피드백 반영이 끝났으니 주제 보내기부터 새로."""
    monkeypatch.setattr(flow, "load_profile", _profile(10, review=True))
    store, results, sent, _ = env
    st = flow.FlowState(account="women", stage="theme_review", week=[theme(1), theme(2)])
    flow.save_state(results, st)
    assert "남은 주제 2개" in flow.resend(results, "women")
    tg = FakeTg()
    assert tick(env, tg, T0)[0].startswith("남은 주제 2개 확인 요청")
    assert "남은 주제 2개를 확인해 주세요" in tg.sent[-1]
    assert flow.load_state(results, "women").stage == "theme_review"
    flow.save_state(results, flow.FlowState(account="women", stage="pick_wait"))
    assert "아니에요" in flow.resend(results, "women")


def test_resend_from_schedule_cron(env, monkeypatch, capsys):
    """schedule.yml 에 입력을 더하면 실행이 승인 대기로 막혀 cron 칸에 resend:<계정> 으로 받는다."""
    from tools import schedule as sched

    store, results, sent, _ = env
    flow.save_state(results, flow.FlowState(account="women", stage="theme_review", week=[theme(1)]))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(sched, "execute", lambda *a, **k: [])
    monkeypatch.setattr(sched, "retry_limited", lambda *a, **k: [])
    argv = ["run", "--cron", "resend:women", "--results", str(results)]
    assert sched.main([*argv, "--dry-run"]) == 0
    assert "(시험) women" in capsys.readouterr().out
    assert flow.load_state(results, "women").stage == "theme_review"
    monkeypatch.setattr(sched.GitHub, "from_env", classmethod(lambda cls: None))
    assert sched.main(argv) == 0
    assert flow.load_state(results, "women").stage == "theme"


def test_ref_photos_overflow_after_album():
    # 2026-10-26 사용자: 앨범은 10장까지라 12장을 보내면 2장이 따로 간다 (설명 없이)
    tg = FakeTg()
    tg.photo(1, "ref_a", T0 + 10, caption="참고")
    tg.ups[0]["message"]["media_group_id"] = "g1"
    tg.ups.append({"update_id": 2, "message": {
        "date": T0 + 10, "chat": {"id": 42}, "media_group_id": "g1",
        "photo": [{"file_id": "ref_b", "width": 1280}]}})  # fmt: skip
    tg.photo(3, "ref_c", T0 + 30)
    tg.photo(4, "ref_d", T0 + 31)
    tg.photo(5, "scene", T0 + 31 + flow.REF_GAP + 1)  # 한참 뒤 사진은 참고가 아니다
    got = [fid for _, fid in flow.ref_photos(tg.ups, CHAT, T0)]
    assert got == ["ref_a", "ref_b", "ref_c", "ref_d"]
    assert [p.file_id for p in flow.scene_photos(tg.ups, CHAT, T0, 0)] == ["scene"]


def test_scenes_carry_to_next_theme(tmp_path):
    """장면 사진·설명은 새로 보낼 때까지 다음 주제에도 쓴다 (2026-10-27 사용자)."""
    old = tmp_path / "flow/women/2026-10-27"
    old.mkdir(parents=True)
    for name in ("scene.jpg", "scene_2.jpg", "scene.txt"):
        (old / name).write_text(name)
    st = flow.FlowState(
        account="women",
        date=date(2026, 10, 28),
        scenes=["flow/women/2026-10-27/scene.jpg", "flow/women/2026-10-27/scene_2.jpg"],
        scene="flow/women/2026-10-27/scene.jpg",
    )
    flow._carry_scenes(tmp_path, "women", st, date(2026, 10, 27), False)
    new = tmp_path / "flow/women/2026-10-28"
    assert st.scene == "flow/women/2026-10-28/scene.jpg"
    assert st.scenes[1] == "flow/women/2026-10-28/scene_2.jpg"
    assert sorted(f.name for f in new.iterdir()) == ["scene.jpg", "scene.txt", "scene_2.jpg"]


def test_face_photos_are_not_scenes():
    # 2026-10-29 사용자: 모델 얼굴 참고 사진은 설명 "얼굴" 로 보낸다 (장면 사진을 바꾸지 않음)
    tg = FakeTg()
    tg.photo(1, "scene_a", T0 + 5)
    tg.photo(2, "face_a", T0 + 400, caption="얼굴")
    tg.photo(3, "face_b", T0 + 410)  # 설명 없이 이어 보낸 사진도 얼굴
    assert flow.face_photos(tg.ups, CHAT, T0) == [(2, "face_a"), (3, "face_b")]
    assert [p.file_id for p in flow.scene_photos(tg.ups, CHAT, T0, 0)] == ["scene_a"]
    assert flow.ref_photos(tg.ups, CHAT, T0) == []
