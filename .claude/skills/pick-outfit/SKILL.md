---
name: pick-outfit
description: 주제에 맞춰 상품 창고에서 코디 한 벌(아우터·상의·하의·가방·신발·소품)을 고르고 코디컷 배치 정보까지 plan.yaml 에 쓴다. daily-feed 가 부른다.
---

# pick-outfit: 상품 조합

기준: profile `outfit` (items_min, items_max, slots, reuse_cooldown_days, max_per_shop),
`style`, `product_source` (min_rating, min_reviews, prefer_flat_shot).

## 후보 보기
카테고리마다:
```bash
python -m tools.plan candidates --account $ACCOUNT --date $DATE --category 아우터
python -m tools.plan candidates --account $ACCOUNT --date $DATE --category 상의 --query "코튼 셔츠"
```
판매 중, 평점·리뷰 기준 이상, 최근에 쓰지 않은 상품만 좋은 순으로 나온다.
각 줄: `id | 카테고리 | 짧은 이름 | 쇼핑몰 | 가격 | 평점(리뷰) | 단독컷/모델컷 | 스타일 | 검색어 | 원문`.

## 고르는 기준 (순서대로)
1. **주제 slots 을 채운다**. 필수 카테고리(`required: true`)는 반드시, 선택 카테고리는 주제와 계절에 맞으면.
2. **색**: 한 벌에 주 색 2~3개. `style.palette` 안에서. 포인트 색은 하나만.
   상품명에 색이 없거나 여러 색(2color, 7color)이면 `color` 에 쓰려는 색을 적고, 사진 확인에서 확인한다.
3. **분위기**: `style_tags` 가 주제 분위기와 맞는 것 (예: 오피스룩이면 심플베이직·모던시크·오피스룩).
   한 벌 안에서 러블리와 모던시크처럼 반대 분위기를 섞지 않는다.
4. **코디컷이 잘 나오는 것**: 가방·신발·주얼리는 단독컷이 있는 상품을 먼저 (`prefer_flat_shot`).
5. **품질**: 같은 조건이면 리뷰 수가 많은 것.
6. **규칙**: 개수 `items_min`~`items_max`, 한 쇼핑몰 `max_per_shop` 개까지,
   슬라이드는 표지 + 상품 수 + 요약 ≤ 10장.
7. 가격대가 한 상품만 튀지 않게 한다 (다른 상품의 3배 이상이면 다시 본다).

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

마지막에 `reason` 에 조합 이유를 2~3문장으로 쓴다 (색, 분위기, 왜 이 상품인지).
