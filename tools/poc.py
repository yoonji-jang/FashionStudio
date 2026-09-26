"""2단계 이미지 품질 실험. 같은 코디를 방식마다 여러 번 만들어 원본과 비교표를 만든다.

사용법:
  python -m tools.poc --samples samples/women/fall_leather.yaml --out out/poc/fall_leather
  python -m tools.poc ... --fake            # API 없이 흐름만 확인
  python -m tools.poc ... --variants A B --repeats 2

결과 (out 폴더):
  originals/  상품 페이지 원본 사진      prepped/  흰 배경 정사각형으로 정리
  extracted/  Gemini 정리컷              results/  코디컷 (<방식>_<회차>.jpg)
  sheets/     비교표 (extract.jpg, variant_<방식>.jpg)
  report.md   요약, scores.csv  사람(또는 검수 에이전트)이 채울 점수표, run.json  실행 기록
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import yaml
from PIL import Image
from pydantic import BaseModel, Field

from core.models import Category
from core.profile import REPO_ROOT, load_profile
from tools import compare, scout
from tools.gemini_image import (
    CATEGORY_EN,
    ComposeItem,
    FakeClient,
    GeminiClient,
    ImageClient,
    QuotaError,
    compose,
    extract,
    prep_image,
    render_prompt,
    resolve_model,
)

EXPERIMENT_PATH = REPO_ROOT / "experiments" / "image_poc.yaml"


class SampleItem(BaseModel):
    category: Category
    desc: str
    url: str | None = None
    image: str | None = None  # 저장소 기준 경로 (직접 캡처한 사진)
    alternates: list[str] = []


class SampleSet(BaseModel):
    account: str
    name: str
    items: list[SampleItem] = Field(min_length=1, max_length=10)


class Variant(BaseModel):
    id: str
    name: str
    model: str = "default"
    inputs: Literal["extracted", "raw"] = "extracted"
    layout: bool = True


class Experiment(BaseModel):
    repeats: int = Field(default=5, ge=1, le=20)
    image_size: str = "2K"
    extract_size: str = "1K"
    variants: list[Variant] = Field(min_length=1)


def _slug(i: int, item: SampleItem) -> str:
    return f"{i:02d}_{CATEGORY_EN[item.category]}"


def gather_originals(samples: SampleSet, out: Path, log: dict) -> list[Path]:
    """상품마다 원본 사진 1장을 확보한다. image 가 있으면 복사, 없으면 scout 로 받는다."""
    paths = []
    for i, item in enumerate(samples.items, 1):
        dest_stem = out / "originals" / _slug(i, item)
        if item.image:
            src = REPO_ROOT / item.image
            dest = dest_stem.with_suffix(src.suffix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dest)
            log["items"].append({"slot": _slug(i, item), "source": item.image})
            paths.append(dest)
            continue
        errors = []
        for url in [item.url, *item.alternates]:
            if not url:
                continue
            try:
                dest, img_url = scout.fetch_product_image(url, dest_stem.with_suffix(".img"))
                fmt = (Image.open(dest).format or "img").lower().replace("jpeg", "jpg")
                dest = dest.rename(dest.with_suffix(f".{fmt}"))
                log["items"].append({"slot": _slug(i, item), "page": url, "image_url": img_url})
                paths.append(dest)
                break
            except Exception as e:  # noqa: BLE001 - 다음 후보로 넘어간다
                errors.append(f"{url}: {e}")
        else:
            raise RuntimeError(f"{item.category} 사진을 받지 못했습니다:\n" + "\n".join(errors))
    return paths


def run(
    samples_path: Path,
    out: Path,
    experiment: Experiment,
    fake: bool = False,
    variant_ids: list[str] | None = None,
    workers: int = 2,
) -> dict:
    samples = SampleSet.model_validate(yaml.safe_load(samples_path.read_text(encoding="utf-8")))
    profile = load_profile(samples.account)
    variants = [v for v in experiment.variants if not variant_ids or v.id in variant_ids]
    clients: dict[str, ImageClient] = {}

    def client_for(model_key: str) -> ImageClient:
        if model_key not in clients:
            model = resolve_model(model_key)
            clients[model_key] = FakeClient(model) if fake else GeminiClient(model)
        return clients[model_key]

    log: dict = {
        "samples": str(samples_path),
        "account": samples.account,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "items": [],
        "extract": [],
        "runs": [],
    }
    out.mkdir(parents=True, exist_ok=True)

    # 1) 원본 확보 → 2) 전처리
    originals = gather_originals(samples, out, log)
    prepped = [
        prep_image(src, out / "prepped" / f"{_slug(i, it)}.png")
        for i, (src, it) in enumerate(zip(originals, samples.items, strict=True), 1)
    ]

    # 3) 정리컷 (필요한 방식이 있을 때만)
    extracted: list[Path] = []
    if any(v.inputs == "extracted" for v in variants):
        extract_client = client_for("default")
        for i, (src, it) in enumerate(zip(prepped, samples.items, strict=True), 1):
            dest = out / "extracted" / f"{_slug(i, it)}.png"
            t0 = time.monotonic()
            try:
                extract(
                    extract_client,
                    src,
                    it.category,
                    it.desc,
                    dest,
                    image_size=experiment.extract_size,
                )
                entry = {"slot": _slug(i, it), "ok": True}
            except QuotaError:
                raise
            except Exception as e:  # noqa: BLE001 - 실패하면 원본으로 대신한다
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dest)
                entry = {"slot": _slug(i, it), "ok": False, "error": str(e)[:300]}
            entry.update(model=extract_client.model, sec=round(time.monotonic() - t0, 1))
            log["extract"].append(entry)
            extracted.append(dest)

    # 4) 코디컷: 방식 × 회차 (클라이언트는 병렬 실행 전에 만들어 둔다)
    for v in variants:
        client_for(v.model)
    jobs = [(v, n) for v in variants for n in range(1, experiment.repeats + 1)]

    def do(job: tuple[Variant, int]) -> dict:
        v, n = job
        inputs = extracted if v.inputs == "extracted" else prepped
        items = [
            ComposeItem(category=it.category, desc=it.desc, image=p)
            for it, p in zip(samples.items, inputs, strict=True)
        ]
        dest = out / "results" / f"{v.id}_{n}.jpg"
        client = client_for(v.model)
        t0 = time.monotonic()
        entry = {
            "variant": v.id,
            "run": n,
            "model": client.model,
            "file": str(dest.relative_to(out)),
        }
        try:
            compose(
                client,
                items,
                profile.visual.backdrop,
                dest,
                ratio=profile.visual.feed_ratio,
                layout=v.layout,
                image_size=experiment.image_size,
            )
            entry["ok"] = True
        except QuotaError:
            raise
        except Exception as e:  # noqa: BLE001 - 한 회차 실패는 기록만 하고 계속
            entry.update(ok=False, error=str(e)[:300])
        entry["sec"] = round(time.monotonic() - t0, 1)
        print(f"[{v.id}-{n}] {'ok' if entry['ok'] else 'FAIL'} {entry['sec']}s", flush=True)
        return entry

    with ThreadPoolExecutor(max_workers=workers) as pool:
        log["runs"] = list(pool.map(do, jobs))

    # 5) 비교표·보고서
    labels = [f"{i} {CATEGORY_EN[it.category]}" for i, it in enumerate(samples.items, 1)]
    if extracted:
        compare.contact_sheet(
            list(zip([f"{lb} (orig)" for lb in labels], prepped, strict=True)),
            list(zip([f"{lb} (extract)" for lb in labels], extracted, strict=True)),
            out / "sheets" / "extract.jpg",
            title="Originals vs extracted product shots",
            result_h=260,
            per_row=len(labels),
        )
    for v in variants:
        done = [r for r in log["runs"] if r["variant"] == v.id and r["ok"]]
        if not done:
            continue
        compare.contact_sheet(
            list(zip(labels, prepped, strict=True)),
            [(f"{v.id}-{r['run']}", out / r["file"]) for r in done],
            out / "sheets" / f"variant_{v.id}.jpg",
            title=f"Variant {v.id}: inputs={v.inputs}, layout={v.layout}, model={done[0]['model']}",
        )

    log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    log["images_generated"] = sum(e["ok"] for e in log["extract"]) + sum(
        r["ok"] for r in log["runs"]
    )
    log["prompts"] = {
        "extract": render_prompt("extract", category_en="<category>", desc="<desc>"),
    }
    (out / "run.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    write_scores_template(out / "scores.csv", log["runs"], samples)
    write_report(out / "report.md", samples, variants, log)
    return log


def write_scores_template(path: Path, runs: list[dict], samples: SampleSet) -> None:
    """회차 × 상품마다 한 줄. 색·형태·디테일은 0~2점, 추가물건·누락은 예/아니오."""
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["variant", "run", "item", "color", "shape", "detail", "missing", "extra_items"])
        for r in runs:
            if not r["ok"]:
                continue
            for i, it in enumerate(samples.items, 1):
                w.writerow([r["variant"], r["run"], f"{i} {it.category}", "", "", "", "", ""])


def write_report(path: Path, samples: SampleSet, variants: list[Variant], log: dict) -> None:
    lines = [
        f"# 이미지 품질 실험: {samples.name}",
        "",
        f"- 계정: {samples.account}, 생성 이미지 수: {log['images_generated']}",
        f"- 시작 {log['started']}, 종료 {log['finished']}",
        "",
        "## 상품",
        "",
        "| # | 카테고리 | 설명 | 출처 |",
        "|---|---|---|---|",
    ]
    for i, (it, src) in enumerate(zip(samples.items, log["items"], strict=True), 1):
        lines.append(
            f"| {i} | {it.category} | {it.desc} | {src.get('page') or src.get('source')} |"
        )
    lines += [
        "",
        "## 방식별 결과",
        "",
        "| 방식 | 설명 | 성공 | 평균 시간(초) | 비교표 |",
        "|---|---|---|---|---|",
    ]
    for v in variants:
        runs = [r for r in log["runs"] if r["variant"] == v.id]
        ok = [r for r in runs if r["ok"]]
        avg = round(sum(r["sec"] for r in ok) / len(ok), 1) if ok else "-"
        sheet = f"sheets/variant_{v.id}.jpg" if ok else "-"
        lines.append(f"| {v.id} | {v.name} | {len(ok)}/{len(runs)} | {avg} | {sheet} |")
    failed = [r for r in log["runs"] if not r["ok"]] + [e for e in log["extract"] if not e["ok"]]
    if failed:
        lines += ["", "## 실패", ""] + [f"- {json.dumps(f, ensure_ascii=False)}" for f in failed]
    lines += [
        "",
        "## 판정 기준",
        "",
        "상품마다 색·형태·디테일을 0~2점으로 매기고, 빠진 상품이나 추가된 물건이 있으면 표시한다.",
        "모든 상품이 색·형태 2점이고 추가 물건이 없으면 '통과'. 20장 중 14장 이상 통과가 목표.",
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
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--fake", action="store_true", help="API 없이 가짜 이미지로 흐름만 확인")
    args = ap.parse_args(argv)

    exp = Experiment.model_validate(yaml.safe_load(args.experiment.read_text(encoding="utf-8")))
    if args.repeats:
        exp.repeats = args.repeats
    try:
        log = run(args.samples, args.out, exp, args.fake, args.variants, args.workers)
    except QuotaError as e:
        print(
            "Gemini 사용 한도에 막혔습니다. Google AI Studio에서 결제(유료 등급)가 켜져 있는지, "
            f"한도가 남았는지 확인하세요.\n{e}",
            file=sys.stderr,
        )
        return 2
    ok = sum(r["ok"] for r in log["runs"])
    print(f"완료: 코디컷 {ok}/{len(log['runs'])}, 결과 {args.out / 'report.md'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
