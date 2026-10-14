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
   - **코디컷은 고쳐 그린다** (2026-10-13 사용자: /fix 는 새로 그리지 말고 지난 그림을 고쳐 그리기):
     표지·벌 코디컷 피드백이면 `cover:` 를 지우기 전에 그 값을 같은 자리의 `edit_from:` 에 옮기고,
     `edit_request:` 에 고칠 점을 영어 한두 문장으로 쓴다 (무엇을 어디서 어떻게, 예: "Make the shoes neatly
     paired side by side to the right of the trouser hems, toes pointing up."). 그러면 도구가 지난 그림을
     첫 그림으로 보내 고칠 곳만 바꾸고 괜찮던 부분은 그대로 둔다. 상품을 바꾼 벌(다른 상품으로 교체·추가·빼기)은
     지난 그림에 없는 옷이라 `edit_from` 없이 처음부터 다시 그린다. 아래 `styling`·`desc`·`size` 고치기는
     그대로 해 둔다 (다음에 새로 그릴 때 쓰인다).
   - **표지 코디컷** (옷 모양·크기·배치·색·입은 모양): `cover:` 줄을 지운다 (위 "고쳐 그린다" 대로 `edit_from` 으로 옮김). 피드백을 영어로 바꿔
     plan.yaml 의 `styling` 에 한 문장씩 덧붙이고(있던 문장과 어긋나면 그 문장을 고친다), 특정 상품의
     크기·기장·핏이면 그 상품의 `desc` 영어 부분, `size`, `cm` 을 고친다. 배치 기준은 `pick-outfit`
     스킬의 "코디컷 배치"를 따른다. 예: "신발 더 크게" → 신발 `size` 를 실제 치수로 분명히 적고
     `styling` 에 "The shoes are drawn at their true size, clearly larger than before." 를 더한다.
   - **표지 제목**: `text.title_lines` 를 `write-copy` 스킬 기준으로 고친다.
   - **표지 모양** (여러 벌 게시물): "콜라주 말고 첫 코디로" 면 plan.yaml 에 `cover_collage: false`, 다시 콜라주로면
     `cover_collage: true` 를 쓴다. "표지를 3번 벌로" 면 `cover_look: 3` 과 `cover_collage: false`.
     이 게시물만 바뀌고 코디컷은 다시 그리지 않는다 (`cover:` 는 그대로).
     (표지 벌만 바꾸는 건 /fix 없이 텔레그램 "/cover 3"·"/ok 3" 으로도 된다, tools.cover_look)
   - **상품 장**: 사진이 문제면(색이 다름, 잘림, 얼굴) `python -m tools.plan photos out/$DATE/plan.yaml` 로
     사진 확인표를 만들고 `photo-checker` 에이전트에게 그 상품만 다시 골라 달라고 해 `photo` 를 바꾼다.
     이름·코멘트가 문제면 `name`, `comment` 를 `write-copy` 기준으로 고친다.
     상품 자체를 바꿔 달라는 요청이면 같은 카테고리 상품을 `python -m tools.plan candidates` 에서 골라
     바꾸고, 사진 확인(`photo-checker`), 옷이면 실측(`daily-feed` 6-1), 이름·코멘트를 새로 한 뒤 `cover:` 를 지운다
     (표지에도 보이므로).
   - **요약 장**: 상품 이름이 문제면 그 상품의 `name` 을 고친다. 레이아웃 같은 코드 문제면 고치지 않고 적는다.
   - **댓글 안내 장**: 문구·모양은 profile 설정이라 고치지 않고 `fix_result.yaml` 에 적는다.
   - **캡션**: `text.intro`, `text.points`, `text.hashtags` 를 `write-copy` 기준으로 고친다.
     고지 문구와 제품 목록은 도구가 붙이므로 손대지 않는다.
   - **코디 장** (`kind: look`, 여러 벌 게시물, `look` = 벌 번호): plan.yaml `looks[look-1]` 만 고친다.
     코디컷(옷 모양·배치) 문제면 그 벌의 `cover:` 를 `edit_from:` 으로 옮기고 `edit_request:` 를 쓴다
     (위 "고쳐 그린다"). 기장·크기 문제인데 옷에 `size` 실측이 없으면 `daily-feed` 6-1 실측을 먼저 하고, 그 벌의 `styling`·상품 `desc`·`size` 를 위 "표지
     코디컷" 방식으로 고친다 (다른 벌은 `cover:` 를 두어 다시 그리지 않음). 상품 이름 글자면 `name`.
     지난 지시문은 `before/prompt_<벌 번호>.txt`. 1장 표지는 1번 벌 코디컷이라 표지 코디컷 피드백은 1번 벌을 고친다.
     모델컷 게시물(plan `shot: model`)에서 배경·포즈·헤어·분위기가 어색하다는 피드백이면 그 벌 `pose` 를
     지금 profile `model_shot.poses` 의 장면으로 바꾼다 (지난 장면이 옛 목록일 수 있음). 벌 n 에는 목록의 n번째 장면
     (목록보다 많으면 처음부터 다시)을 쓰고, 고치지 않는 벌의 장면과 같으면 다음 장면을 쓴다. plan `mood` 가 빛을 정해 두었으면
     (골든아워 등) profile `model_shot.mood` 로 되돌린다. 인물 문장은 바꾸지 않는다.
   - **장면 모델컷** (plan `scene_image` 가 있을 때, 2026-10-14): 위 `model_shot.poses` 로 바꾸기는 하지 않는다.
     옷·얼굴·포즈·손·테두리 같은 그 컷 안의 문제면 `cover:` 를 `edit_from:` 으로 옮기고 `edit_request:` 에 영어로
     쓴다 (예: "Hide more of her face: she looks down so her hair covers her eyes. Keep the face natural, not blurred.").
     포즈·자리를 아예 바꿔 달라면 그 벌 `pose` 를 같은 무드 안의 새 자리·포즈로 고치고 `edit_from` 없이 새로 그린다.
     무드(빛·색감·장소)가 장면 사진과 다르다는 피드백이면 plan `mood` 를 장면 사진(`scene_image`, Read 로 봄)에 맞게 고치고
     그 벌을 새로 그린다. `scene_image` 는 바꾸지 않는다 (워크플로가 같은 경로에 다시 둠).
   - **전체**: 피드백이 어느 장을 말하는지 판단해 위 중 하나로 처리한다.
3. **검사**: `python -m tools.plan check out/$DATE/plan.yaml`. 오류는 고치고 다시 (최대 3번).
4. **만들기**: `python -m tools.plan build out/$DATE/plan.yaml --outfit $OUTFIT`
   (`cover:` 가 있으면 코디컷을 새로 만들지 않는다). 종료 코드 2(이미지 서비스 한도)면 `cover:` 를 지난
   값으로 되돌려 다시 만들고, 표지를 못 고쳤다고 적는다.
5. **이미지 검수**: `daily-feed` 스킬 10번과 똑같이 한다 (여러 벌이면 그 스킬의 "여러 벌 게시물" 절대로 벌 폴더마다,
   다시 그린 벌만 검수하고, 끝나면 `tools.review finish`) (`tools.review prep` → `image-reviewer` →
   `tools.review apply`, 종료 코드 3이면 `more`). 코디컷 피드백(표지·벌 장)이 있었으면 `image-reviewer` 에게 넘길 때
   그 피드백을 함께 적어, 후보마다 `feedback_ok`(요청이 그림에 보이는지)도 쓰게 한다. 여러 벌이면 그 벌에 온
   피드백만 적는다. 도구는 반영한 후보를 먼저 고른다. 점수와 `feedback_ok`, `faults` 는 받은 그대로 쓴다.
   `cover:` 를 둔 벌(안 고친 벌)은 지난 검수 결과가 그대로 남으니 다시 검수하지 않는다.
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
