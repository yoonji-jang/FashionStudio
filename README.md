# FashionStudio

매일 인스타그램 코디 게시물을 자동으로 만들고 올리는 파이프라인입니다.

- **Claude**: 주제 선정, 상품 조합, 제목·캡션, 이미지 검수 (Claude Code 스킬·에이전트)
- **Gemini**: 상품 정리컷과 회색 배경 플랫레이 코디컷 생성
- **GitHub Actions**: 매일 생성 → 텔레그램 승인 → 인스타 게시

계정별 설정은 `accounts/<계정>/` 에 있고, 코드와 스킬은 모든 계정이 함께 씁니다.
지금은 여성 계정(`women`) 하나로 시작합니다.

## 시작하기

```bash
uv sync
uv run python -m core.profile women   # 프로필 검증
uv run pytest
```

비밀값은 `.env.example` 을 참고해 로컬 `.env` 또는 GitHub Secrets에 등록합니다. `.env` 는 커밋되지 않습니다.

## 새 계정 추가

1. `accounts/_template/` 을 `accounts/<id>/` 로 복사하고 `profile.yaml` 의 `id` 를 폴더 이름과 맞춥니다.
2. `voice.md` 를 채웁니다.
3. `accounts/index.yaml` 에 `<id>` 를 추가합니다.
4. `IG_<ID>_TOKEN`, `IG_<ID>_USER_ID` 를 GitHub Secrets에 등록합니다.

## 진행 단계

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | 저장소 뼈대, 설정, 데이터 규격 | 진행 중 |
| 2 | 이미지 생성 품질 검증 | |
| 3 | 제목 글자 합성 | |
| 4 | 상품 수집 | |
| 5 | Claude Code 스킬·에이전트 | |
| 6 | 이미지 검수 | |
| 7 | 인스타 게시 | |
| 8 | GitHub Actions 매일 실행 | |
| 9 | 릴스 | |
