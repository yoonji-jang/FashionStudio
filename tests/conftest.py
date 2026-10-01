import pytest

import core.profile

# 시간표 로직 테스트는 이 고정 시간표로 본다. 계정 profile.yaml 의 실제 시각을 바꿔도
# (예: 외부 스케줄러에 맞춰 정각으로) 시간 계산 테스트를 다시 쓰지 않아도 되게.
FIXED_SCHEDULE = {
    "candidates_at": "10:17",
    "pick_check_every": 30,
    "pick_deadline": "15:17",
    "publish_at": "20:03",
    "publish_retry_every": 30,
    "publish_retries": 4,
    "ok_check_every": 60,
    "ok_deadline": None,
    "ok_remind_at": None,
    "themes_at": "일 18:17",
    "collect_at": "월,목 04:17",
    "token_refresh_at": "일 18:47",
    "reels_at": None,
}


@pytest.fixture
def fixed_times(monkeypatch, request):
    """load_profile 이 women 계정을 FIXED_SCHEDULE 시간표로 돌려주게 한다."""
    original = core.profile.load_profile

    def load(account, *args, **kwargs):
        profile = original(account, *args, **kwargs)
        if account == "women":
            profile.schedule = profile.schedule.model_copy(update=FIXED_SCHEDULE)
        return profile

    import tools.comments
    import tools.feedback
    import tools.notify
    import tools.pick
    import tools.publish
    import tools.schedule
    import tools.share

    mods = (tools.schedule, tools.share, tools.pick, tools.comments, tools.feedback, tools.notify)
    for mod in (*mods, tools.publish, request.module):
        if hasattr(mod, "load_profile"):
            monkeypatch.setattr(mod, "load_profile", load)


# 20단계부터 women 계정은 코디 여러 벌 게시물(looks)이다. 한 벌 게시물 흐름 테스트는 예전 구성
# (표지 → 상품 장 → 요약 → 댓글 안내)으로 보고,
# 여러 벌 테스트만 @pytest.mark.looks 로 실제 설정을 쓴다.
SINGLE_OUTFIT = {
    "looks": {"enabled": False},
    "feed": {"slides": ["cover", "products", "summary", "comment"]},
    "ai_label": {"slides": ["cover"]},
}


def pytest_configure(config):
    config.addinivalue_line("markers", "looks: women 계정의 실제 여러 벌(looks) 설정으로 테스트")


@pytest.fixture(autouse=True)
def single_outfit(monkeypatch, request):
    if request.node.get_closest_marker("looks"):
        return
    original = core.profile.Profile.model_validate.__func__

    def validate(cls, data, *args, **kwargs):
        if isinstance(data, dict) and data.get("id") == "women":
            data = dict(data)
            for key, values in SINGLE_OUTFIT.items():
                data[key] = {**(data.get(key) or {}), **values}
        return original(cls, data, *args, **kwargs)

    monkeypatch.setattr(core.profile.Profile, "model_validate", classmethod(validate))
