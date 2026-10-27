import json
from datetime import date, datetime

import httpx
import pytest
import yaml

from core.models import PickRecord, Theme
from core.profile import load_profile
from core.store import Store
from tests.test_plan import CATS, daily, fake_photos, product, store  # noqa: F401 - fixture
from tests.test_publish import CHAT, FakeApis
from tools import notify, pick

DAY = date(2026, 9, 27)


def tg_of(api: FakeApis) -> notify.Telegram:
    return notify.Telegram("TOKEN", CHAT, httpx.Client(transport=httpx.MockTransport(api)))


def write_candidates(out, plans):
    d = out / "picks"
    d.mkdir(parents=True, exist_ok=True)
    for n, p in enumerate(plans, 1):
        (d / f"p{n}.yaml").write_text(
            yaml.safe_dump(p.model_dump(mode="json", exclude_none=True), allow_unicode=True),
            encoding="utf-8",
        )


def variant(swap_top: bool = False, bad: bool = False):
    p = daily(text=None)
    if swap_top:
        p.items[1].product = "zigzag:99"
    if bad:
        p.items[0].product = "zigzag:12345"  # 창고에 없음
    return p


@pytest.fixture
def out(tmp_path, store):  # noqa: F811
    o = tmp_path / "out"
    write_candidates(o, [variant(), variant(bad=True), variant(), variant(swap_top=True)])
    return o


def test_validate_drops_bad_and_duplicate_candidates(out, store):  # noqa: F811
    notes = pick.validate(out, load_profile("women"), store)
    assert len(notes) == 2  # 창고에 없는 상품, 같은 구성
    paths = pick.candidate_paths(out)
    assert [p.name for p in paths] == ["p1.yaml", "p2.yaml"]
    assert pick.load_candidates(out)[1].items[1].product == "zigzag:99"


@pytest.mark.usefixtures("fixed_times")
def test_send_makes_sheets_and_keyboard(out, store):  # noqa: F811
    profile = load_profile("women")
    pick.validate(out, profile, store)
    api = FakeApis()
    rec = pick.send(out, "2026-09-27-run3", tg_of(api), profile, store)
    assert rec.count == 2 and rec.round == 1
    assert (out / "picks" / "sheets" / "p1.jpg").exists()
    assert (out / "picks" / "pick.json").exists()
    methods = [c[1].rsplit("/", 1)[-1] for c in api.calls]
    assert methods == ["sendMediaGroup", "sendMessage"]
    kb = pick.keyboard(profile, DAY, 5, True)["keyboard"]
    assert kb[0][0]["text"] == "/pick 1 women 09-27" and kb[-1][0]["text"].startswith("/again")
    assert "/again" not in str(pick.keyboard(profile, DAY, 5, False))
    text = pick.control_text(pick.load_candidates(out), profile, DAY, 1)
    assert "15:17까지 고르지 않으면 1번으로" in text


def rec(**kw) -> PickRecord:
    base = dict(account="women", date=DAY, run="r", count=5, sent_at=1000, message_ids=[11])
    return PickRecord(**(base | kw))


def upd(i, text, at=1100, to=None):
    msg = {"date": at, "chat": {"id": int(CHAT)}, "text": text}
    if to:
        msg["reply_to_message"] = {"message_id": to}
    return {"update_id": i, "message": msg}


def test_decide():
    profile = load_profile("women")
    d, note, _ = pick.decide([upd(1, "/pick 3 women 09-27")], rec(), CHAT, profile, False, 200, 5)
    assert d.choice == 3 and d.by == "user"
    # 마지막 명령이 이긴다, 범위 밖 번호·다른 날은 무시
    ups = [upd(1, "/pick 2"), upd(2, "/pick 9"), upd(3, "/pick 4 women 09-28")]
    assert pick.decide(ups, rec(), CHAT, profile, False, 200, 5)[0].choice == 2
    assert pick.decide([upd(1, "/skip")], rec(), CHAT, profile, False, 200, 5)[0].choice == "skip"
    # 직접 친 "2번", "4", "다시"
    assert pick.decide([upd(1, "2번")], rec(), CHAT, profile, False, 200, 5)[0].choice == 2
    assert pick.decide([upd(1, "4")], rec(), CHAT, profile, False, 200, 5)[0].choice == 4
    assert pick.decide([upd(1, "다시")], rec(), CHAT, profile, False, 200, 5)[0].choice == "again"
    assert pick.decide([], rec(), CHAT, profile, False, 200, 5)[0] is None
    # 마감: on_timeout
    d, _, _ = pick.decide([], rec(), CHAT, profile, True, 0, 5)
    assert d.choice == 1 and d.by == "timeout"
    profile.pick.on_timeout = "skip"
    assert pick.decide([], rec(), CHAT, profile, True, 0, 5)[0].choice == "skip"


def test_decide_again_rules():
    profile = load_profile("women")
    d, _, _ = pick.decide([upd(1, "/again")], rec(), CHAT, profile, False, 200, 5)
    assert d.choice == "again"
    # 마감이 가까우면 받지 않고 한 번만 알린다
    d, note, rid = pick.decide([upd(1, "/again")], rec(), CHAT, profile, False, 30, 5)
    assert d is None and "마감" in note and rid == 1
    d, note, _ = pick.decide([upd(1, "/again")], rec(), CHAT, profile, False, 30, 5, {1})
    assert d is None and note == ""
    # 횟수를 다 쓰면
    d, note, _ = pick.decide([upd(1, "/again")], rec(round=3), CHAT, profile, False, 200, 5)
    assert d is None and "2번까지" in note


def test_check_writes_decision_once(tmp_path):
    profile = load_profile("women")
    run = tmp_path / "2026-09-27-run3"
    (run / "picks").mkdir(parents=True)
    r = rec(run=run.name, sent_at=1000)
    (run / "picks" / "pick.json").write_text(r.model_dump_json())
    api = FakeApis(replies=[(1100, "/pick 2 women 09-27", None)])
    now = datetime.fromtimestamp(2000)
    act = pick.check(profile, DAY, tmp_path, tg_of(api), False, 200, now)
    assert (act.action, act.run, act.choice) == ("render", run.name, 2)
    assert pick.load_decision(run).choice == 2
    again = pick.check(profile, DAY, tmp_path, tg_of(api), False, 200, now)
    assert again.action == "done"
    # 후보가 없는 날
    assert pick.check(profile, date(2026, 9, 28), tmp_path, None, True, 0, now).action == "none"


def test_themes_again(store):  # noqa: F811
    profile = load_profile("women")
    day = date(2026, 9, 28)
    slot = [{"category": "아우터", "desc": "트렌치"}]
    store.save_themes(
        [Theme(date=day, title_hint="월요일 출근룩", mood="차분", slots=slot, type="상황")]
    )
    api = FakeApis()
    sent = pick.send_themes(profile, store, day, tg_of(api))
    assert sent.first == day and not sent.handled
    assert pick.themes_again(profile, store, tg_of(api)) is None
    api.replies = [(sent.sent_at + 5, "/themes again women", None)]
    assert pick.themes_again(profile, store, tg_of(api)).first == day
    assert pick.themes_again(profile, store, tg_of(api)) is None  # 한 번만


def page_html(pid: str, name: str, sales: str = "ON_SALE") -> str:
    item = {
        "name": name,
        "sales_status": sales,
        "final_price": 59000,
        "product_image_list": [{"origin_url": f"https://cf.example.com/{pid}.jpg"}],
    }
    data = {"product": item, "shop": {"name": "퍼샵"}}
    nxt = {"props": {"pageProps": {"dehydratedState": {"queries": [{"state": {"data": data}}]}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nxt)}</script>'


def test_guess_category():
    assert pick.guess_category("[MADE] 에코퍼 숏 자켓 (3color)") == "아우터"
    assert pick.guess_category("퍼 트리밍 니트 롱 부츠") == "신발"
    assert pick.guess_category("데님 롱 스커트") == "하의"
    assert pick.guess_category("퍼 숄더백") == "가방"
    assert pick.guess_category("무지 울 머플러") == "소품"
    assert pick.guess_category("무지 울 머플러", "/fix 2 (링크) 이걸로 아우터") == "아우터"


def test_add_links_puts_products_in_store(tmp_path, store):  # noqa: F811
    out = tmp_path / "out"
    (out / "picks").mkdir(parents=True)
    spec = {
        "run": "r",
        "fixes": [
            {
                "update_id": 1,
                "looks": [2],
                "text": "https://zigzag.kr/catalog/products/555 이걸로 아우터",
            },
            {
                "update_id": 2,
                "looks": [3],
                "text": "https://zigzag.link/abc 이거랑 https://zigzag.kr/catalog/products/777",
            },
            {"update_id": 3, "looks": [4], "text": "이너를 나시로"},
        ],
    }
    (out / "picks" / "edit.json").write_text(json.dumps(spec), encoding="utf-8")
    pages = {
        "555": page_html("555", "에코퍼 숏 자켓"),
        "666": page_html("666", "퍼 롱 부츠"),
        "777": page_html("777", "니트 원피스", "SOLD_OUT"),
    }
    calls = []

    def fetch(url):
        calls.append(url)
        if "zigzag.link" in url:
            return 200, "https://zigzag.kr/catalog/products/666?utm=share", ""
        pid = url.rsplit("/", 1)[-1]
        return 200, url, pages[pid]

    added = pick.add_links(out, store, DAY, fetch)
    assert [p.id for p in added] == ["zigzag:555", "zigzag:666"]
    got = {p.id: p for p in store.products()}
    assert got["zigzag:555"].category == "아우터" and got["zigzag:666"].category == "신발"
    assert got["zigzag:555"].shop == "퍼샵" and got["zigzag:555"].price == 59000
    assert "zigzag:777" not in got
    fixes = json.loads((out / "picks" / "edit.json").read_text(encoding="utf-8"))["fixes"]
    assert fixes[0]["products"][0]["id"] == "zigzag:555"
    assert fixes[1]["products"][0]["id"] == "zigzag:666"
    assert fixes[1]["link_errors"][0]["why"] == "품절이에요"
    assert "products" not in fixes[2]
    saved = json.loads((out / pick.USER_PRODUCTS).read_text(encoding="utf-8"))
    assert {r["id"] for r in saved} == {"zigzag:555", "zigzag:666"}

    # 다음 실행: 창고(main)에 없으면 결과 브랜치의 user_products.json 에서 다시 넣는다
    prev = tmp_path / "prev"
    (prev / "2026-10-27-run1").mkdir(parents=True)
    (out / pick.USER_PRODUCTS).rename(prev / "2026-10-27-run1" / pick.USER_PRODUCTS)
    fresh = Store("women", root=tmp_path / "fresh")
    fresh.save_products([p for p in store.products() if p.id not in ("zigzag:555", "zigzag:666")])
    assert pick.restore_user(fresh, prev) == 2
    assert pick.restore_user(fresh, prev) == 0
    assert "zigzag:555" in {p.id for p in fresh.products()}


def test_add_links_unreadable(tmp_path, store):  # noqa: F811
    out = tmp_path / "out"
    (out / "picks").mkdir(parents=True)
    spec = {"run": "r", "fixes": [{"update_id": 1, "looks": [1], "text": "https://zigzag.link/x"}]}
    (out / "picks" / "edit.json").write_text(json.dumps(spec), encoding="utf-8")

    def fetch(url):
        raise httpx.ConnectError("down")

    assert pick.add_links(out, store, DAY, fetch) == []
    fix = json.loads((out / "picks" / "edit.json").read_text(encoding="utf-8"))["fixes"][0]
    assert fix["link_errors"][0]["why"] == "링크를 열지 못했어요"
    assert not (out / pick.USER_PRODUCTS).exists()
