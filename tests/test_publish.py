import json
import shutil
from datetime import date

import httpx
import pytest

from core.models import PreviewRecord
from core.profile import load_profile
from tests.test_review import built, fake_photos, store  # noqa: F401 - pytest fixture
from tools import notify, publish
from tools.ig_publish import IGError, Instagram
from tools.media_host import GitHubHost

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
            return httpx.Response(200, json={"ok": True, "result": updates})
        if method == "sendMediaGroup":
            body = req.content.decode("utf-8", "ignore")
            n = body.count('filename="p')
            ids = [{"message_id": self._id()} for _ in range(n)]
            return httpx.Response(200, json={"ok": True, "result": ids})
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
    profile.monetize = "manual_link"
    errors = "\n".join(publish.guard(m, profile, images, {}))
    assert "수익 방식이 바뀌었습니다" in errors and "광고 고지" in errors and "#광고" in errors


def sent(api: FakeApis, run) -> PreviewRecord:
    tg, _, _ = clients(api)
    return notify.preview(run, run.name, tg)


def test_publish_after_ok(results, store):  # noqa: F811
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
    done = json.loads((run / "published.json").read_text())
    assert done["media_id"] == "m777" and done["decision"] == "ok"
    assert api.files == {}  # 게시 후 사진 지움
    slides = len(notify.slide_paths(notify.load_manifest(run), run))
    puts = [c for c in api.calls if c[0] == "PUT"]
    assert len(puts) == slides and puts[0][1].endswith("/contents/women/2026-09-27-run5/01.jpg")
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
