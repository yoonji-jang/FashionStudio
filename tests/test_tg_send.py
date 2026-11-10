import pytest

from tools.tg_send import batches, parse_items


def test_parse_items_requires_https():
    with pytest.raises(ValueError):
        parse_items('[{"url": "http://a/b.png"}]')
    assert parse_items('["https://a/b.png"]') == [{"url": "https://a/b.png", "caption": ""}]


def test_batches_group_photos_and_split_videos():
    items = parse_items(
        "["
        + ",".join(f'"https://h/{i}.png"' for i in range(12))
        + ',"https://h/v.mp4","https://h/z.jpg"]'
    )
    kinds = [(k, len(g)) for k, g in batches(items)]
    assert kinds == [("photo", 10), ("photo", 2), ("video", 1), ("photo", 1)]
