import base64
from datetime import UTC, date, datetime

from core.models import PostLink, PostRecord, ShareRequest
from core.profile import load_profile
from core.store import Store
from tests.test_publish import DAY, FakeApis, clients, results, sent  # noqa: F401 - fixture
from tests.test_review import built, fake_photos, store  # noqa: F401 - pytest fixture
from tools import link_page, publish, schedule, share

NOW = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)  # 서울 9/28 12:00


def links(n: int) -> list[PostLink]:
    return [
        PostLink(
            product=f"zigzag:{i}",
            category="상의",
            shop=f"샵{i}",
            name=f"상품 {i}",
            url=f"https://zigzag.kr/catalog/products/{i}",
        )
        for i in range(1, n + 1)
    ]


def test_parse_and_read_links_merge_replies():
    text = "1 https://s.zigzag.kr/a\n메모\n3) https://s.zigzag.kr/c\nhttps://s.zigzag.kr/b"
    assert share.parse_links(text, 3) == {
        1: "https://s.zigzag.kr/a",
        3: "https://s.zigzag.kr/c",
        2: "https://s.zigzag.kr/b",
    }
    req = ShareRequest(sent_at=100, message_ids=[7])

    def msg(uid, text, to=7, at=200, chat="42"):
        return {
            "update_id": uid,
            "message": {
                "date": at,
                "chat": {"id": chat},
                "text": text,
                "reply_to_message": {"message_id": to},
            },
        }

    updates = [
        msg(1, "1 https://a"),
        msg(2, "2 https://b"),
        msg(3, "1 https://old", at=50),  # 요청보다 이전
        msg(4, "2 https://other", to=8),  # 다른 메시지 답장
        msg(5, "2 https://x", chat="99"),  # 다른 대화
    ]
    assert share.read_links(req, updates, "42", 3)
    assert req.links == {1: "https://a", 2: "https://b"}
    assert not share.read_links(req, updates, "42", 3)  # 같은 답장은 바뀐 것 없음
    assert share.read_links(req, [*updates, msg(6, "1 https://fixed")], "42", 3)
    assert req.links[1] == "https://fixed" and req.links[2] == "https://b"


def test_page_uses_share_links(tmp_path):
    profile = load_profile("women")
    post = PostRecord(
        date=DAY,
        title="제목",
        links=links(2),
        share=ShareRequest(sent_at=1, links={2: "https://s.zigzag.kr/mine"}),
    )
    html = link_page.render(profile, [post], Store("women", root=tmp_path))
    assert 'href="https://zigzag.kr/catalog/products/1"' in html
    assert 'href="https://s.zigzag.kr/mine"' in html
    assert "products/2" not in html


def test_publish_asks_for_links_then_schedule_applies_them(
    results,  # noqa: F811
    store,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(share, "enabled", lambda profile: True)
    run = results / "2026-09-27-run5"
    api = FakeApis()
    rec = sent(api, run)
    api.replies = [(rec.sent_at + 5, "/ok women 09-27", None)]
    tg, ig, host = clients(api)
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    post = store.posts()[-1]
    assert post.share is not None and post.share.message_ids and post.share.links == {}

    # 사용자가 게시 알림에 답장으로 링크를 붙임 → schedule 이 반영
    ask = post.share.message_ids[0]
    api.replies.append((post.share.sent_at + 60, "1 https://s.zigzag.kr/one", ask))
    monkeypatch.setattr(share, "Store", lambda account: store)
    now = datetime.fromtimestamp(post.share.sent_at + 120, UTC)
    line = share.check("women", tg, now, host=host)
    assert "1개" in line
    post = store.posts()[-1]
    assert post.share is not None and post.share.links == {1: "https://s.zigzag.kr/one"}
    page = base64.b64decode(api.files["women/index.html"]).decode()
    assert "https://s.zigzag.kr/one" in page and post.links[1].url in page
    # 새 답장이 없으면 다시 올리지 않는다
    puts = sum(1 for c in api.calls if c[0] == "PUT")
    assert "새 링크 없음" in share.check("women", tg, now, host=host)
    assert sum(1 for c in api.calls if c[0] == "PUT") == puts


def test_check_off_or_old(tmp_path, monkeypatch):
    profile = load_profile("women")
    assert not share.enabled(profile)  # monetize: none
    assert "꺼짐" in share.check("women", object(), NOW)
    monkeypatch.setattr(share, "enabled", lambda profile: True)
    st = Store("women", root=tmp_path)
    old = PostRecord(date=date(2026, 9, 1), title="예전", links=links(1))
    old.share = ShareRequest(sent_at=1, message_ids=[3])
    st.save_posts([old])
    monkeypatch.setattr(share, "Store", lambda account: st)
    assert "기다리는 게시물 없음" in share.check("women", object(), NOW)


def test_schedule_adds_share_task(monkeypatch):
    real = schedule.load_profile

    def manual(account):
        p = real(account)
        p.monetize = "manual_link"
        return p

    monkeypatch.setattr(schedule, "load_profile", manual)
    monkeypatch.setattr(schedule, "all_slots", lambda accounts=None: [])
    assert schedule.due("17 1 * * *", NOW) == []  # 깨어난 작업이 없으면 따로 돌지 않음
    monkeypatch.undo()
    monkeypatch.setattr(schedule, "load_profile", manual)
    tasks = schedule.due("17 1 * * *", NOW)
    assert [t.task for t in tasks] == ["candidates", "share"]
