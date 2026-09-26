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
| `accounts/<계정>/profile.yaml` | 대상·스타일·주제 기준·코디 규칙·이미지 모양·캐러셀 구성·시간·수익·승인 | 처음 한 번, 가끔 조정 |
| `accounts/<계정>/voice.md` | 제목·캡션 말투, 좋은 제목 예시, 해시태그, 금지 표현 | 반응 보면서 가끔 |
| `accounts/index.yaml` | 매일 돌릴 계정 목록 | 계정 추가·중지할 때 |
| GitHub Secrets | API 키, 인스타 토큰 등 비밀값 (저장소 밖에 보관) | 처음 한 번, 토큰 만료 시 |
| `config/models.yaml` | 이미지 API(Photoroom) 설정 | 거의 안 바꿈 |
| `config/layout.yaml` | 코디컷에서 상품별 위치·크기 | 배치를 바꾸고 싶을 때 |
| `prompts/` | Photoroom에 보내는 지시문(옷 펼치기, 배경) | 이미지 품질을 다듬을 때 |
| `assets/fonts/` | 제목 폰트 파일 (Pretendard 기본 포함) | 폰트를 바꿀 때 |

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
| `weather` | `{name: 서울, lat: 37.57, lon: 126.98}` | 날씨 예보를 볼 곳 (Open-Meteo, 키 필요 없음). `null` 이면 날씨를 보지 않음 |

유형 예시: 아이템("레더자켓 입어보세요"), 상황("월요일 출근룩"), 컬러무드("무채색으로 분위기"),
타깃("첫 데이트"), 팁("컬러 매치 TIP")

#### `product_source` : 상품을 어디서 어떤 기준으로
| 항목 | 예시 | 의미 |
|---|---|---|
| `name` | `zigzag` | 상품 소스 (`sources/<name>.py`). 계정마다 다른 쇼핑몰 가능 |
| `min_rating` | 4.5 | 이 평점 미만 상품 제외 |
| `min_reviews` | 20 | 리뷰 수가 이보다 적으면 제외 |
| `prefer_flat_shot` | true | 모델 착용컷보다 단독컷 있는 상품 우선 (이미지 품질에 유리) |
| `queries` | 아우터: [레더자켓, 트렌치코트], 상의: [셔츠, 니트] … | 상품 창고를 채울 카테고리별 검색어 |
| `per_query` | 10 | 검색어마다 상품 페이지까지 확인할 상품 수 (많을수록 수집이 오래 걸림) |
| `max_per_category` | 60 | 카테고리별로 창고에 남길 최대 상품 수 (평점·리뷰 좋은 순) |
| `recheck_days` | 3 | 창고 상품이 아직 판매 중인지 다시 확인하는 주기 (판매 종료·품절이면 창고에서 뺌) |

#### `visual` : 이미지 모양
| 항목 | 예시 | 의미 |
|---|---|---|
| `backdrop` | warm light-grey textured plaster surface… | 배경 묘사 (영어, 이미지 생성 프롬프트에 들어감) |
| `backdrop_ref` | 없음 | 배경 참고 이미지 파일명 (`accounts/<계정>/` 기준) |
| `feed_ratio` | `4:5` | 피드 이미지 비율 (`4:5` 또는 `1:1`) |
| `font` | Pretendard-Bold.otf | 제목 폰트 (`assets/fonts/` 기준) |
| `title_font_size` | 70 | 제목 글자 크기 (폭 1080px 기준). 줄이 너무 길면 자동으로 줄어듦 |
| `title_line_spacing` | 1.34 | 제목 줄 간격 (글자 크기의 배수) |
| `title_max_lines` | 2 | 제목 최대 줄 수 |
| `title_align` | `left` | 제목 정렬 (`left` 또는 `center`). `center` 면 세로선은 생략 |
| `title_margin_x` | 0.104 | 제목 좌우 여백 (폭 대비). 프로필 격자 미리보기가 양옆을 조금 자르므로 0.035 이상 |
| `title_bottom` | 0.155 | 이미지 아래 끝에서 제목 마지막 줄까지 거리 (높이 대비) |
| `title_color` | `#FFFFFF` | 제목 색 |
| `text_shadow` | 0.45 | 글자 뒤 그림자 진하기 (0이면 없음). 밝은 옷 위에서도 읽히게 해 줌 |
| `accent_color` | `#7A2E34` | 제목 왼쪽 세로선 색 (계정 포인트 색). `null` 이면 선 없음 |
| `accent_width` | 6 | 세로선 굵기 (px, 폭 1080 기준) |
| `accent_gap` | 26 | 세로선과 제목 사이 간격 (px) |
| `gradient_height` | 0.38 | 제목 뒤 어두운 그라데이션 높이 (이미지 아래 38%) |
| `gradient_opacity` | 0.5 | 그라데이션 맨 아래 진하기 (0~1) |
| `logo_text` | `@계정이름` | 제목 아래 계정 표시. **본인 인스타 아이디로 바꿔야 함**. 빈 문자열이면 생략 |
| `logo_font` | Pretendard-Medium.otf | 계정 표시 폰트. 없으면 `font` 사용 |
| `logo_font_size` | 22 | 계정 표시 글자 크기 |
| `logo_align` | `left` | 계정 표시 위치 (`left` 는 제목 왼쪽 끝에 맞춤, `center` 는 가운데) |
| `logo_bottom` | 0.065 | 이미지 아래 끝에서 계정 표시까지 거리 (높이 대비) |
| `logo_color` | `#EBEBEB` | 계정 표시 색 |

제목 모양은 이미지를 새로 만들지 않고 바로 확인할 수 있습니다:
`uv run python -m tools.overlay --account women --image 코디컷.jpg --title "첫 줄" "둘째 줄" --out 확인.jpg`

#### `feed` : 캐러셀 구성 (5단계)
한 게시물의 슬라이드 순서와 모양입니다. 굵은 글자 폰트는 `visual.font`, 포인트 색(세로선·번호·저장 아이콘)은
`visual.accent_color`, 계정 이름은 `visual.logo_text` 를 같이 씁니다. 크기는 폭 1080px 기준입니다.

| 항목 | 예시 | 의미 |
|---|---|---|
| `slides` | `[cover, products, summary]` | 슬라이드 순서. `cover` 표지(코디컷+제목), `products` 상품마다 한 장, `summary` 코디 목록. 빼고 싶은 장은 지우면 됨. 전체 10장까지 |
| `product_image` | `photo` | 상품 장 사진. `photo` 는 쇼핑몰 원본 사진(모서리 둥근 카드), `cutout` 은 누끼(코디컷과 같은 모양) |
| `hide_faces` | `true` | 원본 사진에서 모델 얼굴이 없는 사진을 먼저 고르고, 모두 얼굴이 있으면 얼굴 아래로 잘라 씀 (얼굴 검출 모델 YuNet, `assets/models/`) |
| `photo_candidates` | 6 | 상품마다 앞에서부터 살펴볼 사진 수 |
| `background` | `#EEEBE6` | 상품·요약 장 배경색 |
| `background_image` | 없음 | 배경 사진 파일명 (`accounts/<계정>/` 기준). 있으면 배경색 대신 씀 |
| `text_color` | `#222222` | 상품명·가격 색 |
| `sub_color` | `#8C867E` | 쇼핑몰·카테고리·계정 이름 같은 보조 글자 색 |
| `comment_color` | `#57524B` | 코멘트 색 |
| `body_font` | Pretendard-Medium.otf | 보조 글자 폰트 |
| `margin_x` | 0.104 | 좌우 여백 (폭 대비, 표지 제목과 같게) |
| `text_top` | 0.70 | 상품 장 글자가 시작하는 높이 (이미지 대비). 넘겨 볼 때 같은 자리에 오도록 고정, 글자가 많으면 자동으로 올라감 |
| `show_index` | `true` | 상품 장 위쪽 "01 아우터" 표시 |
| `show_shop` / `show_price` | `true` | 쇼핑몰 이름, 가격 표시 |
| `show_total` | `false` | 요약 장에 가격 합계 표시 |
| `index_size` | 26 | 위쪽 번호·카테고리 글자 크기 |
| `shop_size` | 28 | 쇼핑몰 이름 (요약 장 제목 아래 줄도 이 크기) |
| `name_size` / `name_max_lines` | 52 / 2 | 상품명 크기와 최대 줄 수 (넘치면 …) |
| `price_size` | 40 | 가격 |
| `comment_size` / `comment_max_lines` | 31 / 3 | 코멘트 크기와 최대 줄 수. 코멘트가 없으면 생략 |
| `heading` / `heading_size` | 오늘의 코디 / 60 | 요약 장 제목 |
| `row_name_size` / `row_meta_size` | 32 / 25 | 요약 장 목록의 상품명, 카테고리·쇼핑몰 |
| `cta_lines` | 저장해 두고… | 요약 장 맨 아래 문구. 첫 줄 앞에 저장 아이콘, `{logo}` 는 계정 이름으로 바뀜 |
| `cta_size` | 30 | 맨 아래 문구 크기 |
| `photo_credit` | 사진 출처 {source} · {shop} | 상품 사진 오른쪽 아래 출처 표기. `{source}` 는 `source_names` 의 이름, `{shop}` 은 스토어 이름. 빈 문자열이면 생략 |
| `summary_credit` | 사진 출처 지그재그 · 각 상품 스토어 | 요약 장 목록 아래 출처 한 줄. 빈 문자열이면 생략 |
| `credit_size` | 20 | 출처 글자 크기 |
| `source_names` | `{zigzag: 지그재그}` | 상품 소스 이름을 화면에 보일 이름으로 바꿈 |

#### `copy` : 제목·문구·캡션 규칙 (6단계)
Claude(write-copy 스킬)가 따르고 `tools.plan check` 가 자동으로 검사합니다. 말투와 좋은 예시는 `voice.md` 에 씁니다.

| 항목 | 예시 | 의미 |
|---|---|---|
| `title_chars` | `[8, 16]` | 표지 제목 한 줄 글자 수 (공백 포함). 벗어나면 경고 |
| `name_max_chars` | 18 | 상품 장 짧은 이름 최대 글자 수 (넘으면 경고) |
| `comment_max_chars` | 60 | 상품 장 코멘트 최대 글자 수 (넘으면 경고) |
| `points` | `[3, 3]` | 캡션 코디 포인트 줄 수 |
| `hashtags` | `[10, 15]` | 기본 해시태그 포함 최종 해시태그 개수 |
| `base_hashtags` | #데일리룩 #오늘의코디 … | 매번 붙는 해시태그 |
| `max_emoji` / `max_exclaim` | 2 / 1 | 캡션 전체 이모지·느낌표 수 (제목·상품 장에는 이모지 금지) |
| `banned` | 무조건, 인생템, 역대급, 최저가 … | 쓰면 오류가 나는 표현 |
| `ad_notice` / `ad_hashtag` | (광고) … / #광고 | `monetize: manual_link` 일 때 캡션 첫 줄과 첫 해시태그 |
| `product_line` | `{shop} · {name}` | 캡션 제품 목록 한 줄 모양 |
| `link_line` | 상품 정보는 프로필 링크에서 확인하세요 | 제품 목록 아래 안내 |
| `ai_notice` | 이미지는 AI로 합성했으며 … | 모든 캡션 끝에 붙는 AI 합성 고지 |

#### `review` : 이미지 검수 (7단계)
코디컷은 AI 입히기 결과가 매번 조금씩 달라서, 후보를 여러 장 만들고 Claude(image-reviewer 에이전트)가
원본 상품 사진과 비교해 점수를 매깁니다. 항목 5개(색, 모양, 빠진 상품, 삐져나온 조각, 겹침·잘림) × 0~2점 = 10점 만점.

| 항목 | 예시 | 의미 |
|---|---|---|
| `candidates` | 2 | 한 번에 만드는 코디컷 후보 수. 늘리면 Photoroom 호출도 그만큼 늘어남 |
| `max_rounds` | 2 | 후보가 모두 기준 미달일 때 다시 만드는 횟수 (첫 회 포함). 넘으면 사람 확인으로 표시 |
| `min_score` | 8 | 이 점수 이상이고 0점 항목이 없어야 통과 |
| `check_slides` | true | 상품 장 사진 색·모델 얼굴·글자 잘림도 확인 |

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
| `CLAUDE_CODE_OAUTH_TOKEN` | GitHub Actions에서 Claude Code 실행 (터미널에서 `claude setup-token` 을 실행해 나온 값, Pro·Max 구독) |
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
`feed_fill` 은 캐러셀 상품 장에서 누끼가 사진 자리를 채우는 최대 비율로, 귀걸이 같은 작은 상품이 옷만큼 커 보이지 않게 합니다.
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

`profile.yaml` 의 `visual.font` 에 적은 파일을 넣습니다. 기본으로 Pretendard Bold·Medium(SIL OFL, 상업 이용 가능)이 들어 있어 따로 받을 필요는 없습니다.

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

## 상품 수집 (4단계)

프로필의 `product_source.queries` 검색어로 지그재그에서 후보를 모아 `data/<계정>/products.json`
(상품 창고)에 쌓습니다. 6단계에서 Claude가 이 창고에서 그날 코디를 고릅니다.

1. **검색**: 검색 페이지를 브라우저로 열어 상품 번호, 이름, 쇼핑몰, 가격, 평점, 리뷰 수를 읽습니다.
   `min_rating`, `min_reviews` 기준에 못 미치거나 품절인 상품은 여기서 뺍니다.
2. **판매 확인**: 상품 페이지에서 판매 중인지와 사진 목록을 읽습니다. 판매 종료·품절·비공개면 뺍니다.
3. **사진 고르기**: 사진마다 점수를 매겨 사람 없이 상품 하나만 단색 배경에 찍힌 사진을 앞으로 보냅니다
   (`image_urls` 첫 장이 누끼에 쓸 사진). 그런 사진이 있으면 `has_flat_shot: true`.
   베이지·카멜 색 옷은 피부색으로 잘못 잡힐 수 있어, 최종 확인은 이미지 검수(7단계)가 합니다.
4. **창고 정리**: 이미 있던 상품은 `recheck_days` 가 지나면 다시 확인해 판매가 끝났으면 뺍니다.
   카테고리마다 `max_per_category` 개까지 평점·리뷰 좋은 순으로 남깁니다. 사용 기록(`last_used`)은 유지합니다.

수집 예절: 로그인 없이 공개 페이지만 읽고, 페이지는 3초, 사진은 1초 간격을 둡니다. 막히면(403·429)
30초, 90초 쉬었다 다시 시도하고, 그래도 막히면 거기서 멈추고 모은 것까지만 저장합니다.
지그재그 이용약관에는 비정상적인 접근(제14조)과 사전 승낙 없는 영리 목적 재이용(제13조 4항)을
금지하는 조항이 있습니다. 자동 수집과 게시는 이 위험을 알고 쓰는 것을 전제로 합니다.

실행 방법
- GitHub → Actions → `collect` → Run workflow. 카테고리·검색어를 지정할 수 있고, `commit` 을 켜면
  상품 창고를 그 브랜치에 커밋합니다. 보고서는 artifact와 `scout-results` 브랜치의 `collect-<번호>/` 에
  올라갑니다 (`report.md` 요약·표, `sheet.jpg` 새 상품 미리보기, `photos/`·`photo_scores.csv` 사진별 점수).
- 로컬: `uv sync --group scout && uv run playwright install chromium` 후
  `uv run python -m tools.collect --account women --max-queries 2 --report out/collect/test`

## 피드 구성 (5단계)

`tools.feed` 가 게시물 한 개의 캐러셀 슬라이드(JPEG)를 프로필 `feed` 순서대로 만듭니다. 재료는 YAML 파일
하나(`core.models.FeedSpec`)이고, 6단계에서 Claude가 이 파일을 씁니다.

```yaml
account: women
title_lines: [가을 출근룩 고민이라면, 레더자켓 하나로 충분해요]
cover: out/2026-09-27/look.jpg   # 제목 없는 코디컷 (없으면 표지 장 생략)
items:
  - product: zigzag:165576378    # 상품 창고의 이름·쇼핑몰·가격을 기본으로 씀
    name: 싱글 레더 하프자켓 블랙  # 슬라이드에 보일 짧은 이름 (없으면 광고 문구를 뺀 원래 이름)
    comment: 허리를 살짝 덮는 기장이라 슬랙스와도 잘 어울려요.
    cutout: out/2026-09-27/cutouts/outer.png   # 누끼 (또는 photo: 원본 사진)
```

- 로컬: `uv run python -m tools.feed --spec samples/women/feed_leather.yaml --out out/feed/test`
  (`--product-image cutout` 으로 누끼 방식 비교, `--photos <폴더>` 를 주면 사진이 없는 상품은 창고 사진 중 모델 얼굴이 없는 것을 골라 받아 씀)
- Actions: `feed-sample` → Run workflow. 상품 창고 사진을 받아 슬라이드를 만들고 artifact와
  `feed-results` 브랜치의 `feed-<번호>/` 에 올립니다 (받은 원본 사진은 올리지 않음).

## AI 기획 (6단계)

사람이 고르던 주제·상품·문구를 Claude Code 스킬이 맡습니다 (`.claude/skills/`, `.claude/agents/`).
판단은 스킬이 하고, 규칙 검사와 파일 만들기는 `tools.plan` 이 합니다. 하루 기획은 YAML 파일 하나
(`core.models.DailyPlan`, 예시 `samples/women/plan_2026-09-27.yaml`)로 남아 무엇을 왜 골랐는지 볼 수 있습니다.

```
오늘 정보(날씨·요일·재고) → 주제·검색어 → (새 상품 수집) → 상품 조합 → 사진 확인(색·얼굴)
 → 제목·코멘트·캡션 → 규칙 검사 → 코디컷(Photoroom) → 캐러셀 → 캡션 → manifest.json
```

- **버튼 하나로**: Actions → `daily` → Run workflow. `CLAUDE_CODE_OAUTH_TOKEN` 이 필요합니다.
  결과(슬라이드, `preview.jpg`, `caption.txt`, `plan.yaml`, `manifest.json`)는 artifact와
  `daily-results` 브랜치의 `<날짜>-run<번호>/` 에 올라갑니다. 게시는 하지 않습니다.
  - `plan` 에 기획 파일을 주면 Claude 없이 그 기획으로 만들기만 합니다
  - `sandbox` 를 켜면 Photoroom 무료 샌드박스(워터마크), `outfit: none` 이면 코디컷 없이
  - `collect` 를 켜면 주제 검색어로 지그재그 새 상품을 먼저 모읍니다
- **로컬 (Claude Code)**: `ACCOUNT=women claude "/daily-feed"`
- **도구만**:
  - `uv run python -m tools.plan context --account women` 오늘 정보
  - `uv run python -m tools.plan candidates --account women --category 아우터` 고를 수 있는 상품
  - `uv run python -m tools.plan check <plan.yaml>` 규칙 검사 (재고·판매 상태·재사용 기간·쇼핑몰 수·필수 카테고리·글자 수·금지 표현·해시태그)
  - `uv run python -m tools.plan build <plan.yaml> --outfit none` 캐러셀·캡션·manifest

## 이미지 검수 (7단계)

```
코디컷 후보 N장 (build) → 검수표 (tools.review prep) → image-reviewer 점수 → tools.review apply
   통과: 가장 좋은 후보를 표지로, manifest 에 점수
   모두 미달: tools.review more 로 후보 한 묶음 더 (max_rounds 까지) → 다시 검수
   그래도 미달, 또는 상품 장 문제: 가장 나은 후보를 표지로 두고 needs_human (8단계 미리보기에서 사람이 판단)
```

- 결과: `out/<날짜>/review/` 에 `covers.jpg`(원본과 후보 비교), `slides.jpg`, `input.md`, `scores.yaml`,
  `review.json`. `manifest.json` 의 `review_score`, `needs_human`, `review` 와 `report.md` 에도 남습니다.
- 도구가 직접 찾는 문제: 상품 장에 남은 얼굴, `…` 으로 잘린 이름·코멘트, 표지에 안 들어가는 제목.
  색·모양·삐져나온 조각은 Claude 가 봅니다.
- Actions `daily` 를 기획 파일(`plan`)로 돌리면 Claude 가 없어서 후보만 만들고 검수표까지 남깁니다
  (`needs_human: true`).
- 도구만:
  - `uv run python -m tools.review prep out/<날짜>` 검수표
  - `uv run python -m tools.review apply out/<날짜> --scores out/<날짜>/review/scores.yaml` 점수 적용·표지 고르기
  - `uv run python -m tools.review more out/<날짜>` 후보 더 만들기

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
| 2 | 이미지 생성 품질 검증 | 완료 |
| 3 | 제목 글자 합성 | 완료 |
| 4 | 상품 수집 | 완료 |
| 5 | 피드 구성 (캐러셀 슬라이드) | 완료 |
| 6 | AI 기획 (Claude Code 스킬·에이전트) | 완료 |
| 7 | 이미지 검수 | 진행 중 |
| 8 | 인스타 게시 | |
| 9 | GitHub Actions 매일 실행 | |
| 10 | 상품 창고를 Actions 캐시로 이전 | |
| 11 | 릴스 | |
