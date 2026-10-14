"""코디컷 지시문과 참고판을 텔레그램으로 보내기 (2026-10-14 사용자: 제미나이에서 따로 돌려 보려고).

결과 폴더(daily-results 의 <날짜>-run<번호>)의 벌마다 그림 AI 에 보낸 것과 같은 것을 보낸다.
- 참고판: 상품마다 한 장(쇼핑몰 사진, 얼굴 가림) + 기준 코디 사진이 있으면 마지막 한 장.
  다시 만들고 out/ 에만 둔다.
- 지시문: 지금 코드로 그 벌의 첫 그림 지시문을 새로 만든다
  (지시문을 고친 뒤 옛 결과 폴더로도 보낼 수 있게, 그림을 못 그린 회차여도 된다).
그림은 새로 그리지 않아 이미지 비용이 없다. 텔레그램 보내기는 워크플로(prompt-share)만 한다.

  python -m tools.prompt_share --account women \
      --results /tmp/results/2026-10-14-run246 --out out/prompt_share
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from core.models import DailyPlan
from core.profile import load_profile
from core.store import Store
from tools import generate, outfit_gen


def _plans(results: Path) -> list[tuple[int, DailyPlan]]:
    """결과 폴더의 벌 기획들.

    그린 회차면 looks/<번호>/plan.yaml, 후보만 있는 회차면 picks/p1.yaml 의 벌들.
    """
    paths = sorted((results / "looks").glob("*/plan.yaml"), key=lambda p: int(p.parent.name))
    if paths:
        return [
            (
                int(p.parent.name),
                DailyPlan.model_validate(yaml.safe_load(p.read_text(encoding="utf-8"))),
            )
            for p in paths
        ]
    picks = results / "picks" / "p1.yaml"
    if not picks.exists():
        return []
    plan = DailyPlan.model_validate(yaml.safe_load(picks.read_text(encoding="utf-8")))
    return [(i + 1, plan.look_plan(i)) for i in range(len(plan.looks))]


def build(
    results: Path, account: str, out: Path, only: int | None = None, start: int = 1
) -> list[tuple[int, list[Path], Path]]:
    """벌마다 (번호, 참고판 파일들, 지시문 파일). only 면 그 벌만, 번호는 start 부터 다시 매긴다."""
    profile = load_profile(account)
    store = Store(account)
    products = {p.id: p for p in store.products()}
    cfg = generate.load_config()
    cache = generate.CACHE_DIR / account
    made = []
    n = start
    for src_n, plan in _plans(results):
        if only is not None and src_n != only:
            continue
        work = out / f"look{n}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            panels = outfit_gen.reference_panels(plan, products, work, cfg, cache)
        except Exception as e:  # noqa: BLE001 - 한 벌이 안 돼도 나머지는 보낸다
            print(f"LOOK {src_n}: 참고판을 만들지 못함 ({e})", file=sys.stderr)
            continue
        worn = outfit_gen.worn_ref_panel(plan, profile, cache)
        text = outfit_gen.build_prompt(plan, products, profile, cfg)
        if worn:
            text = f"{text.rstrip()}\n{outfit_gen.WORN_REF}\n"
        files = []
        for i, data in enumerate([*panels, *([worn] if worn else [])], 1):
            path = work / f"{i:02d}.jpg"
            path.write_bytes(data)
            files.append(path)
        prompt = work / f"LOOK{n}_prompt.txt"
        prompt.write_text(text, encoding="utf-8")
        made.append((n, files, prompt))
        n += 1
    return made


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--account", required=True)
    ap.add_argument(
        "--results",
        required=True,
        help="결과 폴더 (looks/<번호>/plan.yaml 또는 picks/p1.yaml). 여러 개는 + 로 잇고,"
        " 한 벌만이면 <폴더>/<번호> (예 2026-10-15-run249+2026-10-14-run247/5)",
    )
    ap.add_argument("--root", type=Path, default=Path("."), help="결과 폴더들이 있는 곳")
    ap.add_argument("--out", type=Path, default=Path("out/prompt_share"))
    ap.add_argument("--send", action="store_true", help="텔레그램으로 보냄 (워크플로만)")
    args = ap.parse_args(argv)
    made: list[tuple[int, list[Path], Path]] = []
    for part in args.results.split("+"):
        name, _, look = part.partition("/")
        made += build(
            args.root / name,
            args.account,
            args.out,
            only=int(look) if look else None,
            start=len(made) + 1,
        )
    print(f"{len(made)}벌 준비")
    if not args.send:
        return 0
    from tools import notify

    tg = notify.Telegram.from_env()
    for n, files, prompt in made:
        tg.send_photos(
            files,
            f"LOOK {n} 참고판 {len(files)}장. 이 순서대로 붙이고 아래 지시문을 붙여 넣어요"
            " (마지막 장이 착용 사진이면 기준 코디).",
        )
        tg.send_document(prompt, f"LOOK {n} 지시문")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
