# Claude Code 스킬과 에이전트

모든 스킬은 환경변수 `ACCOUNT` 로 `accounts/$ACCOUNT/profile.yaml` 과 `voice.md` 를 읽어 판단한다.
판단은 스킬이, 규칙 검사·파일 만들기는 `python -m tools.plan` 이 한다.

| 스킬 | 역할 | 결과 |
|---|---|---|
| daily-feed | 하루 전체 지휘 (아래 스킬과 도구를 순서대로). 고른 후보가 있으면 사진 확인부터 | `out/<날짜>/plan.yaml`, `manifest.json`, 슬라이드, 캡션 |
| outfit-candidates | 코디 후보 여러 개 (문구·이미지 없이). 사람이 텔레그램에서 고른 것만 daily-feed 가 이어서 만듦 | `out/<날짜>/picks/p1.yaml` … |
| plan-themes | 주간 주제 7개 또는 하루 주제 + 지그재그 검색어 | `data/<계정>/themes.json` 또는 plan.yaml `theme` |
| pick-outfit | 상품 창고에서 코디 한 벌 고르기, 코디컷 배치 정보 | plan.yaml `items`, `reason` |
| write-copy | 표지 제목, 상품 장 이름·코멘트, 캡션 포인트, 해시태그 | plan.yaml `text` |
| fix-feed | 텔레그램 `/fix` 피드백대로 같은 기획에서 고칠 곳만 바꿔 다시 만들고 검수 (15단계, daily mode=fix) | 고친 슬라이드·캡션, `fix_result.yaml` |
| feedback-rules | 새 `/fix` 피드백 중 되풀이될 지적을 지시문·검수 기준·스킬·계정 설정에 옮김 (하루 한 번, feedback-rules 워크플로가 PR 을 만듦) | 고친 규칙 파일, `out/feedback_rules/pr_body.md` |

| 에이전트 | 역할 |
|---|---|
| photo-checker | 상품 사진이 고른 색과 맞는지, 얼굴이 없는지 보고 사진 번호를 고름 (읽기 전용) |
| image-reviewer | 코디컷 후보를 원본 상품과 비교해 5개 항목 점수, 캐러셀 장 색·얼굴·글자 확인 (읽기 전용) |

앞으로 붙일 것: `reels-storyboard` (12단계), `new-account`.

## 흐름

9단계 매일 자동 실행에서는 둘로 나뉜다 (`tools.schedule`, `tools.pick`):
```
(후보 시각) outfit-candidates ─▶ tools.pick validate ─▶ tools.pick send (텔레그램, 이미지 AI 없음)
(사람이 번호 선택) ─▶ daily-feed "고른 후보로 시작" (사진 확인 → 문구 → 만들기 → 검수) ─▶ 미리보기
```

```
tools.plan context ─▶ plan-themes ─▶ (tools.collect 검색어 수집) ─▶ pick-outfit ─▶ tools.plan photos
   ─▶ photo-checker ─▶ write-copy ─▶ tools.plan check (오류면 고치기) ─▶ tools.plan build (후보 N장)
   ─▶ tools.review prep ─▶ image-reviewer ─▶ tools.review apply
        (0 통과 / 3 모두 미달 → tools.review more 후 다시 / 4 사람 확인)
```
