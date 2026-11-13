r"""인스타 해시태그로 참고 코디 사진 찾기 (2026-11-13 사용자 "인스타검색 만들어").

공식 Instagram Graph API 만 쓴다 (페이지는 열지 않는다):
  ig_hashtag_search → <해시태그 id>/top_media·recent_media (media_type, media_url, children)
영상·릴스(VIDEO)는 빼고 사진만.
여러 장 게시물은 앞 사진 몇 장만 (profile instagram.hashtag.children).
계정마다 7일에 서로 다른 해시태그 30개까지라 검색어는 사용자가 승인한 것만 넣는다
(사용자 규칙: 검색하기 전에 해시태그 목록부터 확인받기, 주제마다 굵직한 것 두 개 정도).

저장소가 공개라 사진·게시물 주소·계정 이름은 남기지 않는다.
게시물 주소와 계정 이름은 요청조차 하지 않는다.
확인표와 사진 원본은 config/ig_view.pub 로 잠가(`seal`) 결과 브랜치에 두고,
클로드 스레드가 프로젝트 파일의 개인 키로 풀어 본다 (tools.sealed open --key-file).
고른 사진은 config/ref_seal.pub 로 다시 잠가 data/<계정>/user_refs/ 에 두고
refs.json 의 photo 를 "sealed:<이름>" 으로 쓴다
(사용자 사진과 같은 길: collect from_refs → candidates).

사용법 (환경 변수: 프로필 instagram.hashtag 의 토큰·사용자 id Secret 이름과 같은 이름):
  python -m tools.ig_hashtag sheets --account women --out out/ig_hashtag \
      --tag 크리스마스코디 --tag 연말데이트룩
  python -m tools.ig_hashtag seal out/ig_hashtag out/ig_sealed
  python -m tools.ig_hashtag adopt <푼 폴더> s1_03 s2_11 --name ig1113 --account women  # 스레드
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
from pathlib import Path

import httpx
from nacl.public import PublicKey

from tools import ref_search, sealed

VIEW_PUBKEY = Path("config/ig_view.pub")
FIELDS = "id,media_type,media_url,children{media_type,media_url}"
PAGE = 50  # Graph API 한 번에 최대 50
OTHER_BASE = {
    "graph.facebook.com": "https://graph.instagram.com",
    "graph.instagram.com": "https://graph.facebook.com",
}
# 거절 이유를 사람이 알아들을 말로 (Meta 오류 코드)
HELP = (
    "해시태그 검색은 'Instagram API with Facebook Login' 에서만 됩니다. 필요한 것: "
    "① 인스타 계정을 페이스북 페이지에 연결(비즈니스·크리에이터 계정) "
    "② developers.facebook.com 앱에 'Instagram Public Content Access' 기능 추가 "
    "③ instagram_basic·pages_show_list 권한으로 받은 Facebook 사용자 토큰(장기) "
    "④ 그 토큰과 페이스북 쪽 인스타 사용자 id 를 Secrets 에 넣고 profile instagram.hashtag 의 "
    "token_secret·user_id_secret 에 그 이름을 적기"
)


class SearchRefused(RuntimeError):
    """Meta 가 검색을 거절함 (권한·토큰 종류). 메시지에 토큰은 넣지 않는다."""


def _error(r: httpx.Response) -> str:
    try:
        e = r.json().get("error", {})
    except ValueError:
        return f"HTTP {r.status_code}"
    parts = [f"HTTP {r.status_code}"]
    for k in ("code", "error_subcode", "type"):
        if e.get(k) is not None:
            parts.append(f"{k}={e[k]}")
    if e.get("message"):
        parts.append(str(e["message"])[:300])
    return " ".join(parts)


def _get(c: httpx.Client, url: str, params: dict | None) -> dict:
    # params 를 비워 넘기면 httpx 가 주소의 쿼리를 지우므로 None 이면 주소 그대로
    r = c.get(url, params=params) if params else c.get(url)
    if r.status_code != 200:
        raise SearchRefused(_error(r))
    return r.json()


def hashtag_id(c: httpx.Client, base: str, token: str, user_id: str, tag: str) -> str | None:
    data = _get(
        c, f"{base}/ig_hashtag_search", {"user_id": user_id, "q": tag, "access_token": token}
    ).get("data", [])
    return str(data[0]["id"]) if data else None


def edge_media(
    c: httpx.Client, base: str, token: str, user_id: str, hid: str, edge: str, limit: int
) -> list[dict]:
    """해시태그의 인기(top_media)·최근 24시간(recent_media) 게시물. 페이지를 넘기며 limit 개까지."""
    url: str | None = f"{base}/{hid}/{edge}"
    params: dict | None = {
        "user_id": user_id,
        "fields": FIELDS,
        "limit": min(PAGE, limit),
        "access_token": token,
    }
    out: list[dict] = []
    while url and len(out) < limit:
        data = _get(c, url, params)
        out.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        params = None  # next 주소에 이미 다 들어 있다
    return out[:limit]


def photos(items: list[dict], tag: str, edge: str, children: int) -> list[ref_search.Hit]:
    """영상·릴스는 빼고 사진 주소만. 여러 장 게시물은 앞 사진 children 장 (중간 영상은 건너뜀)."""
    hits: list[ref_search.Hit] = []

    def add(url: str | None) -> None:
        if url and url.startswith("https://"):
            hits.append(ref_search.Hit(url, 0, 0, f"인스타 #{tag}", tag, edge))

    for m in items:
        kind = m.get("media_type")
        if kind == "IMAGE":
            add(m.get("media_url"))
        elif kind == "CAROUSEL_ALBUM":
            kids = [
                k for k in m.get("children", {}).get("data", []) if k.get("media_type") == "IMAGE"
            ]
            if kids:
                for k in kids[:children]:
                    add(k.get("media_url"))
            else:
                add(m.get("media_url"))  # children 을 못 받으면 대표 사진만
    return hits


def search(
    tags: list[str],
    edges: list[str],
    per_tag: int,
    base: str,
    token: str,
    user_id: str,
    children: int = 3,
    client: httpx.Client | None = None,
) -> tuple[list[ref_search.Hit], list[str], str]:
    """(사진, 알림, 실제로 쓴 API 주소). 같은 사진 주소는 한 번만. 해시태그·목록을 번갈아 섞는다.
    첫 해시태그에서 거절되면 다른 Graph API 주소(facebook ↔ instagram)로 한 번 더 해 본다.
    둘 다 거절되면 SearchRefused (두 거절 이유 + 할 일)."""
    notes: list[str] = []
    c = client or httpx.Client(timeout=30)
    per: list[list[ref_search.Hit]] = []
    try:
        bases = [base.rstrip("/")]
        host = httpx.URL(base).host
        if host in OTHER_BASE:
            bases.append(OTHER_BASE[host] + httpx.URL(base).path.rstrip("/"))
        first = tags[0]
        refusals: list[str] = []
        hid = None
        for b in bases:
            try:
                hid = hashtag_id(c, b, token, user_id, first)
                base = b
                break
            except SearchRefused as e:
                refusals.append(f"{httpx.URL(b).host}: {e}")
        else:
            raise SearchRefused(" / ".join(refusals) + " → " + HELP)
        ids = {first: hid}
        for t in tags[1:]:
            ids[t] = hashtag_id(c, base, token, user_id, t)
        for t, i in ids.items():
            if i is None:
                notes.append(f"#{t}: 해시태그를 찾지 못함")
                continue
            for e in edges:
                try:
                    items = edge_media(c, base, token, user_id, i, e, per_tag)
                except SearchRefused as err:
                    notes.append(f"#{t} {e}: 실패 ({err})")
                    continue
                videos = sum(1 for m in items if m.get("media_type") == "VIDEO")
                got = photos(items, t, e, children)
                notes.append(
                    f"#{t} {e}: 게시물 {len(items)}개 (영상 {videos}개 뺌) → 사진 {len(got)}장"
                )
                per.append(got)
    finally:
        if client is None:
            c.close()
    seen: set[str] = set()
    hits: list[ref_search.Hit] = []
    for i in range(max((len(p) for p in per), default=0)):
        for p in per:
            if i < len(p) and p[i].image not in seen:
                seen.add(p[i].image)
                hits.append(p[i])
    return hits, notes, base


def seal_dir(src: Path, dest: Path, pub: PublicKey | None = None) -> int:
    """확인표(sheet_N.jpg)와 사진(photos/sN_MM.jpg)을 보기 열쇠로 잠가 dest 에. 요약(summary.json)은
    사진 주소를 뺀 채로 옮긴다. 잠근 파일 수."""
    pub = pub or PublicKey(base64.b64decode(VIEW_PUBKEY.read_text().strip()))
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.glob("sheet_*.jpg")) + sorted((src / "photos").glob("*.jpg")):
        (dest / f"{f.stem}.sealed").write_bytes(sealed.seal(f.read_bytes(), pub))
        n += 1
    s = src / "summary.json"
    if s.exists():
        summary = json.loads(s.read_text(encoding="utf-8"))
        (dest / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return n


def adopt(src: Path, picks: list[str], name: str, account: str) -> list[str]:
    """푼 사진(src/sN_MM.jpg) 중 고른 것을 참고 사진 열쇠(config/ref_seal.pub)로 다시 잠가
    data/<계정>/user_refs/<name>_<번호>.sealed 에 둔다. refs.json 에 쓸 photo 값 목록."""
    out = Path("data") / account / "user_refs"
    out.mkdir(parents=True, exist_ok=True)
    urls = []
    for i, p in enumerate(picks, 1):
        f = src / f"{p.removesuffix('.jpg')}.jpg"
        if not f.exists():
            raise FileNotFoundError(f"푼 사진이 없음: {f}")
        stem = f"{name}_{i:02d}"
        (out / f"{stem}.sealed").write_bytes(sealed.seal(f.read_bytes()))
        urls.append(f"{sealed.PREFIX}{stem}")
    return urls


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheets", help="해시태그 검색 → 확인표·사진 (out 안에만)")
    s.add_argument("--account", required=True)
    s.add_argument(
        "--tag", action="append", required=True, help="사용자가 승인한 해시태그 (# 없이)"
    )
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--limit", type=int, default=320, help="확인표에 넣을 최대 사진 수")
    s.add_argument("--edge", action="append", choices=["top_media", "recent_media"])
    k = sub.add_parser("seal", help="확인표·사진을 보기 열쇠로 잠금")
    k.add_argument("src", type=Path)
    k.add_argument("dest", type=Path)
    a = sub.add_parser("adopt", help="고른 사진을 참고 사진(sealed:)으로")
    a.add_argument("src", type=Path)
    a.add_argument("picks", nargs="+", help="s<확인표>_<칸> (예 s1_03)")
    a.add_argument("--name", required=True, help="파일 이름 앞부분 (예 ig1113)")
    a.add_argument("--account", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "seal":
        n = seal_dir(args.src, args.dest)
        print(f"{n}개 잠금")
        return 0 if n else 1
    if args.cmd == "adopt":
        for u in adopt(args.src, args.picks, args.name, args.account):
            print(u)
        return 0

    from core.profile import load_profile

    ig = load_profile(args.account).instagram
    cfg = ig.hashtag
    token = os.environ.get(cfg.token_secret or ig.token_secret, "")
    user_id = os.environ.get(cfg.user_id_secret or ig.user_id_secret, "")
    if not token or not user_id:
        print("토큰·사용자 id 환경 변수가 없다", file=sys.stderr)
        return 1
    tags = [t.strip().lstrip("#").replace(" ", "") for t in args.tag if t.strip().lstrip("#")]
    args.out.mkdir(parents=True, exist_ok=True)
    summary: dict = {"tags": tags, "found": 0, "sheets": 0, "images": 0, "notes": []}
    try:
        hits, notes, base = search(
            tags, args.edge or cfg.edges, cfg.per_tag, cfg.api_base, token, user_id, cfg.children
        )
    except (SearchRefused, httpx.HTTPError) as e:
        msg = str(e) if isinstance(e, SearchRefused) else f"연결 실패 ({type(e).__name__})"
        summary["refused"] = msg.replace(token, "***")
        (args.out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"검색 거절: {summary['refused']}", file=sys.stderr)
        return 2
    photos_dir = args.out / "photos"
    if photos_dir.exists():
        shutil.rmtree(photos_dir)
    rows = ref_search.sheets(hits, args.out, args.limit, keep=photos_dir)
    shown = sum(len(r["images"]) for r in rows)
    summary.update(
        {
            "api": httpx.URL(base).host,
            "found": len(hits),
            "sheets": len(rows),
            "images": shown,
            "notes": notes,
            # 확인표 칸마다 어느 해시태그·목록에서 왔는지 (사진 주소는 넣지 않음)
            "cells": [
                [{"tag": q, "edge": e} for q, e in zip(r["queries"], r["engines"], strict=True)]
                for r in rows
            ],
        }
    )
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"사진 {len(hits)}장 → 확인표 {len(rows)}장 (사진 {shown}장)")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
