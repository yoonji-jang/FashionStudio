"""하루 표지 코디컷을 생성형 AI 로 만든다 (11단계, profile outfit_image.engine: generate).

누끼를 오려 붙이지 않고, 상품마다 쇼핑몰 사진(기획의 ref_photos, 고른 색만)을 한 장의
참고판으로 이어 붙여 보내면 모델(config/models.yaml generate, 기본 Nano Banana Pro)이
코디 전체를 한 장으로 그린다. 기획 shot 이 model 이면 (16단계) 옷을 입은 얼굴 없는 AI 모델컷을
그린다 (profile model_shot, 인물·포즈는 기획 pose).
Photoroom 을 쓰지 않는다. 상품이 원본과 달라질 수 있어 검수(tools.review, image-reviewer)가 꼭 본다.

tools.plan build / tools.review more 가 부른다. 결과 (work 폴더):
  originals/<번호>_<카테고리>.jpg  보낸 상품 참고판 (검수 원본. 쇼핑몰 사진이라 결과 브랜치에
                                    올리지 않음)
  results/G_<n>.jpg                코디컷 후보 (1080x1350)
  prompt.txt                        보낸 지시문
  gen.json                          후보마다 성공 여부·시간·비용
API 키는 환경 변수 OPENROUTER_API_KEY.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from pathlib import Path

import yaml
from PIL import Image

from core.models import DailyPlan, PlanItem, Product
from core.profile import REPO_ROOT, Profile
from core.store import Store
from tools import faces, feed, generate
from tools.photoroom import PROMPTS_DIR, render_prompt

PARTS_DIR = PROMPTS_DIR / "outfit"  # 그 벌에 해당할 때만 붙이는 지시문 조각 (2026-10-12)
CLOTHES_FROM_SHOULDER = ("아우터", "상의", "원피스")


def english(desc: str) -> str:
    """기획 desc "한국어 (영어)" 에서 괄호 안 영어. 괄호가 없으면 desc 그대로.
    영어 안에 괄호가 또 있어도 ("… boots, one pair (two boots))") 바깥 괄호 전체를 쓴다."""
    groups, depth, start = [], 0, 0
    for i, ch in enumerate(desc):
        if ch == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0:
                groups.append(desc[start:i])
    found = [g for g in groups if re.search(r"[A-Za-z]", g)]
    return found[-1].strip() if found else desc.strip()


def item_size(it: PlanItem, product: Product, cfg: dict) -> str:
    """상품의 실제 크기 (영어). 기획 size, 옷은 cm (기획·이름·layout.yaml), 나머지는
    config default_sizes."""
    from tools import plan as plan_tool
    from tools.compose import load_layout

    if it.size:
        return it.size
    cat = product.category
    sizes = cfg.get("default_sizes", {})
    if cat in (*CLOTHES_FROM_SHOULDER, "하의"):
        layout = load_layout()
        cm = plan_tool.item_length(it, product, layout) or layout["slots"][cat]["cm"]
        where = "from waist to hem" if cat == "하의" else "from shoulder to hem"
        return f"about {cm:g} cm long {where}"
    return sizes.get(it.slot or "", "") or sizes.get(cat, "")


def ref_indexes(it: PlanItem) -> list[int]:
    """참고로 보낼 창고 사진 번호. 기획 ref_photos, 없으면 cutout_photo 와 photo."""
    if it.ref_photos:
        return list(dict.fromkeys(it.ref_photos))
    return list(dict.fromkeys([it.cutout_photo] + ([it.photo] if it.photo is not None else [])))


def styling_text(plan: DailyPlan, products: dict[str, Product], profile: Profile) -> str:
    """입는 방법. 기획 styling, 없으면 아우터 기본(profile)과 상품별 wear_how."""
    if plan.styling:
        return plan.styling
    cats = [products[it.product].category for it in plan.items]
    outer = profile.outfit_image.outer_styling if "아우터" in cats else ""
    parts = [outer] if outer else []
    parts += layer_sentences(plan, products, profile)
    for it in plan.items:
        if not it.wear_how or it.wear_modes == ["beside"]:
            continue
        if outer and it.wear_with == "아우터":
            continue  # 예전 누끼 방식의 "활짝 열기" 지시와 겹치지 않게 아우터 기본을 쓴다
        parts.append(it.wear_how)
    return " ".join(parts) or "Each item lies naturally as described, like a person wearing it."


def layer_sentences(plan: DailyPlan, products: dict[str, Product], profile: Profile) -> list[str]:
    """상의 레이어드 (21단계): wear_with: 상의 인 상의를 다른 상의 안에 입힌다."""

    def label(i: int) -> str:
        cat = generate.CATEGORY_EN.get(products[plan.items[i].product].category, "item")
        return f"{cat} in the {sheet_name(i + 1)}"

    tops = [i for i, it in enumerate(plan.items) if products[it.product].category == "상의"]
    inner = [i for i in tops if plan.items[i].wear_with == "상의"]
    outer = [i for i in tops if i not in inner]
    if not inner or not outer or not profile.outfit_image.layer_styling:
        return []
    return [
        profile.outfit_image.layer_styling.format(inner=label(i), top=label(outer[0]))
        for i in inner
    ]


ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"]


def sheet_name(i: int) -> str:
    """참고판 이름. 2026-10-13: "Image 1" 처럼 숫자로 부르면 이미지 AI 가 그림에 숫자를 그려 넣어서
    낱말(first sheet)로 부른다."""
    return f"{ORDINALS[i - 1]} sheet" if 0 < i <= len(ORDINALS) else f"sheet {i}"


def item_lines(plan: DailyPlan, products: dict[str, Product], cfg: dict, start: int = 1) -> str:
    lines = []
    for i, it in enumerate(plan.items, start):
        p = products[it.product]
        cat = generate.CATEGORY_EN.get(p.category, "item")
        size = item_size(it, p, cfg)
        line = f"The {sheet_name(i)}: the {cat}, {english(it.desc)}."
        lines.append(line + (f" Real size: {size}." if size else ""))
    return "\n".join(lines)


def season_en(d) -> str:
    """월로 본 계절 (영어, tools.plan.season 과 같은 기준). 모델컷 장소의 빛·나무를 맞춘다."""
    name = ["winter", "spring", "summer", "autumn"][(d.month % 12) // 3]
    return ["early ", "", "late "][d.month % 3] + name


def build_prompt(plan: DailyPlan, products: dict[str, Product], profile: Profile, cfg: dict) -> str:
    oi = profile.outfit_image
    if plan.shot == "model":
        ms = profile.model_shot
        # 모델컷 지시문은 상품 정보 자리(왼쪽 아래)를 비우는 구도를 따로 갖고 있다
        return render_prompt(
            ms.prompt,
            items=item_lines(plan, products, cfg),
            styling=styling_text(plan, products, profile),
            pose=plan.pose or ms.poses[0],
            mood=plan.mood or ms.mood,
            season=season_en(plan.date),
        )
    styling = styling_text(plan, products, profile)
    return render_prompt(
        oi.prompt,
        items=item_lines(plan, products, cfg),
        styling=styling,
        parts=" ".join(prompt_parts(plan, products, styling)),
        layout=profile.looks.composition,
        scene=oi.scene,
    )


def edit_prompt(plan: DailyPlan, products: dict[str, Product], cfg: dict) -> str:
    """지난 코디컷(첫 그림)에서 고칠 곳만 바꾸는 지시문 (prompts/outfit_edit.md)."""
    return render_prompt(
        "outfit_edit",
        request=plan.edit_request or "Fix only what is wrong compared with the product photos.",
        items=item_lines(plan, products, cfg, start=2),
    )


def prompt_parts(
    plan: DailyPlan, products: dict[str, Product], styling: str, parts_dir: Path = PARTS_DIR
) -> list[str]:
    """그 벌에 해당하는 지시문 조각 (prompts/outfit/parts.yaml 의 조건).
    2026-10-12: 모든 규칙을 한 지시문에 넣으면 길어져 이미지 AI 가 지시를 놓쳐서,
    하의·신발·겹쳐 입기 규칙은 해당할 때만 붙인다."""
    rules = yaml.safe_load((parts_dir / "parts.yaml").read_text(encoding="utf-8")) or {}
    items = [
        (products[it.product], f"{it.name} {products[it.product].name} {it.desc}".lower())
        for it in plan.items
    ]
    cats = [p.category for p, _ in items]
    texts = []
    for name, rule in rules.items():
        if rule.get("styling_words") and not any(
            w.lower() in styling.lower() for w in rule["styling_words"]
        ):
            continue
        cat = rule.get("category")
        if cat:
            hit = [
                text
                for p, text in items
                if p.category == cat
                and (not rule.get("words") or any(w.lower() in text for w in rule["words"]))
                and not any(w.lower() in text for w in rule.get("not_words", []))
            ]
            if not hit:
                continue
            with_cat = rule.get("with_category")
            if with_cat and cats.count(with_cat) < (2 if with_cat == cat else 1):
                continue
        texts.append(render_prompt(name, parts_dir))
    return texts


def reference_panels(
    plan: DailyPlan, products: dict[str, Product], work: Path, cfg: dict, cache: Path
) -> list[bytes]:
    """상품마다 참고판 한 장. 쇼핑몰 사진은 cache(결과 폴더 밖)에 받고, 모델 얼굴은 잘라 낸다."""
    panels = []
    (work / "originals").mkdir(parents=True, exist_ok=True)
    for i, it in enumerate(plan.items, 1):
        p = products[it.product]
        pid = p.id.split(":")[-1]
        shots = []
        for k in ref_indexes(it):
            path = feed.fetch_photo(str(p.image_urls[k]), cache / f"{pid}_{k}.jpg")
            img, ok = faces.hide_faces(Image.open(path).convert("RGB"))
            if not ok:
                print(f"{it.product} #{k}: 얼굴을 잘라 낼 수 없어 뺌", file=sys.stderr)
                continue
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=92)
            shots.append(buf.getvalue())
        if not shots:
            raise ValueError(f"{it.product} ({it.name}): 얼굴 없이 참고로 쓸 사진이 없습니다")
        panel = generate.reference_panel(shots, cfg.get("panel_max_px", 1536))
        cat = generate.CATEGORY_EN.get(p.category, "item")
        (work / "originals" / f"{i:02d}_{cat}.jpg").write_bytes(panel)
        panels.append(panel)
    return panels


def client_for(cfg: dict, fake: bool) -> generate.Client:
    if fake:
        return generate.FakeClient()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY 가 없어 코디컷을 만들 수 없습니다 (GitHub Secrets 에 등록)"
        )
    return generate.OpenRouterClient(cfg, key)


def make_candidates(
    plan: DailyPlan,
    store: Store,
    work: Path,
    profile: Profile,
    count: int,
    fake: bool = False,
    client: generate.Client | None = None,
) -> list[Path]:
    """코디컷 후보를 count 장 만든다 (같은 지시문, 회차마다 결과가 다름). 성공한 것만 돌려준다.
    크레딧이 없으면(402) 한 장도 못 만들었을 때만 generate.QuotaError 를 그대로 올린다."""
    cfg = generate.load_config()
    key = profile.outfit_image.model
    if key not in cfg["models"]:
        raise ValueError(f"outfit_image.model '{key}' 이 config/models.yaml 에 없습니다")
    model = cfg["models"][key]
    products = {p.id: p for p in store.products()}
    work.mkdir(parents=True, exist_ok=True)
    panels = reference_panels(plan, products, work, cfg, generate.CACHE_DIR / plan.account)
    if plan.edit_from and plan.shot != "model":
        # /fix 고쳐 그리기: 지난 코디컷을 첫 그림으로 보내고 고칠 곳만 바꾸게 한다
        base = (REPO_ROOT / plan.edit_from).read_bytes()
        panels = [base, *panels]
        prompt = edit_prompt(plan, products, cfg)
    else:
        prompt = build_prompt(plan, products, profile, cfg)
    (work / "prompt.txt").write_text(prompt, encoding="utf-8")
    client = client or client_for(cfg, fake)

    (work / "results").mkdir(exist_ok=True)
    made: list[Path] = []
    log: list[dict] = []
    for n in range(1, count + 1):
        t0 = time.time()
        entry: dict = {"model": key, "n": n}
        try:
            data, cost = client.generate(model, prompt, panels)
            path = work / "results" / f"G_{n}.jpg"
            generate.fit_canvas(data).save(path, "JPEG", quality=93)
            made.append(path)
            entry.update(ok=True, cost=cost, file=str(path.relative_to(work)))
        except generate.QuotaError:
            if not made:
                raise
            entry.update(ok=False, error="크레딧 부족 (402)")
            log.append(entry)
            break
        except Exception as e:  # noqa: BLE001 - 한 장이 실패해도 나머지는 만든다
            entry.update(ok=False, error=str(e)[:300])
            print(f"코디컷 {n} 실패: {e}", file=sys.stderr)
        entry["sec"] = round(time.time() - t0, 1)
        log.append(entry)
    (work / "gen.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), "utf-8")
    return made


def cost(work: Path) -> float:
    """work 폴더에서 쓴 생성 비용 합계 (달러, OpenRouter 가 알려 준 값)."""
    path = work / "gen.json"
    if not path.exists():
        return 0.0
    return sum(e.get("cost") or 0 for e in json.loads(path.read_text(encoding="utf-8")))
