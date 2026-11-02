import io

import yaml
from PIL import Image

from tools import video_test


def _img(path, color):
    Image.new("RGB", (2000, 1000), color).save(path, "JPEG")


def test_fake_run_sends_refs_and_background(tmp_path, monkeypatch):
    face, look, bg = tmp_path / "face.jpg", tmp_path / "look.jpg", tmp_path / "bg.jpg"
    for p, c in ((face, "red"), (look, "green"), (bg, "blue")):
        _img(p, c)
    (tmp_path / "p.txt").write_text("walk in from the right", encoding="utf-8")
    spec = tmp_path / "s.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "refs": [str(face), str(look)],
                "background": {"image": str(bg)},
                "prompt_file": str(tmp_path / "p.txt"),
                "models": ["grok_lite"],
            }
        ),
        encoding="utf-8",
    )
    sent = []

    class Spy(video_test.FakeVideoClient):
        def make(self, body):
            sent.append(body)
            return super().make(body)

    monkeypatch.setattr(video_test, "FakeVideoClient", Spy)
    rec = video_test.run(spec, tmp_path / "out", None, fake=True)
    assert [r["ok"] for r in rec["results"]] == [True]
    body = sent[0]
    assert body["model"] == "x-ai/grok-imagine-video-1.5-lite"
    assert body["aspect_ratio"] == "9:16" and body["generate_audio"] is False
    refs = body["input_references"]
    assert len(refs) == 3 and refs[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # 참고 사진은 긴 변을 줄여 보낸다
    out_bg = Image.open(io.BytesIO((tmp_path / "out" / "background.jpg").read_bytes()))
    assert max(out_bg.size) == video_test.REF_MAX_PX
    assert (tmp_path / "out" / "prompt.txt").read_text(encoding="utf-8").startswith("walk")


def test_image_config_skips_size_for_cheap_model():
    from tools.generate import _image_config

    cfg = {"aspect_ratio": "9:16", "image_size": "2K"}
    assert _image_config({"image_size": None}, cfg) == {"aspect_ratio": "9:16"}
    assert _image_config({}, cfg) == {"aspect_ratio": "9:16", "image_size": "2K"}


def test_first_frame_model_sends_frame_images():
    model = {"id": "kwaivgi/kling-v3.0-pro", "inputs": "first_frame"}
    body = video_test.payload(model, {"duration": 4}, "walk", [b"a", b"b"], b"frame")
    assert "input_references" not in body
    assert body["frame_images"][0]["frame_type"] == "first_frame"
    ref = video_test.payload({"id": "x", "duration": 5, "resolution": "2K"}, {}, "walk", [b"a"])
    assert ref["duration"] == 5 and ref["resolution"] == "2K" and len(ref["input_references"]) == 1


def test_music_only_spec(tmp_path):
    spec = tmp_path / "m.yaml"
    spec.write_text(yaml.safe_dump({"music": {"prompt": "winter pop"}}), encoding="utf-8")
    rec = video_test.run(spec, tmp_path / "out", None, fake=True)
    assert rec["results"][0]["ok"] and (tmp_path / "out" / "music.mp3").exists()
