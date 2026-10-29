import json
import shutil
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest
from PIL import Image

from core.models import PublishRecord, ReelsSpec
from core.profile import Reels, load_profile
from tests.test_publish import CHAT, FakeApis, clients, results, sent  # noqa: F401 - fixture
from tests.test_review import fake_photos, store  # noqa: F401 - pytest fixture
from tools import ai_label, notify, publish, reels, schedule

pytestmark = pytest.mark.usefixtures("fixed_times")

DAY = date(2026, 9, 27)
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")


def slides(tmp_path, names=("01_cover", "02_아우터", "03_상의", "04_summary", "05_comment")):
    paths = []
    for i, n in enumerate(names):
        p = tmp_path / f"{n}.jpg"
        Image.new("RGB", (1080, 1350), (40 * i, 100, 200 - 30 * i)).save(p)
        paths.append(p)
    return paths


def test_timeline_follows_carousel_order_and_seconds(tmp_path):
    names = ("01_cover", "02_look1", "03_look2", "04_comment")
    cfg = Reels(seconds={"cover": 3, "comment": 2})
    clips = reels.timeline(slides(tmp_path, names), cfg)
    assert [c.kind for c in clips] == ["cover", "looks", "looks", "comment"]
    assert [c.seconds for c in clips] == [3, 1.6, 1.6, 2]  # 벌 장은 default_sec
    # 다음 장은 앞 장 끝나기 transition_sec 전에 들어온다
    assert reels.total_seconds(clips, 0.5) == pytest.approx(8.2 - 1.5)
    # reels.slides 를 적으면 그 종류만 (순서는 캐러셀 그대로)
    only = reels.timeline(slides(tmp_path), Reels(slides=["summary", "cover"]))
    assert [c.kind for c in only] == ["cover", "summary"]
    with pytest.raises(ValueError):
        reels.timeline(slides(tmp_path, ("01_cover",)), Reels(slides=["summary"]))


def test_frames_slide_like_carousel(tmp_path):
    cfg = Reels(transition="slide", transition_sec=0.5, top=0.1)
    clips = reels.timeline(slides(tmp_path, ("01_cover", "02_아우터")), cfg)
    fr = reels.Frames(clips, cfg, "#E7E9EB", size=(108, 192))
    w, h = fr.size
    first = fr.at(0)
    assert first.size == (108, 192)
    assert first.getpixel((5, 2)) == (231, 233, 235)  # 위쪽 여백은 배경색
    assert first.getpixel((5, fr.top + 5)) != (231, 233, 235)
    # 넘기는 중간: 왼쪽은 앞 장, 오른쪽은 다음 장
    mid = fr.at(fr.starts[1] + 0.25)
    left, right = mid.getpixel((10, 100)), mid.getpixel((w - 10, 100))
    assert left == first.getpixel((10, 100)) and right != left
    assert fr.at(fr.starts[1] + 0.5).getpixel((10, 100)) == right


def test_tall_slides_fill_the_screen(tmp_path):
    """9:16 코디컷 날: 슬라이드 폴더 tall/ 판이 있는 장은 화면을 꽉 채우고, 없는 장은 위쪽에."""
    paths = slides(tmp_path, ("01_cover", "02_look1", "03_comment"))
    (tmp_path / "tall").mkdir()
    for p in paths[:2]:
        Image.new("RGB", (1080, 1920), (10, 200, 10)).save(tmp_path / "tall" / p.name, quality=100)
    cfg = Reels(top=0.1)
    fr = reels.Frames(reels.timeline(paths, cfg), cfg, "#E7E9EB", size=(108, 192))
    assert fr.tops == [0, 0, fr.top] and fr.top > 0
    assert fr.at(0).getpixel((5, 2))[1] > 190  # 위쪽 띠 없이 9:16 판
    assert fr.at(fr.starts[2]).getpixel((5, 2)) == (231, 233, 235)  # 댓글 장은 그대로


def test_music_rotates_by_date(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    for n in ("a.mp3", "b.m4a", "README.md"):
        (folder / n).write_bytes(b"x")
    cfg = Reels(music_dir="music")
    got = {reels.pick_music(cfg, date(2026, 10, d), tmp_path).name for d in range(1, 5)}
    assert got == {"a.mp3", "b.m4a"}
    assert reels.pick_music(Reels(music_dir="none"), DAY, tmp_path) is None
    assert reels.pick_music(Reels(), DAY, tmp_path) is None


@needs_ffmpeg
def test_build_writes_video_and_manifest(results):  # noqa: F811
    run = results / "2026-09-27-run5"
    spec = reels.build(run, load_profile("women"), music=False, size=(216, 384))
    assert spec.video == "reels/reel.mp4" and spec.audio is None
    assert 3 <= spec.duration_sec <= 90
    assert (run / spec.video).stat().st_size > 1000 and (run / spec.cover).exists()
    assert notify.load_manifest(run).reels == spec
    assert ai_label.read_video(run / spec.video) == "trainedAlgorithmicMedia"


def with_reel(run, seconds: float = 12.5):
    """ffmpeg 없이 게시 흐름을 보려고 가짜 영상을 manifest 에 붙인다."""
    (run / "reels").mkdir(exist_ok=True)
    ftyp = b"\0\0\0\x18ftypmp42" + b"\0" * 12
    mdat = (8 + 2000).to_bytes(4, "big") + b"mdat" + b"0" * 2000
    (run / "reels" / "reel.mp4").write_bytes(ftyp + mdat)
    m = notify.load_manifest(run)
    m.reels = ReelsSpec(video="reels/reel.mp4", cover=None, duration_sec=seconds)
    reels.write_manifest(run, m)


class ReelApis(FakeApis):
    """릴스 업로드 서버(rupload)와 두 번째 게시물(릴스)도 흉내 낸다."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.uploads: list[dict] = []
        self.media: list[dict] = []
        self.published = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.host == "rupload.facebook.com":
            self.calls.append((req.method, f"{req.url.host}{req.url.path}"))
            self.uploads.append(dict(req.headers) | {"bytes": len(req.content)})
            return httpx.Response(200, json={"success": True})
        if req.url.host == "graph.instagram.com":
            path = req.url.path
            if req.method == "POST" and path.endswith("/media"):
                self.media.append({k: v[0] for k, v in parse_qs(req.content.decode()).items()})
            if path.endswith("/media_publish"):
                self.calls.append((req.method, f"{req.url.host}{path}"))
                self.published += 1
                return httpx.Response(200, json={"id": "m777" if self.published == 1 else "r888"})
            if path.endswith("/r888"):
                self.calls.append((req.method, f"{req.url.host}{path}"))
                return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/x/"})
        return super().__call__(req)


def ok_published(results, store, api, monkeypatch):  # noqa: F811
    run = results / "2026-09-27-run5"
    with_reel(run)
    rec = sent(api, run)
    api.replies = [(rec.sent_at + 5, "/ok women 09-27", None)]
    return run


def test_preview_sends_reel_video(results):  # noqa: F811
    run = results / "2026-09-27-run5"
    with_reel(run)
    api = ReelApis()
    tg, _, _ = clients(api)
    rec = notify.preview(run, run.name, tg)
    methods = [c[1].rsplit("/", 1)[-1] for c in api.calls]
    assert methods == ["sendMediaGroup", "sendVideo", "sendMessage", "sendMessage"]
    # 영상에 /ok 로 답장해도 이 미리보기 것
    slides_n = len(notify.slide_paths(notify.load_manifest(run), run))
    assert len(rec.message_ids) == slides_n + 3


def test_reels_at_posts_reel_later_with_same_ok(results, store, monkeypatch):  # noqa: F811
    api = ReelApis()
    run = ok_published(results, store, api, monkeypatch)
    tg, ig, host = clients(api)
    # women 은 reels_at 이 있어 캐러셀 게시 때는 릴스를 올리지 않는다
    monkeypatch.setattr(publish, "load_profile", reels_at("19:00"))
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert api.published == 1 and not api.uploads
    # 19:00 릴스: 영상을 사진 저장소에 잠시 올려 공개 주소(video_url)로 넘기고 게시
    assert publish.run_reels("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert api.published == 2
    reel = api.media[-1]
    assert reel["media_type"] == "REELS" and "upload_type" not in reel
    assert reel["video_url"].endswith("-reel/01.mp4") and not api.uploads
    # women 은 그리드에서 빼고 릴스 탭에만
    assert reel["share_to_feed"] == "false" and "#" not in reel["caption"]  # 해시태그는 댓글로
    # 게시 API 에 AI 레이블 값이 없어 영상 안에 IPTC 표시를 넣는다
    assert ai_label.read_video(run / "reels" / "reel.mp4") == "trainedAlgorithmicMedia"
    done = PublishRecord.model_validate_json((run / "published.json").read_text())
    assert done.media_id == "m777" and done.reel_media_id == "r888"
    post = store.posts()[-1]
    assert post.reel_permalink == "https://www.instagram.com/reel/x/"
    assert "릴스도 올렸어요" in api.texts[-1]
    # 다시 불려도 두 번 올리지 않는다
    assert publish.run_reels("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert api.published == 2
    # again: 음악을 넣어 다시 만든 영상처럼 한 번 더 올린다
    kw = dict(store=store, tg=tg, ig=ig, host=host, again=True)
    assert publish.run_reels("women", DAY, results, **kw) == 0
    assert api.published == 3


def reel_container(api: ReelApis) -> str:
    """릴스 컨테이너 id (마지막으로 만든 /media)."""
    return list(api.statuses)[-1]


def reels_at(at, delivery="instagram"):
    def load(account):
        p = notify.load_profile(account)
        p.schedule.reels_at = at
        p.reels.delivery = delivery
        return p

    return load


def test_no_reel_without_carousel(results, store):  # noqa: F811
    run = results / "2026-09-27-run5"
    with_reel(run)
    api = ReelApis()
    tg, ig, host = clients(api)
    assert publish.run_reels("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert api.published == 0 and not api.texts


def test_reel_right_after_carousel_when_no_time(results, store, monkeypatch):  # noqa: F811
    api = ReelApis()
    run = ok_published(results, store, api, monkeypatch)
    monkeypatch.setattr(publish, "load_profile", reels_at(None))
    tg, ig, host = clients(api)
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert api.published == 2
    done = json.loads((run / "published.json").read_text())
    assert done["reel_media_id"] == "r888"
    assert "릴스도 올렸어요" in api.texts[-1]


def test_stale_reel_rebuilt_before_posting(results, store, monkeypatch):  # noqa: F811
    """2026-10-28 run380: /order 뒤 옛 순서 영상이 그대로 올라감. 낡았으면 다시 만들고 올린다."""
    from tools import cover_look

    api = ReelApis()
    run = ok_published(results, store, api, monkeypatch)
    monkeypatch.setattr(publish, "load_profile", reels_at(None))
    monkeypatch.setattr(cover_look, "reel_stale", lambda out: True)
    built = []
    monkeypatch.setattr(publish, "rebuild_reel", lambda out, *a: built.append(out))
    tg, ig, host = clients(api)
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert built == [run] and api.published == 2


def test_reel_failure_keeps_carousel(results, store, monkeypatch):  # noqa: F811
    api = ReelApis()
    run = ok_published(results, store, api, monkeypatch)
    with_reel(run, seconds=120)  # 인스타 릴스 길이를 넘음
    monkeypatch.setattr(publish, "load_profile", reels_at(None))
    tg, ig, host = clients(api)
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert api.published == 1 and store.posts()[-1].media_id == "m777"
    assert "릴스를 올리지 못했어요" in api.texts[-1]


def test_reel_sent_to_telegram_instead_of_posting(results, store, monkeypatch):  # noqa: F811
    api = ReelApis()
    run = ok_published(results, store, api, monkeypatch)
    tg, ig, host = clients(api)
    monkeypatch.setattr(publish, "load_profile", reels_at("19:00", "telegram"))
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    before = len(api.calls)
    # 19:00: 인스타에는 올리지 않고 영상과 캡션을 텔레그램으로
    assert publish.run_reels("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert api.published == 1 and not api.uploads
    methods = [c[1].rsplit("/", 1)[-1] for c in api.calls[before:]]
    # 게시 전에 최종본(사진 + 영상)을 이미 보냈으면 영상은 다시 보내지 않는다 (2026-10-29)
    assert methods == ["sendMessage", "sendMessage"]
    assert any(c[1].endswith("/sendVideo") for c in api.calls[:before])
    m = notify.load_manifest(run)
    assert publish.post_caption(publish.load_profile("women"), m) in api.texts[-1]
    done = PublishRecord.model_validate_json((run / "published.json").read_text())
    assert done.reel_sent_at and done.reel_media_id is None
    # 다시 불려도 두 번 보내지 않는다
    assert publish.run_reels("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert len(api.calls) == before + 2


def test_reel_telegram_right_after_carousel_when_no_time(results, store, monkeypatch):  # noqa: F811
    api = ReelApis()
    run = ok_published(results, store, api, monkeypatch)
    monkeypatch.setattr(publish, "load_profile", reels_at(None, "telegram"))
    tg, ig, host = clients(api)
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert api.published == 1
    assert any(c[1].endswith("/sendVideo") for c in api.calls)
    assert json.loads((run / "published.json").read_text())["reel_sent_at"]


def test_reel_skipped_quietly_without_video(results, store, monkeypatch):  # noqa: F811
    api = ReelApis()
    run = ok_published(results, store, api, monkeypatch)
    tg, ig, host = clients(api)
    monkeypatch.setattr(publish, "load_profile", reels_at("19:00", "telegram"))
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    # 릴스 기능 전에 만든 결과 (영상 없음): 실패하지 않고 알림만
    m = notify.load_manifest(run)
    m.reels = None
    reels.write_manifest(run, m)
    before = len(api.calls)
    assert publish.run_reels("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert "릴스 영상이 없어 건너뛰었어요" in api.texts[-1]
    assert not any(c[1].endswith("/sendVideo") for c in api.calls[before:])


def test_reels_slot_and_dispatch(monkeypatch):
    import core.profile

    monkeypatch.setattr(schedule, "load_profile", core.profile.load_profile)
    p = core.profile.load_profile("women")
    p.schedule.enabled = True  # 프로필에서 잠시 꺼도 시간 계산은 시험
    # women 은 캐러셀 게시 직후 같이 올려 따로 깨어나는 칸이 없다
    assert p.schedule.reels_at is None
    assert not [sl for sl in schedule.slots(p) if sl.task == "reels"]
    p.schedule.reels_at = "19:00"
    got = [sl for sl in schedule.slots(p) if sl.task == "reels"]
    assert [sl.local() for sl in got] == ["19:00"]
    p.reels.enabled = False
    assert not [sl for sl in schedule.slots(p) if sl.task == "reels"]
    gh = schedule.DryRun()
    t = schedule.Task("women", "reels", "2026-10-02", True, True, 0)
    schedule.execute([t], None, gh)
    assert gh.sent == [
        (
            "publish.yml",
            {
                "account": "women",
                "date": "2026-10-02",
                "dry_run": False,
                "scheduled": True,
                "reels": True,
            },
        )
    ]
