# FashionStudio

매일 인스타그램 코디 게시물을 자동으로 만들고 올리는 파이프라인입니다.

- **Claude**: 주제 선정, 상품 조합, 제목·캡션, 이미지 검수 (Claude Code 스킬·에이전트)
- **생성형 이미지 AI (OpenRouter, 기본 Nano Banana Pro)**: 쇼핑몰 사진을 참고로 바닥에 펼친 코디컷 한 장을 그림.
  빛·주름·겹침이 자연스러운 대신 상품이 원본과 달라질 수 있어 Claude 검수가 원본과 대조함
- **코드(Pillow)**: 제목 글자 합성, 캐러셀 상품 장(쇼핑몰 원본 사진) 만들기
- (예전 방식) **Photoroom API**: 누끼를 따서 코드로 배치. `outfit_image.engine: photoroom` 으로 계속 쓸 수 있음
- **GitHub Actions**: 매일 코디 후보 → 텔레그램에서 고르기 → 고른 코디만 이미지 생성 → 텔레그램 승인 → 인스타 게시

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
| 매주 1회 | 다음 주 주제 목록 확인 (마음에 안 들면 `/themes again`) | 텔레그램 | `themes.review_before_use: false` |
| 매일 오전 | 코디 후보 5개 중 하나 고르기 (버튼 또는 "3번", 새 후보 "다시", 건너뛰기 `/skip`) | 텔레그램 | `pick.enabled: false` (마감까지 안 고르면 `pick.on_timeout`) |
| 매일 오후 | 고른 코디로 만든 미리보기에 `/ok` (게시) 또는 `/skip` (건너뛰기) | 텔레그램 | `approval: optional` 이면 `/skip` 만 필요 |
| 빨리 올리고 싶을 때 | `/ok` 뒤에 `/now` (또는 "지금 올려") → 게시 시각을 기다리지 않고 다음 매시 확인 때 바로 게시 | 텔레그램 | 보내지 않으면 원래 시각에 게시 |
| 고칠 점이 있을 때 | 미리보기에 `/fix 1 신발 더 크게` (1 = 사진 순서), `/fix 캡션 더 짧게` → 고친 미리보기가 다시 옴 | 텔레그램 | `feedback.enabled: false` |
| 문제가 생겼을 때 | 이미지 검수 2회 실패, 토큰 만료 등 알림 확인 | 텔레그램 | - |
| 제휴 링크를 쓸 때만 | 게시 후 받은 상품 목록에 공유리워드 링크 붙여 답장 (댓글 DM 에 씀) | 텔레그램 | `comments.link_mode: zigzag` 이면 없음 |
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
| `accounts/<계정>/styling.md` | 코디 기준 (센스 포인트, 색 조합, 소품, 레이어드). 상품 가격대·포인트 말·수집 검색어는 `profile.yaml` | 코디 느낌을 바꿀 때 |
| `accounts/index.yaml` | 매일 돌릴 계정 목록 | 계정 추가·중지할 때 |
| GitHub Secrets | API 키, 인스타 토큰 등 비밀값 (저장소 밖에 보관) | 처음 한 번, 토큰 만료 시 |
| `config/models.yaml` | 이미지 AI(생성형 모델, Photoroom) 설정 | 거의 안 바꿈 |
| `config/publish.yaml` | 인스타 게시 API 주소, 게시용 사진 저장소 | 거의 안 바꿈 |
| `config/layout.yaml` | 코디컷에서 상품별 위치·크기 | 배치를 바꾸고 싶을 때 |
| `prompts/` | 이미지 AI 지시문 (`outfit_generate.md` 하루 코디컷, 나머지는 실험·예전 방식) | 이미지 품질을 다듬을 때 |
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
| `min_reviews` | 5 | 리뷰 수가 이보다 적으면 제외 (브랜드·디자이너 숍 상품은 리뷰가 적음) |
| `prefer_flat_shot` | true | 모델 착용컷보다 단독컷 있는 상품 우선 (이미지 품질에 유리) |
| `queries` | 아우터: [레더자켓, 트렌치코트], 상의: [셔츠, 니트] … | 상품 창고를 채울 카테고리별 검색어 |
| `per_query` | 10 | 검색어마다 상품 페이지까지 확인할 상품 수 (많을수록 수집이 오래 걸림) |
| `max_per_category` | 60 | 카테고리별로 창고에 남길 최대 상품 수 (아래 순서로) |
| `price_range` | 상의: [25000, 150000] … | 카테고리별 가격대(원). 벗어나면 수집하지 않고 창고에서도 뺌 |
| `exclude_words` | [만장, 1위, 특가 …] | 상품 이름에 있으면 제외 (가성비 베스트셀러 광고 문구) |
| `review_cap` | 300 | 순서를 정할 때 리뷰 수를 이 수까지만 셈 (많이 팔린 기본템이 늘 앞서지 않게) |

`style.point_words` (스웨이드, 레이스, 체크, 쁘띠 스카프 …) 가 이름에 있는 상품은 창고와 코디 후보에서 앞에 오고,
포인트 상품이 하나도 없는 코디는 `tools.plan check` 가 경고합니다. 코디 기준 글은 `accounts/<계정>/styling.md` (21단계).
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
| `slides` | `[cover, products, summary, comment]` | 슬라이드 순서. `cover` 표지(코디컷+제목), `products` 상품마다 한 장, `summary` 코디 목록, `comment` "상품 정보는 댓글로" 안내 장 (댓글 DM 을 켠 계정용). 빼고 싶은 장은 지우면 됨. 전체 10장까지 |
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
| `comment_title` | 상품 정보가 궁금하다면 / 댓글을 남겨 주세요 | 댓글 안내 장의 큰 글자 (줄마다 하나). `{logo}` 는 계정 이름 |
| `comment_sub` | 댓글을 남기면 DM으로 / 상품 링크를 보내 드려요 | 큰 글자 아래 작은 글자 |
| `comment_box` | 댓글 달기... | 댓글 입력창 모양 안의 글자. 빈 문자열이면 입력창 없이 |
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
| `hashtags` | `[8, 12]` | 기본 해시태그 포함 코디 해시태그 개수 (`comment_tags` 소통용은 따로) |
| `base_hashtags` | #데일리룩 #오늘의코디 … | 매번 붙는 해시태그 |
| `intro` | false | true 면 캡션 맨 앞에 도입 한 줄(`text.intro`)이 필수 |
| `max_emoji` / `max_exclaim` | 2 / 1 | 도입·코디 포인트의 이모지·느낌표 수 (제목·상품 장에는 이모지 금지) |
| `max_emoji_per_line` | 1 | 도입·코디 포인트 한 줄의 이모지 수 |
| `banned` | 무조건, 인생템, 역대급, 최저가 … | 쓰면 오류가 나는 표현 |
| `title_banned` | 월요일 … 일요일 | 표지 제목에 쓰면 오류가 나는 말 (캡션에는 써도 됨) |
| `ad_notice` / `ad_hashtag` | (광고) … / #광고 | `monetize: manual_link` 일 때 캡션 첫 줄과 첫 해시태그 |
| `product_line` | `{shop} · {name}` | 캡션 제품 목록 한 줄 모양 |
| `link_line` | 상품 정보는 프로필 링크에서 확인하세요 | 제품 목록 아래 안내 |
| `ai_notice` | 이미지는 AI로 합성했으며 … | 모든 캡션 끝에 붙는 AI 합성 고지 |

`comment_tags` 를 켜면 게시 직후 내 댓글(`comment`, 기본 🤍)을 달고, 그 대댓글로 해시태그를 답니다.
대댓글은 코디 해시태그 뒤에 `follow_hashtags`(예: #맞팔 #선팔 #소통)를 붙여 최대 `max`(15)개이고,
캡션에는 해시태그를 넣지 않습니다 (`manual_link` 의 #광고 는 고지라서 캡션에 남김).
댓글을 달지 못해도 게시는 그대로 두고 텔레그램 게시 알림에 적습니다.

#### `ai_label` : 인스타 "AI 정보" 레이블 (13단계)
인스타 게시 API 에는 AI 레이블을 켜는 값이 없습니다. 대신 Meta 는 이미지 파일 안의 업계 표준 표시
(IPTC `DigitalSourceType`, XMP)를 읽어 게시물에 "AI 정보" 레이블을 붙입니다. 그래서 슬라이드를 저장할 때
그 표시를 파일에 넣습니다 (`tools.ai_label`). 캡션의 `ai_notice` 고지는 이와 별개로 늘 넣습니다.

| 항목 | 기본값 | 뜻 |
|---|---|---|
| `mode` | `none` | `metadata` 면 표시를 넣음, `none` 이면 안 넣음 |
| `source_type` | `composite` | `composite`: 실제 사진 + AI 편집 (누끼·AI 그림자 코디컷), `generated`: AI 가 통째로 만든 이미지 |
| `slides` | `[cover]` | 표시를 넣는 슬라이드 종류 (`cover`, `products`, `summary`, `comment`) |

게시 직전(`tools.publish`)에도 표시가 없으면 픽셀을 다시 압축하지 않고 넣습니다.
파일 확인: `uv run python -m tools.ai_label out/<날짜>/slides/*.jpg`

#### `review` : 이미지 검수 (7단계)
코디컷은 AI 입히기 결과가 매번 조금씩 달라서, 후보를 여러 장 만들고 Claude(image-reviewer 에이전트)가
원본 상품 사진과 비교해 점수를 매깁니다. 항목 5개(색, 모양, 빠진 상품, 삐져나온 조각, 겹침·잘림) × 0~2점 = 10점 만점.

| 항목 | 예시 | 의미 |
|---|---|---|
| `candidates` | 3 | 한 번에 만드는 코디컷 후보 수. 생성 1장 약 $0.15 (Nano Banana Pro), 후보 수만큼 늘어남 |
| `max_rounds` | 2 | 후보가 모두 기준 미달일 때 다시 만드는 횟수 (첫 회 포함). 넘으면 사람 확인으로 표시 |
| `min_score` | 8 | 이 점수 이상이고 0점 항목이 없어야 통과 |
| `check_slides` | true | 상품 장 사진 색·모델 얼굴·글자 잘림도 확인 |

#### `looks` : 코디 여러 벌 게시물 (20단계)
켜면 하루 게시물이 같은 주제의 코디 여러 벌이 됩니다. 1장 표지(1번 벌 코디컷 + 제목) → 벌마다 한 장
(코디컷 + 왼쪽 아래 상품 정보 몇 줄, "LOOK 2 / 아우터 울 하프 코트 스토어") → 댓글 안내 장.
상품 장·요약 장은 없고, 상품 링크는 댓글 DM 과 텔레그램 상품 링크 목록(벌별로 묶음)으로 받습니다.
켤 때 `feed.slides` 를 `[cover, looks, comment]` 로, 끄면 `[cover, products, summary, comment]` 로 둡니다.

| 항목 | 예시 | 의미 |
|---|---|---|
| `enabled` | true | false 면 예전처럼 한 벌 + 상품 장 |
| `count` | 6 | 게시물에 넣는 벌 수 (표지·댓글 안내 포함 10장 안) |
| `min` | 5 | 텔레그램에서 직접 고를 때 최소 벌 수 |
| `spare` | 2 | 후보로 더 보내는 예비 벌 수 ("4번 빼고" 하면 채움, 이미지 비용 없음) |
| `share_categories`, `max_share` | [가방, 신발, 소품], 2 | 이 카테고리는 같은 상품을 두 벌까지 같이 써도 됨 (옷은 한 벌에만) |
| `candidates` | 1 | 벌마다 처음 그리는 코디컷 수. 검수에서 떨어진 벌만 `review.max_rounds` 까지 다시 |
| `repeat_cover_look` | true | 표지 다음 장에 1번 벌을 상품 정보와 함께 다시 보여 줌 |
| `label`, `show_shop`, `show_price` | "LOOK {n}", true, false | 벌 장 글자 (가격은 바뀔 수 있어 기본으로 뺌) |
| `composition` | (영어) | 벌 코디컷 지시문 끝에 붙여 글자 자리(왼쪽 아래)를 비움 |

비용: 코디컷 1장 약 $0.15. 6벌 × 1장 + 떨어진 벌 다시 그리기로 하루 약 $1.0~1.3 (월 $30~40).
상품을 하루 30개 넘게 쓰므로 `outfit.reuse_cooldown_days` 를 줄이고(7) `product_source.max_per_category` 를 늘렸습니다(150).

#### `outfit_image` : 표지 코디컷 만드는 방식 (11단계)
| 항목 | 예시 | 의미 |
|---|---|---|
| `engine` | generate | `generate` 생성형 AI가 코디 전체를 그림 / `photoroom` 예전 누끼 배치 |
| `model` | nano_banana_pro | `config/models.yaml` 의 `generate.models` 키 |
| `prompt` | outfit_generate | `prompts/<이름>.md` 지시문 |
| `scene` | linen floor, window light … | 바닥과 빛 (영어) |
| `outer_styling` | almost closed … | 아우터가 있는 날 입히는 방법 (영어). 기획의 `styling` 이 있으면 그것을 씀 |

생성형은 상품마다 쇼핑몰 사진 1~3장(사진 확인 단계가 고른 `ref_photos`, 고른 색만)을 한 장의 참고판으로 붙여 보냅니다.
모델 얼굴은 잘라 내고 보내며, 참고판은 결과 브랜치에 올리지 않습니다. 한 달 비용은 하루 후보 3장 기준 약 $13~14
(다시 만드는 날이 많으면 늘어남)이고, 매일 `report.md` 에 그날 생성 비용이 남습니다.

#### `schedule` : 매일 자동 실행 시간표 (9단계)
시간은 `timezone` 기준 `HH:MM`, 주간 작업은 `"요일 HH:MM"` (여러 요일은 쉼표, 예: `"월,목 04:17"`).

| 항목 | 예시 | 의미 |
|---|---|---|
| `enabled` | true | false 면 이 계정은 자동으로 돌지 않음 (Actions 버튼으로만) |
| `timezone` | Asia/Seoul | 시간대 |
| `candidates_at` | "10:17" | 그날 코디 후보를 텔레그램으로 보냄 |
| `pick_check_every` | 30 | 고른 번호를 확인하는 간격 (분). 고른 뒤 이만큼 안에 이미지 만들기 시작 |
| `pick_deadline` | "15:17" | 이때까지 안 고르면 `pick.on_timeout` 대로. 게시 시각보다 1시간 이상 앞서야 함 |
| `publish_at` | "20:03" | 게시 (승인이 있으면) |
| `publish_retry_every` | 30 | 게시 시각에 `/ok` 가 없으면 다시 보는 간격 (분) |
| `publish_retries` | 4 | 다시 보는 횟수. 늦게 `/ok` 해도 이 안이면 게시 (승인 마감이 있으면 게시 실패 때만 의미) |
| `ok_deadline` | null | 승인 마감 "HH:MM". 이 뒤에 온 `/ok` 는 게시에 쓰지 않고, `/fix` 는 `feedback.render_minutes` 전까지 받음. 고르기 마감과 게시 사이 (게시가 다음 날 아침이면 그 전 새벽) |
| `ok_remind_at` | null | 이때까지 `/ok`·`/skip` 이 없으면 승인 마감을 한 번 알림. 승인 마감에도 "게시 안 함"을 한 번 알림 |
| `themes_at` | "일 18:17" | 다음 주 주제 정하기 (없으면 `null`, 그날그날 주제를 정함) |
| `collect_at` | "월,목 04:17" | 지그재그 새 상품 수집 |
| `token_refresh_at` | "일 18:47" | 인스타 토큰 연장 (60일 만료라 매주) |
| `reels_at` | null | 릴스 게시 (19단계, `reels.enabled` 일 때). 그날 캐러셀을 게시했을 때만. `null` 이면 캐러셀 바로 뒤에. 바꾸면 `tools.schedule sync` |
| `github_cron` | all | GitHub 예약 줄. 외부 스케줄러를 설정했으면 `backup` (주요 칸만 예비로 남겨 Actions 시간 절약) |

**시간을 바꾼 뒤에는** `uv run python -m tools.schedule sync` 를 돌려 `.github/workflows/schedule.yml` 의
cron 줄을 맞추고 함께 커밋하세요 (맞지 않으면 CI 테스트가 실패합니다). `uv run python -m tools.schedule list` 로
계정별 시간표와 UTC cron 을 볼 수 있습니다. GitHub 예약 실행은 늦거나 빠질 때가 많아(정각에 더 몰림)
외부 스케줄러(아래 "외부 스케줄러")가 정각에 깨우게 하고, 모든 칸을 정각으로 두면 외부 작업 하나(Every 1 hour)로
충분합니다. 예약 실행은 기본 브랜치(main)에 합친 뒤부터 돕니다.

#### `pick` : 코디 고르기 (9단계)
이미지 AI 비용을 아끼려고, Claude 가 코디 후보를 여러 개 짜서 **쇼핑몰 원본 사진으로** 먼저 보여 주고
고른 한 벌만 코디컷을 만듭니다. 후보 단계에서는 이미지 AI 를 부르지 않습니다.

| 항목 | 예시 | 의미 |
|---|---|---|
| `enabled` | true | false 면 고르지 않고 Claude 가 정한 한 벌을 바로 만듦 |
| `count` | 5 | 보내는 후보 수 (2~8) |
| `on_timeout` | first | 마감까지 안 고르면 `first`(1번으로 진행) 또는 `skip`(그날 건너뜀) |
| `max_again` | 2 | `/again` 으로 새 후보를 받을 수 있는 횟수 |

여러 벌 게시물(`looks`)이면 후보는 벌 `count + spare` 개를 한 번에 보냅니다 (벌마다 사진 시트 한 장).
그대로 두면 마감에 앞의 `count` 벌로 만들고, 바로 만들려면 `/pick`, 빼려면 "4번 빼고"(`/drop 4`, 예비 벌이 채움),
직접 고르려면 "1 2 3 5 6 7" 처럼 번호를 보냅니다 (`min`~`count` 벌).

#### `feedback` : 미리보기 고칠 점 `/fix` (15단계)
미리보기를 받은 뒤 고칠 점을 텔레그램으로 보내면 같은 코디로 고쳐 다시 만들고 새 미리보기를 보냅니다.
자세한 사용법은 아래 [미리보기 고치기](#미리보기-고치기-15단계).

| 항목 | 예시 | 의미 |
|---|---|---|
| `enabled` | true | false 면 `/fix` 를 읽지 않음 |
| `max_fixes` | 2 | 하루에 다시 만드는 횟수 (넘으면 피드백만 기록) |
| `check_every` | 60 | 고르기 마감~게시 시각 사이 `/fix` 를 확인하는 간격(분). 0 이면 다른 자동 실행 때만. 바꾸면 `tools.schedule sync` |
| `render_minutes` | 40 | 게시 마지막 확인 시각까지 이만큼(분) 남아야 다시 만듦 |
| `recent_days` | 14 | 최근 며칠 피드백을 다음 기획·검수에 보여 줄지 (0 이면 안 보임) |
| `rules_at` | 템플릿 08:00 | 새 피드백을 규칙에 반영하는 PR 을 만드는 시각 (하루 한 번, `null` 이면 안 함). 바꾸면 `tools.schedule sync` |

#### `comments` : 댓글 → DM 코디 링크 (10단계)
캡션에 "댓글을 남겨 주세요" 안내를 넣고, 댓글(키워드를 정하면 그 말이 든 댓글)이 달리면 인스타 공식 기능(비공개 답장)으로
상품 링크를 DM 으로 보냅니다. 켜기 전에 아래 [ManyChat 연결](#manychat-연결-처음-한-번)을 먼저 하세요.
`check_at` 부터 `share_wait_hours` 까지는 `mode: builtin` 일 때만 씁니다.

| 항목 | 예시 | 의미 |
|---|---|---|
| `enabled` | false | true 면 캡션 안내 문구(`copy.link_line` 대신)가 들어감 (builtin 이면 댓글 확인도) |
| `mode` | external | `external`: ManyChat 이 DM (여기서는 캡션만) / `builtin`: 자동 실행 때 확인해 직접 DM, 바꾸면 `tools.schedule sync` |
| `cta` | `코디 정보가 궁금하면 댓글을 남겨 주세요…` | 캡션 안내 문구. `{keyword}` 를 쓰면 `keywords` 첫 번째로 바뀜 |
| `keywords` | [] | 비어 있으면 모든 댓글에 DM. `[정보]` 처럼 적으면 그 말이 든 댓글만 (띄어쓰기·대소문자 무시) |
| `check_at` | ["08:17", "17:17", "23:17"] | 댓글만 확인하러 따로 깨어나는 시각 (한 번에 Actions 약 1분) |
| `with_other_ticks` | true | 후보·고르기·게시로 깨어날 때도 같이 확인 (추가 시간 거의 없음) |
| `days` | 7 | 게시 후 며칠 동안 댓글을 볼지 (인스타 규칙상 최대 7) |
| `dm_intro`, `dm_item`, `dm_outro` | | DM 글. `{title}` 제목, 상품 줄 `{index} {category} {shop} {name} {url}` |
| `public_reply` | "" | 댓글에 공개 답글도 달기 (예: "DM 보내 드렸어요"). 빈칸이면 안 함 |
| `link_mode` | zigzag | `zigzag`: 상품 링크 / `share_reward`: 공유리워드 링크 (`monetize: manual_link` 필요) |
| `share_wait_hours` | 6 | 공유 링크를 기다리는 시간. 넘으면 상품 링크로 보냄 |

#### `reels` : 릴스 (19단계)
그날 캐러셀 슬라이드를 세로 영상(1080×1920)으로 이어 붙여 `schedule.reels_at` 에 올립니다.
영상 AI 는 쓰지 않아 추가 비용이 없습니다. 자세한 내용은 아래 [릴스](#릴스-19단계).

| 항목 | 예시 | 의미 |
|---|---|---|
| `enabled` | true | false 면 릴스를 만들지도 올리지도 않음 (바꾸면 `tools.schedule sync`) |
| `slides` | null | 넣을 장 종류. `null` 이면 그날 캐러셀 장 전부를 캐러셀 순서대로 (펼친컷 날·모델컷 날 모두) |
| `seconds` | {cover: 2.5, comment: 2.0} | 장 종류별로 보여 주는 시간 (초) |
| `default_sec` | 1.6 | `seconds` 에 없는 장(벌 장, 상품 장)의 시간 |
| `transition` | cut | 장 사이 효과: `cut`(바로 다음 장으로) / `slide`(캐러셀 넘기듯) / `fade` |
| `transition_sec` | 0.35 | 넘기는 시간 (초, `cut` 이면 안 씀) |
| `top` | 0.105 | 슬라이드 위쪽 끝 위치 (세로 대비). 아래쪽은 인스타 캡션·버튼이 덮는 자리라 비움 |
| `background` | null | 남는 곳 색. `null` 이면 `feed.background` |
| `music_dir` | accounts/women/music | 배경 음악 폴더. 상업 이용이 허락된 mp3·m4a 를 넣으면 날짜마다 돌려 씀. 비어 있으면 소리 없음 |
| `music_volume` | 0.8 | 음악 크기 |
| `share_to_feed` | true | 프로필 격자에도 릴스가 보이게 (`instagram` 일 때) |
| `delivery` | telegram | `telegram`: `reels_at` 에 영상·캡션을 텔레그램으로 (앱에서 음악 붙여 직접 게시) / `instagram`: 자동 게시 |

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
| `OPENROUTER_API_KEY` | 생성형 코디컷 (OpenRouter에서 발급, 아래 참고) |
| `PHOTOROOM_API_KEY` | (예전 방식 `engine: photoroom` 일 때만) 상품 누끼·그림자·배경 |
| `PHOTOROOM_API_KEY_SANDBOX` | (선택, 예전 방식) 무료 테스트용 샌드박스 키 |
| `CLAUDE_CODE_OAUTH_TOKEN` | GitHub Actions에서 Claude Code 실행 (터미널에서 `claude setup-token` 을 실행해 나온 값, Pro·Max 구독) |
| `IG_<계정>_TOKEN`, `IG_<계정>_USER_ID` | 인스타 게시 (아래 참고) |
| `MEDIA_REPO_TOKEN` | 인스타가 가져갈 사진을 잠시 올려 둘 공개 저장소에 쓰는 토큰 (아래 참고) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 코디 고르기·미리보기·승인·알림 (@BotFather) |
| `SECRETS_TOKEN` | (선택) 인스타 토큰을 연장했을 때 값이 바뀌면 `IG_<계정>_TOKEN` 을 자동으로 바꾸는 토큰 (아래 참고) |

#### 인스타 계정 연결

비밀번호는 어디에도 넣지 않습니다. 공식 게시 API(Instagram API with Instagram Login)는 **액세스 토큰**을 씁니다.
이 방식은 페이스북 페이지 연결이 필요 없습니다.

1. 인스타 앱 → 설정 → 계정 유형 및 도구 → **프로페셔널 계정으로 전환** (크리에이터 또는 비즈니스 둘 다 됨)
2. [developers.facebook.com](https://developers.facebook.com) 에 로그인 → 내 앱 → **앱 만들기**
   - 사용 사례: **Instagram에서 메시지 및 콘텐츠 관리** 선택, 앱 유형은 비즈니스
3. 앱 대시보드 → 앱 역할 → 역할 → **Instagram 테스터 추가** 에 게시할 인스타 아이디 입력
   → 인스타 앱(또는 웹) 설정 → 웹사이트 권한 → 앱 및 웹사이트 → **테스터 초대** 수락
   (본인 계정에만 올리므로 앱 검수·공개 전환은 필요 없음)
4. 앱 대시보드 → Instagram → **Instagram 로그인을 통한 API 설정** → 액세스 토큰 생성 → 계정 추가 → 로그인
   → **토큰 생성** 을 누르면 장기 토큰(약 60일, `IGAA…`)이 나옵니다
5. 인스타 사용자 ID(숫자): 같은 화면의 계정 옆 숫자, 또는 브라우저에서
   `https://graph.instagram.com/v23.0/me?fields=user_id,username&access_token=<토큰>` 을 열어 `user_id` 값
6. GitHub Secrets에 `IG_<계정>_TOKEN`(토큰), `IG_<계정>_USER_ID`(숫자)로 등록하고, 같은 이름을 `profile.yaml` 의
   `instagram` 에 적기 (여성 계정은 이미 `IG_WOMEN_TOKEN`, `IG_WOMEN_USER_ID`)
7. 인스타 아이디(@...)는 `profile.yaml` 의 `visual.logo_text` 에 적기

토큰은 약 60일 뒤 만료됩니다. 만료되면 게시가 실패하고 텔레그램으로 알림이 오니, 4번에서 새로 만들어 Secret 을
바꾸면 됩니다 (자동 연장은 9단계).

#### 게시용 사진 저장소 (공개 GitHub 저장소)

인스타 게시 API 는 사진 파일을 직접 받지 않고 **누구나 열 수 있는 주소**로만 받습니다. 이 저장소는 비공개라서
게시할 사진만 따로 공개 저장소에 잠시 올리고, 게시가 끝나면 지웁니다. 무료이고 카드 등록이 필요 없습니다.
(인스타에 곧 공개될 슬라이드만 올라가고, 쇼핑몰 원본 사진·기획 파일은 올라가지 않습니다.)

1. GitHub → **New repository** → 이름 `<이 저장소 이름>-media` (예: `FashionStudio-media`), **Public**,
   **Add a README file** 체크 (main 브랜치가 생겨야 함)
2. GitHub 설정 → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token
   - Repository access: **Only select repositories** → 방금 만든 `…-media` 저장소만
   - Permissions → Repository permissions → **Contents: Read and write**
   - 만료일은 원하는 대로 (만료되면 게시 실패 알림이 옴)
3. 나온 토큰을 이 저장소 Secrets 에 `MEDIA_REPO_TOKEN` 으로 등록

저장소 이름을 다르게 지었으면 `config/publish.yaml` 의 `image_host.repo` 에 `owner/이름` 으로 적습니다.

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

#### OpenRouter API 키 발급

1. [OpenRouter](https://openrouter.ai)에 가입하고 Credits 에서 선불로 충전합니다 (한 달 약 $15면 충분).
2. Keys 에서 키를 만들 때 사용 한도(credit limit)를 걸어 두면 비용이 넘치지 않습니다.
3. 키를 GitHub Secrets에 `OPENROUTER_API_KEY` 로 등록합니다.
4. 크레딧이 떨어지면 그날은 코디컷 없이 만들어지고 사람 확인으로 남습니다 (텔레그램 미리보기에서 보임).

#### Photoroom API 키 발급 (예전 방식일 때만)

1. [Photoroom API 대시보드](https://app.photoroom.com/api-dashboard)에 가입하고 API 키를 만듭니다.
2. **Plus 요금제**(Image Editing API)가 필요합니다. 옷 펼치기(Flat Lay), AI 그림자·배경이 여기에 들어 있고,
   호출 1번에 약 $0.10입니다. Basic 요금제는 배경 지우기만 돼서 쓸 수 없습니다.
3. 키를 GitHub Secrets에 `PHOTOROOM_API_KEY` 로 등록합니다.
4. 요금 없이 흐름만 보려면 샌드박스를 씁니다 (하루 100회 무료, 결과에 워터마크). 대시보드의 샌드박스 키를
   `PHOTOROOM_API_KEY_SANDBOX` 로 등록하고 실행할 때 샌드박스 옵션을 켜면 그 키를 씁니다.
   샌드박스 키가 없으면 기본 키 앞에 `sandbox_` 를 붙여 호출합니다.

### 5. `config/models.yaml`, `config/layout.yaml` : 이미지 설정

`models.yaml` 의 `generate` 는 생성형 코디컷 설정입니다 (OpenRouter 주소, 비율·해상도, 모델 목록, 기획에 크기가
없을 때 쓰는 `default_sizes`). 아래 `image` 는 예전 Photoroom 방식의 옵션입니다.

| 항목 | 기본값 | 의미 |
|---|---|---|
| `image.flat_lay_size` | PORTRAIT_HD_3_2 | 옷 누끼 비율 |
| `image.backdrop_size` | PORTRAIT_4_3 | 배경 사진 생성 비율 (합성 때 4:5로 자름) |
| `image.shadow_model_version` | 2026-04-15 | AI 그림자 모델 버전 |

`layout.yaml` 은 1080×1350 캔버스에서 카테고리별 위치와 실제 크기입니다. `cm` 는 상품의 실제 치수
(`measure: height` 는 옷 기장, `long` 은 가방·신발처럼 긴 쪽)이고, `px_per_cm` 를 곱해 화면 크기를 정하므로
상품끼리 사람 몸 비율이 맞습니다. `center` 는 상품 중심(0~1 비율), `z` 는 겹칠 때 위에 오는 순서입니다.
`feed_fill` 은 캐러셀 상품 장에서 누끼가 사진 자리를 채우는 최대 비율로, 귀걸이 같은 작은 상품이 옷만큼 커 보이지 않게 합니다.
아우터가 없으면 `no_outer` 값을 씁니다. 아우터와 상의가 함께면 상의를 아우터에 입히고(기획이 정하지 않으면
아우터를 연 모양), 입히기가 실패해 따로 놓게 되면 `outer_and_top` 값으로 나란히 둡니다.
`lengths` 는 상품 이름에 든 말(롱, 하프, 크롭, 스커트 …)로 실제 기장을 정하는 표입니다 (롱코트 110cm).
기본보다 긴 아우터는 어깨를 맞춰 아래로 늘어나 입은 것처럼 하의 위를 덮습니다.

배경 사진은 프로필 `visual.backdrop` 문구로 한 번 생성합니다. 마음에 드는 배경을
`accounts/<계정>/backdrop.jpg` 로 넣고 `visual.backdrop_ref: backdrop.jpg` 로 지정하면 그 사진을 계속 씁니다.

### 6. `prompts/` : 이미지 AI 지시문

하루 코디컷은 `outfit_generate.md` 하나를 씁니다 (상품 설명·실제 크기·입는 방법·바닥이 채워짐).
`gen_*.md` 는 모델 비교 실험(`tools.generate`), 아래 표는 예전 Photoroom 방식의 지시문입니다.

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
   `min_rating`, `min_reviews`, `price_range`, `exclude_words` 기준에 못 미치거나 품절인 상품은 여기서 뺍니다.
2. **판매 확인**: 상품 페이지에서 판매 중인지와 사진 목록을 읽습니다. 판매 종료·품절·비공개면 뺍니다.
3. **사진 고르기**: 사진마다 점수를 매겨 사람 없이 상품 하나만 단색 배경에 찍힌 사진을 앞으로 보냅니다
   (`image_urls` 첫 장이 누끼에 쓸 사진). 그런 사진이 있으면 `has_flat_shot: true`.
   베이지·카멜 색 옷은 피부색으로 잘못 잡힐 수 있어, 최종 확인은 이미지 검수(7단계)가 합니다.
4. **창고 정리**: 이미 있던 상품은 `recheck_days` 가 지나면 다시 확인해 판매가 끝났으면 뺍니다.
   기준(가격대·제외어)이 바뀌면 있던 상품도 같이 거릅니다. 카테고리마다 `max_per_category` 개까지
   평점 × 리뷰(최대 `review_cap`) 순, 포인트 상품 우선으로 남깁니다. 사용 기록(`last_used`)은 유지합니다.

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
 → 제목·코멘트·캡션 → 규칙 검사 → 코디컷(생성형 AI) → 캐러셀 → 캡션 → manifest.json
```

- **버튼 하나로**: Actions → `daily` → Run workflow. `CLAUDE_CODE_OAUTH_TOKEN` 이 필요합니다.
  결과(슬라이드, `preview.jpg`, `caption.txt`, `plan.yaml`, `manifest.json`)는 artifact와
  `daily-results` 브랜치의 `<날짜>-run<번호>/` 에 올라갑니다. 게시는 하지 않습니다.
  - `plan` 에 기획 파일을 주면 Claude 없이 그 기획으로 만들기만 합니다
  - `outfit` 은 `auto`(계정 설정), `generate`, `photoroom`, `none`(코디컷 없이).
    `sandbox` 는 photoroom 방식일 때만 무료 샌드박스(워터마크)
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

## 인스타 게시 (8단계)

```
daily (전날 밤) → 텔레그램 미리보기: 슬라이드 전체, 캡션, 검수 점수, "사람 확인 필요" 이유
                  입력창 아래 버튼 [/ok women 09-27] [/skip women 09-27]
publish (게시 시간) → 게시 전 검사 → 답장 확인 → 사진을 공개 저장소에 올림 → 인스타 캐러셀 게시
                   → 사진 지움 → 게시 기록(data/<계정>/posts.json) 커밋 → 텔레그램으로 게시물 주소
```

- **승인**: 프로필 `approval: required` 면 `/ok` 가 있어야 게시하고, `optional` 이면 `/skip` 이 없으면 게시합니다.
  단 검수에서 "사람 확인 필요"가 붙은 날은 `optional` 이어도 `/ok` 가 있어야 게시합니다.
  미리보기 메시지에 답장하거나 `/ok` 만 보내도 되고, 계정이 여러 개면 버튼처럼 계정·날짜를 붙입니다.
  미리보기를 다시 보내면(같은 날짜로 다시 만들면) 그 뒤에 온 답장만 봅니다.
- **게시 전 검사**: 사진 1~10장·JPEG·8MB·비율, 캡션 2,200자, 해시태그 30개, AI 합성 고지,
  `manual_link` 일 때 캡션 첫 줄 광고 고지와 첫 해시태그 `#광고`. 하나라도 어긋나면 게시하지 않고 알립니다.
- **샌드박스 결과는 게시하지 않습니다** (코디컷에 워터마크). 미리보기는 보내되 버튼이 없습니다.
- **두 번 게시하지 않음**: 같은 계정·날짜가 `posts.json` 에 있으면 건너뜁니다. 이 기록은 6단계 상품 고르기가
  최근에 쓴 상품을 피할 때도 씁니다 (`reuse_cooldown_days`).
- 봇 서버 없이 동작합니다. 답장은 publish 가 돌 때 읽으며, 텔레그램은 답장을 24시간 보관합니다.

실행 방법
- 미리보기: Actions → `daily` → Run workflow (`preview` 체크, 기본 켜짐)
- 게시: Actions → `publish` → Run workflow. **`dry_run` 이 기본으로 켜져 있어** 처음에는 검사와 승인 확인만 합니다.
  끄고 돌리면 실제로 게시합니다. `force` 는 텔레그램 승인 없이 게시합니다. 매일 자동 실행은 아래 9단계.
- 로컬 시험: `uv run python -m tools.publish --account women --date 2026-09-27 --results <daily-results 폴더> --dry-run`

Claude 는 게시·알림 도구(`tools.publish`, `tools.ig_publish`, `tools.notify`, `tools.media_host`)를 부를 수 없게
`.claude/settings.json` 에서 막아 두었습니다.

## 매일 자동 실행 (9단계)

`main` 에 합치면 사람 손 없이 아래처럼 돕니다 (시간은 `profile.yaml` 의 `schedule`, 예시는 women 계정).
게시가 후보 시각보다 이르면 다음 날 게시입니다 (D 에 후보 → D+1 아침 게시).

```
D 09:00  schedule → daily(mode=candidates): Claude 가 코디 후보 5개 (outfit-candidates 스킬, 이미지 AI 없음)
         → 텔레그램: 후보마다 상품 원본 사진 한 장 모음 + 목록
           버튼 [/pick 1 women 10-02] … [/pick 5 …] [/again …] [/skip …]
10:00~18:00 (1시간마다) schedule: 고른 번호 확인
         번호 → daily(mode=render): 그 후보로 사진 확인 → 문구 → 코디컷(생성형 AI) → 검수 → 미리보기
         /again → 겹치지 않는 새 후보 5개 (max_again 번까지, 마감 1시간 전까지)
         /skip → 그날 건너뜀
         18:00 까지 안 고르면 → on_timeout (1번으로 만들기 또는 건너뛰기)
미리보기 뒤 매시 schedule: /ok·/skip 을 결과 폴더에 기록(approval.json), /ok 면 상품 링크 목록, /fix 면 고치기
22:00    아직 /ok 가 없으면 "승인 마감" 알림 한 번
D+1 05:00  승인 마감. /ok 가 없으면 "게시하지 않아요" 알림 (/fix 는 04:20 까지)
D+1 07:00  schedule → publish: 마감 전 /ok 가 있으면 게시. 실패하면 08:00 에 한 번 더
토 18:00  다음 주(월~일 게시) 주제 7개 (themes 워크플로) → 텔레그램. /themes again 이면 그 주를 다시 정함
월·목 04:00  지그재그 상품 수집 (collect, 창고 커밋)
일 19:00  인스타 토큰 연장 (token)
```

- **밤 알림**: `config/publish.yaml` 의 `telegram.quiet` 시간대(기본 23:00~07:00)에는 텔레그램을 소리 없이 보냅니다.
- **승인 기록**: 텔레그램은 답장을 24시간만 보관해서, 매시 확인할 때 본 `/ok`·`/skip` 을 daily-results 의
  `<날짜>-run<번호>/approval.json` 에 남기고 게시 때 텔레그램에 없으면 이 기록을 씁니다.

- **워크플로**: `schedule.yml` 이 깨어나 `tools.schedule` 로 할 일을 찾고,
  무거운 작업은 `daily`, `publish`, `themes`, `collect`, `token` 워크플로에 실행을 요청합니다.
  깨어나면 "지금까지 해야 했는데 아직 안 한 칸"을 찾아서 하므로(auto), 늦게 깨어나도 밀린 일을 따라잡고
  같은 칸을 두 번 하지 않습니다. GitHub 예약은 자주 빠지거나 늦어서 아래 **외부 스케줄러**를 권장합니다.
- **고른 번호 확인**: 봇 서버 없이 schedule 이 돌 때 텔레그램 답장을 읽습니다. 그래서 번호를 누른 뒤
  다음 확인(최대 `pick_check_every` 분)에 이미지 만들기가 시작되고, 30분쯤 뒤 미리보기가 옵니다.
  결정은 daily-results 브랜치의 `<날짜>-run<번호>/picks/picked.json` 에 남아 두 번 만들지 않습니다.
- **이미지 AI 사용량**: 후보 단계는 0회, 고른 한 벌만 코디컷 후보 `review.candidates` 장을 만듭니다.
  모두 기준 미달이면 한 묶음 더 만듭니다 (`max_rounds`).
- **실패 알림**: daily·themes·collect·schedule 이 실패하면 텔레그램으로 실행 주소를 보냅니다. 게시 실패와
  토큰 문제는 publish·token 이 직접 알립니다. 검수를 통과하지 못한 날은 미리보기에 "사람 확인 필요"로 옵니다.
- **인스타 토큰 연장**: 토큰은 발급 24시간 뒤부터 연장할 수 있고, 연장하면 다시 60일이 됩니다.
  연장 결과로 토큰 값이 바뀌면 Secret 을 바꿔야 하는데, `SECRETS_TOKEN` 이 있으면 자동으로 바꿉니다.
  만드는 법: GitHub → Settings → Developer settings → Fine-grained tokens → 이 저장소만 선택 →
  Repository permissions 의 **Secrets: Read and write** → 만든 값을 Secret `SECRETS_TOKEN` 으로 등록.
  없으면 값이 바뀌었을 때 텔레그램으로 알려 드립니다 (토큰 값은 보내지 않음).
- **Actions 사용 시간**: 비공개 저장소는 GitHub 무료 요금제에서 한 달 2,000분까지입니다. 확인 칸 하나가
  1분씩(하루 약 16번), Claude 가 도는 후보·만들기가 각각 20~40분이라 한 달 2,000분 안팎이 됩니다.
  넘으면 `pick_check_every` 를 60으로 늘리거나 `publish_retries` 를 줄이세요.
- **시험**: Actions → `schedule` → Run workflow 에 `auto`(지금 할 일) 또는 cron 줄(`tools.schedule list` 참고,
  기록과 상관없이 그 칸을 다시 함)을 넣습니다.
  `dry_run` 이면 답장만 읽고 무엇을 할지 보여 줍니다 (실행 요청·텔레그램 알림·결정 기록 없음).
  후보만 보고 싶으면 Actions → `daily` → Run workflow 에서 `mode` 를 `candidates` 로.
- 도구만:
  - `uv run python -m tools.schedule list` 시간표, `sync` cron 줄 맞추기, `due --cron "17 1 * * *"` 그 칸의 할 일
  - `uv run python -m tools.pick validate out/<날짜>` 후보 검사, `send` 텔레그램으로 보내기
  - `uv run python -m tools.ig_token refresh --account women` 토큰 연장

Claude 는 `tools.pick`, `tools.ig_token`, `tools.schedule run` 도 부를 수 없게 막아 두었습니다.

### 외부 스케줄러 (12단계, 권장)

GitHub 예약(cron)은 부하가 많으면 몇 시간 늦거나 통째로 빠집니다 (2026-09-29 최대 6시간 지연,
09-30 오전 칸 전부 미실행). 그래서 무료 서비스 [cron-job.org](https://cron-job.org) 가 정해진 시각에
`schedule` 워크플로를 `cron=auto` 로 실행하게 하고, GitHub 예약은 예비로 둡니다.

**어떻게 도나**
- 어느 쪽으로 깨어나든 `auto` 가 아직 안 한 칸을 찾습니다. 한 칸은 daily-results 브랜치 `ticks.json` 에
  기록해서, 외부 스케줄러와 GitHub 예약이 둘 다 깨워도 후보·게시가 두 번 나가지 않습니다.
- 밀린 칸이 여럿이면 같은 작업은 가장 최근 칸으로 한 번만 합니다 (예: 10:47~12:17 고르기 확인이 밀리면 한 번).
- 늦게 해도 소용없는 칸은 건너뛰고 텔레그램으로 알립니다: 후보 보내기는 고르기 마감 30분 전까지,
  게시는 마지막 재시도 칸 + 간격까지. 주요 작업(후보, 게시, 주간 주제)이 30분 넘게 늦게 돌면 "N분 늦게 돌았어요" 알림.

**1) GitHub 토큰 만들기** (실행 요청 전용)
GitHub → 오른쪽 위 사진 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens**
→ Generate new token
- Token name: `cron-job` / Expiration: 1년 (만료 전에 GitHub 이 메일로 알려 줌)
- Repository access: **Only select repositories** → 이 저장소 하나
- Permissions → Repository permissions → **Actions: Read and write** (Metadata: Read 는 자동). 다른 권한은 주지 않습니다.
- 만든 토큰(`github_pat_...`)을 복사해 둡니다. 이 토큰은 cron-job.org 에만 넣고 저장소나 Secrets 에는 넣지 않습니다.

**2) cron-job.org 작업 만들기**
가입(무료) → Settings 에서 Timezone 을 계정 시간대(예: Asia/Seoul)로 → Cronjobs → **Create cronjob**
- COMMON 탭
  - Title: 예) `fashionstudio 10~14시`
  - URL: `https://api.github.com/repos/<owner>/<repo>/actions/workflows/schedule.yml/dispatches`
  - Execution schedule: **Every 1 hour** (기본 시간표일 때, 아래 참고)
- ADVANCED 탭
  - Request method: **POST**
  - Headers (4줄):
    - `Authorization`: `Bearer github_pat_...` (1에서 만든 토큰)
    - `Accept`: `application/vnd.github+json`
    - `X-GitHub-Api-Version`: `2022-11-28`
    - `Content-Type`: `application/json`
  - Request body: `{"ref":"main","inputs":{"cron":"auto","dry_run":"false"}}`
- 저장 후 **TEST RUN** → 응답이 `204` 면 성공 (GitHub Actions 탭에 `schedule` 실행이 생김).
  `401`/`403` 은 토큰·권한, `404` 는 주소(owner/repo) 확인. 알림 탭에서 실패 시 메일 받기를 켜 두면 좋습니다.

작업 시각은 `uv run python -m tools.schedule external` 이 계정 시간표에서 만들어 줍니다.
기본값(women)은 모든 칸이 정각이라 **Execution schedule → Every 1 hour** 작업 하나면 됩니다
(밤에도 깨우지만 할 일이 없으면 1분 안에 끝남). 칸 시각을 정각이 아닌 값으로 바꾸면 **Custom** 에서
`external` 이 알려 주는 요일·시·분을 골라 작업을 나눠 만드세요.

**3) GitHub 예약 줄이기** (선택): 외부 스케줄러가 잘 도는 것을 확인했으면 profile `schedule.github_cron: backup`
으로 바꾸고 `uv run python -m tools.schedule sync` → 커밋. GitHub 예약이 주요 칸 몇 개만 남아 Actions 시간이
두 배로 들지 않습니다 (빠진 칸은 auto 가 따라잡음).

---

## 미리보기 고치기 (15단계)

미리보기가 마음에 들지 않으면 텔레그램에 고칠 점을 보냅니다. 봇 서버 없이 자동 실행(schedule)이 깨어날 때
읽어서(매시 정각) 같은 코디로 고쳐 다시 만들고, 20~40분 뒤 "고친 미리보기"가 옵니다. 그 미리보기에 `/ok` 하면 게시돼요.

| 보내는 글 | 뜻 |
|---|---|
| `/fix 1 신발 더 크게, 가방 오른쪽 위로` | 1장(표지) 코디컷을 고침. 숫자는 미리보기 사진 순서 |
| `/fix 3 사진이 너무 어두워요` | 3장(상품 장) 사진이나 이름·코멘트를 고침 |
| `/fix 캡션 도입을 더 짧게` | 캡션 |
| `/fix 전체 더 가을 느낌으로` (또는 번호 없이) | Claude 가 어느 장인지 판단 |
| 미리보기 사진 한 장에 답장으로 `/fix 색이 달라요` | 그 장 |

- 여러 개를 연달아 보내거나 한 메시지에 줄을 바꿔 `/fix` 를 여러 줄 적으면 한 번에 모아 고칩니다 (횟수 1번).
- **표지 코디컷**을 고칠 때만 이미지 AI 를 다시 부릅니다. 상품 장·캡션·제목만 고치면 이미 검수한 코디컷을
  그대로 써서 비용이 들지 않습니다.
- 다시 만드는 동안에는 게시하지 않습니다 (`approval: optional` 이어도). 고치지 않고 지금 것으로 게시하려면
  `/fix` 뒤에 `/ok` 를 보내세요.
- 하루 `max_fixes` 번까지, 게시 마감 `render_minutes` 분 전까지만 다시 만듭니다. 그 밖(이미 게시함, 횟수 초과,
  마감 임박)에는 피드백만 기록하고 텔레그램으로 알려 드려요.
- 받은 피드백은 모두 `data/<계정>/feedback.jsonl` 에 날짜·장·내용·처리 결과로 쌓입니다. 최근
  `recent_days` 일 피드백은 다음 날 기획(`tools.plan context`)과 이미지 검수(`tools.review prep`)에 보여 줘서
  같은 문제가 반복되지 않게 합니다. 모아 보기: `uv run python -m tools.feedback summary --account women`
  (자주 나오는 것은 검수 기준·지시문·배치 규칙에 반영할 후보).
- 흐름: schedule `fix` (tools.feedback check) → daily `mode=fix` (`tools.feedback prepare` → Claude `fix-feed`
  스킬 → `tools.plan build` → 검수) → 새 미리보기.

### 피드백을 규칙에 반영하는 PR (하루 한 번)

`feedback.rules_at`(women 은 게시 다음 08:00)에 `feedback-rules` 워크플로가 돕니다.

1. 지난번 PR 뒤로 새 피드백이 없으면 아무것도 하지 않습니다.
2. 있으면 Claude(`feedback-rules` 스킬)가 되풀이될 만한 지적만 골라 코디컷 지시문(`prompts/`), 검수 기준
   (`.claude/agents/`), 스킬, 계정 설정(`voice.md`, `profile.yaml`)을 고칩니다. 그날 상품 하나에만 해당하는
   지적이나 코드로 고칠 문제는 고치지 않고 PR 설명에 적습니다.
3. 고친 게 있으면 `feedback-rules/<계정>-<날짜>` 브랜치로 PR 을 만들고 검사(ci)를 돌린 뒤, 텔레그램으로
   "확인하고 합쳐 주세요" 알림과 PR 주소를 보냅니다. **자동으로 합치지는 않습니다.** 합쳐야 다음 게시물부터 적용돼요.
4. 다룬 피드백은 `data/<계정>/feedback_rules.json` 에 적어 다음 날 다시 다루지 않습니다 (PR 을 닫아도 마찬가지).

처음 한 번: 저장소 **Settings → Actions → General → Workflow permissions** 에서
**Allow GitHub Actions to create and approve pull requests** 를 켜야 워크플로가 PR 을 만들 수 있습니다.
직접 돌리려면 Actions 탭 → feedback-rules → Run workflow.

## 모델컷 게시물 (16단계)

요일마다 게시물 종류를 정합니다 (`schedule.post_days`, 게시 날짜 기준). women 은 월·수·금 펼친 코디컷,
화·목·토 **전날 올린 벌들을 옷을 입은 얼굴 없는 AI 모델컷으로 다시 그린 게시물**, 일요일은 쉽니다.
`post_days` 를 지우면 예전처럼 매일 펼친 코디컷입니다. `post_days_from` (날짜)을 두면 그 게시 날짜 전까지는 요일과 상관없이 펼친 코디컷입니다.

- 모델컷 날은 후보·고르기가 없습니다. 전날 게시물이 올라간 뒤 후보 시각(09:00)에 schedule 이 daily 를
  `mode=model` 로 부르고, 미리보기가 오면 평소처럼 `/ok`·`/fix` 하면 됩니다. 상품 링크·댓글 DM 도 같은 상품입니다.
- 전날 게시물이 올라가지 않았으면 그날 모델컷은 쉬고 텔레그램으로 알려 드려요.
- 흐름: `tools.model_post prepare` (전날 기획을 `shot: model` 로, 벌마다 포즈) → Claude `model-feed` 스킬
  (제목·캡션 새로 쓰기, 장소 분위기) → `tools.plan build` (지시문 `prompts/outfit_model.md`) → 검수 → 미리보기.
- 인물·포즈·장소는 `model_shot` (`poses` 를 벌마다 돌려 씀, 인물 묘사를 같게 써서 한 사람처럼). 장면은 사용자가 고른
  핀터레스트 사진처럼 실제 거리·실내에서 친구가 찍어 준 필름 사진 느낌. 얼굴은 고개 숙임·돌림·머리카락·컵·폰으로
  일부만 보이고 정면 얼굴은 그리지 않습니다. 얼굴 검출 도구가 얼굴로 본 자리는 잘라 두고, 검수가 열어 정면 얼굴이
  또렷하면 떨어뜨립니다.
- 비용은 펼친 코디컷과 같습니다 (벌마다 한 장, 떨어진 벌만 한 장 더).
- 상품 창고가 수집 때 정리되어 전날 상품이 빠져 있어도, 전날 결과의 manifest 에 남은 상품 정보로 다시 넣어 씁니다.
- 시험: `experiments/model_look*.yaml` 을 `gen-experiment` 워크플로로 (`[gen-exp] [spec:…] [shots:model]`).

---

## 댓글 → DM 코디 링크 (10단계)

댓글 달자마자 DM 이 가려면 메타가 댓글을 즉시 알려 주는 **웹훅**을 받는 서버가 있어야 합니다. 서버를 직접 두지
않고 메타 공식 파트너인 **ManyChat** 이 댓글을 받아 DM 을 보내고, 이 저장소는 캡션 안내 문구와 DM 에 넣을
**상품 페이지**를 맡습니다 (`comments.mode: external`).

```
게시 (캡션: "코디 정보가 궁금하면 댓글을 남겨 주세요…")
  → publish 가 상품 페이지(최근 게시물 상품 링크)를 새로 올림 (tools.link_page)
댓글 → ManyChat 이 바로 DM: "코디 상품 링크는 여기에 모아 뒀어요 [상품 보기]" → 상품 페이지
```

- **상품 페이지**: 공개 사진 저장소의 GitHub Pages 에 `<계정>/index.html` 로 올립니다
  (예: `https://yoonji-jang.github.io/FashionStudio-media/women/`). 최근 `link_page.keep` 개 게시물의 제목·날짜·
  상품 링크와 상품별 썸네일(쇼핑몰 대표 사진)만 들어가고, 최신 게시물이 맨 위입니다. DM 주소는 한 번만 넣어 두면
  됩니다.
- **주소를 내 이름으로**: 도메인(예: `janggu.zip`)을 사서 사진 저장소 Settings → Pages → **Custom domain** 에
  적고, 도메인 판매처 DNS 에 GitHub Pages 주소를 연결한 뒤 `config/publish.yaml` 의 `image_host.pages_base` 에
  `https://janggu.zip` 을 적습니다. 그러면 주소가 `https://janggu.zip/women/` 이 됩니다.
- **ManyChat 없이** (`comments.mode: builtin`): `tools.comments` 가 자동 실행 때 댓글을 확인해 인스타 비공개
  답장으로 게시물별 링크를 보냅니다. 서버가 없어 30분~몇 시간 늦고, 댓글·메시지 권한 토큰과 (일반 사용자에게
  보내려면) 메타 앱 검수가 필요합니다. 아래 [직접 보내기](#직접-보내기-mode-builtin) 참고.

### ManyChat 연결 (처음 한 번)

1. **상품 페이지 켜기**: GitHub 에서 사진 저장소(`FashionStudio-media`) → Settings → **Pages** → Source
   **Deploy from a branch**, Branch **main** / **(root)** → Save. 그다음 이 저장소 Actions → `link-page` →
   Run workflow → 실행 기록의 주소를 폰에서 열어 보기
2. **인스타 설정**: 인스타 앱 → 설정 → 메시지 및 스토리 답장 → 메시지 제어 → 연결된 도구 →
   **메시지 액세스 허용** 켜기 (꺼져 있으면 ManyChat 도 DM 을 못 보냄)
3. **ManyChat 가입**: [manychat.com](https://manychat.com) → Instagram 으로 시작 → 게시할 인스타 계정 연결
4. **자동화 만들기**: Automation → New Automation → 트리거 **사용자가 게시물·릴스에 댓글을 남김**
   - 게시물: **모든 게시물(Any post)** (무료 요금제에서 막혀 있으면 Pro 가 필요할 수 있음)
   - 키워드: **모든 댓글(Any comment / 키워드 없음)** (`profile.yaml` 의 `comments.keywords: []` 와 같게.
     특정 말만 받으려면 둘 다 같은 말로)
   - (선택) 댓글 공개 답글: "DM 보내 드렸어요"
   - 액션 **메시지 보내기**: "댓글 남겨 주셔서 고마워요. 코디 상품 링크는 여기에 모아 뒀어요" + 버튼
     "상품 보기" → 1번의 상품 페이지 주소
5. 자동화를 **켜고(Set Live)**, 다른 인스타 계정으로 게시물에 아무 댓글 → DM 이 바로 오는지 확인
6. 잘 되면 `profile.yaml` 의 `comments.enabled: true` → main 에 합치기. 다음 게시물부터 캡션에 안내 문구가 들어감

#### `link_page` 설정
| 항목 | 예시 | 의미 |
|---|---|---|
| `enabled` | true | 게시할 때마다 상품 페이지를 새로 올림. 끈 뒤 이미 올린 페이지는 Actions → **link-page** 를 `action: remove` 로 실행해 내림 |
| `title`, `intro` | janggu.zip 코디 상품 | 페이지 맨 위 제목과 한 줄 설명 |
| `keep` | 14 | 보여 줄 최근 게시물 수 |
| `item_line` | `{category} · {shop}` | 상품 이름 위 작은 글자 |
| `share_days` | 7 | `monetize: manual_link` 일 때 게시 뒤 이 기간 동안 공유리워드 링크 답장을 반영 |
| `send_links` | false | `/ok` 뒤 상품별 지그재그 링크 목록을 텔레그램으로 보냄 (`monetize: none` 이어도, 링크를 직접 만들 때 씀). 답장은 반영하지 않음 |

#### 공유리워드 링크로 수익화 (`monetize: manual_link`)

지그재그 공유리워드 링크는 로그인한 앱에서만 만들 수 있어 자동으로 만들지 않고, 보내 주신 링크를 씁니다.

```
미리보기에 /ok → schedule 이 확인하는 대로 (후보를 보낸 뒤~승인 마감 사이 ok_check_every 분마다, 기본 1시간)
  상품 번호와 지그재그 상품 링크 목록을 보냄 (게시 전에 답장해 두면 게시 때 바로 반영)
게시 → 텔레그램 "게시했어요 ✓" 알림에도 같은 목록이 붙어 옴 (받은 상품은 ✓ 받음. 자동 승인이면 이때 처음)
사용자: 상품 링크를 지그재그 앱에서 열어 공유리워드 링크를 만들고, 목록 메시지에 답장으로
  "1 https://…" 처럼 한 줄에 하나씩 붙이기 (나눠 보내도 됨)
schedule 이 다음에 깨어날 때 (tools.share): 답장을 읽어 게시 기록(posts.json)에 넣고 상품 페이지 다시 올림
  → 텔레그램으로 "공유리워드 링크 반영" 알림
```

- /ok 확인 칸(`schedule.ok_check_every`)은 외부 스케줄러가 매시 깨우는 시각과 같아서 Actions 시간이 거의 늘지 않습니다 (`github_cron: backup`). `0` 이면 끕니다.
- 링크를 안 보낸 상품은 상품 링크 그대로입니다. 같은 번호를 다시 보내면 새 링크로 바뀝니다.
- 텔레그램은 답장을 24시간만 보관합니다. schedule 은 하루에도 여러 번 깨어나므로 보통 몇십 분~반나절 안에
  반영됩니다 (22:03 이후 답장은 다음 날 10:17).
- 켜면 캡션 맨 앞 광고 고지와 첫 해시태그 `#광고` 가 자동으로 붙고, 상품 페이지 위에도 광고 고지가 나옵니다.
- **켜는 때**: 게시 job 은 결과물의 수익 방식이 프로필과 다르면 게시하지 않습니다. 그날 게시물을 이미 만든
  뒤(코디 후보 시각 이후)에 바꾸면 그날 게시가 막히므로, 그날 게시가 끝난 밤에 바꿉니다.

### 직접 보내기 (mode: builtin)

```
schedule 이 깨어날 때마다 (다른 작업 때 + check_at) tools.comments:
  최근 7일 게시물의 댓글 읽기 → 키워드 댓글 → 비공개 답장 DM (게시물별 상품 링크)
  → data/<계정>/dm.json 에 댓글 id 해시만 기록 (커밋)
```

- **인스타 규칙**: 댓글 하나에 DM 한 번, 댓글을 단 뒤 7일 안에만 보낼 수 있습니다. 같은 사람이 여러 번
  댓글을 달아도 한 번만 보냅니다. 내 계정 댓글에는 보내지 않습니다.
- **개인정보**: 댓글 작성자 이름·id·댓글 내용은 저장하지 않습니다. 두 번 보내지 않으려고 댓글 id 의 해시만
  남기고, 기간이 지나면 지웁니다.
- **DM 이 가는 때**: 10:17~15:17·20:03~22:03 은 30분마다, 그 밖에는 `check_at` (기본 08:17·17:17·23:17).
- **Actions 사용 시간**: `check_at` 한 칸이 하루 1분이라 기본 3칸이면 한 달 약 90분이 늘어납니다.
- **공유리워드**: 지그재그 공유리워드 링크는 로그인한 앱에서만 만들 수 있어 자동으로 만들지 않습니다.
  `link_mode: share_reward` 면 게시 뒤 첫 확인 때 텔레그램으로 상품 목록을 보내고, 그 메시지에 답장으로
  링크를 붙이면(한 줄에 하나, `1 https://…` 처럼 번호를 붙여도 됨) 그 링크로 DM 을 보냅니다.
  `share_wait_hours` 안에 안 오면 상품 링크로 보냅니다. 공유 링크를 보낼 때는 DM 맨 앞에 광고 고지가 붙습니다.
- **오류**: 토큰 만료·권한 부족이면 텔레그램으로 하루 한 번 알리고, 다음 확인 때 다시 시도합니다.

준비:
1. 위 ManyChat 연결 2번처럼 **메시지 액세스 허용** 켜기
2. [developers.facebook.com](https://developers.facebook.com) → 내 앱 → 사용 사례 **Instagram에서 메시지 및
   콘텐츠 관리** → 권한에 `instagram_business_manage_comments`, `instagram_business_manage_messages` 추가
3. **Instagram 로그인을 통한 API 설정** → **토큰 생성** 을 다시 눌러 새 토큰을 받고 Secret `IG_WOMEN_TOKEN` 교체
4. 시험: 다른 계정으로 아무 댓글 → Actions → `comments` → Run workflow (`dry_run` 끄면 실제 DM)
5. 잘 되면 `comments.enabled: true`, `mode: builtin` → `uv run python -m tools.schedule sync` → main 에 합치기

개발 모드 앱은 앱 역할(관리자·테스터)이 있는 계정끼리만 메시지 기능이 확실히 됩니다. 일반 계정에 권한 오류가
나면 앱을 라이브 모드로 바꾸고, 그래도 안 되면 두 권한의 **고급 액세스(앱 검수)** 가 필요합니다.

- 도구만: `uv run python -m tools.link_page build --account women` 상품 페이지 파일로 만들기,
  `uv run python -m tools.comments text --account women` DM 글 미리 보기

Claude 는 `tools.comments`, `tools.link_page push`/`remove` 를 부를 수 없게 막아 두었습니다.

---

## 릴스 (19단계)

캐러셀과 같은 코디로 짧은 세로 영상을 만들어 따로 올립니다. 영상 AI 없이 코드(`tools.reels`, ffmpeg)로 만듭니다.

1. **만들기**: daily 워크플로가 캐러셀을 다 만들면 `python -m tools.reels build out/<날짜>` 로
   `reels/reel.mp4` 를 만듭니다. 그날 캐러셀 장을 순서대로 보여 주고, 장은 효과 없이 바로 바뀝니다 (확대·밀기 없음, 8장이면 약 14초).
   `/fix` 로 고친 미리보기도 릴스를 다시 만듭니다. 릴스 만들기가 실패해도 캐러셀은 그대로 갑니다.
2. **미리보기**: 텔레그램 미리보기에 영상이 같이 옵니다. **`/ok` 하나로 캐러셀과 릴스를 같이 승인**합니다.
3. **보내기·게시**: schedule 이 `schedule.reels_at` 에 publish 워크플로(`reels: true`)를 부릅니다.
   그날 캐러셀을 게시했을 때만 합니다. `reels.delivery: telegram`(기본)이면 영상과 캡션(댓글 해시태그 포함)을
   텔레그램으로 보내고, 인스타 앱에서 음악을 붙여 직접 올립니다 (앱 음악은 게시 API 로 넣을 수 없음).
   `instagram` 이면 같은 캡션으로 자동 게시하고 댓글 해시태그(`comment_tags`)도 답니다.
   영상은 사진 저장소에 잠시 올려 공개 주소로 넘기고, 게시가 끝나면 지웁니다
   (`config/publish.yaml` `instagram.reels_upload: url`). 인스타 로그인 방식 API 는 파일을 바로 받는
   `file` 방식을 받지 않습니다.
4. **음악**: 인스타 앱의 음악은 게시 API 로 넣을 수 없습니다. `reels.music_dir` 폴더에 상업 이용이 허락된
   음원을 넣으면 영상에 직접 넣습니다 (`accounts/women/music/README.md`). 비어 있으면 소리 없는 릴스입니다.
5. **ManyChat**: 마지막 장이 "댓글을 남기면 DM" 안내라서, ManyChat 댓글 규칙이 릴스에도 걸리게(모든 게시물·릴스) 해 두세요.

손으로 만들어 보기: `uv run python -m tools.reels build out/2026-10-01 --force` (ffmpeg 필요).
릴스만 손으로 올리기: Actions → publish → `reels` 체크 (`dry_run` 을 끄면 실제 게시).

---

## 새 계정 추가

1. `accounts/_template/` 을 `accounts/<id>/` 로 복사
2. `profile.yaml` 의 `id` 를 폴더 이름과 같게 바꾸고 나머지 값 채우기
3. `voice.md`, `styling.md` 채우기
4. `accounts/index.yaml` 에 `<id>` 추가
5. GitHub Secrets에 `IG_<ID>_TOKEN`, `IG_<ID>_USER_ID` 등록 (텔레그램 봇과 사진 저장소는 계정끼리 함께 씀)
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
| 7 | 이미지 검수 | 완료 |
| 8 | 인스타 게시 (텔레그램 승인) | 완료 |
| 9 | 매일 자동 실행 + 텔레그램 코디 고르기 | 완료 |
| 10 | 댓글 → DM 코디 링크 | 진행 중 |
| 11 | 표지 컨셉 변경 (사람이 입은 핀터레스트 감성) | |
| 12 | 상품 창고를 Actions 캐시로 이전 | |
| 13 | 릴스 | 진행 중 (19단계) |
