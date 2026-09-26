# Claude Code 스킬과 에이전트

모든 스킬은 환경변수 `ACCOUNT` 로 `accounts/$ACCOUNT/profile.yaml` 과 `voice.md` 를 읽어 판단한다.
판단은 스킬이, 규칙 검사·파일 만들기는 `python -m tools.plan` 이 한다.

| 스킬 | 역할 | 결과 |
|---|---|---|
| daily-feed | 하루 전체 지휘 (아래 스킬과 도구를 순서대로) | `out/<날짜>/plan.yaml`, `manifest.json`, 슬라이드, 캡션 |
| plan-themes | 주간 주제 7개 또는 하루 주제 + 지그재그 검색어 | `data/<계정>/themes.json` 또는 plan.yaml `theme` |
| pick-outfit | 상품 창고에서 코디 한 벌 고르기, 코디컷 배치 정보 | plan.yaml `items`, `reason` |
| write-copy | 표지 제목, 상품 장 이름·코멘트, 캡션 포인트, 해시태그 | plan.yaml `text` |

| 에이전트 | 역할 |
|---|---|
| photo-checker | 상품 사진이 고른 색과 맞는지, 얼굴이 없는지 보고 사진 번호를 고름 (읽기 전용) |

앞으로 붙일 것: `reviewer` (7단계 이미지 검수), `reels-storyboard` (11단계), `new-account`.

## 흐름

```
tools.plan context ─▶ plan-themes ─▶ (tools.collect 검색어 수집) ─▶ pick-outfit ─▶ tools.plan photos
   ─▶ photo-checker ─▶ write-copy ─▶ tools.plan check (오류면 고치기) ─▶ tools.plan build
```
