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
