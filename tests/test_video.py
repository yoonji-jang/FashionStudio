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
    assert bg == {"step": "frames", "background": True, "note": "배경:눈 더"}
    assert video.action(rec("prompts"), Reply(kind="ok")) == {"step": "clips"}
    assert video.action(rec("prompts"), Reply(kind="fix", text="빨리"))["step"] == "prompts"
    assert video.action(rec("final"), Reply(kind="speed", number=1.3)) == {
        "step": "edit",
        "speed": "1.3",
    }
    assert video.action(rec("final"), fix3)["step"] == "clips"
    assert video.action(rec("final"), Reply(kind="frame", look=2, text="x"))["step"] == "redo"
    assert video.action(rec("final"), Reply(kind="music")) == {"step": "music"}
    assert video.action(rec("frames"), Reply(kind="speed", number=1.3)) is None  # 이 단계엔 없음


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
    f = video.final_guide("[women] 10-15", rec("final"), p)
    assert all(c in f for c in ("/speed", "/제목", "/order", "/fix 3", "/frame 3", "/music", "/ok"))


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
    assert wf == "video.yml" and inputs["step"] == "frames" and inputs["run"] == out.name
    assert flow.load_state(results, "women").stage == "video_making"
    assert not any(w == "publish.yml" for w, _ in sent)

    def arrive(step, at):
        video.save_record(out, rec(step, sent_at=at))

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
