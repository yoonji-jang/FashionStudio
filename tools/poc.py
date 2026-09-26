"""2단계 이미지 품질 실험 (Photoroom). 같은 코디를 방식마다 여러 번 만들어 원본과 비교표를 만든다.

흐름: 상품 원본 사진 → Photoroom 누끼(옷은 Flat Lay, 나머지는 잘라내기)
      → 코드로 배치 → 그림자·배경 입히기 (방식마다 다름)

사용법:
  python -m tools.poc --samples samples/women/fall_leather.yaml --out out/poc/fall_leather
  python -m tools.poc ... --fake            # API 없이 흐름만 확인
  python -m tools.poc ... --variants A B --repeats 1

결과 (out 폴더):
  originals/  상품 페이지 원본 사진        cutouts/r<회차>/  누끼
  backdrop.jpg 배경 사진                   results/  코디컷 (<방식>_<회차>.jpg)
  sheets/     비교표 (cutouts.jpg, variant_<방식>.jpg)
  report.md   요약, scores.csv 점수표, run.json 실행 기록
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Literal

import yaml
from PIL import Image
from pydantic import BaseModel, Field

from core.models import Category
from core.profile import REPO_ROOT, account_dir, load_profile
from tools import compare, compose, photoroom, scout

EXPERIMENT_PATH = REPO_ROOT / "experiments" / "image_poc.yaml"
PRICE_PER_CALL_USD = 0.10

CATEGORY_EN = {
    "아우터": "outer",
    "상의": "top",
    "하의": "bottoms",
    "원피스": "dress",
    "가방": "bag",
    "신발": "shoes",
    "소품": "accessory",
}


class SampleItem(BaseModel):
    category: Category
    desc: str
    url: str | None = None
    image: str | None = None  # 저장소 기준 경로 (직접 캡처한 사진)
    alternates: list[str] = []
    crop: tuple[float, float, float, float] | None = None  # 사진 일부만 쓸 때 (0~1 비율)
    method: photoroom.CutoutMethod | None = None  # 생략하면 옷은 flatlay, 나머지는 segment
    segment: str | None = None  # segment 방식에서 남길 물건 (영어 단수)
    negative: str | None = None  # segment 방식에서 지울 물건 (예: 옆에 찍힌 다른 색 가방)


class SampleSet(BaseModel):
    account: str
    name: str
    items: list[SampleItem] = Field(min_length=1, max_length=10)


class Variant(BaseModel):
    id: str
    name: str
    finish: Literal["code", "ai_shadow", "ai_background"]


class Experiment(BaseModel):
    repeats: int = Field(default=3, ge=1, le=10)
    variants: list[Variant] = Field(min_length=1)


def _slug(i: int, item: SampleItem) -> str:
    return f"{i:02d}_{CATEGORY_EN[item.category]}"


def gather_originals(samples: SampleSet, out: Path, log: dict) -> list[Path]:
    """상품마다 원본 사진 1장을 확보한다. image 가 있으면 복사, 없으면 scout 로 받는다."""
    paths = []
    for i, item in enumerate(samples.items, 1):
        stem = out / "originals" / _slug(i, item)
        stem.parent.mkdir(parents=True, exist_ok=True)
        if item.image:
            src = REPO_ROOT / item.image
            dest = stem.with_suffix(src.suffix)
            shutil.copy(src, dest)
            entry = {"slot": _slug(i, item), "source": item.image}
        else:
            errors = []
            for url in [u for u in [item.url, *item.alternates] if u]:
                try:
                    dest, img_url = scout.fetch_product_image(url, stem.with_suffix(".img"))
                    fmt = (Image.open(dest).format or "img").lower().replace("jpeg", "jpg")
                    dest = dest.rename(dest.with_suffix(f".{fmt}"))
                    entry = {"slot": _slug(i, item), "page": url, "image_url": img_url}
                    break
                except Exception as e:  # noqa: BLE001 - 다음 후보로 넘어간다
                    errors.append(f"{url}: {e}")
            else:
                raise RuntimeError(f"{item.category} 사진을 받지 못했습니다:\n" + "\n".join(errors))
        if item.crop:
            dest = photoroom.crop_fraction(dest, item.crop, stem.with_name(stem.name + "_crop.png"))
        log["items"].append(entry)
        paths.append(dest)
    return paths


def get_backdrop(client: photoroom.EditClient, samples: SampleSet, out: Path, log: dict) -> Path:
    profile = load_profile(samples.account)
    if profile.visual.backdrop_ref:
        path = account_dir(samples.account) / profile.visual.backdrop_ref
        log["backdrop"] = str(path.relative_to(REPO_ROOT))
        return path
    log["backdrop"] = "generated"
    return photoroom.backdrop(client, profile.visual.backdrop, out / "backdrop.jpg")


def run(
    samples_path: Path,
    out: Path,
    experiment: Experiment,
    fake: bool = False,
    variant_ids: list[str] | None = None,
) -> dict:
    samples = SampleSet.model_validate(yaml.safe_load(samples_path.read_text(encoding="utf-8")))
    profile = load_profile(samples.account)
    variants = [v for v in experiment.variants if not variant_ids or v.id in variant_ids]
    client: photoroom.EditClient = photoroom.FakeClient() if fake else photoroom.PhotoroomClient()
    layout = compose.load_layout()
    size = tuple(layout["canvas"])

    log: dict = {
        "samples": str(samples_path),
        "account": samples.account,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sandbox": getattr(client, "api_key", "").startswith("sandbox_"),
        "items": [],
        "cutouts": [],
        "runs": [],
    }
    out.mkdir(parents=True, exist_ok=True)

    originals = gather_originals(samples, out, log)
    backdrop = get_backdrop(client, samples, out, log)

    cutout_sets: list[list[Path] | None] = []
    for r in range(1, experiment.repeats + 1):
        paths: list[Path] = []
        for i, (src, it) in enumerate(zip(originals, samples.items, strict=True), 1):
            method = it.method or photoroom.default_method(it.category)
            dest = out / "cutouts" / f"r{r}" / f"{_slug(i, it)}.png"
            if method == "segment" and r > 1 and cutout_sets[0]:
                # 잘라내기는 매번 같으므로 1회차 결과를 재사용 (요금 절약)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(cutout_sets[0][i - 1], dest)
                paths.append(dest)
                continue
            t0 = time.monotonic()
            entry = {"repeat": r, "slot": _slug(i, it), "method": method}
            try:
                photoroom.cutout(
                    client,
                    src,
                    it.category,
                    it.desc,
                    dest,
                    method,
                    it.segment,
                    negative=it.negative,
                )
                entry["ok"] = True
                paths.append(dest)
            except photoroom.QuotaError:
                raise
            except Exception as e:  # noqa: BLE001 - 기록하고 이번 회차 합성은 건너뛴다
                entry.update(ok=False, error=str(e)[:500])
            entry["sec"] = round(time.monotonic() - t0, 1)
            print(f"[cutout r{r} {entry['slot']}] {'ok' if entry['ok'] else 'FAIL'}", flush=True)
            log["cutouts"].append(entry)
        cutout_sets.append(paths if len(paths) == len(samples.items) else None)

    for r, paths in enumerate(cutout_sets, 1):
        if not paths:
            continue
        pairs = [(it.category, p) for it, p in zip(samples.items, paths, strict=True)]
        layered_path = out / "layered" / f"r{r}.png"
        compose.layer(pairs, layout).save(_mk(layered_path))
        for v in variants:
            dest = out / "results" / f"{v.id}_{r}.jpg"
            entry = {"variant": v.id, "run": r, "file": str(dest.relative_to(out))}
            t0 = time.monotonic()
            try:
                if v.finish == "code":
                    compose.compose(pairs, backdrop, dest, layout=layout)
                else:
                    photoroom.finish(
                        client,
                        layered_path,
                        dest,
                        v.finish,
                        size,
                        backdrop_image=backdrop,
                        backdrop_text=profile.visual.backdrop,
                    )
                entry["ok"] = True
            except photoroom.QuotaError:
                raise
            except Exception as e:  # noqa: BLE001 - 한 회차 실패는 기록만 하고 계속
                entry.update(ok=False, error=str(e)[:500])
            entry["sec"] = round(time.monotonic() - t0, 1)
            print(f"[{v.id}-{r}] {'ok' if entry['ok'] else 'FAIL'}", flush=True)
            log["runs"].append(entry)

    # 비교표
    labels = [f"{i} {CATEGORY_EN[it.category]}" for i, it in enumerate(samples.items, 1)]
    refs = list(zip(labels, originals, strict=True))
    cut_results = [
        (f"r{r} {lb}", p)
        for r, paths in enumerate(cutout_sets, 1)
        if paths
        for lb, p in zip(labels, paths, strict=True)
    ]
    if cut_results:
        compare.contact_sheet(
            refs,
            cut_results,
            out / "sheets" / "cutouts.jpg",
            title="Originals (top) vs Photoroom cutouts, one row per repeat",
            result_h=320,
            per_row=len(labels),
        )
    for v in variants:
        done = [r for r in log["runs"] if r["variant"] == v.id and r["ok"]]
        if done:
            compare.contact_sheet(
                refs,
                [(f"{v.id}-{r['run']}", out / r["file"]) for r in done],
                out / "sheets" / f"variant_{v.id}.jpg",
                title=f"Variant {v.id}: finish={v.finish}",
            )

    log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    log["api_calls"] = client.calls
    log["est_cost_usd"] = (
        0 if (fake or log["sandbox"]) else round(client.calls * PRICE_PER_CALL_USD, 2)
    )
    (out / "run.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    write_scores_template(out / "scores.csv", log["runs"], samples)
    write_report(out / "report.md", samples, variants, log)
    return log


def _mk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_scores_template(path: Path, runs: list[dict], samples: SampleSet) -> None:
    """회차 × 상품마다 한 줄. 색·형태·디테일은 0~2점, 누락·추가물건은 예/아니오."""
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["variant", "run", "item", "color", "shape", "detail", "missing", "extra_items"])
        for r in runs:
            if r["ok"]:
                for i, it in enumerate(samples.items, 1):
                    w.writerow([r["variant"], r["run"], f"{i} {it.category}", "", "", "", "", ""])


def write_report(path: Path, samples: SampleSet, variants: list[Variant], log: dict) -> None:
    lines = [
        f"# 이미지 품질 실험: {samples.name}",
        "",
        f"- 계정: {samples.account}, Photoroom 호출 {log['api_calls']}회"
        f" (예상 ${log['est_cost_usd']}{', 샌드박스' if log['sandbox'] else ''})",
        f"- 배경: {log['backdrop']}",
        f"- 시작 {log['started']}, 종료 {log['finished']}",
        "",
        "## 상품",
        "",
        "| # | 카테고리 | 설명 | 출처 |",
        "|---|---|---|---|",
    ]
    for i, (it, src) in enumerate(zip(samples.items, log["items"], strict=True), 1):
        origin = src.get("page") or src.get("source")
        lines.append(f"| {i} | {it.category} | {it.desc} | {origin} |")
    lines += [
        "",
        "## 방식별 결과",
        "",
        "| 방식 | 설명 | 성공 | 비교표 |",
        "|---|---|---|---|",
    ]
    for v in variants:
        runs = [r for r in log["runs"] if r["variant"] == v.id]
        ok = [r for r in runs if r["ok"]]
        sheet = f"sheets/variant_{v.id}.jpg" if ok else "-"
        lines.append(f"| {v.id} | {v.name} | {len(ok)}/{len(runs)} | {sheet} |")
    failed = [e for e in log["cutouts"] + log["runs"] if not e["ok"]]
    if failed:
        lines += ["", "## 실패", ""] + [f"- {json.dumps(f, ensure_ascii=False)}" for f in failed]
    lines += [
        "",
        "## 판정 기준",
        "",
        "상품마다 색·형태·디테일을 0~2점으로 매기고, 빠진 상품이나 추가된 물건이 있으면 표시한다.",
        "누끼(cutouts.jpg)에서 원본과 달라진 곳이 없는지, 코디컷이 게시할 만한지 함께 본다.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--experiment", type=Path, default=EXPERIMENT_PATH)
    ap.add_argument("--variants", nargs="*", help="일부 방식만 (예: A B)")
    ap.add_argument("--repeats", type=int, help="회차 수 덮어쓰기")
    ap.add_argument("--fake", action="store_true", help="API 없이 가짜 이미지로 흐름만 확인")
    args = ap.parse_args(argv)

    exp = Experiment.model_validate(yaml.safe_load(args.experiment.read_text(encoding="utf-8")))
    if args.repeats:
        exp.repeats = args.repeats
    try:
        log = run(args.samples, args.out, exp, args.fake, args.variants)
    except photoroom.QuotaError as e:
        print(
            f"Photoroom 크레딧·요금제 한도에 막혔습니다. 요금제와 결제를 확인하세요.\n{e}",
            file=sys.stderr,
        )
        return 2
    ok = sum(r["ok"] for r in log["runs"])
    print(f"완료: 코디컷 {ok}/{len(log['runs'])}, Photoroom 호출 {log['api_calls']}회")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
