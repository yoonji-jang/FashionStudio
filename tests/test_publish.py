import base64
import json
import shutil
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

from core.models import PreviewRecord
from core.profile import load_profile
from tests.test_review import built, fake_photos, store  # noqa: F401 - pytest fixture
from tools import ai_label, notify, publish, share
from tools.ig_publish import IGError, Instagram
from tools.media_host import GitHubHost

pytestmark = pytest.mark.usefixtures("fixed_times")

DAY = date(2026, 9, 27)
CHAT = "4242"


class FakeApis:
    """텔레그램, 인스타, GitHub 사진 저장소를 흉내 낸다. 받은 요청을 기록한다."""

    def __init__(self, replies: list[tuple[int, str, int | None]] | None = None):
        self.calls: list[tuple[str, str]] = []
        self.replies = replies or []  # (보낸 시각, 글, 답장한 메시지 id)
        self.next_id = 100
        self.files: dict[str, str] = {}
        self.statuses: dict[str, list[str]] = {}
        self.texts: list[str] = []  # 보낸 텔레그램 글
        self.comments: list[tuple[str, str]] = []  # 인스타 댓글·대댓글

    def __call__(self, req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        self.calls.append((req.method, f"{host}{path}"))
        if host == "api.telegram.org":
            return self.telegram(req, path.rsplit("/", 1)[-1])
        if host == "graph.instagram.com":
            return self.instagram(req, path)
        if host == "api.github.com":
            return self.github(req, path)
        raise AssertionError(req.url)

    def telegram(self, req, method):
        if method == "getUpdates":
            updates = [
                {
                    "update_id": i,
                    "message": {
                        "message_id": 500 + i,
                        "date": at,
                        "chat": {"id": int(CHAT), "type": "private"},
                        "text": text,
                        **({"reply_to_message": {"message_id": to}} if to else {}),
                    },
                }
                for i, (at, text, to) in enumerate(self.replies, 1)
            ]
            # 텔레그램처럼 limit(기본 100)개까지, 오래된 것부터. 음수 offset 은 끝에서 그만큼
            form = parse_qs(req.content.decode())
            limit = int(form.get("limit", ["100"])[0])
            offset = int(form.get("offset", ["0"])[0])
            if offset < 0:
                updates = updates[offset:]
            return httpx.Response(200, json={"ok": True, "result": updates[:limit]})
        if method == "sendMediaGroup":
            body = req.content.decode("utf-8", "ignore")
            n = body.count('filename="p')
            ids = [{"message_id": self._id()} for _ in range(n)]
            return httpx.Response(200, json={"ok": True, "result": ids})
        if method == "sendMessage":
            form = parse_qs(req.content.decode())
            self.texts.append(form.get("text", [""])[0])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": self._id()}})

    def instagram(self, req, path):
        if path.endswith("/content_publishing_limit"):
            data = {"data": [{"quota_usage": 1, "config": {"quota_total": 100}}]}
            return httpx.Response(200, json=data)
        if req.method == "POST" and path.endswith("/media"):
            cid = f"c{self._id()}"
            self.statuses[cid] = ["IN_PROGRESS", "FINISHED"]
            return httpx.Response(200, json={"id": cid})
        if path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "m777"})
        if req.method == "POST" and path.endswith("/comments"):
            msg = parse_qs(req.content.decode()).get("message", [""])[0]
            self.comments.append(("comment", msg))
            return httpx.Response(200, json={"id": "k1"})
        if req.method == "POST" and path.endswith("/replies"):
            msg = parse_qs(req.content.decode()).get("message", [""])[0]
            self.comments.append(("reply", msg))
            return httpx.Response(200, json={"id": "k2"})
        key = path.rsplit("/", 1)[-1]
        if key in self.statuses:
            left = self.statuses[key]
            return httpx.Response(
                200, json={"status_code": left.pop(0) if len(left) > 1 else left[0]}
            )
        if key == "m777":
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/abc/"})
        raise AssertionError(path)

    def github(self, req, path):
        name = path.split("/contents/", 1)[1]
        if req.method == "GET":
            return httpx.Response(404, json={"message": "Not Found"})
        if req.method == "PUT":
            self.files[name] = json.loads(req.content)["content"]
            return httpx.Response(
                201, json={"content": {"sha": f"s-{name}"}, "commit": {"sha": "abc123"}}
            )
        self.files.pop(name)
        return httpx.Response(200, json={})

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id


def clients(api: FakeApis):
    http = httpx.Client(transport=httpx.MockTransport(api))
    tg = notify.Telegram("TOKEN", CHAT, http)
    ig = Instagram("1789", "IGTOKEN", client=http, sleep=lambda s: None)
    host = GitHubHost("me/FashionStudio-media", "GHTOKEN", client=http)
    return tg, ig, host


@pytest.fixture
def results(store, tmp_path):  # noqa: F811
    out = built(store, tmp_path)
    res = tmp_path / "results"
    shutil.copytree(out, res / "2026-09-27-run5")
    (res / "2026-09-27-run5" / "run.json").write_text(
        '{"run": "2026-09-27-run5", "sandbox": false}'
    )
    return res


def record(**kw) -> PreviewRecord:
    base = dict(account="women", date=DAY, run="r", sent_at=1000, message_ids=[11, 12])
    return PreviewRecord(**(base | kw))


def test_parse_command():
    r = record()
    assert notify.parse_command("/ok", r) == "ok"
    assert notify.parse_command("/OK women 09-27", r) == "ok"
    assert notify.parse_command("/skip 2026-09-27", r) == "skip"
    assert notify.parse_command("/ok@my_bot women", r) == "ok"
    assert notify.parse_command("/ok men 09-27", r) is None  # 다른 계정
    assert notify.parse_command("/ok women 09-28", r) is None  # 다른 날
    assert notify.parse_command("좋아요", r) is None


def test_find_decision_uses_latest_reply_after_preview():
    r = record()
    upd = [
        {"update_id": 1, "message": {"date": 900, "chat": {"id": 4242}, "text": "/ok"}},  # 전
        {"update_id": 2, "message": {"date": 1100, "chat": {"id": 9}, "text": "/ok"}},  # 남
        {"update_id": 3, "message": {"date": 1200, "chat": {"id": 4242}, "text": "/skip"}},
    ]
    assert notify.find_decision(upd, r, CHAT) == "skip"
    upd.append({"update_id": 4, "message": {"date": 1300, "chat": {"id": 4242}, "text": "/ok"}})
    assert notify.find_decision(upd, r, CHAT) == "ok"
    assert notify.find_decision(upd[:2], r, CHAT) is None
    # 다른 미리보기(다른 계정)에 답장한 /ok 는 이 미리보기 것이 아니다
    reply = {
        "date": 1400,
        "chat": {"id": 4242},
        "text": "/skip",
        "reply_to_message": {"message_id": 99},
    }
    assert notify.find_decision([{"update_id": 5, "message": reply}], r, CHAT) is None
    reply["reply_to_message"] = {"message_id": 12}
    assert notify.find_decision([{"update_id": 5, "message": reply}], r, CHAT) == "skip"
    reply["text"] = "/skip women 10-01"  # 날짜를 적었으면 날짜가 맞아야 한다
    assert notify.find_decision([{"update_id": 5, "message": reply}], r, CHAT) is None


def test_preview_sends_slides_caption_and_buttons(results):
    run = results / "2026-09-27-run5"
    api = FakeApis()
    tg, _, _ = clients(api)
    rec = notify.preview(run, "2026-09-27-run5", tg)
    methods = [c[1].rsplit("/", 1)[-1] for c in api.calls]
    assert methods == ["sendMediaGroup", "sendMessage", "sendMessage"]
    slides = notify.slide_paths(notify.load_manifest(run), run)
    assert len(rec.message_ids) == len(slides) + 2
    # 상품 링크는 /ok 뒤에 보낸다 (tools.share.send_after_ok)
    assert "zigzag.kr" not in api.texts[-1] and "상품 링크를 보내 드려요" in api.texts[-1]
    saved = notify.load_record(run)
    assert saved.run == "2026-09-27-run5" and saved.needs_human  # 검수 전
    text = notify.control_text(notify.load_manifest(run), load_profile("women"), False)
    assert "사람 확인 필요" in text and "/ok women 09-27" in text
    sandbox = notify.control_text(notify.load_manifest(run), load_profile("women"), True)
    assert "게시하지 않아요" in sandbox and "/ok" not in sandbox


def test_guard(results):
    run = results / "2026-09-27-run5"
    profile = load_profile("women")
    m = notify.load_manifest(run)
    images = notify.slide_paths(m, run)
    assert publish.guard(m, profile, images, {}) == []
    m.caption = m.caption.replace(profile.copy_rules.ai_notice, "")
    assert any("AI 합성 고지" in e for e in publish.guard(m, profile, images, {}))
    # 광고 표기 없이 만든 결과를 manual_link 프로필로 게시하려 하면 막는다
    c = profile.copy_rules
    m.monetize = "none"
    m.caption = m.caption.replace(c.ad_notice, "").lstrip()
    m.hashtags = [t for t in m.hashtags if t != c.ad_hashtag]
    profile.monetize = "manual_link"
    errors = "\n".join(publish.guard(m, profile, images, {}))
    assert "수익 방식이 바뀌었습니다" in errors and "광고 고지" in errors and "#광고" in errors


def sent(api: FakeApis, run) -> PreviewRecord:
    tg, _, _ = clients(api)
    return notify.preview(run, run.name, tg)


def with_link_page(account):
    """상품 페이지를 켠 프로필 (women 은 2026-09-30 부터 끔).
    fixed_times 가 바꿔 둔 load_profile 을 쓰려고 tools.notify 것을 부른다
    (다른 테스트 파일에서도)."""
    p = notify.load_profile(account)
    p.link_page.enabled = True
    return p


def test_publish_after_ok(results, store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(publish, "load_profile", with_link_page)
    monkeypatch.setattr(share, "load_profile", with_link_page)
    run = results / "2026-09-27-run5"
    api = FakeApis()
    rec = sent(api, run)
    api.replies = [(rec.sent_at + 5, "/ok women 09-27", None)]
    tg, ig, host = clients(api)
    code = publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host)
    assert code == 0
    posts = store.posts()
    assert posts[-1].date == DAY and posts[-1].permalink == "https://www.instagram.com/p/abc/"
    assert len(posts[-1].products) == 7
    # 댓글 DM 에 쓸 게시물 id 와 상품 링크
    assert posts[-1].media_id == "m777" and len(posts[-1].links) == 7
    assert posts[-1].links[0].url.startswith("https://")
    assert posts[-1].links[0].image.startswith("https://")
    done = json.loads((run / "published.json").read_text())
    assert done["media_id"] == "m777" and done["decision"] == "ok"
    # 게시 후 사진은 지우고, 상품 페이지(link_page)만 남는다
    assert set(api.files) == {"women/index.html"}
    page = base64.b64decode(api.files["women/index.html"]).decode()
    assert posts[-1].links[0].url in page and posts[-1].title in page
    assert posts[-1].links[0].image in page
    slides = len(notify.slide_paths(notify.load_manifest(run), run))
    puts = [c for c in api.calls if c[0] == "PUT"]
    assert len(puts) == slides + 1
    assert puts[0][1].endswith("/contents/women/2026-09-27-run5/01.jpg")
    # 표지에는 AI 합성 표시(IPTC)가 들어간 채로 올라간다 (women 프로필 ai_label: metadata)
    assert ai_label.read(run / "slides" / "01_cover.jpg") == "trainedAlgorithmicMedia"
    # 다시 돌려도 두 번 게시하지 않는다
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert sum(1 for c in api.calls if c[1].endswith("/media_publish")) == 1


def test_publish_waits_or_skips(results, store):  # noqa: F811
    run = results / "2026-09-27-run5"
    api = FakeApis()
    rec = sent(api, run)
    tg, ig, host = clients(api)
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 3
    api.replies = [(rec.sent_at + 5, "/skip", None)]
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert store.posts() == [] and not (run / "published.json").exists()
    assert not any("graph.instagram.com" in c[1] for c in api.calls)


def test_optional_approval_still_needs_ok_when_human_check(results):
    m = notify.load_manifest(results / "2026-09-27-run5")
    profile = load_profile("women")
    profile.approval = "optional"
    assert publish.approval(profile, m, record(), None)[0] == "wait"  # needs_human
    m.needs_human = False
    assert publish.approval(profile, m, record(), None)[0] == "auto"
    assert publish.approval(profile, m, record(), "skip")[0] == "skip"


def test_sandbox_result_is_not_published(results, store):  # noqa: F811
    run = results / "2026-09-27-run5"
    api = FakeApis()
    tg, ig, host = clients(api)
    notify.preview(run, run.name, tg, sandbox=True)
    with pytest.raises(publish.Blocked, match="샌드박스"):
        publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host, force=True)
    (run / "telegram.json").unlink()
    (run / "run.json").unlink()  # 만든 방식을 모르는 옛 결과도 막는다
    with pytest.raises(publish.Blocked, match="run.json"):
        publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host, force=True)


def test_find_run_prefers_previewed(results):
    shutil.copytree(results / "2026-09-27-run5", results / "2026-09-27-run6")
    assert publish.find_run(results, "women", DAY).name == "2026-09-27-run6"
    (results / "2026-09-27-run5" / "telegram.json").write_text(record().model_dump_json())
    assert publish.find_run(results, "women", DAY).name == "2026-09-27-run5"
    with pytest.raises(FileNotFoundError):
        publish.find_run(results, "men", DAY)


def test_instagram_errors_hide_token():
    def handler(req):
        return httpx.Response(400, json={"error": {"message": "bad IGTOKEN", "code": 190}})

    ig = Instagram("1", "IGTOKEN", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(IGError) as e:
        ig.publish(["https://x/1.jpg"], "c")
    assert e.value.token_problem and "IGTOKEN" not in str(e.value)


def test_find_chats_checks_token_and_lists_private_chats():
    api = FakeApis(replies=[(1, "hi", None), (2, "/start", None)])

    def handler(req):
        if req.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "my_bot"}})
        if req.url.path.endswith("/getWebhookInfo"):
            return httpx.Response(200, json={"ok": True, "result": {"url": ""}})
        return api(req)

    bot, chats = notify.find_chats("T", httpx.Client(transport=httpx.MockTransport(handler)))
    assert bot == "my_bot" and chats == {int(CHAT): 2}


def test_scheduled_publish_without_results_is_quiet(tmp_path, store):  # noqa: F811
    (tmp_path / "empty").mkdir()
    code = publish.run("women", DAY, tmp_path / "empty", store=store, scheduled=True)
    assert code == 0
    with pytest.raises(FileNotFoundError):
        publish.run("women", DAY, tmp_path / "empty", store=store)


def test_quiet_wait_sends_nothing(results, store):  # noqa: F811
    run = results / "2026-09-27-run5"
    api = FakeApis()
    sent(api, run)
    tg, _, _ = clients(api)
    before = len(api.calls)
    code = publish.run("women", DAY, results, tg=tg, store=store, notify_wait=False)
    assert code == publish.WAITING
    assert [c for c in api.calls[before:] if c[1].endswith("sendMessage")] == []


def overnight(account):
    """게시가 다음 날 아침이고 승인 마감이 새벽인 시간표 (women 2026-10-01 부터)."""
    p = with_link_page(account)
    p.schedule = p.schedule.model_copy(
        update={
            "candidates_at": "09:00",
            "pick_deadline": "18:00",
            "publish_at": "07:00",
            "ok_deadline": "05:00",
        }
    )
    return p


def test_ok_deadline_is_night_before_morning_post():
    p = overnight("women")
    dl = notify.ok_deadline(p, DAY)
    assert dl is not None and dl.isoformat() == "2026-09-27T05:00:00+09:00"
    p.schedule.ok_deadline = "23:00"  # 게시(07:00)보다 늦은 시각이면 전날 밤
    assert notify.ok_deadline(p, DAY).isoformat() == "2026-09-26T23:00:00+09:00"


def test_ok_after_deadline_is_not_used(results, store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(publish, "load_profile", overnight)
    run = results / "2026-09-27-run5"
    api = FakeApis()
    rec = sent(api, run)  # 지금 보낸 미리보기라 /ok 도 마감(9/27 05:00) 뒤
    api.replies = [(rec.sent_at + 5, "/ok women 09-27", None)]
    tg, ig, host = clients(api)
    before = len(api.texts)
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert not store.posts() or store.posts()[-1].date != DAY
    assert len(api.texts) == before  # 마감 알림은 schedule remind 가 보냄


def test_remembered_ok_is_used_when_telegram_forgot(results, store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(publish, "load_profile", with_link_page)
    monkeypatch.setattr(share, "load_profile", with_link_page)
    monkeypatch.setattr(share, "Store", lambda account: store)
    run = results / "2026-09-27-run5"
    api = FakeApis()
    rec = sent(api, run)
    api.replies = [(rec.sent_at + 5, "/ok women 09-27", None)]
    tg, ig, host = clients(api)
    # 매시 확인이 /ok 를 결과 폴더에 남긴다
    share.send_after_ok("women", str(DAY), results, tg)
    assert notify.remembered(run) == "ok"
    # 텔레그램이 24시간 지나 답장을 지웠어도 게시한다
    api.replies = []
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert store.posts()[-1].date == DAY
    # 텔레그램에 남은 /skip 이 기록보다 우선
    api.replies = [(rec.sent_at + 9, "/skip women 09-27", None)]
    assert (
        notify.approval_now(run, rec, with_link_page("women"), tg.updates(), tg.chat_id) == "skip"
    )


def test_quiet_hours_send_without_sound():
    from datetime import UTC, datetime

    cfg = {"telegram": {"timezone": "Asia/Seoul", "quiet": ["23:00", "07:00"]}}
    kst = [(14, 30, True), (21, 59, True), (22, 0, False), (13, 59, False)]  # UTC 시각
    for h, m, quiet in kst:
        assert notify.quiet_now(cfg, datetime(2026, 9, 30, h, m, tzinfo=UTC)) is quiet
    assert not notify.quiet_now({}, datetime(2026, 9, 30, 15, 0, tzinfo=UTC))


def test_remind_until_ok_then_deadline_notice(results, store, monkeypatch):  # noqa: F811
    from tools import schedule

    monkeypatch.setattr(schedule, "Store", lambda account: store)
    run = results / "2026-09-27-run5"
    api = FakeApis()
    rec = sent(api, run)
    tg, _, _ = clients(api)
    day = str(DAY)
    assert "보냄" in schedule.remind("women", day, results, tg)
    assert "게시하지 않아요" in api.texts[-1] and "/ok women 09-27" in api.texts[-1]
    assert "보냄" in schedule.remind("women", day, results, tg, final=True)
    assert "승인 마감" in api.texts[-1]
    api.replies = [(rec.sent_at + 5, "/ok women 09-27", None)]
    assert "이미 답함" in schedule.remind("women", day, results, tg)
