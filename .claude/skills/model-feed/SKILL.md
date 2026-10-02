---
name: model-feed
description: 전날 올린 코디 게시물의 벌들을 옷을 입은 얼굴 없는 AI 모델컷으로 다시 그린 게시물을 만든다 (문구 → 검사 → 만들기 → 이미지 검수). GitHub Actions daily 워크플로의 mode=model 에서 쓴다 (16단계, profile schedule.post_days 의 model 날).
---

# model-feed: 전날 코디를 모델컷으로 (16단계)

입력: 환경변수 `ACCOUNT`, `DATE` (게시 날짜), `OUTFIT` (없으면 `auto`).
`out/$DATE/` 에는 도구(`tools.model_post prepare`)가 전날 게시물로 만든 것이 있다.

- `plan.yaml`: 전날 기획 그대로의 벌·상품·참고 사진(`ref_photos`). `shot: model`, `source_run`(전날 결과),
  `mood`(장소·분위기), 벌마다 `pose`(인물·포즈)가 채워져 있고, `text` 와 벌 `point` 는 비어 있다.
- `before/caption.txt`, `before/preview.jpg`: 전날 게시물 (제목·캡션이 겹치지 않게 본다).

결과: `out/$DATE/manifest.json`, 슬라이드, 캡션, `report.md`.
모든 판단은 `accounts/$ACCOUNT/profile.yaml` 과 `accounts/$ACCOUNT/voice.md` 를 따른다.
게시·삭제·외부 전송은 하지 않는다. 판매자 이름·연락처 같은 개인정보는 읽거나 저장하지 않는다.

## 원칙
- 벌과 상품은 바꾸지 않는다. 전날 사람이 고르고 승인한 구성이다. 상품 이름(`name`)도 그대로 둔다
  (댓글 DM 링크와 같은 상품).
- 사진 확인(photo-checker)은 하지 않는다. 전날 고른 사진 번호를 그대로 쓴다.
- `image-reviewer` 는 반드시 앞에서(`run_in_background: false`) 불러 결과를 받은 뒤 다음 단계로 간다.

## 순서
1. **읽기**: `plan.yaml`, `before/caption.txt`, `before/preview.jpg`, profile, voice.md 를 읽는다.
2. **모델컷 지시 다듬기** (영어, 선택):
   - `mood`: 주제(`theme.title_hint`, `theme.mood`)의 상황에 맞게 한 문장으로 바꿔도 된다
     (예: 출근룩이면 "a quiet office street in Seoul on an early autumn morning"). 인물·장소는 실제 사람이나
     가게 이름을 쓰지 않는다. 비우면 profile `model_shot.mood`.
   - 벌 `styling`: 펼친 코디컷용 문장("laid", "flat")이 있으면 사람이 입은 모양으로 바꾼다
     (예: "The shirt is tucked into the front of the slacks; the cardigan is worn open").
   - `pose` 는 도구가 profile `model_shot.poses` 에서 돌려 넣은 것이다. 바꾸지 않는다.
3. **문구**: `write-copy` 스킬의 "여러 벌" 절로 `text`(제목·도입·해시태그)와 벌마다 `point` 를 새로 쓴다.
   전날 게시물과 같은 옷을 사람이 입은 모습이라는 점을 살린다 (예: 도입 "어제 코디, 입으면 이런 느낌이에요").
   전날 제목·도입·point 문장을 그대로 다시 쓰지 않는다. 제목 규칙(요일 금지 등)은 그대로 지킨다.
4. **검사**: `python -m tools.plan check out/$DATE/plan.yaml`. 오류를 고치고 다시 검사한다 (최대 3번).
5. **만들기**: `python -m tools.plan build out/$DATE/plan.yaml --outfit $OUTFIT`. 벌마다
   `out/$DATE/looks/<n>/` 에 모델컷 후보가 생긴다. 종료 코드 2(이미지 서비스 한도)면 그대로 보고에 적고
   6을 건너뛴다 (`--outfit none` 으로 만들지 않는다. 모델컷 없는 게시물은 의미가 없다).
6. **이미지 검수**: `daily-feed` 스킬의 "여러 벌 게시물" 10번과 똑같이 한다
   (`tools.review prep` → 벌마다 `image-reviewer` → `scores.yaml` → `tools.review apply`, 종료 코드 3이면
   그 벌만 `more` → 모두 끝나면 `tools.review finish out/$DATE`).
   `image-reviewer` 에게 넘길 때 "모델컷: image-reviewer.md 의 모델컷 기준(얼굴, 없는 물건, 입은 모양, 기장)을 본다"고 말한다.
7. **보고**: `out/$DATE/preview.jpg` 를 직접 보고, `out/$DATE/report.md` 에 짧게 덧붙인다:
   전날 게시물(`source_run`), 바꾼 mood·styling, 벌마다 검수 점수와 남은 문제.

## 지키는 것
- 검수 점수는 image-reviewer 가 준 그대로 쓴다. 통과시키려고 점수를 올리거나 후보를 빼지 않는다.
- `data/` 는 고치지 않는다.
