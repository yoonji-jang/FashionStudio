r"""코디 영상 시험 (2026-10-30 사용자: 벌마다 첫 장면 사진 없이 참고 사진으로 바로 영상).

얼굴 사진 + 그 벌의 모델 정면컷 + 빈 배경 사진을 영상 AI 에 참고 사진(input_references)으로
보내 짧은 영상을 만든다. 배경 사진이 없으면 싼 이미지 모델로 한 장 그린다.
OpenRouter 요금이 나가므로 사람이 켤 때만 (video-test 워크플로).

사용법:
  python -m tools.video_test --spec experiments/video_1030/look1.yaml --out out/video_test
  python -m tools.video_test --spec ... --out ... --models grok_lite seedance_mini
  python -m tools.video_test --spec ... --out ... --fake   # API 없이 흐름만
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx
import yaml
from PIL import Image

from tools.generate import CONFIG_PATH, REPO_ROOT, OpenRouterClient, read_ref

REF_MAX_PX = 1280  # 참고 사진 긴 변. 크면 요청이 무거워진다


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def jpeg(data: bytes, max_px: int = REF_MAX_PX) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def data_url(data: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def payload(
    model: dict, spec: dict, prompt: str, refs: list[bytes], first: bytes | None = None
) -> dict:
    """모델 inputs 가 first_frame 이면 첫 장면 한 장(frame_images),
    아니면 참고 사진들(input_references).
    둘을 함께 보내면 frame_images 만 쓰인다 (OpenRouter 문서)."""
    body = {
        "model": model["id"],
        "prompt": prompt,
        "duration": int(model.get("duration") or spec.get("duration", 4)),
        "resolution": model.get("resolution") or spec.get("resolution", "720p"),
        "aspect_ratio": spec.get("aspect_ratio", "9:16"),
        "generate_audio": False,
    }
    if model.get("inputs") == "first_frame":
        if first is None:
            raise ValueError("first_frame 모델인데 spec 에 first_frame 이 없어요")
        frame = {"type": "image_url", "image_url": {"url": data_url(first)}}
        body["frame_images"] = [frame | {"frame_type": "first_frame"}]
    else:
        body["input_references"] = [
            {"type": "image_url", "image_url": {"url": data_url(b)}} for b in refs
        ]
    return body


class VideoClient:
    def __init__(self, cfg: dict, key: str):
        self.cfg = cfg
        self.key = key
        self.http = httpx.Client(timeout=120, headers={"Authorization": f"Bearer {key}"})

    def make(self, body: dict) -> tuple[bytes, float | None, str]:
        res = self.http.post(self.cfg["endpoint"], json=body)
        if res.status_code >= 400:
            raise RuntimeError(f"HTTP {res.status_code}: {res.text[:400]}")
        job = res.json()
        url = job.get("polling_url") or f"{self.cfg['endpoint']}/{job['id']}"
        end = time.time() + self.cfg.get("timeout_sec", 1500)
        while time.time() < end:
            time.sleep(self.cfg.get("poll_sec", 15))
            st = self.http.get(url).json()
            status = st.get("status")
            if status == "completed":
                cost = (st.get("usage") or {}).get("cost")
                links = st.get("unsigned_urls") or []
                link = links[0] if links else f"{self.cfg['endpoint']}/{job['id']}/content?index=0"
                if link.startswith("https://openrouter.ai/api/"):
                    video = self.http.get(link, follow_redirects=True)
                else:  # 남의 저장소 주소에는 키를 보내지 않는다
                    video = httpx.get(link, timeout=300, follow_redirects=True)
                video.raise_for_status()
                return video.content, cost, job["id"]
            if status in ("failed", "cancelled", "expired"):
                raise RuntimeError(f"{status}: {json.dumps(st, ensure_ascii=False)[:400]}")
        raise TimeoutError(f"{job['id']} 가 시간 안에 끝나지 않았어요")


class FakeVideoClient:
    def make(self, body: dict) -> tuple[bytes, float | None, str]:
        return b"fake-mp4", 0.0, "fake"


def background(spec: dict, out: Path, cfg: dict, fake: bool) -> bytes:
    """빈 배경 사진. spec background.image 가 있으면 그것, 없으면 background.prompt 로 그린다."""
    bg = spec["background"]
    if bg.get("image"):
        data = read_ref(bg["image"])
    elif fake:
        im = Image.new("RGB", (720, 1280), "#e8eef2")
        buf = io.BytesIO()
        im.save(buf, "JPEG")
        data = buf.getvalue()
    else:
        gcfg = {**cfg["generate"], "aspect_ratio": spec.get("aspect_ratio", "9:16")}
        client = OpenRouterClient(gcfg, os.environ["OPENROUTER_API_KEY"])
        model = gcfg["models"][bg.get("model", "nano_banana")]
        data, cost = client.generate(model, bg["prompt"], [])
        (out / "background_cost.json").write_text(json.dumps({"cost": cost}), "utf-8")
    data = jpeg(data)
    (out / "background.jpg").write_bytes(data)
    return data


def first_frame(spec: dict, refs: list[bytes], out: Path, cfg: dict, fake: bool) -> bytes | None:
    """첫 장면 사진: 얼굴·코디·배경을 넣어 고급 이미지 모델로 한 장. 얼굴과 옷 디테일을 고정한다."""
    ff = spec.get("first_frame")
    if not ff:
        return None
    if fake:
        data = refs[-1]
    else:
        gcfg = {**cfg["generate"], "aspect_ratio": spec.get("aspect_ratio", "9:16")}
        client = OpenRouterClient(gcfg, os.environ["OPENROUTER_API_KEY"])
        prompt = (REPO_ROOT / ff["prompt_file"]).read_text(encoding="utf-8")
        model = gcfg["models"][ff.get("model", "nano_banana_pro")]
        data, cost = client.generate(model, prompt, refs)
        (out / "first_frame_cost.json").write_text(json.dumps({"cost": cost}), "utf-8")
    data = jpeg(data, 1920)
    (out / "first_frame.jpg").write_bytes(data)
    return data


def run(spec_path: Path, out: Path, models: list[str] | None, fake: bool = False) -> dict:
    cfg = load_config()
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    refs = [jpeg(read_ref(r)) for r in spec["refs"]]
    refs.append(background(spec, out, cfg, fake))
    first = first_frame(spec, refs, out, cfg, fake)
    prompt = (REPO_ROOT / spec["prompt_file"]).read_text(encoding="utf-8").strip()
    (out / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    vcfg = cfg["video"]
    client = FakeVideoClient() if fake else VideoClient(vcfg, os.environ["OPENROUTER_API_KEY"])
    record: dict = {"spec": str(spec_path), "results": []}
    for key in models or spec.get("models") or list(vcfg["models"]):
        model = vcfg["models"][key]
        t0 = time.time()
        entry: dict = {"model": key, "id": model["id"]}
        try:
            video, cost, job = client.make(payload(model, spec, prompt, refs, first))
            (out / f"{key}.mp4").write_bytes(video)
            entry.update(ok=True, cost=cost, job=job, file=f"{key}.mp4")
        except Exception as e:  # 한 모델이 실패해도 나머지는 계속
            entry.update(ok=False, error=str(e)[:400])
        entry["sec"] = round(time.time() - t0, 1)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        record["results"].append(entry)
    (out / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), "utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--models", nargs="*")
    ap.add_argument("--fake", action="store_true")
    args = ap.parse_args(argv)
    rec = run(args.spec, args.out, args.models, args.fake)
    return 0 if any(r["ok"] for r in rec["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
