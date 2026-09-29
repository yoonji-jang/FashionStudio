import base64
from datetime import UTC, date, datetime

from core.models import PostLink, PostRecord, ShareRequest
from core.profile import load_profile
from core.store import Store
from tests.test_publish import DAY, FakeApis, clients, results, sent  # noqa: F401 - fixture
from tests.test_review import built, fake_photos, store  # noqa: F401 - pytest fixture
from tools import link_page, notify, publish, schedule, share

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
    assert post.share is not None and post.share.links == {}
    # 미리보기 메시지와 게시 알림 모두 답장을 받는다. 게시 알림에도 상품별 지그재그 링크
    assert set(rec.message_ids) < set(post.share.message_ids)
    assert post.links[0].url in api.texts[-1] and "게시했어요" in api.texts[-1]

    # 사용자가 게시 알림에 답장으로 링크를 붙임 → schedule 이 반영
    ask = post.share.message_ids[-1]
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
    monkeypatch.setattr(share, "enabled", lambda profile: False)
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
    assert [t.task for t in tasks] == ["candidates", "links", "share"]
    # 고르기 마감 뒤 /ok 확인 칸 (서울 17:17)
    tasks = schedule.due("17 8 * * *", datetime(2026, 9, 28, 8, 20, tzinfo=UTC))
    assert [(t.task, t.date) for t in tasks] == [("links", "2026-09-28"), ("share", "2026-09-28")]


def test_links_sent_after_ok_and_replies_used_at_publish(
    results,  # noqa: F811
    store,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(share, "Store", lambda account: store)
    monkeypatch.setattr(share, "enabled", lambda profile: True)
    run = results / "2026-09-27-run5"
    api = FakeApis()
    rec = sent(api, run)
    tg, ig, host = clients(api)
    day = str(DAY)
    assert "/ok 기다림" in share.send_after_ok("women", day, results, tg)
    api.replies = [(rec.sent_at + 5, "/ok women 09-27", None)]
    assert "시험" in share.send_after_ok("women", day, results, tg, dry_run=True)
    assert not (run / share.ASKED).exists()
    assert "7개 보냄" in share.send_after_ok("women", day, results, tg)
    items = notify.load_manifest(run).plan.items
    assert all(str(p.url) in api.texts[-1] for p in items) and "/ok 확인" in api.texts[-1]
    [ask] = share.asked_ids(run)
    assert "이미 보냄" in share.send_after_ok("women", day, results, tg)

    # 게시 전에 링크 목록에 답장 → 게시하자마자 올린 첫 상품 페이지부터 공유리워드 링크
    api.replies.append((rec.sent_at + 9, "2 https://s.zigzag.kr/two", ask))
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    post = store.posts()[-1]
    assert post.share is not None and post.share.links == {2: "https://s.zigzag.kr/two"}
    page = base64.b64decode(api.files["women/index.html"]).decode()
    assert "https://s.zigzag.kr/two" in page
    assert "2. " in api.texts[-1] and "✓ 받음" in api.texts[-1]
    assert "이미 보냄" in share.send_after_ok("women", day, results, tg)


def test_links_slots_between_deadline_and_publish():
    profile = load_profile("women")
    profile.monetize = "manual_link"
    got = [s.local() for s in schedule.slots(profile) if s.task == "links"]
    assert got == ["16:17", "17:17", "18:17", "19:17"]
    profile.schedule.ok_check_every = 0
    assert not [s for s in schedule.slots(profile) if s.task == "links"]
    profile.schedule.ok_check_every = 60
    profile.monetize = "none"
    profile.link_page.send_links = True  # 수익화를 꺼도 링크 목록은 보냄
    assert [s for s in schedule.slots(profile) if s.task == "links"]
    profile.link_page.send_links = False
    assert not [s for s in schedule.slots(profile) if s.task == "links"]


def test_links_list_without_monetize_and_comment_hashtags(
    results,  # noqa: F811
    store,  # noqa: F811
    monkeypatch,
):
    """수익화를 꺼도 /ok 뒤 상품 링크 목록은 보내고(답장 안내 없이), 게시 뒤 🤍 댓글에 해시태그."""
    monkeypatch.setattr(share, "Store", lambda account: store)
    run = results / "2026-09-27-run5"
    api = FakeApis()
    rec = sent(api, run)
    tg, ig, host = clients(api)
    api.replies = [(rec.sent_at + 5, "/ok women 09-27", None)]
    assert "7개 보냄" in share.send_after_ok("women", str(DAY), results, tg)
    assert "답장" not in api.texts[-1] and "zigzag.kr" in api.texts[-1]
    assert publish.run("women", DAY, results, store=store, tg=tg, ig=ig, host=host) == 0
    assert store.posts()[-1].share is None
    manifest = notify.load_manifest(run)
    profile = load_profile("women")
    assert "#" not in publish.post_caption(profile, manifest)  # 해시태그는 캡션에 없음
    (kind, heart), (kind2, tags) = api.comments
    assert (kind, heart, kind2) == ("comment", "🤍", "reply")
    words = tags.split()
    assert len(words) <= profile.comment_tags.max and words[-1] == "#소통" and "#맞팔" in words
