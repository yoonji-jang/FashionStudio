import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from PIL import Image

from core.profile import load_profile
from tests.test_publish import DAY, FakeApis, clients, results, sent  # noqa: F401 - fixture
from tests.test_review import fake_photos, store  # noqa: F401 - pytest fixture
from tools import ai_label, publish, schedule, story

KST = ZoneInfo("Asia/Seoul")


def cover(color=(200, 60, 60)) -> Image.Image:
    return Image.new("RGB", (1080, 1350), color)


def test_render_is_story_size_with_card_in_the_middle():
    p = load_profile("women")
    img = story.render(cover(), p)
    assert img.size == (1080, 1920)
    # 가운데는 피드 사진, 위·아래 끝은 흐린 배경(어둡게)
    assert img.getpixel((540, 960)) == (200, 60, 60)
    top = img.getpixel((540, 20))
    assert top[0] < 200 and abs(top[1] - top[2]) < 10
    # 인스타 위(프로필)·아래(답장 칸) 화면 요소를 피해 사진이 있다
    w = round(1080 * p.story.card_width)
    h = round(1350 * w / 1080)
    y0 = round(1920 * p.story.card_center - h / 2)
    assert y0 > 250 and y0 + h < 1920 - 300


def test_render_solid_background_and_no_text():
    p = load_profile("women")
    p.story.background = "#E7E9EB"
    p.story.heading = p.story.cta = ""
    p.story.show_logo = False
    img = story.render(cover(), p)
    assert img.getpixel((540, 100)) == (0xE7, 0xE9, 0xEB)
    assert img.getpixel((540, 300)) == (0xE7, 0xE9, 0xEB)  # 제목 자리도 비어 있음


def test_make_takes_first_slides_and_keeps_ai_mark(tmp_path):
    p = load_profile("women")
    p.story.slides = 2
    src = []
    for name in ("01_cover", "02_아우터", "03_상의"):
        path = tmp_path / f"{name}.jpg"
        cover().save(path)
        src.append(path)
    paths = story.make(p, src, tmp_path / "story")
    assert [x.name for x in paths] == ["01_story.jpg", "02_story.jpg"]
    assert Image.open(paths[0]).size == (1080, 1920)
    # 표지에서 만든 스토리에는 같은 AI 합성 표시, 상품 장에는 없음
    assert ai_label.read(paths[0]) == "trainedAlgorithmicMedia"
    assert ai_label.read(paths[1]) is None


def test_post_now_follows_delay():
    p = load_profile("women")
    assert story.post_now(p, DAY)  # 기본: 게시 직후
    p.story.delay_minutes = 180  # 20:03 + 3시간 = 23:03
    assert not story.post_now(p, DAY, datetime(2026, 9, 27, 20, 10, tzinfo=KST))
    # 승인이 늦어 schedule 칸이 지난 뒤 게시하면 바로 올린다
    assert story.post_now(p, DAY, datetime(2026, 9, 27, 23, 30, tzinfo=KST))
    p.story.enabled = False
    assert not story.post_now(p, DAY)


def test_story_failure_keeps_feed_post(results, store, monkeypatch):  # noqa: F811
    run = results / "2026-09-27-run5"
    api = FakeApis()
    rec = sent(api, run)
    api.replies = [(rec.sent_at + 5, "/ok", None)]
    tg, ig, host = clients(api)

    def broken(url):
        raise publish.IGError("스토리 처리 실패")

    monkeypatch.setattr(ig, "publish_story", broken)
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert (run / "published.json").exists() and not (run / "story.json").exists()
    assert store.posts()[-1].media_id == "m777"
    assert "스토리는 올리지 못했어요" in api.texts[-1]
    assert not api.files  # 스토리 사진도 지움


def delayed(account):
    p = load_profile(account)
    p.story.delay_minutes = 120
    return p


def test_delayed_story_is_posted_later_once(results, store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(publish, "load_profile", delayed)
    monkeypatch.setattr(story, "load_profile", delayed)
    monkeypatch.setattr(story, "post_now", lambda p, d, now=None: False)  # 아직 칸 전
    run = results / "2026-09-27-run5"
    api = FakeApis()
    rec = sent(api, run)
    api.replies = [(rec.sent_at + 5, "/ok", None)]
    tg, ig, host = clients(api)
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert not (run / "story.json").exists()

    assert story.run_post("women", DAY, results, ig, host, tg) == 0
    rec = json.loads((run / "story.json").read_text())
    assert rec["media_ids"] == ["m777"] and rec["run"] == run.name
    assert "스토리 1장도 올렸어요" in api.texts[-1]
    n = sum(1 for c in api.calls if c[1].endswith("/media_publish"))
    assert story.run_post("women", DAY, results, ig, host, tg) == 0  # 두 번 올리지 않음
    assert sum(1 for c in api.calls if c[1].endswith("/media_publish")) == n
    # 게시하지 않은 날은 조용히 끝
    assert story.run_post("women", date(2026, 9, 28), results, ig, host, tg) == 0


def test_schedule_story_slot_only_when_delayed(monkeypatch):
    p = load_profile("women")
    assert not [s for s in schedule.slots(p) if s.task == "story"]
    p.story.delay_minutes = 180
    [sl] = [s for s in schedule.slots(p) if s.task == "story"]
    # 게시 20:00 (매시 정각 시간표, PR #29) + 180분
    assert sl.local() == "23:00" and sl.cron("Asia/Seoul", DAY) == "0 14 * * *"
    assert schedule._task(p, sl, DAY, datetime(2026, 9, 27, 23, 5, tzinfo=KST)).date == str(DAY)
    gh = schedule.DryRun()
    schedule.execute([schedule.Task("women", "story", str(DAY), True, True, 0)], None, gh)
    assert gh.sent == [
        ("publish.yml", {"account": "women", "date": str(DAY), "dry_run": False, "story": True})
    ]
