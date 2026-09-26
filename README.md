# FashionStudio

매일 인스타그램 코디 게시물을 자동으로 만들고 올리는 파이프라인입니다.

- **Claude**: 주제 선정, 상품 조합, 제목·캡션, 이미지 검수 (Claude Code 스킬·에이전트)
- **Photoroom API**: 상품 사진에서 누끼 따기(옷은 바닥에 펼친 모양으로), 그림자·배경 입히기
- **코드(Pillow)**: 누끼를 배경 위에 배치해 코디컷 합성. 상품을 AI가 새로 그리지 않아 원본 그대로 나옴
- **GitHub Actions**: 매일 생성 → 텔레그램 승인 → 인스타 게시

계정별 설정은 `accounts/<계정>/` 에 있고, 코드와 스킬은 모든 계정이 함께 씁니다.
기본으로 여성 계정 예시(`accounts/women/`)가 들어 있습니다.

## 시작하기

```bash
uv sync
uv run python -m core.profile women   # 프로필 검증 (계정 이름 생략 시 index.yaml의 모든 계정)
uv run pytest
```

---

## 사람이 하는 일

나머지(주제 선정, 상품 고르기, 이미지·문구 생성, 검수, 게시)는 자동으로 돌아가고, 사람은 아래 지점에서만 개입합니다.
(개발 단계별로 순차 구현 중이라, 아래는 완성 시 기준입니다.)

| 언제 | 하는 일 | 어디서 | 끌 수 있나 |
|---|---|---|---|
| 처음 한 번 | 계정 설정, 비밀값 등록, 인스타·텔레그램 연결 | 이 README의 설정 가이드 | - |
| 매주 1회 | 다음 주 주제 목록 확인 (고칠 것만 답장) | 텔레그램 | `themes.review_before_use: false` |
| 매일 1회 | 전날 밤 도착한 미리보기에 `/ok` (게시) 또는 `/skip` (건너뛰기) | 텔레그램 | `approval: optional` 이면 `/skip` 만 필요 |
| 문제가 생겼을 때 | 이미지 검수 2회 실패, 토큰 만료 등 알림 확인 | 텔레그램 | - |
| 제휴 링크를 쓸 때만 | 게시 후 받은 상품 목록에 공유리워드 링크 붙여 답장 | 텔레그램 | `monetize: none` 이면 없음 |
| 가끔 | 반응 보면서 말투·제목 예시·금지 표현 다듬기 | `voice.md`, `profile.yaml` | - |

처음 2~3주는 매일 승인하며 품질을 확인하고, 안정되면 `approval: optional` 로 바꾸는 것을 권장합니다.

---

## 설정 가이드

코드를 몰라도 아래 파일만 고치면 동작이 바뀝니다. 고친 뒤에는 `uv run python -m core.profile <계정>` 으로
검사하세요. PR을 올리면 GitHub Actions(`ci.yml`)가 모든 계정 설정을 자동으로 검사합니다.

### 한눈에 보기

| 파일 | 무엇을 정하나 | 얼마나 자주 |
|---|---|---|
| `accounts/<계정>/profile.yaml` | 대상·스타일·주제 기준·코디 규칙·이미지 모양·시간·수익·승인 | 처음 한 번, 가끔 조정 |
| `accounts/<계정>/voice.md` | 제목·캡션 말투, 좋은 제목 예시, 해시태그, 금지 표현 | 반응 보면서 가끔 |
| `accounts/index.yaml` | 매일 돌릴 계정 목록 | 계정 추가·중지할 때 |
| GitHub Secrets | API 키, 인스타 토큰 등 비밀값 (저장소 밖에 보관) | 처음 한 번, 토큰 만료 시 |
| `config/models.yaml` | 이미지 API(Photoroom) 설정 | 거의 안 바꿈 |
| `config/layout.yaml` | 코디컷에서 상품별 위치·크기 | 배치를 바꾸고 싶을 때 |
| `prompts/` | Photoroom에 보내는 지시문(옷 펼치기, 배경) | 이미지 품질을 다듬을 때 |
| `assets/fonts/` | 제목 폰트 파일 | 처음 한 번 |

사용자가 고치지 않는 곳: `core/`, `sources/`, `tools/`, `tests/`, `CLAUDE.md`, `.claude/settings.json`

### 1. `accounts/<계정>/profile.yaml`

표의 값은 `accounts/women/` 예시 값입니다.

#### 기본 정보
| 항목 | 예시 | 의미 |
|---|---|---|
| `id` | `women` | 계정 구분 이름. **폴더 이름과 같아야 함** (영문 소문자, 숫자, `-`, `_`) |
| `display_name` | 여성 데일리 코디 | 사람이 보는 이름 |
| `language` | `ko` | 글 언어 |

#### `audience` : 누구를 위한 계정인가
| 항목 | 예시 | 의미 |
|---|---|---|
| `gender` | `women` | `women` / `men` / `unisex` |
| `age_range` | `[20, 34]` | 대상 나이대 |
| `description` | 출근과 주말을 오가는 20~30대 여성… | 주제·상품을 고를 때 참고하는 독자 설명 |

#### `style` : 코디 분위기
| 항목 | 예시 | 의미 |
|---|---|---|
| `keywords` | 미니멀, 클래식, 데일리, 오피스룩 | 추구하는 스타일 |
| `palette` | 블랙, 아이보리, 그레이, 베이지, 네이비, 버건디 | 선호 색상 |
| `avoid` | 과한 로고, 형광색, 코스튬 느낌 | 고르지 않을 것 |

#### `outfit` : 코디 한 벌 구성 규칙
| 항목 | 예시 | 의미 |
|---|---|---|
| `items_min` / `items_max` | 5 / 7 | 한 코디의 아이템 수 (최대 10) |
| `slots` | 아우터(선택), 상의, 하의, 가방, 신발, 소품(선택) | 들어갈 카테고리. `required: false` 면 생략 가능 |
| `reuse_cooldown_days` | 14 | 같은 상품을 다시 쓰기까지 최소 일수 |
| `max_per_shop` | 2 | 한 코디에서 같은 쇼핑몰 상품 최대 개수 |

카테고리는 아우터, 상의, 하의, 원피스, 가방, 신발, 소품 중에서만 쓸 수 있습니다.

#### `themes` : 주제 정하는 기준 (매주 다음 주 주제를 한꺼번에 생성)
| 항목 | 예시 | 의미 |
|---|---|---|
| `per_week` | 7 | 한 주에 만들 주제 수 |
| `type_mix` | 아이템 2, 상황 2, 컬러무드 1, 타깃 1, 팁 1 | 유형별 개수. **합계가 `per_week` 와 같아야 함** |
| `consider` | 계절, 서울 날씨 예보, 요일, 시즌 이벤트, 지난 게시물 반응 | 주제 고를 때 반영할 요소 |
| `events` | 개강, 추석, 할로윈, 연말 모임, 크리스마스 등 | 챙길 시즌 이벤트 |
| `no_repeat_days` | 30 | 비슷한 주제를 다시 쓰지 않는 기간 |
| `avoid` | 노출이 과한 코디, 체형 비하, 정치·사회 이슈 | 다루지 않을 주제 |
| `review_before_use` | true | 주제 목록을 텔레그램으로 먼저 보고 확정할지 |

유형 예시: 아이템("레더자켓 입어보세요"), 상황("월요일 출근룩"), 컬러무드("무채색으로 분위기"),
타깃("첫 데이트"), 팁("컬러 매치 TIP")

#### `product_source` : 상품을 어디서 어떤 기준으로
| 항목 | 예시 | 의미 |
|---|---|---|
| `name` | `zigzag` | 상품 소스 (`sources/<name>.py`). 계정마다 다른 쇼핑몰 가능 |
| `min_rating` | 4.5 | 이 평점 미만 상품 제외 |
| `min_reviews` | 20 | 리뷰 수가 이보다 적으면 제외 |
| `prefer_flat_shot` | true | 모델 착용컷보다 단독컷 있는 상품 우선 (이미지 품질에 유리) |

#### `visual` : 이미지 모양
| 항목 | 예시 | 의미 |
|---|---|---|
| `backdrop` | warm light-grey textured plaster surface… | 배경 묘사 (영어, 이미지 생성 프롬프트에 들어감) |
| `backdrop_ref` | 없음 | 배경 참고 이미지 파일명 (`accounts/<계정>/` 기준) |
| `feed_ratio` | `4:5` | 피드 이미지 비율 (`4:5` 또는 `1:1`) |
| `font` | Pretendard-SemiBold.otf | 제목 폰트 (`assets/fonts/` 기준) |
| `title_font_size` | 64 | 제목 글자 크기 |
| `title_line_spacing` | 1.25 | 제목 줄 간격 |
| `gradient_height` | 0.35 | 제목 뒤 어두운 그라데이션 높이 (이미지 아래 35%) |
| `gradient_opacity` | 0.45 | 그라데이션 진하기 (0~1) |
| `logo_text` | `@계정이름` | 이미지 하단 계정 표시. **본인 인스타 아이디로 바꿔야 함** |

#### `schedule` : 시간 (`HH:MM` 형식)
| 항목 | 예시 | 의미 |
|---|---|---|
| `timezone` | Asia/Seoul | 시간대 |
| `generate_at` | 21:17 | 전날 밤 게시물 생성 |
| `publish_at` | 07:23 | 다음날 게시 |
| `reels_at` | 18:47 | 릴스 게시 (없으면 `null`) |

GitHub 예약 실행은 정각에 몰려 늦어지기 쉬워서 정각을 피하는 것을 권장합니다.

#### 수익·승인
| 항목 | 예시 | 선택지 |
|---|---|---|
| `monetize` | `none` | `none`: 상품 링크만, 광고 표기 없음 / `manual_link`: 제휴(공유리워드) 링크 사용, 캡션 맨 앞 `(광고)` 와 첫 해시태그 `#광고` 자동 적용 |
| `approval` | `required` | `required`: 텔레그램에서 `/ok` 해야 게시 / `optional`: `/skip` 하지 않으면 자동 게시 |

#### `instagram` : 비밀값 **이름** (값이 아님)
| 항목 | 예시 | 의미 |
|---|---|---|
| `token_secret` | `IG_WOMEN_TOKEN` | GitHub Secrets에 등록한 인스타 토큰의 이름 |
| `user_id_secret` | `IG_WOMEN_USER_ID` | 인스타 사용자 ID의 이름 |

### 2. `accounts/<계정>/voice.md` : 말투 가이드

제목·캡션·해시태그를 쓸 때 이 파일을 그대로 따릅니다. 자유로운 글로 고치면 됩니다.

| 섹션 | 쓰는 내용 |
|---|---|
| 말투 | 존댓말·반말, 느낌표·이모지 사용 한도 |
| 커버 제목 | 줄 수, 줄당 글자 수, 구성 방식 |
| 좋은 제목 예시 | 계정 톤에 맞는 예시 10~20개 |
| 캡션 구조 | 코디 포인트, 제품 목록, 링크 안내, AI 합성 고지 순서 |
| 해시태그 | 개수, 기본 묶음 |
| 금지 표현 | 체형 비하, 과장, 가격 단정 등 |

### 3. `accounts/index.yaml` : 매일 돌릴 계정

```yaml
accounts:
  - women
```
계정을 추가하면 한 줄 더 넣고, 잠시 쉬고 싶은 계정은 빼면 됩니다.

### 4. 비밀값 (GitHub Secrets)

저장소 Settings → Secrets and variables → Actions 에 등록합니다. **파일에 적지 마세요.**
로컬에서 실행할 때는 `.env.example` 을 `.env` 로 복사해 값을 채웁니다 (`.env` 는 커밋되지 않음).

| 이름 | 용도 |
|---|---|
| `PHOTOROOM_API_KEY` | 상품 누끼·그림자·배경 (Photoroom API 대시보드에서 발급, 아래 참고) |
| `PHOTOROOM_API_KEY_SANDBOX` | (선택) 무료 테스트용 샌드박스 키 |
| `CLAUDE_CODE_OAUTH_TOKEN` | GitHub Actions에서 Claude Code 실행 (`claude setup-token`, Pro·Max 구독) |
| `IG_<계정>_TOKEN`, `IG_<계정>_USER_ID` | 인스타 게시 (아래 참고) |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL` | 인스타가 가져갈 이미지 공개 주소 (Cloudflare R2) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 미리보기·승인·알림 (@BotFather) |

#### 인스타 계정 연결

비밀번호는 어디에도 넣지 않습니다. 공식 게시 API는 아이디·비밀번호 대신 **액세스 토큰**을 씁니다.

1. 인스타 앱 → 설정 → 계정 유형 → **프로페셔널 계정 → 비즈니스** 로 전환
2. [developers.facebook.com](https://developers.facebook.com) 에서 앱 생성 → "Instagram 로그인(Instagram API with Instagram Login)" 추가
3. 권한 `instagram_business_basic`, `instagram_business_content_publish` 로 본인 계정 연결 → 장기 토큰(약 60일)과 인스타 사용자 ID(숫자) 받기
4. GitHub Secrets에 `IG_<계정>_TOKEN`, `IG_<계정>_USER_ID` 로 등록하고, 같은 이름을 `profile.yaml` 의 `instagram` 에 적기
5. 인스타 아이디(@...)는 `profile.yaml` 의 `visual.logo_text` 에 적기

#### 텔레그램 봇 연결

미리보기·승인(`/ok`, `/skip`)·실패 알림을 텔레그램으로 주고받습니다. 봇은 무료입니다.

1. 텔레그램에서 **@BotFather** 를 검색해 대화를 열고 `/newbot` 입력
2. 봇 이름(아무거나)과 아이디(`_bot` 으로 끝나야 함, 예: `my_coordi_bot`)를 정하면 **봇 토큰**이 나옵니다 (`123456789:ABC...` 형태)
3. 방금 만든 봇과 대화를 열고 아무 메시지나 한 번 보냅니다 (봇은 먼저 말을 걸 수 없어서 필요)
4. 브라우저에서 `https://api.telegram.org/bot<봇 토큰>/getUpdates` 를 열고, 결과의 `"chat":{"id":123456789` 숫자가 **채팅 ID** 입니다
5. GitHub Secrets에 `TELEGRAM_BOT_TOKEN` (봇 토큰), `TELEGRAM_CHAT_ID` (채팅 ID) 로 등록

- 여러 계정(여성·남성)을 운영해도 봇 하나로 충분합니다. 메시지에 계정 이름이 붙어서 옵니다.
- 봇 토큰이 알려지면 누구나 봇을 조종할 수 있으니 파일이나 채팅에 붙여 넣지 마세요. 새어 나갔다면 @BotFather 에서 `/revoke` 로 재발급합니다.
- 봇은 등록된 채팅 ID에서 온 명령만 따릅니다.

#### Photoroom API 키 발급

1. [Photoroom API 대시보드](https://app.photoroom.com/api-dashboard)에 가입하고 API 키를 만듭니다.
2. **Plus 요금제**(Image Editing API)가 필요합니다. 옷 펼치기(Flat Lay), AI 그림자·배경이 여기에 들어 있고,
   호출 1번에 약 $0.10입니다. Basic 요금제는 배경 지우기만 돼서 쓸 수 없습니다.
3. 키를 GitHub Secrets에 `PHOTOROOM_API_KEY` 로 등록합니다.
4. 요금 없이 흐름만 보려면 샌드박스를 씁니다 (하루 100회 무료, 결과에 워터마크). 대시보드의 샌드박스 키를
   `PHOTOROOM_API_KEY_SANDBOX` 로 등록하고 실행할 때 샌드박스 옵션을 켜면 그 키를 씁니다.
   샌드박스 키가 없으면 기본 키 앞에 `sandbox_` 를 붙여 호출합니다.

### 5. `config/models.yaml`, `config/layout.yaml` : 이미지 설정

`models.yaml` 은 Photoroom 주소와 옵션입니다.

| 항목 | 기본값 | 의미 |
|---|---|---|
| `image.flat_lay_size` | PORTRAIT_HD_3_2 | 옷 누끼 비율 |
| `image.backdrop_size` | PORTRAIT_4_3 | 배경 사진 생성 비율 (합성 때 4:5로 자름) |
| `image.shadow_model_version` | 2026-04-15 | AI 그림자 모델 버전 |

`layout.yaml` 은 1080×1350 캔버스에서 카테고리별 위치와 실제 크기입니다. `cm` 는 상품의 실제 치수
(`measure: height` 는 옷 기장, `long` 은 가방·신발처럼 긴 쪽)이고, `px_per_cm` 를 곱해 화면 크기를 정하므로
상품끼리 사람 몸 비율이 맞습니다. `center` 는 상품 중심(0~1 비율), `z` 는 겹칠 때 위에 오는 순서입니다.
아우터가 없으면 `no_outer` 값을 씁니다.

배경 사진은 프로필 `visual.backdrop` 문구로 한 번 생성합니다. 마음에 드는 배경을
`accounts/<계정>/backdrop.jpg` 로 넣고 `visual.backdrop_ref: backdrop.jpg` 로 지정하면 그 사진을 계속 씁니다.

### 6. `prompts/` : Photoroom 지시문

| 파일 | 역할 |
|---|---|
| `flatlay.md` | 모델이 입은 옷 사진 → 바닥에 펼친 옷 (Flat Lay) |
| `backdrop.md` | 빈 배경 사진 생성. 프로필 `visual.backdrop` 이 들어감 |
| `background.md` | 코디 뒤에 AI 배경·그림자 생성 |
| `refine.md`, `refine_hints.yaml` | 누끼를 실제 상품 사진처럼 다듬기 (가려진 부분 채우기, 다른 물건 지우기). 힌트는 카테고리별 |
| `style.md` | 두 상품을 입은 것처럼 합치기 (자켓 속 셔츠, 바지에 끼운 벨트) |
| `open_outer.md` | 셔츠를 입히기 전에 아우터 앞을 먼저 열기 |
| `style_over.md` | 안에 입는 옷 사진에 겉옷을 걸쳐 입히기 (`wear_modes: [over]`) |
| `style_peek.md` | 겉옷 사진에 안에 입는 옷의 깃 부분만 잘라 참고로 넣어, 깃·소매 끝·밑단만 보이게 입히기 (`wear_modes: [peek]`) |
| `scene.md` | 코디컷 전체를 실제 촬영 사진처럼 다듬기 |

`$이름` 자리는 코드가 채우니 지우지 마세요. 맨 위 `<!-- -->` 주석은 보내지 않습니다.

### 7. `assets/fonts/` : 폰트

`profile.yaml` 의 `visual.font` 에 적은 파일을 넣습니다. 기본은 Pretendard(SIL OFL, 상업 이용 가능)입니다.

---

## 이미지 품질 실험 (2단계)

같은 코디를 여러 방식으로 만들어 원본 상품과 비교합니다. Photoroom 요금이 나가므로 사람이 켤 때만 돕니다
(기본 설정 한 번에 약 20회 호출, 약 $2. 샌드박스는 하루 100회 무료).

1. `samples/<계정>/<이름>.yaml` 에 코디 한 벌(상품 5~7개)을 적습니다. 상품 페이지 주소(`url`) 또는
   직접 캡처한 사진 경로(`image`)를 씁니다. 한 사진에 여러 색이 있으면 `crop` 으로 일부만 쓰고,
   가방·신발·소품은 `segment` 에 남길 물건을 영어로 적습니다 (예: `bag`, `shoe`, `belt`).
   입은 듯이 합칠 상품은 `wear_with` 에 기준 카테고리, `wear_how` 에 입는 방법을 영어로 적습니다
   (예: 셔츠 `wear_with: 아우터`, 벨트 `wear_with: 하의`). `wear_modes` 에 입히는 방식 후보
   (`direct`, `open`, `over`)를 여러 개 적으면 모두 만들어 비교하고 첫 번째 것을 씁니다.
2. 실험 방식은 `experiments/image_poc.yaml` 에서 정합니다.
   - A: 누끼 그대로 + 실제 비율 배치 + 코드 그림자 (비교 기준)
   - B: AI 다듬기·입은 듯한 코디 + 코드 그림자
   - C: AI 다듬기·입은 듯한 코디 + Photoroom AI 그림자
   - D: AI 다듬기·입은 듯한 코디 + 장면 전체 AI 보정
   - `finish` 는 `code`, `ai_shadow`, `ai_background`, `ai_scene` 중 하나, `clean: true` 면 AI 다듬기를 씁니다.
3. GitHub → Actions → `image-poc` → Run workflow (샌드박스 체크 가능). 끝나면 결과가 artifact와
   `poc-results` 브랜치에 올라갑니다.
   - `sheets/cutouts.jpg`: 원본과 누끼 비교 (옷 누끼는 AI가 다시 그리므로 여기서 원본과 같은지 확인)
   - `sheets/clean.jpg`: AI로 다듬고 입은 듯이 합친 누끼
   - `sheets/variant_<방식>.jpg`: 위 줄은 원본 상품, 아래는 코디컷
   - `scores.csv`: 상품별 색·형태·디테일 점수표
4. 로컬에서는 환경 변수 `PHOTOROOM_API_KEY` 를 설정한 뒤
   `uv run python -m tools.poc --samples samples/women/fall_leather.yaml --out out/poc/test` 로 돌립니다.
   `--fake` 를 붙이면 API 없이 흐름만 확인합니다.

---

## 새 계정 추가

1. `accounts/_template/` 을 `accounts/<id>/` 로 복사
2. `profile.yaml` 의 `id` 를 폴더 이름과 같게 바꾸고 나머지 값 채우기
3. `voice.md` 채우기
4. `accounts/index.yaml` 에 `<id>` 추가
5. GitHub Secrets에 `IG_<ID>_TOKEN`, `IG_<ID>_USER_ID` 등록
6. `uv run python -m core.profile <id>` 로 검사

## 진행 단계

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | 저장소 뼈대, 설정, 데이터 규격 | 완료 |
| 2 | 이미지 생성 품질 검증 | 진행 중 |
| 3 | 제목 글자 합성 | |
| 4 | 상품 수집 | |
| 5 | Claude Code 스킬·에이전트 | |
| 6 | 이미지 검수 | |
| 7 | 인스타 게시 | |
| 8 | GitHub Actions 매일 실행 | |
| 9 | 릴스 | |
