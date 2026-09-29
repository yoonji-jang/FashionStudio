---
name: daily-feed
description: 하루치 인스타 코디 게시물을 처음부터 끝까지 만든다 (주제 → 검색어·수집 → 상품 조합 → 사진 확인 → 문구 → 검사 → 코디컷·캐러셀·캡션·manifest → 이미지 검수). "오늘 게시물 만들어", "/daily-feed", GitHub Actions daily 워크플로에서 쓴다.
---

# daily-feed: 하루 게시물 만들기

입력: 환경변수 `ACCOUNT` (없으면 `women`), `DATE` (없으면 내일, `tools.plan` 기본값),
`OUTFIT` (`photoroom` 또는 `none`, 없으면 `photoroom`), `COLLECT` (`1` 이면 주제 검색어로 새 상품 수집).
결과: `out/<DATE>/plan.yaml` 과 `out/<DATE>/manifest.json` (core.models.Manifest, 검수 점수 포함), 슬라이드, 캡션.

모든 판단은 `accounts/$ACCOUNT/profile.yaml` 과 `accounts/$ACCOUNT/voice.md` 를 따른다.
계정마다 다른 값(스타일, 색, 문구 규칙)을 이 문서에서 가정하지 말고 매번 파일에서 읽는다.
게시·삭제·외부 전송은 하지 않는다. 판매자 이름·연락처 같은 개인정보는 읽거나 저장하지 않는다.

## 고른 후보로 시작 (9단계)
`out/$DATE/plan.yaml` 이 이미 있고 `text` 가 없으면 사람이 텔레그램에서 고른 코디 후보다
(`outfit-candidates` 스킬이 만든 것). 1~5번을 건너뛰고 6번(사진 확인)부터 한다.
상품 구성은 사람이 고른 것이라 바꾸지 않는다. 6번에서 맞는 색 사진이 없는 상품만 같은 카테고리의 비슷한
상품으로 바꾸고, 바꾼 것을 보고에 적는다.

## 에이전트 부르기
`photo-checker`, `image-reviewer` 에이전트는 반드시 앞에서(`run_in_background: false`) 불러 결과를 받은 뒤
다음 단계로 간다. 백그라운드로 부르면 결과를 기다리는 동안 실행이 끝나 버려 게시물이 만들어지지 않는다.
report.md 를 쓰기 전에는 끝내지 않는다.

## 순서

1. **기준 읽기**: `accounts/$ACCOUNT/profile.yaml`, `accounts/$ACCOUNT/voice.md` 를 읽는다.
2. **오늘 정보**: `python -m tools.plan context --account $ACCOUNT --date $DATE`
   날짜·요일·계절·날씨 예보, 정해 둔 주제, 최근 게시, 상품 창고 재고가 나온다.
3. **주제**: 정해 둔 주제가 있으면 그대로 쓴다. 없으면 `plan-themes` 스킬의 "하루 주제" 방법으로 하나 정한다.
   주제에는 `search_terms` (카테고리별 지그재그 검색어 1~3개)를 꼭 넣는다.
4. **새 상품 수집 (COLLECT=1 일 때만)**: 주제 검색어마다
   `python -m tools.collect --account $ACCOUNT --category <카테고리> --query <검색어> --report out/$DATE/collect`
   를 돌린다. 지그재그는 GitHub Actions 에서만 열린다. 실패해도 창고에 있는 상품으로 계속한다.
5. **상품 조합**: `pick-outfit` 스킬을 따른다. `out/$DATE/plan.yaml` 에 theme, items, reason 을 쓴다.
6. **사진 확인**: `python -m tools.plan photos out/$DATE/plan.yaml` 로 사진 확인표(`photo_check.jpg`)를 만들고
   `photo-checker` 에이전트에게 plan.yaml 과 확인표를 넘긴다. 에이전트가 돌려준 번호로
   items 의 `photo`, `cutout_photo` 를 채운다. 맞는 색 사진이 없는 상품은 같은 카테고리 다른 상품으로 바꾸고
   이 단계를 다시 한다.
7. **문구**: `write-copy` 스킬로 plan.yaml 의 `text` 와 items 의 `name`, `comment` 를 쓴다.
8. **검사**: `python -m tools.plan check out/$DATE/plan.yaml`.
   오류가 있으면 고치고 다시 검사한다 (최대 3번). 경고는 이유가 있으면 두어도 된다.
9. **만들기**: `python -m tools.plan build out/$DATE/plan.yaml --outfit $OUTFIT`
   코디컷 후보(profile `review.candidates` 장), 슬라이드, `caption.txt`, `preview.jpg`, `manifest.json`,
   `review/review.json` 이 생긴다. 검수 전에는 첫 후보가 표지다.
   Photoroom 한도(종료 코드 2)면 `--outfit none` 으로 다시 만들고 보고에 적는다.
10. **이미지 검수**: 아래를 통과하거나 사람 확인으로 끝날 때까지 한다.
    1. `python -m tools.review prep out/$DATE` → `review/input.md`, `review/covers.jpg`, `review/slides.jpg`
    2. `image-reviewer` 에이전트에게 `out/$DATE` 폴더와 `review/input.md` 를 넘긴다.
       돌려받은 YAML 을 그대로 `out/$DATE/review/scores.yaml` 에 쓴다 (점수를 고치지 않는다).
    3. `python -m tools.review apply out/$DATE --scores out/$DATE/review/scores.yaml`
       - 종료 코드 0: 통과. 가장 좋은 후보가 표지가 되고 manifest 에 점수가 남는다. 11로.
       - 3: 표지 후보가 모두 기준 미달. `python -m tools.review more out/$DATE` 로 후보를 더 만들고 1부터.
         (`more` 가 2면 Photoroom 한도, 3이면 최대 회차: 그대로 11로)
       - 4: 사람 확인 필요. 캐러셀 장 문제가 이유면 한 번만 고쳐 본다: plan.yaml 의 `photo` 번호를
         다른 사진으로 바꾸거나 이름·코멘트를 줄이고, `cover:` 에 `review/review.json` 의 표지(chosen) 파일
         경로(저장소 기준, 예: `out/$DATE/outfit/results/C_2.jpg`)를 적은 뒤 8~9를 다시 하고 1부터.
         Photoroom 은 다시 부르지 않고 이미 매긴 점수도 그대로 남는다. 그래도 4면 11로.
11. **확인·보고**: `out/$DATE/preview.jpg` 를 직접 보고, 표지 제목이 잘리거나 상품 장 사진이 이상하면
    plan.yaml 을 고쳐 8~10을 다시 한다. 마지막에 `out/$DATE/report.md` 에 짧게 덧붙인다:
    주제와 이유, 고른 상품과 이유, 바꾼 것, 검수 결과(표지 후보 점수, 남은 문제), 남은 경고.

## 지키는 것
- 상품 창고에 없는 상품은 쓰지 않는다 (id 는 `tools.plan candidates` 결과에서만 복사).
- 상품의 색·소재·기능은 상품명과 사진으로 확인한 것만 쓴다. 모르면 쓰지 않는다.
- 검수 점수는 image-reviewer 가 준 그대로 쓴다. 통과시키려고 점수를 올리거나 후보를 빼지 않는다.
- plan.yaml 외에 `data/` 를 고치는 것은 주제 저장(`tools.plan themes`)과 수집(`tools.collect`)뿐이다.
