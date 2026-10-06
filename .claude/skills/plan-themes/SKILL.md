---
name: plan-themes
description: 게시물 주제를 정한다. 다음 주 주제를 한꺼번에 정해 data/<계정>/themes.json 에 저장하거나(주간), 정해 둔 주제가 없는 날 하나를 정한다(하루). 주제별 지그재그 검색어도 만든다.
---

# plan-themes: 주제 정하기

기준: `accounts/$ACCOUNT/profile.yaml` 의 `themes` (per_week, type_mix, consider, events,
no_repeat_days, avoid), `audience`, `style`, `accounts/$ACCOUNT/styling.md` (코디 기준), `voice.md` 의 좋은 제목 예시.

## 재료
- `python -m tools.plan context --account $ACCOUNT --date <날짜>`: 요일, 계절, 날씨 예보, 최근 게시, 재고.
  주간이면 7일치를 각각 보거나 첫날 기준으로 본다.
- 상품 창고 재고: 주제에 필요한 카테고리의 "고를 수 있음"이 5개 미만이면 그 주제는 search_terms 로
  새로 모아야 한다 (daily-feed 의 COLLECT).

## 주제 한 개의 모양 (core.models.Theme)
```yaml
- date: 2026-09-28
  type: 상황            # profile themes.type_mix 의 유형 중 하나
  title_hint: 출근룩, 네이비 셔츠에 그레이 블레이저로 톤 맞추기
  mood: 네이비·그레이 차분한 오피스룩
  why: 월요일, 최고 21도라 셔츠 한 장에 가벼운 아우터
  slots:               # 코디에 들어갈 자리와 대략의 상품
    - {category: 아우터, desc: 그레이 싱글 블레이저}
    - {category: 상의, desc: 네이비 셔츠}
    - {category: 하의, desc: 블랙 와이드 슬랙스}
    - {category: 가방, desc: 블랙 숄더백}
    - {category: 신발, desc: 블랙 로퍼}
  search_terms:        # 카테고리별 지그재그 검색어 1~3개. 창고에 이미 충분하면 비워도 됨
    아우터: [싱글 블레이저]
    상의: [네이비 셔츠]
```

## 정하는 방법
1. **계절과 날씨가 먼저**: 기온에 맞는 겹 수(반팔, 셔츠 한 장, 가벼운 아우터, 코트)를 먼저 정한다.
   비 예보면 우산·레인부츠·어두운 하의 같은 실용 주제를 고려한다.
2. **요일**: 월~금 앞쪽은 출근·오피스, 금요일은 퇴근 후 약속, 주말은 나들이·데이트·브런치.
3. **이벤트**: `themes.events` 중 날짜가 가까운 것만 (2주 안). 억지로 넣지 않는다.
4. **유형 비율**: 주간이면 `type_mix` 개수를 맞춘다. 같은 유형이 이틀 연속되지 않게 섞는다.
5. **반복 피하기**: 최근 `no_repeat_days` 일 게시와 주요 아이템·색이 겹치지 않게 한다.
   `themes.avoid` 에 해당하는 주제는 쓰지 않는다.
6. **센스 있는 데일리룩**: `audience.description` 의 독자가 저장해 두고 따라 하고 싶은 꾸민 듯한 조합.
   "출근룩"처럼 상황만 있는 주제보다 styling.md 의 포인트(주인공 아이템, 소재, 톤온톤 색)가 보이는 주제
   (예: "스웨이드 자켓 하나로 끝내는 가을 코디", "올블랙에 소재만 바꾸기", "그레이 톤온톤 출근룩").
   하루 게시물은 여러 벌이라 주제가 한 색을 정하면 여섯 벌이 모두 그 색을 억지로 넣게 된다 (2026-10-06).
   색은 `mood` 에 "깊은 가을 톤"처럼 넓게 쓰고, "브라운에 하늘색"처럼 먼 두 색을 정하지 않는다.
   `style.keywords`, `style.palette` 안에서 고르고 `style.avoid` 는 피한다.
7. **검색어**: 지그재그에서 실제로 쓰는 말로 짧게, 포인트가 되는 아이템 위주 (예: "스웨이드 자켓", "체크 미디스커트",
   "니트 뷔스티에", "쁘띠 스카프"). 색 + 종류, 소재 + 종류, 핏 + 종류. 브랜드명, 광고 문구는 넣지 않는다.
   slots 의 desc 도 기본템 이름만 쓰지 말고 포인트를 적는다 (예: "브라운 스웨이드 자켓, 화이트 셔츠 깃만 살짝").

## 저장
- 주간: 7일치를 `out/themes_<첫날>.yaml` 로 쓰고
  `python -m tools.plan themes --account $ACCOUNT --file out/themes_<첫날>.yaml` 로 검사·저장한다.
  보고(`out/themes_<첫날>.md`)에 7개를 한 줄씩 적는다. 텔레그램으로 보내는 것은 themes 워크플로가 한다
  (`themes.review_before_use: true` 면 "/themes again" 버튼이 붙고, 누르면 이 주 주제를 다시 정한다).
  `out/themes_previous.yaml` 이 있으면 사람이 다시 정해 달라고 한 주제다. 겹치지 않게 새로 정한다.
- 하루: plan.yaml 의 `theme` 에 바로 쓴다. 저장은 하지 않아도 된다.
