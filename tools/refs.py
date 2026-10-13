r"""참고 코디 목록(data/<계정>/refs.json) 도구.

2026-10-13 사용자: 한 주 참고 코디를 100벌쯤 한꺼번에 모으고, 그 옷들을 창고에 한꺼번에 수집하자.

흐름: plan-themes 스킬이 쇼핑몰 착용컷을 칸별로 옮겨 refs.json 에 쓴다 → `check` 로 검사 →
`queries` 로 창고에 없는 칸의 지그재그 검색어를 모아 `tools.collect --from-refs` 로 수집 →
`match` 로 칸마다 창고에서 맞을 만한 상품을 찾아 `ready`(꼭 맞춰야 하는 칸이 다 있음)를 표시한다.
이 도구는 글자로만 고른다. 마지막 판단(사진 비교)은 outfit-candidates 스킬이 한다.

사용법:
  python -m tools.refs check --account women       # 검사 + keep_weeks 지난 것 지우기
  python -m tools.refs queries --account women     # 창고에 없는 칸의 검색어 (카테고리<TAB>검색어)
  python -m tools.refs match --account women       # 칸마다 맞을 만한 상품 찾기, ready 표시
  python -m tools.refs list --account women [--ready] [--tag 출근룩]
  python -m tools.refs send --account women    # 참고 사진 묶음을 텔레그램으로 (워크플로만)
  python -m tools.refs zigzag --account women --out out/refs/zigzag  # 창고 상품 착용컷 확인표
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from core.models import Category, Product, RefLook, RefSlot
from core.profile import load_profile
from core.store import Store

# 꼭 맞춰야 하는 칸. 가방·소품은 비슷한 것으로 바꿔도 된다 (styling.md "따라 입기")
REQUIRED: set[Category] = {"아우터", "상의", "하의", "원피스", "신발"}
MAX_MATCHES = 5

# 같은 색으로 보는 말 (상품 이름·색 옵션은 쇼핑몰마다 다르게 쓴다)
COLOR_WORDS: dict[str, list[str]] = {
    "블랙": ["블랙", "검정", "black", "먹색"],
    "화이트": ["화이트", "흰", "white", "오프화이트"],
    "아이보리": ["아이보리", "크림", "에크루", "오트밀", "화이트"],
    "크림": ["크림", "아이보리", "에크루", "오트밀"],
    "그레이": ["그레이", "회색", "멜란지", "grey", "gray"],
    "차콜": ["차콜", "챠콜", "다크그레이", "charcoal"],
    "네이비": ["네이비", "남색", "navy"],
    "베이지": ["베이지", "샌드", "라이트베이지", "beige"],
    "브라운": ["브라운", "갈색", "모카", "초코", "카멜", "다크브라운", "brown"],
    "카멜": ["카멜", "브라운", "탄"],
    "카키": ["카키", "올리브", "khaki"],
    "버건디": ["버건디", "와인", "레드브라운", "burgundy"],
    "데님": ["데님", "청", "블루"],
    "연청": ["연청", "라이트블루", "데님"],
    "중청": ["중청", "미디엄블루", "데님"],
    "진청": ["진청", "다크블루", "인디고", "생지", "데님"],
}


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def color_words(color: str) -> list[str]:
    for key, words in COLOR_WORDS.items():
        if key in color:
            return [norm(w) for w in words]
    return [norm(color)]


def has_color(p: Product, color: str) -> bool:
    words = color_words(color)
    hay = [norm(c) for c in p.colors] + [norm(p.name)]
    return any(w in h for w in words for h in hay)


def kind_score(p: Product, slot: RefSlot) -> float:
    """검색어 단어가 상품 이름에 몇 개 있는지 (0~1)."""
    words = [norm(w) for w in slot.query.split() if w]
    if not words:
        return 0.0
    name = norm(p.name)
    return sum(w in name for w in words) / len(words)


def match_slot(slot: RefSlot, products: list[Product]) -> list[str]:
    """같은 카테고리·같은 색 계열, 검색어 단어가 반 이상 이름에 있는 상품 (좋은 순)."""
    scored = []
    for p in products:
        if p.category != slot.category or p.status != "on_sale":
            continue
        # 이 칸의 검색어로 모은 상품은 검색 결과를 믿는다 (상품 이름이 "니트 조끼"처럼 달라도)
        searched = p.query == f"{slot.color} {slot.query}".strip()
        k = kind_score(p, slot)
        if not searched and (k < 0.5 or not has_color(p, slot.color)):
            continue
        scored.append((searched, k, p.rating or 0, p.id))
    scored.sort(reverse=True)
    return [s[-1] for s in scored[:MAX_MATCHES]]


def match(refs: list[RefLook], products: list[Product]) -> list[RefLook]:
    out = []
    for r in refs:
        found = {str(i): match_slot(s, products) for i, s in enumerate(r.slots)}
        ready = all(found[str(i)] for i, s in enumerate(r.slots) if s.category in REQUIRED)
        out.append(
            r.model_copy(update={"matches": {k: v for k, v in found.items() if v}, "ready": ready})
        )
    return out


def missing_queries(refs: list[RefLook]) -> dict[Category, list[str]]:
    """아직 창고에 맞는 상품이 없는 칸의 검색어 (카테고리별, 겹치면 하나로, 색을 붙여서)."""
    plan: dict[Category, list[str]] = {}
    for r in refs:
        for i, s in enumerate(r.slots):
            if s.category not in REQUIRED or r.matches.get(str(i)):
                continue
            q = f"{s.color} {s.query}".strip()
            if q not in plan.setdefault(s.category, []):
                plan[s.category].append(q)
    return plan


def ref_queries(refs: list[RefLook]) -> set[str]:
    """참고 코디 칸에서 나온 검색어 전부. 창고를 줄일 때 이 검색어로 모은 상품은 남긴다."""
    return {f"{s.color} {s.query}".strip() for r in refs for s in r.slots}


def merge(base: list[RefLook], new: list[RefLook]) -> list[RefLook]:
    """동시에 바뀐 refs.json 합치기: base(원격, 칸 맞추기 결과)에 new 에만 있는 번호를 더한다."""
    have = {r.n for r in base}
    return base + [r for r in new if r.n not in have]


def check(refs: list[RefLook], keep_weeks: int, today: date) -> tuple[list[RefLook], list[str]]:
    """번호가 겹치지 않는지, 사진 주소가 있는지 보고, keep_weeks 지난 참고는 뺀다."""
    errors = []
    seen: set[int] = set()
    for r in refs:
        if r.n in seen:
            errors.append(f"참고 {r.n}: 번호가 겹침")
        seen.add(r.n)
        if not r.photo:
            errors.append(f"참고 {r.n}: 사진 주소(photo)가 없음")
        if not any(s.category in ("하의", "원피스") for s in r.slots):
            errors.append(f"참고 {r.n}: 하의(또는 원피스) 칸이 없음")
    cutoff = today - timedelta(weeks=keep_weeks)
    return [r for r in refs if r.added >= cutoff], errors


def album(refs: list[RefLook], sites: list[str], out: Path, per_sheet: int = 12) -> list[Path]:
    """참고 사진을 번호와 함께 4×3 묶음으로 (2026-10-13 사용자: 참고 이미지를 나도 보고 싶어).
    사진은 out/ 에만 받고 저장소에 올리지 않는다."""
    from PIL import Image, ImageDraw

    from tools import scout
    from tools.overlay import load_font

    tiles = []
    for r in refs:
        if not r.photo:
            continue
        try:
            path = scout.ref_photo(r.photo, sites, out / "photos" / f"ref_{r.n}.jpg")
            im = Image.open(path).convert("RGB")
        except Exception as e:  # noqa: BLE001 - 한 장 못 받아도 나머지는 보낸다
            print(f"참고 {r.n} 사진을 받지 못함 ({e})", file=sys.stderr)
            continue
        tiles.append((r, im))
    cols, w, h, label = 4, 300, 420, 64
    font = load_font("Pretendard-Bold.otf", 26)
    small = load_font("Pretendard-Medium.otf", 18)
    sheets = []
    for start in range(0, len(tiles), per_sheet):
        chunk = tiles[start : start + per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        img = Image.new("RGB", (cols * w, rows * (h + label)), "#F4F2EE")
        draw = ImageDraw.Draw(img)
        for i, (r, im) in enumerate(chunk):
            x, y = (i % cols) * w, (i // cols) * (h + label)
            im.thumbnail((w - 8, h - 8))
            img.paste(im, (x + (w - im.width) // 2, y + (h - im.height) // 2))
            draw.text((x + 8, y + h + 4), str(r.n), font=font, fill="#7A2E34")
            draw.text((x + 60, y + h + 10), r.name[:16], font=small, fill="#222222")
        path = out / f"album_{start // per_sheet + 1}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, quality=85)
        sheets.append(path)
    return sheets


def zigzag_pages(products: list[Product], refs: list[RefLook], count: int = 40) -> list[Product]:
    """착용컷을 볼 창고 상품 고르기: 한 벌의 중심이 되는 옷(아우터·원피스·상의·하의)을 쇼핑몰이
    겹치지 않게 번갈아, 이미 참고로 쓴 사진이 있는 상품은 빼고 (2026-10-13)."""
    used = {r.photo for r in refs}
    order: list[Category] = ["아우터", "원피스", "상의", "하의"]
    pools = {
        c: sorted(
            (
                p
                for p in products
                if p.category == c
                and p.status == "on_sale"
                and not used.intersection(map(str, p.image_urls))
            ),
            key=lambda p: (p.rating or 0) * (p.review_count or 0),
            reverse=True,
        )
        for c in order
    }
    picked: list[Product] = []
    shops: set[str] = set()
    while len(picked) < count and any(pools.values()):
        for c in order:
            pool = pools[c]
            while pool:
                p = pool.pop(0)
                if p.shop not in shops or not any(q.shop not in shops for q in pool):
                    picked.append(p)
                    shops.add(p.shop)
                    break
            if len(picked) >= count:
                break
    return picked


def zigzag_sheets(products: list[Product], out: Path, max_images: int = 12) -> list[dict]:
    """창고 상품마다 지그재그 상세 사진을 번호 붙인 확인표로 (쇼핑몰 MD 가 입힌 전신 코디 찾기).
    확인표 형식은 tools.scout refs 와 같다 (refs.json 의 images 가 칸 번호 순 사진 주소)."""
    import json

    from tools import scout

    out.mkdir(parents=True, exist_ok=True)
    report = []
    with scout.client() as c:
        for n, p in enumerate(products, 1):
            data, kept = [], []
            for u in map(str, p.image_urls[:max_images]):
                try:
                    b = scout.fetch_bytes(c, scout.normalize_image_url(u))
                except Exception:  # noqa: BLE001 - 한 장 실패해도 계속
                    continue
                data.append(b)
                kept.append(u)
            sheet = scout.contact_sheet(data, numbered=True)
            if sheet is None:
                continue
            path = out / f"sheet_{n}.jpg"
            sheet.save(path, quality=80)
            report.append({"url": str(p.url), "product": p.id, "sheet": str(path), "images": kept})
    (out / "refs.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "cmd", choices=["check", "queries", "match", "list", "send", "merge", "zigzag", "add"]
    )
    ap.add_argument("--new", type=Path, help="merge·add: 이 파일의 참고를 refs.json 에 더함")
    ap.add_argument("--out", type=Path, default=Path("out/refs_album"), help="send: 묶음 사진 폴더")
    ap.add_argument("--since", type=date.fromisoformat, help="send: 이 날짜 이후에 더한 것만")
    ap.add_argument("--count", type=int, default=40, help="zigzag: 착용컷을 볼 창고 상품 수")
    ap.add_argument("--account", required=True)
    ap.add_argument("--ready", action="store_true", help="list: 창고로 맞출 수 있는 것만")
    ap.add_argument("--tag", help="list: 이 주제 태그가 있는 것만")
    args = ap.parse_args(argv)
    profile = load_profile(args.account)
    store = Store(args.account)
    refs = store.refs()

    if args.cmd == "zigzag":
        rows = zigzag_sheets(zigzag_pages(store.products(), refs, args.count), args.out)
        print(f"창고 상품 착용컷 확인표 {len(rows)}장")
        return 0
    if args.cmd == "add":
        # Claude 가 새 참고를 out/ 의 JSON 목록으로 쓰고 이 명령으로 더한다 (번호는 이어서)
        import json

        from core.models import RefLook as Ref

        raw = json.loads(args.new.read_text(encoding="utf-8"))
        start = max((r.n for r in refs), default=100) + 1
        new = [Ref.model_validate({**item, "n": start + i}) for i, item in enumerate(raw)]
        store.save_refs(refs + new)
        print(
            f"참고 코디 {len(new)}벌 더함 ({start}~{start + len(new) - 1}번), "
            f"모두 {len(refs) + len(new)}벌"
        )
        return 0
    if args.cmd == "merge":
        from core.store import _refs

        new = _refs.validate_json(args.new.read_bytes())
        merged = merge(refs, new)
        store.save_refs(merged)
        print(f"참고 코디 {len(refs)} → {len(merged)}벌")
        return 0
    if args.cmd == "check":
        rs = profile.themes.ref_search
        kept, errors = check(refs, rs.keep_weeks if rs else 4, date.today())
        if len(kept) != len(refs):
            store.save_refs(kept)
        print(f"참고 코디 {len(kept)}벌 (지난 것 {len(refs) - len(kept)}벌 뺌)")
        for e in errors:
            print(e, file=sys.stderr)
        return 1 if errors else 0
    if args.cmd == "send":
        from tools import notify

        rs = profile.themes.ref_search
        new = [r for r in refs if not args.since or r.added >= args.since]
        sheets = album(new, rs.scout_sites if rs else [], args.out)
        if not sheets:
            print("보낼 참고 사진이 없음")
            return 0
        ready = sum(r.ready for r in new)
        caption = (
            f"[{profile.id}] 참고 코디 {len(new)}벌 (창고로 맞출 수 있음 {ready}벌). "
            "빼고 싶은 코디는 번호로 알려 주세요."
        )
        notify.Telegram.from_env().send_photos(sheets, caption)
        print(f"참고 사진 묶음 {len(sheets)}장 보냄")
        return 0
    if args.cmd == "queries":
        for cat, qs in missing_queries(refs).items():
            for q in qs:
                print(f"{cat}\t{q}")
        return 0
    if args.cmd == "match":
        refs = match(refs, store.products())
        store.save_refs(refs)
        ready = sum(r.ready for r in refs)
        print(f"창고로 맞출 수 있는 참고 코디 {ready}/{len(refs)}벌")
        return 0
    for r in refs:
        if (args.ready and not r.ready) or (args.tag and args.tag not in r.tags):
            continue
        slots = " + ".join(f"{s.color} {s.query}" for s in r.slots)
        print(f"{r.n}\t{'준비' if r.ready else '모자람'}\t{','.join(r.tags)}\t{r.name}\t{slots}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
