---
name: pick-outfit
description: 주제에 맞춰 상품 창고에서 코디 한 벌(아우터·상의·하의·가방·신발·소품)을 고르고 코디컷 배치 정보까지 plan.yaml 에 쓴다. daily-feed 가 부른다.
---

# pick-outfit: 상품 조합

기준: `accounts/$ACCOUNT/styling.md` (코디 기준, 있으면 가장 먼저), profile `outfit` (items_min, items_max,
slots, reuse_cooldown_days, max_per_shop), `style`, `product_source` (min_rating, min_reviews, prefer_flat_shot).

## 후보 보기
카테고리마다:
```bash
python -m tools.plan candidates --account $ACCOUNT --date $DATE --category 아우터
python -m tools.plan candidates --account $ACCOUNT --date $DATE --category 상의 --query "코튼 셔츠"
```
판매 중, 평점·리뷰 기준 이상, 가격대·제외어(profile `product_source`)에 맞고 최근에 쓰지 않은 상품만 나온다.
순서는 포인트 아이템(이름에 profile `style.point_words`)이 앞이고, 리뷰 수는 `review_cap` 까지만 센다.
앞쪽만 보지 말고 `--query` 로 styling.md 의 포인트 아이템(스웨이드, 레이스, 체크, 쁘띠 스카프 …)을 찾아본다.
각 줄: `id | 카테고리 | 짧은 이름 | 쇼핑몰 | 가격 | 평점(리뷰) | 단독컷/모델컷 | 스타일 | 검색어 | 원문`.

## 고르는 기준 (순서대로)
1. **주제 slots 을 채운다**. 필수 카테고리(`required: true`)는 반드시, 선택 카테고리는 주제와 계절에 맞으면.
2. **센스 포인트 2개 이상** (styling.md "벌마다 지키는 것"): 상의 레이어링, 소재 대비, 패턴 하나, 소품 한 끗,
   실루엣 대비 중 서로 다른 두 가지. 기본템(무지 니트, 슬랙스, 기본 숄더백, 로퍼)은 받쳐 주는 역할로만.
   포인트 아이템이 하나도 없으면 `tools.plan check` 가 경고한다.
3. **색**: styling.md 의 두 색 조합 + 무채색. `style.palette` 안에서. 무채색만으로 끝내지 않는다.
   상품명에 색이 없거나 여러 색(2color, 7color)이면 `color` 에 쓰려는 색을 적고, 사진 확인에서 확인한다.
4. **소품 1~3개**: 벨트·양말·모자·목걸이·귀걸이·스카프를 코디가 살아날 때. 여성스러운 것과 스포티한 것을
   섞는 꾸안꾸 믹스는 괜찮다. 코스튬 느낌·과한 로고는 피한다.
5. **레이어드**: 상의 두 개를 겹치면 안에 입는 상의에 `wear_with: 상의` (아래 "생성형 코디컷").
6. **코디컷이 잘 나오는 것**: 가방·신발·주얼리는 단독컷이 있는 상품을 먼저 (`prefer_flat_shot`).
7. **규칙**: 개수 `items_min`~`items_max`, 한 쇼핑몰 `max_per_shop` 개까지,
   슬라이드는 표지 + 상품 수 + 요약 ≤ 10장. 여러 벌 게시물(profile `looks`)은 이 기준이 벌 하나에
   적용되고, 벌 사이 규칙은 `outfit-candidates` 스킬의 "여러 벌" 절.
8. 가격대가 한 상품만 튀지 않게 한다 (다른 상품의 3배 이상이면 다시 본다).

## plan.yaml 에 쓰는 것 (items 한 개, core.models.PlanItem)
```yaml
- product: zigzag:168297481     # candidates 결과의 id 를 그대로 복사
  color: 베이지                  # 이 코디에 쓰는 색 옵션
  desc: 베이지 래글런 하프 트렌치코트 (beige raglan half-length trench coat)  # 코디컷 지시문, 끝에 (영어)
  # 영어 설명에 핏과 기장을 꼭 넣는다 (wide straight-leg, bootcut, cropped, oversized, long …).
  # 모델 착용컷으로 누끼를 만들면 AI가 핏을 바꾸기 쉬워서, 핏을 적어야 원본대로 나온다
  name: 래글런 하프 트렌치코트     # write-copy 가 다듬음
  comment: ""                    # write-copy 가 씀
```
### 생성형 코디컷 (profile `outfit_image.engine: generate`, 기본)
AI 가 쇼핑몰 사진(`ref_photos`, 사진 확인 단계에서 채움)을 참고로 코디 전체를 한 장으로 그린다.
지시문은 `desc` 의 괄호 속 영어, 실제 크기, 입는 방법으로 만든다. 바뀌기 쉬운 것을 영어로 꼭 적는다
(2026-09-30 실험: 귀걸이를 두 쌍으로, 셔츠 칼라를 테일러드 깃으로, 스퀘어토 로퍼를 페니 로퍼로 그림).
- `desc` 영어: 색, 소재, 핏·기장, **깃·목선 모양**(shirt collar, notched lapel, crew neck …),
  **신발 앞코·종류**(square-toe slip-on loafers …), 바지 주름 유무(flat front, no pleats), 벨트·단추.
  핏·기장은 사진에서 본 대로 (오버핏이면 oversized, hip length). 이름만 보고 cropped·ankle length 를 짐작하지 않는다
  (2026-10-06: 오버핏 자켓을 slightly cropped 로 적어 크롭처럼 그려짐). 치마는 소재와 주름 모양(soft unpressed pleats,
  knife pleats)까지. 스카프는 옷에 두르지 않고 옆에 따로 놓이므로 입는 방법 없이 모양만 적는다.
  개수가 헷갈리는 소품은 개수까지: "exactly one pair (two earrings) of ...", "one pair (two shoes) of ...".
  사진에 다른 색·다른 상품이 함께 있으면 "the photos also show a gold pair, ignore it" 처럼 적는다.
- `size` (선택, 영어): 실제 크기. 없으면 도구가 옷은 cm(아래 기장 규칙), 가방·신발·주얼리는
  config/models.yaml `default_sizes` 로 쓴다. 쇼핑몰 실측을 알면 "about 110 cm long from shoulder to hem".
- **상의 레이어드** (21단계): 셔츠를 니트·베스트·뷔스티에 안에 입히면 안쪽 상의에 `wear_with: 상의`.
  도구가 profile `outfit_image.layer_styling`(깃은 목선에, 소매 끝 3cm, 밑단 살짝)으로 입힌다.
  셔츠 위 튜브톱, 어깨에 니트 두르기처럼 다르게 입힐 때는 `styling` 에 영어로 직접 쓴다
  ("The striped knit is draped over the shoulders with its sleeves loosely tied at the chest.").
  여러 벌 게시물은 벌마다 `styling`.
- plan.yaml 맨 위 `styling` (선택, 영어): 한 벌을 어떻게 입혀 펼칠지. 없으면 아우터가 있는 날은
  profile `outfit_image.outer_styling`(거의 닫고 가운데만 살짝 벌림)을 쓴다. 아우터를 활짝 열지 않는다.
  예: "The cardigan is buttoned only at the top; the shirt collar shows at the neckline."
- 아래 `wear_modes`·`segment`·`method` 는 예전 누끼 방식에서만 쓴다 (넣어도 생성형에는 영향 없음).

### 예전 누끼 방식 (profile `outfit_image.engine: photoroom`)
코디컷 배치(2단계에서 확인한 방법):
- **아우터와 상의가 함께 있으면 상의는 반드시 아우터에 입힌다** (`wear_with: 아우터`).
  따로 놓으면 상의가 아우터 뒤에 가려진다 (2026-09-29). 니트·티셔츠처럼 상의 앞판을 보여 줄 옷은
  `wear_modes: [over]`, `wear_how` 는 "the coat is worn open and unbuttoned over the knit, its fronts
  falling naturally to both sides so the whole front of the knit shows" 처럼 아우터를 자연스럽게 연
  모양으로 쓴다. 기획이 정하지 않으면 도구가 이 방식으로 입힌다. 입히기가 실패하면 아우터 옆에 따로 둔다.
  AI 입히기가 아우터 모양(소매 길이·칼라·단추)을 바꾸기 쉬운 옷이면 `wear_modes: [beside]` 로 처음부터
  옆에 나란히 둔다 (2026-09-29 롱코트+니트: over 로 입히자 코트 소매가 7부, 니트가 조끼가 됨).
- **셔츠·블라우스를 아우터 안에 입힐 때**: 상의에 `wear_with: 아우터`, `wear_modes: [peek]`,
  `wear_how` 에 "아우터를 잠그고 셔츠는 깃과 소매 끝만 보이게" 를 영어로. 셔츠 사진을 통째로 주면
  몸판이 삐져나오므로 기본 무늬 없는 상의일 때만 peek 을 쓴다. 무늬·장식이 큰 상의면 아우터를 빼거나
  `wear_modes: [over]` 로 하고 경고로 남긴다.
- **벨트**: `wear_with: 하의`, `segment: belt`, `wear_how: the belt is threaded through the belt loops ...`.
- **가방·신발**: `segment: bag` / `segment: shoe`.
- **착용컷밖에 없는 하의**: Flat Lay 가 와이드·스트레이트 바지를 부츠컷으로 바꾸기 쉽다. 모델이 똑바로 서서
  허리부터 밑단까지 다 보이는 사진이면 `method: segment`, `segment: trousers` 로 사진 속 모양 그대로 오린다.
- **주얼리**: `slot: 목걸이|귀걸이|팔찌`, `segment: necklace|earring|bracelet`. 과하지 않게 1~2개.
- 크기는 실제 기장(cm) 비율로 놓는다. 기본값(config/layout.yaml slots)은 아우터 68, 상의 64, 하의 100.
  상품 이름에 롱·하프·크롭·스커트 같은 말이 있으면 도구가 `lengths` 표로 기장을 정한다 (롱코트 110).
  쇼핑몰 실측(총장)을 알거나 이름으로 알 수 없는 기장이면 `cm` 에 직접 적는다 (예: 크롭 가디건 50).
  기본보다 긴 아우터는 어깨를 맞춰 아래로 늘어나 입은 것처럼 하의 위를 덮는다.

마지막에 `reason` 에 조합 이유를 2~3문장으로 쓴다. 첫 문장은 이 벌의 센스 포인트
(예: "브라운 니트 밖으로 하늘색 셔츠 깃과 소매를 빼고 볼캡으로 블루를 이어 줌"), 다음은 색과 왜 이 상품인지.
