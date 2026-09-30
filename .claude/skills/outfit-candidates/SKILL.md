---
name: outfit-candidates
description: 하루 게시물의 코디 후보 여러 개(profile pick.count)를 상품 창고에서 골라 out/<날짜>/picks/p1.yaml … 로 쓴다. 문구·사진 확인·이미지는 만들지 않는다 (사람이 텔레그램에서 고른 한 벌만 daily-feed 가 이어서 만든다). GitHub Actions daily 워크플로의 mode=candidates 에서 쓴다.
---

# outfit-candidates: 코디 후보 여러 개

입력: 환경변수 `ACCOUNT`, `DATE`, `COLLECT` (daily-feed 와 같음).
결과: `out/$DATE/picks/p1.yaml` … `p<N>.yaml` (N = profile `pick.count`). 파일마다 문구(`text`)가 없는
`core.models.DailyPlan` 이다. 사람이 텔레그램에서 번호를 고르면 그 파일이 `plan.yaml` 이 되어
daily-feed 의 "고른 후보로 시작" 절부터 이어진다.

코디컷 이미지를 만들지 않는다 (`tools.plan build` 금지, 이미지 AI 비용). 사진 확인·문구도 고른 뒤에 하므로 여기서는 하지 않는다.
모든 판단은 `accounts/$ACCOUNT/profile.yaml` 과 `voice.md` 를 따른다. 게시·외부 전송은 하지 않는다.

## 순서

1. daily-feed 의 1~4번(기준 읽기, 오늘 정보, 주제, COLLECT=1 이면 수집)을 그대로 한다.
2. **지난 후보**: `out/$DATE/previous/` 에 파일이 있으면 사람이 "다시"를 누른 것이다. 그 후보들을 읽고
   주인공 상품과 색 조합이 겹치지 않게 새로 고른다 (같은 상품은 새 후보 전체에서 한 번까지).
3. **후보 N개**: `pick-outfit` 스킬 기준으로 한 벌씩 고른다. 파일마다 `account`, `date`, `theme`, `items`,
   `reason` 을 쓰고 `text` 는 쓰지 않는다. items 의 `name` 은 짧은 상품 이름만, `comment` 는 비워 둔다.
   `photo`, `cutout_photo` 는 쓰지 않는다 (고른 뒤 사진 확인에서 정함).
   - 모두 그날 주제를 따르되, 서로 확실히 다르게: 후보마다 **주인공 상품**(아우터 또는 주제의 핵심 아이템)이 다르고,
     주 색 조합도 달라야 한다. 같은 상품은 후보 두 개까지만 쓴다.
   - 1번이 가장 추천하는 후보다. 마감까지 사람이 안 고르면 1번으로 만들 수 있으니 가장 안전한 조합을 1번에 둔다.
   - `reason` 첫 문장은 텔레그램에 한 줄로 보인다. "~로 ~하게" 처럼 그 후보의 차이가 드러나게 짧게 쓴다.
4. **검사**: 파일마다 `python -m tools.plan check out/$DATE/picks/p1.yaml --draft`.
   오류가 있으면 고치고 다시 검사한다 (파일마다 최대 3번). 고칠 수 없으면 그 파일을 지운다.
   재고가 모자라 N개를 다 못 채우면 채운 만큼만 둔다 (최소 2개).
5. **보고**: `out/$DATE/report.md` 에 주제와 이유, 후보마다 한 줄(주인공 상품, 색), 남은 경고를 쓴다.

## 지키는 것
- 상품 창고에 없는 상품은 쓰지 않는다 (id 는 `tools.plan candidates` 결과에서만 복사).
- `data/` 는 주제 저장(`tools.plan themes`)과 수집(`tools.collect`) 외에는 고치지 않는다.
- 판매자 이름·연락처 같은 개인정보는 읽거나 저장하지 않는다.
