r"""클로드 스레드에서 만든 이미지·영상을 텔레그램으로 보낸다 (2026-11-10 사용자 요청).

클로드 컨테이너에는 봇 토큰이 없어서 tg-send 워크플로가 대신 보낸다.
파일은 공개 주소(힉스필드 결과 등)로 받는다. 외부로 보내는 도구라 Claude 허용 목록에 없다.

사용법 (환경 변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요):
  python -m tools.tg_send --text "안내 글" --items '[{"url": "https://…/a.png", "caption": "1번"}]'
items 의 각 항목은 url(https) 과 caption(없어도 됨).
.mp4·.mov·.webm 은 영상, 나머지는 사진으로 보낸다.
이어지는 사진은 10장씩 앨범으로 묶고 각 사진 아래에 자기 caption 을 단다.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from tools.notify import MAX_PHOTO_CAPTION, Telegram

VIDEO_EXT = (".mp4", ".mov", ".webm")
MAX_BYTES = 50 * 1024 * 1024  # 텔레그램 봇 업로드 한도


def parse_items(raw: str) -> list[dict]:
    items = json.loads(raw) if raw.strip() else []
    if not isinstance(items, list):
        raise ValueError("items 는 목록이어야 합니다")
    out = []
    for it in items:
        if isinstance(it, str):
            it = {"url": it}
        url = str(it.get("url", ""))
        if urlparse(url).scheme != "https":
            raise ValueError(f"https 주소만 받습니다: {url[:80]}")
        out.append({"url": url, "caption": str(it.get("caption", ""))[:MAX_PHOTO_CAPTION]})
    return out


def is_video(url: str) -> bool:
    return urlparse(url).path.lower().endswith(VIDEO_EXT)


def batches(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """이어지는 사진은 10장까지 한 묶음, 영상은 하나씩."""
    out: list[tuple[str, list[dict]]] = []
    for it in items:
        if is_video(it["url"]):
            out.append(("video", [it]))
        elif out and out[-1][0] == "photo" and len(out[-1][1]) < 10:
            out[-1][1].append(it)
        else:
            out.append(("photo", [it]))
    return out


def fetch(client: httpx.Client, url: str, dest: Path) -> Path:
    with client.stream("GET", url, follow_redirects=True) as r:
        r.raise_for_status()
        size = 0
        with dest.open("wb") as f:
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError(f"50MB 가 넘는 파일: {url[:80]}")
                f.write(chunk)
    return dest


def send(tg: Telegram, client: httpx.Client, items: list[dict], text: str, tmp: Path) -> None:
    if text:
        tg.send(text)
    for n, (kind, group) in enumerate(batches(items)):
        if kind == "video":
            url = group[0]["url"]
            p = fetch(client, url, tmp / f"v{n}{Path(urlparse(url).path).suffix}")
            tg.send_video(p, group[0]["caption"])
            continue
        paths = [fetch(client, it["url"], tmp / f"p{n}_{i}.img") for i, it in enumerate(group)]
        if len(paths) == 1:
            tg.send_photos(paths, group[0]["caption"])
            continue
        media = [
            {"type": "photo", "media": f"attach://p{i}", "caption": it["caption"]}
            for i, it in enumerate(group)
        ]
        handles = [p.open("rb") for p in paths]
        try:
            files = {f"p{i}": (f"p{i}.jpg", h, "image/jpeg") for i, h in enumerate(handles)}
            tg.call("sendMediaGroup", files=files, chat_id=tg.chat_id, media=media)
        finally:
            for h in handles:
                h.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools.tg_send")
    ap.add_argument("--text", default="")
    ap.add_argument("--items", default="[]")
    a = ap.parse_args(argv)
    items = parse_items(a.items)
    if not items and not a.text:
        print("보낼 것이 없습니다", file=sys.stderr)
        return 1
    client = httpx.Client(timeout=120)
    tg = Telegram.from_env(client)
    with tempfile.TemporaryDirectory() as d:
        send(tg, client, items, a.text, Path(d))
    print(f"보냄: 글 {1 if a.text else 0}개, 파일 {len(items)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
