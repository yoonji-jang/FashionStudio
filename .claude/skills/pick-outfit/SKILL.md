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
  name: 래글런 하프 트렌치코트     # write-copy 가 다듬음
  comment: ""                    # write-copy 가 씀
```
코디컷 배치(2단계에서 확인한 방법):
- **셔츠·블라우스를 아우터 안에 입힐 때**: 상의에 `wear_with: 아우터`, `wear_modes: [peek]`,
  `wear_how` 에 "아우터를 잠그고 셔츠는 깃과 소매 끝만 보이게" 를 영어로. 셔츠 사진을 통째로 주면
  몸판이 삐져나오므로 기본 무늬 없는 상의일 때만 peek 을 쓴다. 무늬·장식이 큰 상의면 아우터를 빼거나
  `wear_modes: [over]` 로 하고 경고로 남긴다.
- **벨트**: `wear_with: 하의`, `segment: belt`, `wear_how: the belt is threaded through the belt loops ...`.
- **가방·신발**: `segment: bag` / `segment: shoe`.
- **주얼리**: `slot: 목걸이|귀걸이|팔찌`, `segment: necklace|earring|bracelet`. 과하지 않게 1~2개.
- 기장이 기본값과 크게 다르면 `cm` (예: 크롭 가디건 50). 롱코트처럼 길면 넣지 말고 기본값을 쓴다.

마지막에 `reason` 에 조합 이유를 2~3문장으로 쓴다 (색, 분위기, 왜 이 상품인지).
