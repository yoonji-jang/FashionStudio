r"""지그재그 검색으로 참고 코디(착용컷) 찾기.

2026-11-16 사용자: "주제별로 지그재그에서 코디를 검색해봐.
내가 매번 참고코디 보내는게 너무 번거로워. 후보를 여러개 줘. 고르는 기준도 정하고".

흐름 (zigzag-refs 워크플로):
  1. `sheets`: 사용자가 승인한 검색어(스타일마다 1~2개)로 지그재그를 검색해
     검색어마다 인기 상품 20개쯤의 상세 사진(모델 착용컷)을 번호 붙인 확인표로 만든다.
     out/zigzag_refs/s<스타일 번호>/sheet_N.jpg + out/zigzag_refs/index.json
     (확인표마다 상품·쇼핑몰·칸 번호 순 사진 주소).
  2. Claude 가 확인표를 보고 기준에 맞는 착용컷을 골라 RefLook JSON 으로 쓰고
     `add` 로 refs.json 에 더한다.
     `add` 는 사진이 확인표에 있는지, 스타일마다·쇼핑몰마다 몇 벌인지, 한 상품에 한 벌인지 검사한다.
  3. `numbers`: 이번에 더한 참고 번호 (collect from_refs → daily candidates 의 pick refs).

검색 순서: 지그재그 기본 검색 결과에서 판매 중인 상품을 리뷰 수 많은 순으로 (인기순 대신).
사진·페이지 원본은 저장하지 않는다 (확인표는 out/ 에만). 쇼핑몰 담당자 이름·연락처는 읽지 않는다.
refs.json 에는 지그재그 상품 사진 주소만 남는다 ("지그재그 착용컷", tools.refs zigzag 와 같은 방식).

사용법:
  python -m tools.zigzag_refs sheets --terms "레드 니트;벨벳=벨벳 원피스;퍼=퍼 자켓"
  python -m tools.zigzag_refs add --account women --new out/zigzag_refs_new_s1.json \
      --theme "크리스마스 데이트룩"
  python -m tools.zigzag_refs numbers
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from core.models import RefLook
from core.store import Store

OUT = Path("out/zigzag_refs")
SOURCE = "지그재그 착용컷"


def parse_terms(text: str) -> list[tuple[str, list[str]]]:
    """ "레드 니트;벨벳=벨벳 원피스,벨벳 스커트" → [(스타일, [검색어 …])].
    스타일은 ; 또는 줄바꿈으로 나누고, = 앞이 스타일 이름, 뒤가 쉼표로 나눈 검색어
    (= 가 없으면 스타일 이름이 검색어)."""
    out: list[tuple[str, list[str]]] = []
    for part in text.replace("\n", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        style, _, rest = part.partition("=")
        style = style.strip()
        terms = [t.strip() for t in (rest or style).split(",") if t.strip()]
        if style and terms:
            out.append((style, terms))
    return out


def pick_cards(responses: list[dict], count: int, seen: set[str]) -> list[dict]:
    """검색 결과 카드 중 판매 중인 상품을 리뷰 수 많은 순으로 count 개 (이미 고른 상품은 빼고)."""
    from sources import zigzag

    cards: dict[str, dict] = {}
    for c in (c for r in responses for c in zigzag.iter_cards(r)):
        pid = str(c["catalog_product_id"])
        if pid in cards or pid in seen or zigzag.is_sold_out(c.get("sellable_status")):
            continue
        cards[pid] = c
    order = list(cards.values())  # 같은 리뷰 수면 지그재그 검색 순서 그대로
    order.sort(
        key=lambda c: zigzag.parse_count(c.get("display_review_count", c.get("review_count"))) or 0,
        reverse=True,
    )
    return order[:count]


def sheets(
    styles: list[tuple[str, list[str]]], per_term: int, out: Path, max_images: int = 12
) -> dict:
    """검색어마다 인기 상품 per_term 개의 상세 사진 확인표를 만든다. 반환: summary."""
    from sources import zigzag
    from tools import scout

    out.mkdir(parents=True, exist_ok=True)
    terms = [t for _, ts in styles for t in ts]
    raw = scout.search_responses(terms, min_items=per_term * 2)
    index: list[dict] = []
    notes: list[str] = []
    seen: set[str] = set()
    with scout.client() as c:
        for si, (style, ts) in enumerate(styles, 1):
            folder = out / f"s{si}"
            folder.mkdir(parents=True, exist_ok=True)
            n = 0
            for term in ts:
                picked = pick_cards(raw.get(term, []), per_term, seen)
                if not picked:
                    notes.append(f"{term}: 검색 결과 없음")
                made = 0
                for card in picked:
                    pid = str(card["catalog_product_id"])
                    seen.add(pid)
                    url = zigzag.PRODUCT_URL.format(id=pid)
                    try:
                        info = zigzag.parse_page(*scout.fetch_page(c, url))
                    except Exception as e:  # noqa: BLE001 - 한 상품 실패해도 계속
                        notes.append(f"{term}: 상품 {pid} 페이지 못 읽음 ({type(e).__name__})")
                        continue
                    if info.status != "on_sale" or not info.image_urls:
                        continue
                    data, kept = [], []
                    for u in info.image_urls[:max_images]:
                        try:
                            b = scout.fetch_bytes(c, u)
                        except Exception:  # noqa: BLE001 - 한 장 실패해도 계속
                            continue
                        data.append(b)
                        kept.append(u)
                    sheet = scout.contact_sheet(data, numbered=True)
                    if sheet is None:
                        continue
                    n += 1
                    made += 1
                    path = folder / f"sheet_{n}.jpg"
                    sheet.save(path, quality=80)
                    index.append(
                        {
                            "style": style,
                            "term": term,
                            "sheet": str(path),
                            "product": f"zigzag:{pid}",
                            "url": url,
                            "shop": info.shop or card.get("shop_name") or "",
                            "name": info.name or card.get("title") or "",
                            "reviews": zigzag.parse_count(
                                card.get("display_review_count", card.get("review_count"))
                            ),
                            "images": kept,
                        }
                    )
                notes.append(f"{term}: 상품 {len(picked)}개 중 확인표 {made}장")
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), "utf-8")
    summary = {
        "styles": [
            {"style": s, "folder": f"s{i}", "terms": ts} for i, (s, ts) in enumerate(styles, 1)
        ],
        "sheets": len(index),
        "notes": notes,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    return summary


def _load(path: Path, default):
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def add(
    items: list[dict],
    index: list[dict],
    added: list[dict],
    refs: list[RefLook],
    theme: str,
    per_style: int,
    per_shop: int,
    today: date,
) -> tuple[list[RefLook], list[dict], list[str]]:
    """Claude 가 고른 착용컷을 검사해 refs 에 더한다. 반환: (새 refs 전체, 새 added, 뺀 이유).
    added 는 이번 실행에 더한 것 [{n, style, shop, product, photo}] (스타일·쇼핑몰 개수 세기)."""
    by_photo = {u: row for row in index for u in row["images"]}
    used_photos = {r.photo for r in refs}
    added = list(added)
    start = max((r.n for r in refs), default=100) + 1
    new: list[RefLook] = []
    skipped: list[str] = []
    for item in items:
        photo = item.get("photo")
        row = by_photo.get(photo or "")
        label = item.get("name") or photo
        if row is None:
            skipped.append(f"{label}: 확인표에 없는 사진 (index.json 의 images 주소 그대로)")
            continue
        if photo in used_photos:
            skipped.append(f"{label}: 이미 refs.json 에 있는 사진")
            continue
        if any(a["product"] == row["product"] for a in added):
            skipped.append(f"{label}: 같은 상품({row['product']})에서 이미 한 벌 골랐음")
            continue
        if sum(a["style"] == row["style"] for a in added) >= per_style:
            skipped.append(f"{label}: 스타일 '{row['style']}' 은 이미 {per_style}벌")
            continue
        if row["shop"] and sum(a["shop"] == row["shop"] for a in added) >= per_shop:
            skipped.append(f"{label}: 한 쇼핑몰은 {per_shop}벌까지")
            continue
        tags = [t for t in (theme, row["style"]) if t]
        tags += [t for t in item.get("tags", []) if t not in tags]
        data = {
            **{k: v for k, v in item.items() if k not in ("n", "matches", "ready")},
            "n": start + len(new),
            "source": SOURCE,
            "added": today.isoformat(),
            "tags": tags,
        }
        try:
            ref = RefLook.model_validate(data)
        except Exception as e:  # noqa: BLE001 - 형식이 틀린 한 벌만 뺀다
            skipped.append(f"{label}: 형식 오류 {e}")
            continue
        new.append(ref)
        used_photos.add(photo)
        added.append(
            {
                "n": ref.n,
                "style": row["style"],
                "shop": row["shop"],
                "product": row["product"],
                "photo": photo,
                # 다시 돌릴 때(readd) 같은 고르기를 그대로 쓰려고 한 벌 전체를 남긴다
                "ref": ref.model_dump(mode="json", exclude={"matches", "ready"}),
            }
        )
    return refs + new, added, skipped


def readd(added: list[dict], refs: list[RefLook]) -> tuple[list[RefLook], list[dict]]:
    """지난 실행이 고른 착용컷(added.json 의 ref)을 번호를 새로 매겨 다시 더한다 (검색·고르기 없이).
    이미 refs.json 에 있는 사진은 그 번호를 그대로 쓴다."""
    by_photo = {r.photo: r for r in refs}
    start = max((r.n for r in refs), default=100) + 1
    new: list[RefLook] = []
    out: list[dict] = []
    for a in added:
        have = by_photo.get(a["photo"])
        if have is None:
            have = RefLook.model_validate({**a["ref"], "n": start + len(new)})
            new.append(have)
        out.append({**a, "n": have.n, "ref": {**a["ref"], "n": have.n}})
    return refs + new, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheets", help="검색어마다 인기 상품 상세 사진 확인표")
    s.add_argument("--terms", required=True, help="스타일;스타일=검색어,… (승인받은 것만)")
    s.add_argument("--per-term", type=int, default=20)
    s.add_argument("--max-images", type=int, default=12)
    s.add_argument("--out", type=Path, default=OUT)
    a = sub.add_parser("add", help="고른 착용컷을 검사해 refs.json 에 더함")
    a.add_argument("--account", required=True)
    a.add_argument("--new", type=Path, required=True)
    a.add_argument("--theme", default="", help="tags 첫 번째로 넣을 주제 제목")
    a.add_argument("--per-style", type=int, default=3)
    a.add_argument("--per-shop", type=int, default=3)
    a.add_argument("--out", type=Path, default=OUT)
    r = sub.add_parser("readd", help="지난 실행의 added.json 을 refs.json 에 다시 더함")
    r.add_argument("--account", required=True)
    r.add_argument("--out", type=Path, default=OUT)
    nb = sub.add_parser("numbers", help="이번에 더한 참고 번호 (JSON 목록)")
    nb.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    if args.cmd == "sheets":
        styles = parse_terms(args.terms)
        if not styles:
            print("검색어가 없습니다", file=sys.stderr)
            return 1
        summary = sheets(styles, args.per_term, args.out, args.max_images)
        print(f"확인표 {summary['sheets']}장")
        for line in summary["notes"]:
            print(line)
        return 0 if summary["sheets"] else 1
    if args.cmd == "numbers":
        print(json.dumps([a["n"] for a in _load(args.out / "added.json", [])]))
        return 0

    store = Store(args.account)
    added_path = args.out / "added.json"
    if args.cmd == "readd":
        old = _load(added_path, [])
        if not old or any("ref" not in a for a in old):
            print("다시 더할 고른 코디가 없음 (added.json 에 ref 가 없음)", file=sys.stderr)
            return 1
        refs, added = readd(old, store.refs())
        store.save_refs(refs)
        added_path.write_text(json.dumps(added, ensure_ascii=False, indent=1), "utf-8")
        print(f"지난 실행의 참고 코디 {len(added)}벌 다시 더함: {[a['n'] for a in added]}")
        return 0
    index = _load(args.out / "index.json", [])
    items = json.loads(args.new.read_text("utf-8"))
    refs, added, skipped = add(
        items,
        index,
        _load(added_path, []),
        store.refs(),
        args.theme,
        args.per_style,
        args.per_shop,
        date.today(),
    )
    before = len(_load(added_path, []))
    store.save_refs(refs)
    added_path.write_text(json.dumps(added, ensure_ascii=False, indent=1), "utf-8")
    print(f"참고 코디 {len(added) - before}벌 더함 (이번 실행 모두 {len(added)}벌)")
    for line in skipped:
        print("뺌:", line)
    styles: dict[str, int] = {}
    for x in added:
        styles[x["style"]] = styles.get(x["style"], 0) + 1
    print("스타일별:", ", ".join(f"{k} {v}" for k, v in styles.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
