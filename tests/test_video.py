import json
from datetime import date

import pytest
from PIL import Image

from tests.test_flow import (  # noqa: F401
    CHAT,
    T0,
    FakeTg,
    _profile,
    env,  # noqa: F401 - fixture
    tick,
)
from tools import flow, notify, video
from tools.video import Reply, VideoRecord


def rec(step="frames", looks=6, sent_at=T0 + 100) -> VideoRecord:
    return VideoRecord(
        account="women", date=date(2026, 10, 15), run="2026-10-15-run9", step=step,
        looks=looks, sent_at=sent_at,
    )  # fmt: skip


@pytest.mark.parametrize(
    "text,kind,look,extra",
    [
        ("/ok", "ok", 0, ""),
        ("/skip", "skip", 0, ""),
        ("/fix 3 얼굴이 달라", "fix", 3, "얼굴이 달라"),
        ("/fix 더 빨리 걷게", "fix", 0, "더 빨리 걷게"),
        ("/fix 배경 눈이 더 많이", "background", 0, "눈이 더 많이"),
        ("/frame 2 다시", "frame", 2, "다시"),
        ("/제목 퍼 하나로 끝", "title", 0, "퍼 하나로 끝"),
        ("/music", "music", 0, ""),
    ],
)
def test_parse(text, kind, look, extra):
    r = video.parse(text, 6)
    assert r is not None and r.kind == kind and r.look == look and r.text == extra


def test_parse_numbers():
    assert video.parse("/speed 1.3", 6).number == 1.3
    assert video.parse("/speed 1.3배", 6).number == 1.3
    assert video.parse("/order 3 1 2 4 5 6", 6).order == [3, 1, 2, 4, 5, 6]
    assert video.parse("/order 3 1 2", 6) is None  # 빠진 벌
    assert video.parse("안녕", 6) is None


def test_action_by_step():
    fix3 = Reply(kind="fix", look=3, text="얼굴이 달라")
    assert video.action(rec("frames"), Reply(kind="ok")) == {"step": "prompts"}
    assert video.action(rec("frames"), fix3) == {
        "step": "frames",
        "looks": "3",
        "note": "3:얼굴이 달라",
    }
    bg = video.action(rec("frames"), Reply(kind="background", text="눈 더"))
    # 빈 배경부터 보고 허락받기 (지금 배경에서 그 부분만 고침)
    assert bg == {"step": "background", "note": "배경:눈 더", "base": "-1"}
    assert video.action(rec("prompts"), Reply(kind="ok")) == {"step": "clips"}
    assert video.action(rec("prompts"), Reply(kind="fix", text="빨리"))["step"] == "prompts"
    assert video.action(rec("final"), Reply(kind="speed", number=1.3)) == {
        "step": "edit",
        "speed": "1.3",
    }
    assert video.action(rec("final"), fix3)["step"] == "clips"
    # /frame N: 새 첫 장면도 먼저 보고 허락받는다 (2026-11-02)
    assert video.action(rec("final"), Reply(kind="frame", look=2, text="x"))["step"] == "frames"
    assert video.action(rec("final"), Reply(kind="music")) == {"step": "music"}
    assert video.action(rec("final"), Reply(kind="music"), music=False) is None
    assert video.action(rec("frames"), Reply(kind="speed", number=1.3)) is None  # 이 단계엔 없음


def test_action_plan_and_background():
    """2026-11-02 사용자: 그리기 전에 무엇을 그릴지 확인받고, 그린 뒤 허락받는다."""
    ok = Reply(kind="ok")
    assert video.action(rec("plan"), ok) == {"step": "background"}
    assert video.action(rec("plan"), Reply(kind="fix", look=3, text="가방 앞으로")) == {
        "step": "plan",
        "note": "3:가방 앞으로",
    }
    assert video.action(rec("plan"), Reply(kind="background", text="바다")) == {
        "step": "plan",
        "note": "배경:바다",
    }
    assert video.action(rec("background"), ok) == {"step": "frames"}
    # 2026-11-03 사용자 "첫 번째 이미지에 문 위치만 앞쪽으로": 말한 곳만 고친다
    assert video.action(rec("background"), Reply(kind="fix", text="눈 더")) == {
        "step": "background",
        "note": "배경:눈 더",
        "base": "-1",
    }
    first = video.parse("/fix 1 문 위치만 앞쪽으로", 6)
    assert video.action(rec("background"), first) == {
        "step": "background",
        "note": "배경:문 위치만 앞쪽으로",
        "base": "1",
    }
    new = Reply(kind="fix", text="새로 해변으로")
    assert video.action(rec("background"), new) == {"step": "background", "note": "배경:해변으로"}
    # 첫 장면에서 배경을 고치면 빈 배경부터 다시 보여 준다
    assert video.action(rec("frames"), Reply(kind="background", text="눈 더"))["step"] == (
        "background"
    )


def test_notes():
    r = rec()
    assert video.add_notes(r, ["3:얼굴이 달라", "더 도도하게"]) == [3]
    assert video.notes_for(r, 3) == ["더 도도하게", "얼굴이 달라"]
    assert video.notes_for(r, 1) == ["더 도도하게"]


def test_look_files(tmp_path):
    look = tmp_path / "looks" / "2"
    (look / "outfit" / "results").mkdir(parents=True)
    (look / "outfit_r2" / "results").mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(look / "outfit" / "results" / "G_1.jpg")
    Image.new("RGB", (10, 10)).save(look / "outfit_r2" / "results" / "G_1.jpg")
    assert video.look_image(tmp_path, 2) == look / "outfit_r2" / "results" / "G_1.jpg"
    (look / "review").mkdir()
    chosen = {"candidates": [{"id": "c1", "file": "outfit/results/G_1.jpg"}], "chosen": "c1"}
    (look / "review" / "review.json").write_text(json.dumps(chosen), "utf-8")
    assert video.look_image(tmp_path, 2) == look / "outfit" / "results" / "G_1.jpg"
    (look / "plan.yaml").write_text(
        "items:\n- desc: 브라운 퍼 자켓 (brown shaggy faux-fur jacket)\n- desc: 블랙 쇼츠\n",
        "utf-8",
    )
    assert video.items_en(tmp_path, 2) == "brown shaggy faux-fur jacket; 블랙 쇼츠"


def test_overlay(tmp_path):
    from core.profile import load_profile

    p = tmp_path / "o.png"
    video.overlay_png(load_profile("women"), 1, "퍼 하나로 끝낸 겨울 여행룩", p)
    im = Image.open(p)
    assert im.size == (1080, 1920) and im.getbbox() is not None


def test_guides_list_commands():
    from core.profile import load_profile

    p = load_profile("women")
    g = video.frames_guide("[women] 10-15")
    assert all(c in g for c in ("/ok", "/fix 3", "/fix 배경", "/skip"))
    b = video.background_guide("[women] 10-15", rec("background"))
    assert all(c in b for c in ("/ok", "/fix", "/skip"))
    f = video.final_guide("[women] 10-15", rec("final"), p)
    assert all(c in f for c in ("/speed", "/제목", "/order", "/fix 3", "/frame 3", "/ok", "/skip"))
    assert "/music" not in f  # 2026-11-02 사용자 "음악은 없애줘" (video.music: none)
    lyria = p.model_copy(update={"video": p.video.model_copy(update={"music": "lyria"})})
    assert "/music" in video.final_guide("[women] 10-15", rec("final"), lyria)


def test_plan_text(tmp_path):
    from core.profile import load_profile
    from tools.video_concept import Concept, Shot

    look = tmp_path / "looks" / "1"
    look.mkdir(parents=True)
    (look / "plan.yaml").write_text(
        "items:\n- desc: 브라운 퍼 자켓 (brown faux-fur jacket)\n- desc: 블랙 쇼츠\n", "utf-8"
    )
    c = Concept(
        title="t", background="눈 오는 골목", background_en="snowy alley", framing="세로 전신",
        light="해 질 무렵", motion="걷기", hook="h",
        shots=[Shot(look=1, seconds=2.5, action="걷기", pose="뒤돌기")],
    )  # fmt: skip
    r = rec("plan", looks=1)
    video.add_notes(r, ["1:가방 앞으로"])
    r.notes["background"] = ["눈 더"]
    t = video.plan_text("[women] 10-15", load_profile("women"), c, r, tmp_path)
    assert "눈 오는 골목" in t and "요청: 눈 더" in t
    assert "LOOK 1: 브라운 퍼 자켓, 블랙 쇼츠 / 걷기 → 뒤돌기 (요청: 가방 앞으로)" in t
    assert all(x in t for x in ("/ok", "/fix 배경", "/fix 3", "/skip"))


def test_flow_video_instead_of_post(env, monkeypatch):  # noqa: F811
    """2026-11-02 사용자: 영상이 캐러셀을 대신한다.
    미리보기 /ok → 첫 장면 → 지시문 → 최종본 → /ok 면 다음 주제."""
    from tools import feedback
    from tools import schedule as sched

    monkeypatch.setattr(flow, "load_profile", _profile(0, video=True))
    store, results, sent, _ = env
    out = results / "2026-10-15-run9"
    out.mkdir()
    prev = type("Rec", (), {"sent_at": T0 + 50})()
    monkeypatch.setattr(feedback, "latest_preview", lambda *a: out)
    monkeypatch.setattr(notify, "load_record", lambda o: prev)
    monkeypatch.setattr(sched, "cover_change", lambda *a: "")
    monkeypatch.setattr(feedback, "check", lambda *a: None)
    monkeypatch.setattr(feedback, "waiting", lambda *a: False)
    monkeypatch.setattr(notify, "approval_now", lambda *a: "ok")
    monkeypatch.setattr(notify, "remember", lambda *a: True)
    posted = []
    monkeypatch.setattr(video, "record_post", lambda s, run: posted.append(run.name))
    st = flow.FlowState(account="women", stage="preview_wait", date=date(2026, 10, 15))
    flow.save_state(results, st)
    tg = FakeTg()
    tick(env, tg, T0 + 100)
    wf, inputs = sent[-1]
    assert wf == "video.yml" and inputs["step"] == "plan" and inputs["run"] == out.name
    assert flow.load_state(results, "women").stage == "video_making"
    assert not any(w == "publish.yml" for w, _ in sent)

    def arrive(step, at):
        video.save_record(out, rec(step, sent_at=at))

    # 그리기 전 계획: 배경·벌 고칠 점을 한 번에 → 계획 다시 → /ok 면 빈 배경
    arrive("plan", T0 + 110)
    tick(env, tg, T0 + 120)
    tg.text(11, "/fix 배경 바다", T0 + 130)
    tg.text(12, "/fix 2 가방", T0 + 131)
    tick(env, tg, T0 + 140)
    assert sent[-1][1]["step"] == "plan" and sent[-1][1]["note"] == "배경:바다\n2:가방"
    arrive("plan", T0 + 150)
    tg.text(13, "/ok", T0 + 160)
    tick(env, tg, T0 + 170)
    assert sent[-1][1]["step"] == "background"
    arrive("background", T0 + 180)
    tg.text(14, "/ok", T0 + 190)
    tick(env, tg, T0 + 195)
    assert sent[-1][1]["step"] == "frames"

    arrive("frames", T0 + 200)
    tick(env, tg, T0 + 250)
    assert flow.load_state(results, "women").stage == "video_wait"
    tg.text(1, "/fix 3 얼굴이 달라", T0 + 300)
    tg.text(2, "/fix 5 가방", T0 + 301)
    tick(env, tg, T0 + 350)
    assert sent[-1][1]["step"] == "frames" and sent[-1][1]["looks"] == "3 5"
    assert sent[-1][1]["note"] == "3:얼굴이 달라\n5:가방"
    arrive("frames", T0 + 400)
    tg.text(3, "/ok", T0 + 450)
    tick(env, tg, T0 + 500)
    assert sent[-1][1]["step"] == "prompts"
    arrive("prompts", T0 + 600)
    tg.text(4, "/ok", T0 + 650)
    tick(env, tg, T0 + 700)
    assert sent[-1][1]["step"] == "clips"
    arrive("final", T0 + 800)
    tg.text(5, "/speed 1.4", T0 + 850)
    tick(env, tg, T0 + 900)
    assert sent[-1][1] == {
        "account": "women", "date": "2026-10-15", "run": out.name, "step": "edit", "speed": "1.4",
    }  # fmt: skip
    arrive("final", T0 + 1000)
    tg.text(6, "/ok", T0 + 1050)
    tick(env, tg, T0 + 1100)
    assert posted == [out.name]
    assert flow.load_state(results, "women").stage in ("theme", "theme_wait")


def test_flow_video_retry(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(flow, "load_profile", _profile(0, video=True))
    store, results, sent, _ = env
    out = results / "2026-10-15-run9"
    out.mkdir()
    st = flow.FlowState(account="women", stage="video_making", date=date(2026, 10, 15),
                        video_run=out.name, asked_at=T0,
                        video_last=json.dumps({"account": "women", "date": "2026-10-15",
                                               "run": out.name, "step": "clips"}))  # fmt: skip
    flow.save_state(results, st)
    r = rec("prompts")
    r.error, r.error_at = "HTTP 402", T0 + 10
    video.save_record(out, r)
    tg = FakeTg()
    tick(env, tg, T0 + 100)
    assert flow.load_state(results, "women").stage == "video_wait" and "/retry" in tg.sent[-1]
    tg.text(1, "/retry", T0 + 200)
    tick(env, tg, T0 + 300)
    assert sent[-1] == ("video.yml", {"account": "women", "date": "2026-10-15", "run": out.name,
                                      "step": "clips"})  # fmt: skip


def test_fix_numbers_are_look_numbers_in_video_mode():
    """캐러셀 없이 영상만 (2026-11-02): 미리보기는 벌 코디컷만 보내고 /fix 3 = LOOK 3."""
    from types import SimpleNamespace

    from tools import feedback

    imgs = ["out/d/slides/01_cover.jpg"]
    imgs += [f"out/d/slides/0{n + 1}_look{n}.jpg" for n in range(1, 7)]
    imgs += ["out/d/slides/08_comment.jpg"]
    m = SimpleNamespace(images=imgs, plan=SimpleNamespace(items=[]))
    looks = feedback.fix_slides(m, True)
    assert [s["look"] for s in looks] == [1, 2, 3, 4, 5, 6]
    assert len(feedback.fix_slides(m, False)) == 8
    record = SimpleNamespace(message_ids=[])
    found = feedback.target(["3", "신발", "크게"], None, record, looks)
    assert found["look"] == 3 and found["slide"] == 4 and found["text"] == "신발 크게"


def test_video_preview_control_text():
    from types import SimpleNamespace

    p = _profile(0, video=True)("women")
    p = p.model_copy(update={"flow": p.flow.model_copy(update={"enabled": True})})
    m = SimpleNamespace(date=date(2026, 10, 15), title_lines=["a", "b"], review=None,
                        needs_human=False)  # fmt: skip
    t = notify.control_text(m, p, False)
    assert "캐러셀은 없음" in t and "그림 계획" in t and "제목:" not in t
    assert "/fix 3" in t and "/cover" not in t and "/제목" not in t


def test_video_from_published_post(env, monkeypatch):  # noqa: F811
    """2026-11-03 사용자: 이미 올린 게시물(니트 여친룩)로 영상. 주제 확인 중에 요청하면
    컨셉 → 영상 단계를 거친 뒤 하던 주제 확인으로 돌아온다 (게시 기록은 그대로)."""
    from tests.test_flow import _concepts, theme
    from tools import video_concept

    monkeypatch.setattr(flow, "load_profile", _profile(10, video=True, review=True))
    store, results, sent, _ = env
    for name in ("2026-10-28-run406", "2026-10-28-run407"):
        (results / name).mkdir()
    (results / "2026-10-28-run407" / "published.json").write_text("{}", "utf-8")
    posted = []
    monkeypatch.setattr(video, "record_post", lambda s, run: posted.append(run.name))
    st = flow.FlowState(account="women", stage="theme_review", date=date(2026, 11, 2),
                        week=[theme(1)], asked_at=T0, read_upto=T0)  # fmt: skip
    flow.save_state(results, st)
    tg = FakeTg()
    tg.text(1, "/영상 10-28", T0 + 100)
    tick(env, tg, T0 + 200)
    st = flow.load_state(results, "women")
    assert st.stage == "video_concept" and st.date == date(2026, 10, 28)
    want = {"account": "women", "date": "2026-10-28", "run": "2026-10-28-run407/plan",
            "feedback": ""}  # fmt: skip
    assert sent[-1] == ("video-concept.yml", want)
    profile = flow.load_profile("women")
    video_concept.save(profile, store, date(2026, 10, 28), _concepts(), looks=2, tg=tg,
                       sent_at=T0 + 300)  # fmt: skip
    tg.text(2, "1", T0 + 400)
    tick(env, tg, T0 + 500)
    assert sent[-1][0] == "video.yml" and sent[-1][1]["step"] == "plan"
    assert sent[-1][1]["run"] == "2026-10-28-run407" and sent[-1][1]["concept"] == "1"
    out = results / "2026-10-28-run407"
    r = rec("final", sent_at=T0 + 600)
    r.date = date(2026, 10, 28)
    video.save_record(out, r)
    tick(env, tg, T0 + 650)
    tg.text(3, "/ok", T0 + 700)
    tick(env, tg, T0 + 800)
    st = flow.load_state(results, "women")
    assert st.stage == "theme_review" and st.date == date(2026, 11, 2) and not st.video_back
    assert posted == []  # 이미 올린 게시물이라 게시 기록은 건드리지 않음
    assert st.read_upto == T0 + 800  # 영상 중에 온 답을 주제 답으로 읽지 않음
    # 같은 요청은 다시 받지 않는다
    tick(env, tg, T0 + 900)
    assert flow.load_state(results, "women").stage == "theme_review"


def test_video_request_file(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(flow, "load_profile", _profile(10, video=True, review=True))
    store, results, sent, _ = env
    (results / "2026-10-28-run407").mkdir()
    (results / "2026-10-28-run407" / "published.json").write_text("{}", "utf-8")
    store.dir.mkdir(parents=True, exist_ok=True)
    flow.video_request_file(store).write_text(
        json.dumps({"date": "2026-10-28", "at": T0 + 50}), "utf-8"
    )
    flow.save_state(results, flow.FlowState(account="women", stage="theme_wait",
                                            date=date(2026, 11, 2)))  # fmt: skip
    tick(env, FakeTg(), T0 + 100)
    st = flow.load_state(results, "women")
    assert st.stage == "video_concept" and st.video_req_at == T0 + 50


def test_background_edit_from_version(tmp_path, monkeypatch):
    """고칠 때는 그 번째 배경을 넣고 말한 곳만 고친다.
    예전 배경은 git 기록에서 번호를 붙여 꺼낸다."""
    import subprocess

    from tools.video_concept import Concept, Shot

    run = tmp_path / "2026-10-28-run407"
    d = run / "video"
    d.mkdir(parents=True)
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git[:3], "init", "-q"], check=True)
    for color in ("red", "blue"):
        Image.new("RGB", (8, 8), color).save(d / "background.jpg")
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", color], check=True)
    r = rec("background")
    calls = []

    class Client:
        def generate(self, model, prompt, images):
            calls.append((prompt, images))
            buf = io.BytesIO()
            Image.new("RGB", (8, 8), "green").save(buf, "JPEG")
            return buf.getvalue(), 0.13

    import io

    from core.profile import load_profile

    shots = [Shot(look=1, seconds=2.5, action="a", pose="p")]
    c = Concept(title="t", background="복도", background_en="hall", framing="f", light="l",
                motion="m", hook="h", shots=shots)  # fmt: skip
    video.make_background(load_profile("women"), run, r, c, Client(), {"nano_banana_pro": "x"},
                          1, "문만 앞으로")  # fmt: skip
    assert r.bg_versions == 3 and r.bg_base == 1
    assert "Change only this: 문만 앞으로" in calls[0][0] and len(calls[0][1]) == 1
    first = Image.open(io.BytesIO(calls[0][1][0])).convert("RGB").getpixel((4, 4))
    assert first[0] > 200 and first[2] < 60  # 1번째(빨강)를 넣었다
    assert video.bg_path(run, 3).read_bytes() == (d / "background.jpg").read_bytes()
    assert "3번째 (1번째를 고침)" in video.background_guide("[women] 10-28", r)
