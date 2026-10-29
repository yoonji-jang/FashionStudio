---
name: feedback-rules
description: 텔레그램 /fix 로 쌓인 새 피드백을 읽고, 되풀이될 만한 지적을 코디컷 지시문·검수 기준·스킬·계정 설정 규칙으로 옮긴다 (PR 은 워크플로가 만든다). GitHub Actions feedback-rules 워크플로에서 하루 한 번 쓴다.
---

# feedback-rules: 피드백을 규칙에 반영하기

입력: 환경변수 `ACCOUNT`, 재료 파일 `out/feedback_rules/<ACCOUNT>.md`
(`python -m tools.feedback pending` 결과: 새 피드백 목록과 최근 30일 모아 보기).
결과: 저장소 규칙 파일 수정과 `out/feedback_rules/pr_body.md` (PR 설명). 커밋·푸시·PR·외부 전송은
워크플로가 하므로 하지 않는다.

## 원칙
- **다음 게시물에도 되풀이될 지적만** 규칙으로 옮긴다. 그날 상품 하나에만 해당하는 것
  (예: "이 가방 사진 어두워요")은 옮기지 않고 PR 설명의 "반영하지 않음"에 이유와 함께 적는다.
  같은 지적이 최근 30일에 두 번 이상 나왔으면 꼭 반영을 검토한다.
- **계정마다 다른 값은 계정 폴더에만** 쓴다 (CLAUDE.md 규칙): 말투·문구는 `accounts/$ACCOUNT/voice.md`,
  숫자·색·글자 크기는 `accounts/$ACCOUNT/profile.yaml`. 모든 계정에 맞는 일반 규칙만 공용 파일에 쓴다.
- 작게 고친다. 있던 문장과 어긋나면 새 문장을 덧붙이지 말고 그 문장을 고친다. 한 피드백에 한두 문장.
- 기존 형식을 따른다. `prompts/*.md` 맨 위 주석에 날짜와 이유를 한 줄 남기는 식이면 똑같이 남긴다.
- 개인정보(판매자 이름·연락처)와 비밀값은 어디에도 쓰지 않는다. `data/` 는 고치지 않는다.

## 어디에 반영하나
| 피드백 종류 | 파일 |
|---|---|
| 코디컷 모양·크기·입은 모양 (표지 1장) | 모든 벌에 해당하면 `prompts/<profile outfit_image.prompt>.md` 짧은 본문, 하의·신발·겹쳐 입기·주얼리처럼 일부 벌만이면 `prompts/outfit/<조각>.md` (붙는 조건은 `prompts/outfit/parts.yaml`). 배치(구도)는 profile `looks.composition` 하나로 고정. 지시문은 길면 이미지 AI 가 놓치므로 문장을 덧붙이지 말고 있는 문장을 고친다 (모든 조각 합쳐 1,000단어 이하, 테스트가 봄). 기획 기준은 `.claude/skills/pick-outfit/SKILL.md` "코디컷 배치", 크기 기본값은 `config/layout.yaml` |
| 검수가 놓친 문제 (같은 문제가 통과됨) | `.claude/agents/image-reviewer.md` 의 생성형 코디컷 확인 목록·점수표 |
| 상품 장 사진 고르기 (색 다름, 얼굴, 잘림) | `.claude/agents/photo-checker.md` |
| 제목·상품 이름·코멘트·캡션 말투 | `accounts/$ACCOUNT/voice.md`, 규칙이면 `.claude/skills/write-copy/SKILL.md` |
| 사용자가 `/제목` 으로 직접 바꾼 제목 (처리 `title`, "'옛 제목' → '새 제목'") | `accounts/$ACCOUNT/voice.md` 커버 제목: 새 제목이 옛 제목보다 나은 점(길이, 말투, 걸기 방식)을 규칙과 예시로 더한다 (2026-10-29 사용자) |
| 글자 수·개수 같은 숫자 | `accounts/$ACCOUNT/profile.yaml` `copy` (바꾸면 `python -m core.profile $ACCOUNT` 로 검증) |
| 코디 조합·주제 | `.claude/skills/pick-outfit/SKILL.md`, `.claude/skills/plan-themes/SKILL.md` |
| 레이아웃·폰트 같은 코드 문제 | 고치지 않고 PR 설명 "코드로 고칠 것"에 적는다 |

## 순서
1. `out/feedback_rules/$ACCOUNT.md` 를 읽고, 새 피드백마다 위 표에서 고칠 곳을 정한다.
   필요하면 그날 결과를 보려고 `python -m tools.feedback summary --account $ACCOUNT` 를 더 본다.
2. 해당 파일을 읽고 고친다. profile 을 고쳤으면 `python -m core.profile $ACCOUNT` 로 검증한다.
3. `python -m pytest -q` 로 검사한다. 실패하면 고친 내용 때문인지 보고 고친다 (테스트를 지우거나 끄지 않는다).
4. `out/feedback_rules/pr_body.md` 를 쓴다 (한국어, 짧게):
   ```markdown
   Before: (어떤 지적이 반복됐는지 한두 문장)

   After: (다음 게시물부터 무엇이 달라지는지 한두 문장)

   ## 반영한 피드백
   - 09-30 1장 표지 "신발 더 크게" → prompts/outfit_generate.md: 신발 실제 크기 문장 강화
   ## 반영하지 않음
   - 09-30 4장 가방 "사진 어두워요": 그날 상품 사진 문제라 규칙 아님
   ## 코드로 고칠 것
   - (없으면 "없음")
   ```
5. 반영할 것이 하나도 없으면 파일을 고치지 말고 `pr_body.md` 에 "반영할 규칙 없음"과 이유만 쓴다
   (워크플로가 PR 을 만들지 않는다).

## 사용자가 직접 고치기 (/fix 규칙)
사용자가 텔레그램에서 "/fix 규칙 …" 을 보내거나 규칙 반영 알림(📝)에 답장으로 "/fix …" 를 보내면,
재료 파일은 `# 규칙 고치기 요청` (사용자가 쓴 글)과 `# 지난 규칙 반영` (가장 최근 규칙 반영 커밋의 바뀐 줄)이다.
- 요청을 그대로 따른다. 사용자의 지시라 "되풀이될 지적만" 거르지 않는다. 지난 반영에서 바꾼 문장을
  고치라면 그 문장을, 새로 더하라면 위 표의 알맞은 파일에 더한다. 되돌리라면 지난 반영 전 문장으로 되돌린다.
- 원칙의 나머지(계정 값은 계정 폴더에, 작게 고치기, 지시문 1,000단어 이하, 개인정보·비밀값 금지)는 그대로 지킨다.
- 요청이 규칙이 아니라 이미 올린 게시물이나 코드 수정이면 고치지 않고 `pr_body.md` 에 그 이유를 쓴다.
- `pr_body.md` 는 같은 형식이되 "## 반영한 피드백" 대신 "## 반영한 요청" 에 요청마다 고친 파일과 내용을 적는다.

## 피드백 반영 단계 (feedback-round 워크플로)
게시를 확인하면 차례 흐름이 단계를 나눠 부른다 (2026-10-22 사용자). 재료 `out/feedback_rules/<ACCOUNT>.md` 맨 위에 `# 단계: …` 가 있다.
- **plan**: 새 피드백(과 지난 계획·사용자 요청이 있으면 그것)을 읽고 **반영 계획만** `out/feedback_rules/plan.md` 에 쓴다.
  저장소 파일은 고치지 않는다. 사용자가 텔레그램에서 읽으므로 한국어로 짧게, 피드백마다 한 줄:
  ```markdown
  ## 반영할 것
  1. 10-21 1장 "신발 더 크게" → 코디컷 지시문(prompts/outfit/shoes.md): 신발 실제 크기 문장을 강하게
  ## 반영하지 않을 것
  - 10-21 4장 "가방 사진 어두워요": 그날 상품 사진 문제라 규칙 아님
  ```
  사용자 요청이 있으면 그대로 따른다 (넣으라면 넣고, 빼라면 빼고, 바꾸라면 바꾼다).
- **apply**: `# 확정한 반영 계획` 대로만 고친다 (계획에 없는 것은 고치지 않는다). 나머지는 위 "순서" 2~4 와 같고,
  `pr_body.md` 의 "## 반영한 피드백" 은 계획 번호 순서로 쓴다.
- **revise**: 지금 PR 브랜치가 꺼내져 있다. `# PR 에 대한 사용자 요청` 의 마지막 요청대로 고치고
  (앞 요청들은 이미 반영됨), `pr_body.md` 를 지금 PR 전체를 설명하게 다시 쓴다.
