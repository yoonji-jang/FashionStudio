from datetime import date

from core.models import RefLook
from tests.test_plan import product
from tools import refs

DAY = date(2026, 10, 19)


def ref(n=101, added=DAY, photo="https://woonan.co.kr/a.jpg", **kw):
    slots = kw.pop("slots", None) or [
        {
            "category": "상의",
            "color": "차콜",
            "desc": "차콜 모크넥 베스트",
            "query": "모크넥 니트베스트",
        },
        {"category": "하의", "color": "블랙", "desc": "블랙 미디스커트", "query": "미디 스커트"},
        {"category": "가방", "color": "블랙", "desc": "블랙 토트", "query": "토트백"},
    ]
    return RefLook(
        n=n, name="t", source="쇼핑몰 코디 (우난)", photo=photo, added=added, slots=slots
    )


def test_match_needs_kind_and_color_for_required_slots():
    vest = product(1, "상의", colors=["챠콜", "아이보리"])
    vest = vest.model_copy(update={"name": "울 모크넥 니트 베스트"})
    skirt_navy = product(2, "하의", colors=["네이비"]).model_copy(update={"name": "미디 스커트"})
    [r] = refs.match([ref()], [vest, skirt_navy])
    assert r.matches == {"0": ["zigzag:1"]}  # 차콜 = 챠콜, 띄어쓰기 달라도 맞음
    assert not r.ready  # 블랙 스커트가 없음 (가방은 없어도 됨)
    assert refs.missing_queries([r]) == {"하의": ["블랙 미디 스커트"]}

    skirt = product(3, "하의", colors=["블랙"]).model_copy(update={"name": "A라인 미디 스커트"})
    [r] = refs.match([ref()], [vest, skirt])
    assert r.ready and refs.missing_queries([r]) == {}


def test_check_reports_problems_and_drops_old():
    old = ref(n=102, added=date(2026, 9, 1))
    bad = ref(n=101, photo=None)
    kept, errors = refs.check([ref(), bad, old], keep_weeks=4, today=DAY)
    assert [r.n for r in kept] == [101, 101]
    assert any("번호가 겹침" in e for e in errors) and any("사진 주소" in e for e in errors)
