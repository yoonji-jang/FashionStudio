"""실측표 준비 (2026-10-06): 기획의 옷 상품마다 상세 본문 이미지를 받아 size-reader 가 실측표를
찾기 쉽게 잘라 둔다. `python -m tools.plan sizes out/<날짜>/plan.yaml` 로 부른다.

지금까지는 기장을 상품 이름의 말(롱, 크롭 …)이나 기본값(자켓 68cm)으로 짐작해서, 오버핏 자켓이
크롭처럼 짧게 그려졌다. 실측표의 총장·어깨·가슴·소매 cm 를 기획 `cm`·`size` 에 적으면 코디컷
지시문의 실제 크기로 들어간다.

받은 이미지와 조각은 out/<날짜>/src/sizes/ 에 둔다. src/ 는 결과 브랜치에 올리지 않는다
(상세 본문 이미지에는 쇼핑몰 안내·연락처 배너가 섞일 수 있음). 본문 HTML 은 저장하지 않는다.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

from core.models import DailyPlan, Product
from core.store import Store

CLOTHES = ("아우터", "상의", "하의", "원피스")
PIECE_WIDTH = 720
PIECE_HEIGHT = 1300
OVERLAP = 80  # 조각 경계에 걸친 표가 잘리지 않게 겹친다
MAX_PIECES = 120
THUMB = 150


def split_tall(img: Image.Image) -> list[Image.Image]:
    """세로로 긴 상세 이미지를 폭 PIECE_WIDTH 로 줄이고 PIECE_HEIGHT 높이 조각으로 나눈다."""
    img = img.convert("RGB")
    if img.width > PIECE_WIDTH:
        img = img.resize((PIECE_WIDTH, round(img.height * PIECE_WIDTH / img.width)), Image.LANCZOS)
    if img.height <= PIECE_HEIGHT:
        return [img]
    pieces, top = [], 0
    while top < img.height:
        pieces.append(img.crop((0, top, img.width, min(top + PIECE_HEIGHT, img.height))))
        if top + PIECE_HEIGHT >= img.height:
            break
        top += PIECE_HEIGHT - OVERLAP
    return pieces


def overview(pieces: list[tuple[str, Path]], dest: Path, cols: int = 8) -> Path:
    """조각 모아 보기: 번호를 달아 한 장에. 실측표(격자·숫자 표)가 어느 조각에 있는지 찾는 용도."""
    rows = max(1, -(-len(pieces) // cols))
    cell_h = round(THUMB * PIECE_HEIGHT / PIECE_WIDTH) + 26
    sheet = Image.new("RGB", (cols * (THUMB + 10) + 10, rows * (cell_h + 10) + 10), (248, 247, 244))
    draw = ImageDraw.Draw(sheet)
    for n, (label, path) in enumerate(pieces):
        x, y = 10 + (n % cols) * (THUMB + 10), 10 + (n // cols) * (cell_h + 10)
        im = Image.open(path)
        im.thumbnail((THUMB, cell_h - 26))
        sheet.paste(im, (x, y + 22))
        draw.text((x, y + 2), label, fill=(122, 46, 52))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest, quality=85)
    return dest


def prepare_product(product: Product, dest: Path, client=None) -> dict:
    """상품 하나: 상세 페이지의 본문 이미지 주소와 실측 글 줄을 읽고, 이미지를 조각으로 저장."""
    from sources import zigzag
    from tools import scout

    own = client is None
    c = client or scout.client()
    try:
        code, page = scout.fetch_page(c, str(product.url))
        if code != 200:
            return {"error": f"상세 페이지 {code}"}
        urls = zigzag.description_images(page)
        lines = zigzag.size_lines(page)
        del page  # 본문 원본은 남기지 않는다
        pieces: list[tuple[str, Path]] = []
        for i, url in enumerate(urls):
            if len(pieces) >= MAX_PIECES:
                break
            try:
                img = Image.open(io.BytesIO(scout.fetch_bytes(c, url)))
            except (scout.ImageUnavailable, scout.Blocked, httpx.HTTPError, OSError) as e:
                # 판매자 서버의 본문 이미지 한 장이 500 이어도 나머지는 받는다 (2026-10-29 run403)
                print(f"상세 이미지를 받지 못해 건너뜀: {url} ({e})", file=sys.stderr)
                continue
            for k, piece in enumerate(split_tall(img)):
                path = dest / f"{i:02d}_{k}.jpg"
                dest.mkdir(parents=True, exist_ok=True)
                piece.save(path, quality=88)
                pieces.append((f"{i}-{k}", path))
    finally:
        if own:
            c.close()
    sheet = overview(pieces, dest / "overview.jpg") if pieces else None
    return {
        "size_lines": lines,
        "overview": str(sheet) if sheet else None,
        "pieces": {label: str(path) for label, path in pieces},
    }


def prepare(plan: DailyPlan, store: Store, out: Path) -> Path:
    """기획의 옷 상품마다 준비해 out/src/sizes/sizes.json 에 모은다. 준비한 상품은 건너뛴다."""
    from tools.plan import rel

    products = {p.id: p for p in store.products()}
    root = out / "src" / "sizes"
    index_path = root / "sizes.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    for it in plan.all_items():
        p = products[it.product]
        if p.category not in CLOTHES or p.id in index:
            continue
        info = prepare_product(p, root / p.id.split(":")[-1])
        info = {"name": it.name or p.name, "category": p.category, **info}
        if info.get("overview"):
            info["overview"] = rel(Path(info["overview"]))
            info["pieces"] = {k: rel(Path(v)) for k, v in info["pieces"].items()}
        index[p.id] = info
    root.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2))
    return index_path


def summary(index_path: Path) -> str:
    index = json.loads(index_path.read_text())
    lines = [f"실측 준비: {index_path}"]
    for pid, info in index.items():
        n = len(info.get("pieces") or {})
        lines.append(
            f"- {pid} {info.get('name')} ({info.get('category')}): 조각 {n}개, "
            f"실측 글 {len(info.get('size_lines') or [])}줄"
            + (f", 모아 보기 {info['overview']}" if info.get("overview") else "")
            + (f", 오류 {info['error']}" if info.get("error") else "")
        )
    return "\n".join(lines)


def size_sentence(category: str, m: dict) -> str | None:
    """size-reader 가 읽은 실측(cm)을 코디컷 지시문의 실제 크기 문장(영어)으로."""

    def num(key: str) -> float | None:
        v = m.get(key)
        return float(v) if isinstance(v, int | float) and v > 0 else None

    length = num("length")
    if not length:
        return None
    if category == "하의":
        parts = [f"about {length:g} cm long from waist to hem"]
        extra = [
            ("waist", "cm across the waist laid flat"),
            ("hip", "cm across the hips laid flat"),
            ("thigh", "cm across the thigh laid flat"),
            ("hem", "cm across the hem laid flat"),
        ]
    else:
        parts = [f"about {length:g} cm long from shoulder to hem"]
        extra = [
            ("shoulder", "cm across the shoulders"),
            ("chest", "cm across the chest laid flat"),
            ("sleeve", "cm long sleeves"),
        ]
    parts += [f"{num(k):g} {words}" for k, words in extra if num(k)]
    return ", ".join(parts)


def apply_measures(
    plan: DailyPlan, store: Store, measures: list[dict]
) -> tuple[DailyPlan, list[str]]:
    """size-reader 결과를 기획의 그 상품 모두(여러 벌이면 벌마다)에 `cm`·`size` 로 적는다."""
    products = {p.id: p for p in store.products()}
    found = {m["product"]: m for m in measures if m.get("found") and m.get("product") in products}
    notes: list[str] = []

    def fix(it):
        m = found.get(it.product)
        if not m:
            return it
        sentence = size_sentence(products[it.product].category, m)
        if not sentence:
            return it
        return it.model_copy(update={"cm": float(m["length"]), "size": sentence})

    for pid, m in found.items():
        sentence = size_sentence(products[pid].category, m)
        size = m.get("size") or "-"
        notes.append(f"{pid}: {sentence or '총장이 없어 적지 않음'} (사이즈 {size})")
    if plan.looks:
        looks = [lk.model_copy(update={"items": [fix(it) for it in lk.items]}) for lk in plan.looks]
        return plan.model_copy(update={"looks": looks}), notes
    return plan.model_copy(update={"items": [fix(it) for it in plan.items]}), notes
