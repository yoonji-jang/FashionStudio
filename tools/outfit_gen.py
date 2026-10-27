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


def item_size(it: PlanItem, product: Product, cfg: dict, worn: bool = False) -> str:
    """상품의 실제 크기 (영어). 기획 size, 옷은 cm (기획·이름·layout.yaml), 나머지는
    config default_sizes. worn 이면(장면 모델컷) 옷 기장은 cm 대신 상품 착용컷을 따르게 한다."""
    from tools import plan as plan_tool
    from tools.compose import load_layout

    cat = product.category
    clothes = cat in (*CLOTHES_FROM_SHOULDER, "하의")
    if worn and clothes:
        # 입은 모습의 기장은 상품 착용컷의 모델 몸에서 끝나는 자리를 따른다. 실측표를 못 읽었을 때
        # 이름으로 짐작한 기본 cm 를 보내면 사진과 어긋난다 (2026-10-27 사용자: 크롭 점퍼에 68cm,
        # "기장을 고정하면 안 되고 상세페이지·참고 코디컷을 그대로")
        same = "its hem ends on her body exactly where it ends on the shop model in its worn photos"
        return f"{it.size}; {same}" if it.size else same
    if it.size:
        return it.size
    sizes = cfg.get("default_sizes", {})
    if clothes:
        layout = load_layout()
        cm = plan_tool.item_length(it, product, layout) or layout["slots"][cat]["cm"]
        where = "from waist to hem" if cat == "하의" else "from shoulder to hem"
        return f"about {cm:g} cm long {where}"
    # 스카프를 목걸이 칸에 넣으면 목걸이 크기가 붙어 체인처럼 그려질 수 있다 (2026-10-14 run246)
    text = f"{product.name} {it.desc}".lower()
    if "스카프" in text or "scarf" in text:
        return sizes.get("스카프", "")
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
    worn = plan.shot == "model" and bool(plan.scene_image)
    for i, it in enumerate(plan.items, start):
        p = products[it.product]
        cat = generate.CATEGORY_EN.get(p.category, "item")
        size = item_size(it, p, cfg, worn)
        line = f"The {sheet_name(i)}: the {cat}, {english(it.desc)}."
        lines.append(line + (f" Real size: {size}." if size else ""))
    return "\n".join(lines)


def season_en(d) -> str:
    """월로 본 계절 (영어, tools.plan.season 과 같은 기준). 모델컷 장소의 빛·나무를 맞춘다."""
    name = ["winter", "spring", "summer", "autumn"][(d.month % 12) // 3]
    return ["early ", "", "late "][d.month % 3] + name


def build_prompt(
    plan: DailyPlan, products: dict[str, Product], profile: Profile, cfg: dict, worn: bool = False
) -> str:
    """worn 이면 장면 모델컷에 기준 코디 사진(상품 참고판 다음, 장면 사진 앞)을 같이 보낸다."""
    oi = profile.outfit_image
    if plan.shot == "model" and plan.scene_image:
        # 장면 모델컷 (profile flow): 사용자가 보낸 장면 사진 1장의 무드로,
        # 벌마다 자리·포즈만 다르게
        # 얼굴 참고를 받았으면 그 특징의 얼굴을 보이게, 아니면 얼굴을 가린다 (2026-10-26 사용자)
        person = (
            render_prompt("scene_face_shown", face=plan.face.strip().rstrip("."))
            if plan.face
            else render_prompt("scene_face_hidden")
        )
        return render_prompt(
            profile.flow.scene_prompt,
            person=person,
            worn=WORN_REF_MODEL if worn else "",
            body=render_prompt("scene_body_ref" if worn else "scene_body"),
            items=item_lines(plan, products, cfg),
            styling=styling_text(plan, products, profile),
            pose=plan.pose or "a relaxed natural pose like in the scene photo",
            mood=plan.mood or "the same kind of photo as the scene photo",
            season=season_en(plan.date),
        )
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


EDIT_KINDS = {
    "flatlay": "a photorealistic top-down flat lay photograph of an outfit",
    "model": "a realistic candid photo of a woman wearing an outfit",
}
EDIT_KEEP = {
    "flatlay": "the floor, the light and shadows and the camera angle",
    # 얼굴·키·다리 길이를 고쳐 달라는 /fix 도 있다 (2026-10-22 사용자: 코끝, 청순한 얼굴, 키)
    "model": (
        "her face, hair and body as they are unless the change is about them,"
        " the place, the light and the camera angle"
    ),
}
# 고쳐 그릴 때 덧붙이는 참고 그림 설명 (상품 참고판 다음 순서대로)
EDIT_WORN = (
    "Image {n} is the reference outfit photo this look copies. Use it for how the clothes fit"
    " and sit on the body (length, looseness, tuck), never for its background, pose or face."
)
EDIT_REFS = (
    "Then come reference photos for this change ({nums}). Use them only for what the change asks"
    " (for example the shape of the nose, or the place and mood of a scene photo); never copy a"
    " person's face or identity from them, and never take clothes from them."
)


def edit_prompt(
    plan: DailyPlan, products: dict[str, Product], cfg: dict, worn: bool = False, refs: int = 0
) -> str:
    """지난 코디컷(첫 그림)에서 고칠 곳만 바꾸는 지시문 (prompts/outfit_edit.md).
    장면 모델컷(scene_image)도 사람 사진으로 고쳐 그린다 (10/15 LOOK3·7 처럼).
    worn 이면 상품 참고판 다음에 기준 코디 사진, refs 는 그 다음 사용자 참고 사진 장 수."""
    kind = "model" if plan.shot == "model" else "flatlay"
    n = 2 + len(plan.items)
    extra = []
    if worn:
        extra.append(EDIT_WORN.format(n=n))
        n += 1
    if refs:
        nums = ", ".join(f"user photo {i + 1} = image {n + i}" for i in range(refs))
        extra.append(EDIT_REFS.format(nums=nums))
    return render_prompt(
        "outfit_edit",
        request=plan.edit_request or "Fix only what is wrong compared with the product photos.",
        items=item_lines(plan, products, cfg, start=2),
        kind=EDIT_KINDS[kind],
        keep=EDIT_KEEP[kind],
        extra=" ".join(extra),
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
    plan: DailyPlan,
    products: dict[str, Product],
    work: Path,
    cfg: dict,
    cache: Path,
    allow_faces: bool = False,
) -> list[bytes]:
    """상품마다 참고판 한 장. 쇼핑몰 사진은 cache(결과 폴더 밖)에 받고, 모델 얼굴은 잘라 낸다.
    allow_faces 면 얼굴을 못 잘라 낸 사진도 그대로 쓴다 (지시문 공유에서만)."""
    panels = []
    (work / "originals").mkdir(parents=True, exist_ok=True)
    for i, it in enumerate(plan.items, 1):
        p = products[it.product]
        pid = p.id.split(":")[-1]
        shots = []
        for k in ref_indexes(it):
            path = feed.fetch_photo(str(p.image_urls[k]), cache / f"{pid}_{k}.jpg")
            img, ok = faces.hide_faces(Image.open(path).convert("RGB"))
            if not ok and allow_faces:
                img = Image.open(path).convert("RGB")
            elif not ok:
                print(f"{it.product} #{k}: 얼굴을 잘라 낼 수 없어 뺌", file=sys.stderr)
                continue
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=92)
            shots.append(buf.getvalue())
        if not shots:
            # 고른 사진이 모두 얼굴을 못 잘라 내면 원본을 쓴다 (10-14 사용자: 얼굴 사진 괜찮음)
            k = ref_indexes(it)[0]
            print(f"{it.product} #{k}: 얼굴 없는 사진이 없어 원본을 씀", file=sys.stderr)
            buf = io.BytesIO()
            Image.open(cache / f"{pid}_{k}.jpg").convert("RGB").save(buf, "JPEG", quality=92)
            shots.append(buf.getvalue())
        panel = generate.reference_panel(shots, cfg.get("panel_max_px", 1536))
        cat = generate.CATEGORY_EN.get(p.category, "item")
        (work / "originals" / f"{i:02d}_{cat}.jpg").write_bytes(panel)
        panels.append(panel)
    return panels


# 2026-10-14 사용자: "모델이 입은 이미지 핏 그대로 바닥에 펼쳐지면 좋겠어"
# (스카프 늘어뜨림, 셔츠 윗단추 풂). 따라 한 기준 코디 사진을 마지막 그림으로 보내
# 입는 모양만 따르게 한다 (배경·포즈·다른 옷은 따르지 않음)
# 2026-10-14 사용자: "코디 이미지에 옷만 지그재그 옷으로 대체하는 느낌으로" →
# 기준 코디 사진을 그대로 다시 만들되 옷만 상품 장의 것으로 바꾼다.
# 넣어 입은 정도·삐져나온 정도까지, 위 일반 규칙보다 사진이 앞선다.
WORN_REF = (
    "The last attached image is the reference outfit photo. Recreate that exact outfit as"
    " this flat lay, with each piece swapped for the matching item from the sheets, as if"
    " the model's clothes were replaced one by one: keep exactly how each piece is worn"
    " there, how much of a top is tucked in and how much hangs out, which shirt tails,"
    " hems, cuffs and collars peek out from under other layers and by how much, how far a"
    " shirt or cardigan is unbuttoned and how its collar falls open, how the sleeves sit,"
    " how open an outer is, how a scarf hangs (never knotted unless knotted there), and"
    " the shape every top and bottom has on her body. Where this photo differs from the"
    " general wearing rules above, follow the photo. Take colors and details only from the"
    " item sheets, and never copy that photo's background or pose. Draw nothing that is"
    " not in the item sheets, even if the model wears it (tights, socks, jewelry, belts)."
)


# 2026-10-27 사용자: "참고 코디컷과 동일하게 입어야 해"
# (코트 허리 묶기, 자켓 밑으로 티셔츠가 얼마나 삐져나오는지).
# 장면 모델컷도 따라 한 기준 코디 사진을 보내 입는 모양을 따르게 한다
# (장면 사진은 무드·자리·포즈만).
# 같은 날: 1. 참고 코디와 최대한 "동일"한 핏·코디 느낌, 2. 실제 제품의 재질·기장·색은
# 절대 왜곡하지 않기. 그래서 입는 모양·핏은 이 사진, 상품 자체(재질·기장·색·디테일)는
# 상품 참고판에서만 가져온다.
WORN_REF_MODEL = (
    "The image after the item sheets is the reference outfit photo this look copies. Dress her"
    " exactly the way the outfit is worn there, as if that model's clothes were swapped one by"
    " one for the items in the sheets, so the fit and the feel of the outfit are the same:"
    " whether an outer is open or closed and how a coat belt or waist tie is tied, how much of a"
    " top is tucked in and how much hangs out, which hems, cuffs and collars peek out from under"
    " other layers and by how much, how sleeves are pushed up, how high the waistband sits and"
    " how much of the bottoms shows below the outer, how loose or close each piece sits on the"
    " body and the overall silhouette. Where this photo differs from the general wearing"
    " rules below, follow this photo. The products themselves come only from the item sheets:"
    " never take fabric, color, pattern, length or details from this photo. If a piece there is"
    " longer or shorter, or has a belt, collar, pleats or buttons the product does not have, draw"
    " the product as it is in its sheet. Never copy that photo's face, background or pose, and"
    " draw nothing that is not in the sheets even if that model wears it, unless the wearing"
    " text below names it."
)


def worn_ref_panel(
    plan: DailyPlan, profile: Profile, cache: Path, edit: bool = False
) -> bytes | None:
    """기준 코디 사진 (얼굴은 가림). 받지 못하면 없이 그린다.
    모델컷은 고쳐 그릴 때 핏 참고로 보낸다 (2026-10-22 사용자: "참고 모델핏과 비슷하게").
    장면 모델컷은 새로 그릴 때도 입는 모양 참고로 보낸다 (2026-10-27 사용자)."""
    if not plan.ref_photo or (plan.shot == "model" and not edit and not plan.scene_image):
        return None
    from tools import scout

    rs = profile.themes.ref_search
    name = re.sub(r"[^0-9A-Za-z]+", "_", plan.ref_photo)[-80:]
    try:
        path = scout.ref_photo(
            plan.ref_photo,
            rs.hosts() if rs else [],
            cache / f"ref_{name}",
            rs.searched() if rs else False,
        )
        img, ok = faces.hide_faces(Image.open(path).convert("RGB"))
    except Exception as e:  # noqa: BLE001 - 참고 사진 없이도 그릴 수 있다
        print(f"기준 코디 사진을 받지 못함: {plan.ref_photo} ({e})", file=sys.stderr)
        return None
    if not ok:
        print("기준 코디 사진의 얼굴을 가릴 수 없어 뺌", file=sys.stderr)
        return None
    img.thumbnail((1536, 1536))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def scene_panel(plan: DailyPlan) -> bytes | None:
    """장면 모델컷의 장면 사진 (사용자가 보낸 1장). 무드 참고로 마지막 그림으로 보낸다.
    지시문이 그 사람의 얼굴·신원을 따르지 말라고 한다
    (얼굴은 자르지 않는다: 포즈·헤어도 무드라서)."""
    if not plan.scene_image or plan.shot != "model":
        return None
    path = REPO_ROOT / plan.scene_image
    if not path.exists():
        raise FileNotFoundError(f"장면 사진이 없습니다: {plan.scene_image}")
    img = Image.open(path).convert("RGB")
    img.thumbnail((1536, 1536))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def edit_ref_panels(plan: DailyPlan) -> list[bytes]:
    """/fix 에 붙여 보낸 참고 사진 (plan.edit_refs, 저장소 기준 경로). 없는 파일은 뺀다."""
    out = []
    for p in plan.edit_refs:
        path = REPO_ROOT / p
        if not path.exists():
            print(f"/fix 참고 사진이 없음: {p}", file=sys.stderr)
            continue
        img = Image.open(path).convert("RGB")
        img.thumbnail((1536, 1536))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        out.append(buf.getvalue())
    return out


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
    if plan.edit_from and (plan.shot != "model" or plan.scene_image):
        # /fix 고쳐 그리기: 지난 코디컷을 첫 그림으로 보내고 고칠 곳만 바꾸게 한다
        base = (REPO_ROOT / plan.edit_from).read_bytes()
        worn = worn_ref_panel(plan, profile, generate.CACHE_DIR / plan.account, edit=True)
        if plan.scene_image and plan.ref_photo and not worn:
            raise RuntimeError(
                f"참고 코디 사진을 받지 못해 고쳐 그리지 않습니다: {plan.ref_photo[:40]}"
            )
        refs = edit_ref_panels(plan)
        panels = [base, *panels, *([worn] if worn else []), *refs]
        prompt = edit_prompt(plan, products, cfg, worn=bool(worn), refs=len(refs))
    else:
        worn = worn_ref_panel(plan, profile, generate.CACHE_DIR / plan.account)
        scene = scene_panel(plan)
        if scene and plan.ref_photo and not worn:
            # 참고 코디 없이 그리면 참고와 다른 핏이 나와 다시 그리는 비용만 든다
            # (2026-10-27 사용자)
            raise RuntimeError(
                f"참고 코디 사진을 받지 못해 그리지 않습니다: {plan.ref_photo[:40]}"
                " (텔레그램 사진은 daily.yml 이 tools.plan refs 로 미리 받음)"
            )
        prompt = build_prompt(plan, products, profile, cfg, worn=bool(scene and worn))
        if scene:
            (work / "originals").mkdir(parents=True, exist_ok=True)
            (work / "originals" / "scene.jpg").write_bytes(scene)
            if worn:
                (work / "originals" / "worn_ref.jpg").write_bytes(worn)
            panels = [*panels, *([worn] if worn else []), scene]
        elif worn:
            (work / "originals").mkdir(parents=True, exist_ok=True)
            (work / "originals" / "worn_ref.jpg").write_bytes(worn)
            panels = [*panels, worn]
            prompt = f"{prompt.rstrip()}\n{WORN_REF}\n"
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
            generate.fit_canvas(data, trim=plan.shot == "model").save(path, "JPEG", quality=93)
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
