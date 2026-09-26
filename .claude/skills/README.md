# Claude Code 스킬

5단계에서 아래 스킬을 `<이름>/SKILL.md` 로 추가한다. 모든 스킬은 환경변수 `ACCOUNT` 로
`accounts/$ACCOUNT/profile.yaml` 과 `voice.md` 를 읽어 판단한다.

| 스킬             | 역할                                               |
|------------------|----------------------------------------------------|
| daily-feed       | 하루 전체 지휘. `out/<날짜>/manifest.json` 까지     |
| plan-themes      | 주간 주제 7개 + 슬롯 → `data/<계정>/themes.json`    |
| pick-outfit      | 후보 중 5~7개 선택                                 |
| art-direct       | 배치·구도 정하기 → `tools.photoroom`, `tools.compose` 호출 |
| write-caption    | 제목 2줄, 캡션, 해시태그, 고지 문구                |
| reels-storyboard | 릴스 스토리보드 (9단계)                            |
| new-account      | 새 계정 폴더 만들기                                |

에이전트(`.claude/agents/`): `scout` (상품 탐색), `reviewer` (원본 대비 검수, 읽기 전용)
