# FashionStudio 프로젝트 규칙

매일 인스타그램 코디 게시물(캐러셀, 릴스)을 자동으로 만드는 파이프라인.
Claude는 기획·상품 선택·문구·검수를, 생성형 이미지 AI(OpenRouter, 기본 Nano Banana Pro)는 표지 코디컷을 맡고,
GitHub Actions가 매일 실행한다.
코디컷은 쇼핑몰 사진(고른 색)을 참고로 생성형 AI가 코디 전체를 한 장으로 그린다 (2026-09-30부터, `tools.outfit_gen`).
AI가 상품을 새로 그리므로 원본과 같은지 image-reviewer 검수를 꼭 거치고, 캐러셀 상품 장은 쇼핑몰 원본 사진을 쓴다.
예전 방식(Photoroom 누끼를 코드로 배치)은 profile `outfit_image.engine: photoroom` 으로 남아 있다.

## 반드시 지킬 것
- 모든 판단은 `accounts/$ACCOUNT/profile.yaml` 과 `accounts/$ACCOUNT/voice.md`, 코디 고르기는 `accounts/$ACCOUNT/styling.md` 도 따른다.
  계정마다 다른 값(스타일, 슬롯, 배경, 폰트, 시간, 수익 방식)을 코드나 스킬에 직접 쓰지 않는다.
- 게시·삭제·외부 전송 같은 공개 작업은 하지 않는다. 게시는 Claude가 없는 별도 워크플로가 한다
  (`tools.publish`, `tools.ig_publish`, `tools.notify`, `tools.media_host`, `tools.pick`, `tools.ig_token`,
  `tools.schedule run`, `tools.comments`, `tools.link_page push` 는 `.claude/settings.json` 에서 막혀 있다).
- 하루 결과물은 반드시 `core.models.Manifest` 규격으로 `out/<날짜>/manifest.json` 에 쓴다.
- 상품 사이트는 `tools.scout` 로만 접근한다 (로그인 없이 공개 페이지만, 요청 간격 유지).
- 판매자·쇼핑몰 담당자 이름, 연락처, 사업자 정보 같은 개인정보는 읽어 저장하지 않는다. 조사용으로도 페이지 원본(HTML, API 응답)을 저장소나 결과 브랜치에 올리지 않는다.
- 이미지 안의 글자는 AI가 그리지 않는다. 제목은 `tools.overlay` 가 합성한다.
- 계정의 `schedule` 시각을 바꾸면 `python -m tools.schedule sync` 로 `.github/workflows/schedule.yml` cron 줄을 맞춘다.
- 이미지 API 설정은 `config/models.yaml`, 배치는 `config/layout.yaml` 에서 읽는다. 프롬프트는 `prompts/` 템플릿으로 분리한다.
- 비밀값(API 키, 토큰)은 파일에 쓰지 않는다. 로컬은 `.env`, Actions는 Secrets. 프로필에는 Secret 이름만 둔다.
- `monetize: manual_link` 이면 캡션 맨 앞 광고 고지와 첫 해시태그 `#광고` 가 필수다.
- 모든 캡션에 AI 합성 고지를 넣는다.

## 폴더
- `accounts/<id>/` 계정별 설정. `accounts/index.yaml` 이 켜진 계정 목록. 새 계정은 `_template/` 복사
- `core/` 데이터 규격(`models.py`), 프로필 로더(`profile.py`), 상태 저장(`store.py`)
- `sources/` 쇼핑몰별 상품 소스 (`base.py` 인터페이스)
- `tools/` Claude가 호출하는 CLI (`python -m tools.<이름>`)
- `config/` 계정과 무관한 설정 (모델 ID)
- `data/<id>/` 상품 후보, 주제, 게시 기록 (git에 커밋해 상태 유지, `cache/` 는 제외)
- `out/` 실행 결과 (git 제외, Actions artifact로 전달)
- `.claude/skills/`, `.claude/agents/` Claude Code 스킬과 에이전트

## 개발
- Python 3.12, 의존성은 uv: `uv sync`
- 검사: `uv run ruff check . && uv run pytest`
- 프로필 검증: `uv run python -m core.profile women`
- 응답과 문서는 한국어로 쓴다.
