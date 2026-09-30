import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from core.models import PostLink, PostRecord
from core.profile import load_profile
from core.store import Store
from tools import comments, notify, schedule, share
from tools.ig_publish import Instagram

DAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)  # 23:00 KST
CHAT = "4242"
ME = "17841400000000000"


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+0000")


class FakeApis:
    """인스타 댓글·DM 과 텔레그램을 흉내 낸다."""

    def __init__(self, comments: list[dict]):
        self.comments = comments
        self.dms: list[tuple[str, str]] = []
        self.replies: list[tuple[str, str]] = []
        self.dm_error: dict | None = None
        self.tg_sent: list[str] = []
        self.tg_updates: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        if host == "api.telegram.org":
            method = path.rsplit("/", 1)[-1]
            if method == "getUpdates":
                return httpx.Response(200, json={"ok": True, "result": self.tg_updates})
            form = dict(httpx.QueryParams(req.content.decode()))
            self.tg_sent.append(form["text"])
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 900}})
        assert host == "graph.instagram.com"
        if path.endswith("/m1/comments"):
            return httpx.Response(200, json={"data": self.comments})
        if path.endswith(f"/{ME}/media"):
            data = [{"id": "m1", "permalink": "https://www.instagram.com/p/abc/"}]
            return httpx.Response(200, json={"data": data})
        if path.endswith(f"/{ME}/messages"):
            if self.dm_error:
                return httpx.Response(400, json={"error": self.dm_error})
            form = dict(httpx.QueryParams(req.content.decode()))
            cid = json.loads(form["recipient"])["comment_id"]
            self.dms.append((cid, json.loads(form["message"])["text"]))
            return httpx.Response(200, json={"recipient_id": "x", "message_id": "y"})
        if path.endswith("/replies"):
            form = dict(httpx.QueryParams(req.content.decode()))
            self.replies.append((path.split("/")[-2], form["message"]))
            return httpx.Response(200, json={"id": "r1"})
        raise AssertionError(path)


def links() -> list[PostLink]:
    return [
        PostLink(
            product=f"zigzag:{i}",
            category=c,
            shop=f"샵{i}",
            name=f"상품 {i}",
            url=f"https://zigzag.kr/catalog/products/{i}",
        )
        for i, c in enumerate(["아우터", "상의", "하의"], 1)
    ]


@pytest.fixture
def store(tmp_path):
    s = Store("women", root=tmp_path / "data")
    s.save_posts(
        [
            PostRecord(
                date=DAY,
                title="가을 트렌치 코디",
                products=[ln.product for ln in links()],
                permalink="https://www.instagram.com/p/abc/",
                links=links(),
            ),
            PostRecord(date=DAY - timedelta(days=10), title="오래된 글", media_id="old"),
        ]
    )
    return s


@pytest.fixture
def profile():
    p = load_profile("women")
    p.comments.enabled = True
    p.comments.mode = "builtin"
    p.comments.keywords = ["정보", "링크"]
    return p


def c(cid: str, text: str, who: str, ago: timedelta = timedelta(hours=1)) -> dict:
    return {"id": cid, "text": text, "timestamp": ts(NOW - ago), "from": {"id": who}}


def clients(api):
    http = httpx.Client(transport=httpx.MockTransport(api))
    ig = Instagram(ME, "tok", client=http)
    return ig, notify.Telegram("bot", CHAT, client=http)


def test_matches_and_parse_share_links():
    assert comments.matches("코디 정 보 부탁해요", ["정보"])
    assert comments.matches("LINK please", ["link"])
    assert not comments.matches("너무 예뻐요", ["정보", "링크"])
    assert comments.matches("너무 예뻐요", [])  # 키워드가 없으면 모든 댓글
    text = "2 https://s.zig.kr/b\nhttps://s.zig.kr/a\n없음\nhttps://s.zig.kr/c extra"
    assert share.parse_links(text, 3) == {
        2: "https://s.zig.kr/b",
        1: "https://s.zig.kr/a",
        3: "https://s.zig.kr/c",
    }


def test_dm_text_fits_instagram_limit(profile, store):
    post = store.posts()[0]
    text = comments.dm_text(profile, post, links())
    assert "'가을 트렌치 코디'" in text and "1. 아우터 · 샵1 상품 1\nhttps://zigzag.kr/" in text
    long = [ln.model_copy(update={"name": "아주 긴 이름 " * 30}) for ln in links() * 4]
    assert len(comments.dm_text(profile, post, long)) <= comments.DM_MAX


def test_keyword_comments_get_one_dm_each_person(profile, store):
    api = FakeApis(
        [
            c("c1", "정보 주세요", "u1"),
            c("c2", "너무 예뻐요", "u2"),
            c("c3", "정보!", "u1", timedelta(minutes=30)),  # 같은 사람 두 번째
            c("c4", "링크요", "u3", timedelta(days=8)),  # 7일 지남
            c("c5", "정보는 DM 으로 보내 드려요", ME),  # 내 댓글
            c("c6", "저도 정보", "u4", timedelta(minutes=10)),
        ]
    )
    ig, tg = clients(api)
    rep = comments.run(profile, store, ig, tg, NOW)
    assert rep.sent == 2 and [cid for cid, _ in api.dms] == ["c1", "c6"]
    assert "https://zigzag.kr/catalog/products/1" in api.dms[0][1]
    raw = (store.dir / "dm.json").read_text()
    # 댓글 id·글·작성자는 저장하지 않는다 (해시만)
    assert "정보" not in raw and ME not in raw
    for secret in ("c1", "u1"):
        assert f'"{secret}"' not in raw
    state = store.dm()
    assert len(state.posts["2026-09-28"].sent) == 2 and state.posts["2026-09-28"].media_id == "m1"
    assert len(state.posts["2026-09-28"].skipped) == 3  # c3, c4, c5
    # 다시 돌려도 같은 댓글에 두 번 보내지 않는다
    rep = comments.run(profile, store, ig, tg, NOW)
    assert rep.sent == 0 and len(api.dms) == 2 and not rep.changed
    assert api.replies == []


def test_dry_run_counts_without_sending(profile, store):
    api = FakeApis([c("c1", "정보", "u1")])
    ig, tg = clients(api)
    rep = comments.run(profile, store, ig, tg, NOW, dry_run=True)
    assert rep.sent == 1 and api.dms == [] and not (store.dir / "dm.json").exists()


def test_disabled_does_nothing(store):
    api = FakeApis([c("c1", "정보", "u1")])
    ig, tg = clients(api)
    rep = comments.run(load_profile("women"), store, ig, tg, NOW)
    assert rep.sent == 0 and api.dms == []


def test_public_reply(profile, store):
    profile.comments.public_reply = "DM 보내 드렸어요"
    api = FakeApis([c("c1", "정보", "u1")])
    ig, tg = clients(api)
    comments.run(profile, store, ig, tg, NOW)
    assert api.replies == [("c1", "DM 보내 드렸어요")]


def test_permission_error_stops_and_alerts_once_a_day(profile, store, monkeypatch):
    api = FakeApis([c("c1", "정보", "u1")])
    api.dm_error = {"message": "Application does not have permission", "code": 10}
    ig, tg = clients(api)
    monkeypatch.setattr(comments, "load_profile", lambda a: profile)
    monkeypatch.setattr(comments, "Store", lambda a: store)
    monkeypatch.setattr(comments, "instagram_for", lambda p: ig)
    msg = comments.check("women", tg, NOW)
    assert "권한" in msg and len(api.tg_sent) == 1
    assert store.dm().posts["2026-09-28"].sent == []  # 다음에 다시 시도
    comments.check("women", tg, NOW)
    assert len(api.tg_sent) == 1  # 같은 날 두 번 알리지 않음


def test_window_error_skips_only_that_comment(profile, store):
    api = FakeApis([c("c1", "정보", "u1")])
    api.dm_error = {"message": "outside of allowed window", "code": 10, "error_subcode": 2534022}
    ig, tg = clients(api)
    rep = comments.run(profile, store, ig, tg, NOW)
    assert rep.skipped == 1 and len(store.dm().posts["2026-09-28"].skipped) == 1


def test_share_reward_waits_for_links_then_uses_them(profile, store):
    profile.monetize = "manual_link"
    profile.comments.link_mode = "share_reward"
    api = FakeApis([c("c1", "정보", "u1")])
    ig, tg = clients(api)
    rep = comments.run(profile, store, ig, tg, NOW)
    assert rep.held == 1 and api.dms == []
    assert (
        "공유리워드 링크를 붙여" in api.tg_sent[0]
        and "zigzag.kr/catalog/products/2" in api.tg_sent[0]
    )
    req = store.dm().posts["2026-09-28"].share
    assert req.message_ids == [900]
    api.tg_updates = [
        {
            "update_id": 1,
            "message": {
                "message_id": 901,
                "date": req.sent_at + 60,
                "chat": {"id": int(CHAT)},
                "reply_to_message": {"message_id": 900},
                "text": "1 https://s.zig.kr/a\n2 https://s.zig.kr/b\n3 https://s.zig.kr/c",
            },
        }
    ]
    rep = comments.run(profile, store, ig, tg, NOW + timedelta(minutes=30))
    assert rep.sent == 1
    text = api.dms[0][1]
    assert text.startswith(profile.copy_rules.ad_notice) and "https://s.zig.kr/b" in text
    assert "zigzag.kr/catalog" not in text


def test_share_reward_falls_back_to_product_links(profile, store):
    profile.monetize = "manual_link"
    profile.comments.link_mode = "share_reward"
    api = FakeApis([c("c1", "정보", "u1")])
    ig, tg = clients(api)
    comments.run(profile, store, ig, tg, NOW)
    later = NOW + timedelta(hours=profile.comments.share_wait_hours, minutes=1)
    api.comments[0]["timestamp"] = ts(later - timedelta(hours=1))
    rep = comments.run(profile, store, ig, tg, later)
    assert rep.sent == 1 and "zigzag.kr/catalog/products/1" in api.dms[0][1]


def test_credentials_from_secrets_json(profile):
    env = {"IG_SECRETS": json.dumps({"IG_WOMEN_TOKEN": "t", "IG_WOMEN_USER_ID": "9", "X": "y"})}
    assert comments.credentials(profile, env) == ("9", "t")
    assert comments.credentials(profile, {"IG_TOKEN": "a", "IG_USER_ID": "1"}) == ("1", "a")


def test_share_reward_needs_manual_link(tmp_path):
    import shutil

    import yaml

    shutil.copytree("accounts", tmp_path / "accounts")
    path = tmp_path / "accounts/women/profile.yaml"
    data = yaml.safe_load(path.read_text())
    data["comments"]["link_mode"] = "share_reward"
    data["monetize"] = "none"
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    with pytest.raises(ValueError, match="manual_link"):
        load_profile("women", tmp_path / "accounts")


@pytest.mark.usefixtures("fixed_times")
def test_schedule_runs_comments_with_other_ticks(monkeypatch, profile):
    profile.comments.check_at = ["08:17"]
    profile.monetize = "none"  # 공유리워드 확인(share)은 test_share
    monkeypatch.setattr(schedule, "load_profile", lambda a: profile)
    crons = {(s.task, s.local()): s.cron("Asia/Seoul", DAY) for s in schedule.slots(profile)}
    assert crons[("comments", "08:17")] == "17 23 * * *"
    now = datetime(2026, 9, 28, 11, 5, tzinfo=UTC)
    tasks = schedule.due("3 11 * * *", now, ["women"])
    assert [t.task for t in tasks if t.task not in ("links", "fix")] == ["publish", "comments"]
    tasks = schedule.due("17 23 * * *", datetime(2026, 9, 27, 23, 20, tzinfo=UTC), ["women"])
    assert [t.task for t in tasks if t.task not in ("links", "fix")] == ["comments"]
    profile.comments.mode = "external"  # ManyChat 이 보내면 확인하지 않음
    ext = schedule.due("3 11 * * *", now, ["women"])
    assert [t.task for t in ext if t.task not in ("links", "fix")] == ["publish"]
    assert not [s for s in schedule.slots(profile) if s.task == "comments"]
    profile.comments.mode = "builtin"
    profile.comments.with_other_ticks = False
    off = schedule.due("3 11 * * *", now, ["women"])
    assert [t.task for t in off if t.task not in ("links", "fix")] == ["publish"]
    ran = []
    monkeypatch.setattr(comments, "check", lambda *a: ran.append(a) or "ok")
    schedule.execute(tasks, None, schedule.DryRun(), None, now, dry_run=True)
    assert ran and ran[0][0] == "women" and ran[0][3] is True


def test_no_keywords_sends_to_every_comment(profile, store):
    profile.comments.keywords = []
    api = FakeApis([c("c1", "너무 예뻐요", "u1"), c("c2", "어디 거예요?", "u2")])
    ig, tg = clients(api)
    rep = comments.run(profile, store, ig, tg, NOW)
    assert rep.sent == 2
