r"""모델컷 게시물 (16단계). 전날 올린 게시물의 벌들을 옷을 입은 얼굴 없는 AI 모델컷으로 다시 그린다.

profile schedule.post_days 에서 model 인 날(예: 화·목·토)의 게시물이다. 전날(flatlay 날) 게시물이
올라간 뒤 후보 시각에 schedule 이 daily 워크플로를 mode=model, pick=<전날 결과 폴더> 로 부른다.
고르기 없이 같은 벌·같은 상품으로 새 기획(shot: model)을 만들고, Claude(model-feed 스킬)가
제목·캡션을 새로 쓴 뒤 만들기·검수·미리보기는 평소와 같다. 링크·댓글 DM 도 같은 상품이다.

상품 창고는 수집(collect) 때 정리되어 전날 상품이 빠져 있을 수 있다. 그래서 전날 결과의
manifest.json 에 남은 상품 정보(사진 주소 포함)를 이 실행 안의 창고에 다시 넣는다
(daily 는 창고를 커밋하지 않으므로 저장소에는 남지 않는다).

사용법:
  python -m tools.model_post prepare <전날 결과 폴더 이름> --prev <daily-results 폴더>
      --date <게시 날짜> --out out/<날짜>
schedule 은 source_run 으로 전날 게시한 결과 폴더를 찾는다 (없으면 그날 모델컷은 쉼).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from core.models import DailyPlan, Manifest, Product
from core.profile import Profile, load_profile
from core.store import Store


def restore_products(manifest: Manifest, store: Store) -> list[str]:
    """manifest 의 상품 중 창고에 없는 것을 창고에 다시 넣는다. 넣은 상품 id 목록."""
    have = store.products()
    ids = {p.id for p in have}
    missing: list[Product] = [p for p in manifest.plan.items if p.id not in ids]
    if missing:
        store.save_products(have + missing)
    return [p.id for p in missing]


def source_run(results: Path, account: str, day: date) -> Path | None:
    """day(모델컷 게시 날짜) 전날 게시한 결과 폴더. 게시하지 않았으면 None."""
    prev = day - timedelta(days=1)
    found = []
    for d in results.glob(f"{prev}-run*"):
        if not (d / "published.json").exists() or not (d / "manifest.json").exists():
            continue
        if json.loads((d / "manifest.json").read_text(encoding="utf-8")).get("account") == account:
            found.append((int(d.name.rsplit("run", 1)[-1]), d))
    return max(found)[1] if found else None


def pose_for(profile: Profile, day: date, n: int) -> str:
    """n 번째(0부터) 벌의 포즈. 날짜마다 시작 위치를 바꿔 같은 포즈가 늘 1번 벌이 되지 않게."""
    poses = profile.model_shot.poses
    return poses[(day.toordinal() + n) % len(poses)]


def model_plan(src_plan: DailyPlan, run: str, day: date, profile: Profile) -> DailyPlan:
    """전날 기획을 모델컷 기획으로. 벌·상품·참고 사진은 그대로, 문구와 코디컷은 새로."""
    looks = [
        look.model_copy(update={"cover": None, "point": "", "pose": pose_for(profile, day, n)})
        for n, look in enumerate(src_plan.looks)
    ]
    items = [it.model_copy(update={"comment": ""}) for it in src_plan.items]
    return src_plan.model_copy(
        update={
            "date": day,
            "theme": src_plan.theme.model_copy(update={"date": day}),
            "looks": looks,
            "items": items,
            "text": None,
            "cover": None,
            "shot": "model",
            "source_run": run,
            "mood": profile.model_shot.mood,
            "pose": None if looks else pose_for(profile, day, 0),
        }
    )


def prepare(run: str, prev_root: Path, day: date, out: Path) -> DailyPlan:
    src = prev_root / run
    if not (src / "plan.yaml").exists() or not (src / "manifest.json").exists():
        raise FileNotFoundError(f"전날 결과에 plan.yaml·manifest.json 이 없습니다: {src}")
    from tools.plan import load_plan

    src_plan = load_plan(src / "plan.yaml")
    profile = load_profile(src_plan.account)
    manifest = Manifest.model_validate_json((src / "manifest.json").read_text(encoding="utf-8"))
    added = restore_products(manifest, Store(src_plan.account))
    if added:
        print(f"창고에서 빠진 전날 상품 {len(added)}개를 이 실행에 다시 넣었습니다")
    plan = model_plan(src_plan, run, day, profile)
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.yaml").write_text(
        yaml.safe_dump(
            plan.model_dump(mode="json", exclude_none=True), allow_unicode=True, sort_keys=False
        ),
        encoding="utf-8",
    )
    # 전날 제목·캡션과 겹치지 않게 Claude 가 본다
    before = out / "before"
    before.mkdir(exist_ok=True)
    for name in ("caption.txt", "preview.jpg"):
        if (src / name).exists():
            shutil.copy2(src / name, before / name)
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("run")
    p.add_argument("--prev", type=Path, required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        plan = prepare(a.run, a.prev, date.fromisoformat(a.date), a.out)
        print(f"모델컷 기획: {a.out / 'plan.yaml'} ({len(plan.looks) or 1}벌, 전날 {a.run})")
        return 0
    except (FileNotFoundError, ValueError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
