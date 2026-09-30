---
name: fix-feed
description: 텔레그램 /fix 로 받은 피드백대로 하루 게시물을 고쳐 다시 만든다 (같은 기획에서 고칠 곳만 바꾸고 → 검사 → 만들기 → 이미지 검수). GitHub Actions daily 워크플로의 mode=fix 에서 쓴다.
---

# fix-feed: 피드백대로 게시물 고치기 (15단계)

입력: 환경변수 `ACCOUNT`, `DATE`, `OUTFIT` (없으면 `auto`).
`out/$DATE/` 에는 도구(`tools.feedback prepare`)가 지난 미리보기에서 가져온 것이 있다.

- `fix.json`: 받은 피드백. `fixes` 는 `{slide, kind, product, category, text}` 목록이다.
  `kind`: `cover`(표지 코디컷·제목), `product`(상품 장, `product` 는 상품 창고 id), `summary`(요약 장),
  `comment`(댓글 안내 장), `caption`(캡션), `all`(장을 정하지 않음), `slide`(그 밖의 장). `slides` 는 장 번호 → 종류 표다.
- `plan.yaml`: 지난 기획. `cover:` 에 지난 표지 코디컷(이미 검수한 것)이 적혀 있다.
- `review/review.json`: 지난 검수 기록 (표지 점수 그대로).
- `before/`: 지난 `slides/`, `preview.jpg`, `caption.txt`, `report.md`, 코디컷 지시문 `prompt.txt`.

결과: 고친 `plan.yaml` 로 다시 만든 슬라이드·캡션·`manifest.json`, `fix_result.yaml`, `report.md`.
모든 판단은 `accounts/$ACCOUNT/profile.yaml` 과 `accounts/$ACCOUNT/voice.md` 를 따른다.
게시·삭제·외부 전송은 하지 않는다. 판매자 이름·연락처 같은 개인정보는 읽거나 저장하지 않는다.

## 원칙
- 사람이 말한 것만 고친다. 말하지 않은 상품·문구·사진은 그대로 둔다 (코디 구성은 사람이 고른 것).
- 표지 코디컷을 다시 그릴 일이 없으면 `cover:` 를 그대로 둔다. 그러면 이미지 AI 를 부르지 않고
  지난 표지 점수도 남는다. 표지 제목만 고칠 때도 `cover:` 를 둔다 (제목은 도구가 합성).
- 피드백이 모호하면 지난 슬라이드(`before/slides/<장>.jpg`)를 직접 보고 가장 그럴듯하게 해석한다.
  해석한 내용은 `fix_result.yaml` 에 적는다.
- 할 수 없는 요청(창고에 없는 상품, 이미지 안에 글자 그리기, 규칙 위반)은 하지 않고 이유를 적는다.

## 순서
1. **읽기**: `fix.json`, `plan.yaml`, `before/report.md`, profile, voice.md 를 읽고, 피드백이 가리키는 장을
   `before/slides/` 에서 본다. 표지 피드백이면 `before/prompt.txt` 도 본다.
2. **고칠 곳 정하기** (피드백마다):
   - **표지 코디컷** (옷 모양·크기·배치·색·입은 모양): `cover:` 줄을 지운다. 피드백을 영어로 바꿔
     plan.yaml 의 `styling` 에 한 문장씩 덧붙이고(있던 문장과 어긋나면 그 문장을 고친다), 특정 상품의
     크기·기장·핏이면 그 상품의 `desc` 영어 부분, `size`, `cm` 을 고친다. 배치 기준은 `pick-outfit`
     스킬의 "코디컷 배치"를 따른다. 예: "신발 더 크게" → 신발 `size` 를 실제 치수로 분명히 적고
     `styling` 에 "The shoes are drawn at their true size, clearly larger than before." 를 더한다.
   - **표지 제목**: `text.title_lines` 를 `write-copy` 스킬 기준으로 고친다.
   - **상품 장**: 사진이 문제면(색이 다름, 잘림, 얼굴) `python -m tools.plan photos out/$DATE/plan.yaml` 로
     사진 확인표를 만들고 `photo-checker` 에이전트에게 그 상품만 다시 골라 달라고 해 `photo` 를 바꾼다.
     이름·코멘트가 문제면 `name`, `comment` 를 `write-copy` 기준으로 고친다.
     상품 자체를 바꿔 달라는 요청이면 같은 카테고리 상품을 `python -m tools.plan candidates` 에서 골라
     바꾸고, 사진 확인(`photo-checker`)과 이름·코멘트를 새로 한 뒤 `cover:` 를 지운다 (표지에도 보이므로).
   - **요약 장**: 상품 이름이 문제면 그 상품의 `name` 을 고친다. 레이아웃 같은 코드 문제면 고치지 않고 적는다.
   - **댓글 안내 장**: 문구·모양은 profile 설정이라 고치지 않고 `fix_result.yaml` 에 적는다.
   - **캡션**: `text.intro`, `text.points`, `text.hashtags` 를 `write-copy` 기준으로 고친다.
     고지 문구와 제품 목록은 도구가 붙이므로 손대지 않는다.
   - **전체**: 피드백이 어느 장을 말하는지 판단해 위 중 하나로 처리한다.
3. **검사**: `python -m tools.plan check out/$DATE/plan.yaml`. 오류는 고치고 다시 (최대 3번).
4. **만들기**: `python -m tools.plan build out/$DATE/plan.yaml --outfit $OUTFIT`
   (`cover:` 가 있으면 코디컷을 새로 만들지 않는다). 종료 코드 2(이미지 서비스 한도)면 `cover:` 를 지난
   값으로 되돌려 다시 만들고, 표지를 못 고쳤다고 적는다.
5. **이미지 검수**: `daily-feed` 스킬 10번과 똑같이 한다 (`tools.review prep` → `image-reviewer` →
   `tools.review apply`, 종료 코드 3이면 `more`). 표지 피드백이 있었으면 `image-reviewer` 에게 넘길 때
   그 피드백을 함께 적어, 고쳐졌는지도 보게 한다. 점수는 받은 그대로 쓴다.
6. **확인**: `out/$DATE/preview.jpg` 를 보고 피드백이 반영됐는지 확인한다. 반영이 안 됐으면 한 번만 더
   2~5를 한다 (표지는 이미지 AI 비용이 들어서 검수 회차 안에서만).
7. **남기기**:
   - `out/$DATE/fix_result.yaml` (미리보기에 그대로 보인다. 한 줄씩 짧게, 장 번호와 무엇을 바꿨는지):
     ```yaml
     changes:
       - "1장 표지: 신발을 실제 크기로 다시 그림 (검수 9/10)"
       - "캡션: 도입 한 줄을 20자로 줄임"
       - "4장 가방: 요청한 '더 밝은 사진'이 없어 그대로 둠"
     ```
   - `out/$DATE/report.md` 맨 아래에 `## 피드백 반영 (N회)` 절: 피드백 원문, 해석, 바꾼 값, 검수 결과.

## 지키는 것
- 상품 창고에 없는 상품은 쓰지 않는다 (id 는 `tools.plan candidates` 결과에서만 복사).
- 검수 점수는 image-reviewer 가 준 그대로 쓴다. 통과시키려고 점수를 올리거나 후보를 빼지 않는다.
- plan.yaml 과 out/$DATE 밖은 고치지 않는다 (`data/` 도 고치지 않는다).
