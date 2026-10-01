---
name: write-copy
description: 기획된 코디의 표지 제목 두 줄, 상품 장 짧은 이름과 코멘트, 캡션 도입 한 줄과 코디 포인트, 해시태그를 voice.md 말투로 쓴다. 고지 문구와 제품 목록은 도구가 붙인다.
---

# write-copy: 문구 쓰기

기준: `accounts/$ACCOUNT/voice.md` (말투, 좋은 제목 예시, 캡션 구조, 금지 표현)와
profile `copy` (글자 수, 개수, 기본 해시태그, 금지 표현, 고지 문구), `monetize`.
숫자 규칙은 `python -m tools.plan check` 가 검사하므로, 여기서는 말투와 내용에 집중한다.

## 쓰는 곳 (plan.yaml)
```yaml
items:
  - name: 래글런 하프 트렌치코트          # 상품 장 짧은 이름
    comment: 어깨선이 없는 래글런이라 …    # 상품 장 코멘트
text:
  title_lines: [아침저녁 쌀쌀할 땐, 하프 트렌치코트 하나로]
  intro: 아침저녁으로 쌀쌀한 주말, 하프 트렌치코트 하나 걸쳐 보세요 🍂   # copy.intro 가 켜져 있을 때
  points: [ …, …, … ]                  # 캡션 코디 포인트
  hashtags: ["#트렌치코트", …]          # 기본 해시태그는 도구가 붙이므로 주제 해시태그만
```

## 표지 제목
- `visual.title_max_lines` 줄 (보통 2줄). 두 줄을 합쳐 이게 어떤 코디인지 한 문장으로 설명하고,
  이 코디만의 포인트 하나(색 조합, 소재, 입는 방법, 디테일, 소품 한 끗 중 사진에서 바로 보이는 것)를 넣는다.
  아이템 이름만 말하는 평범한 제목은 쓰지 않는다. `copy.title_banned` 의 말(요일 등)은 제목에 넣지 않는다.
- voice.md 의 제목 스타일(A~F)을 골고루 섞는다. 최근 게시 제목과 같은 스타일이 이틀 연속되지 않게.
- voice.md 의 스타일 예시와 같은 호흡으로. 예시 문장을 그대로 베끼지 않는다.
- 이모지 없음. 글자 수는 `copy.title_chars`.

## 상품 장
- 이름: 쇼핑몰 원문에서 광고 문구·품번·검색용 단어를 빼고 색 + 특징 + 종류. `copy.name_max_chars` 이하.
- 코멘트: 한두 문장. 이 코디에서 이 상품의 역할이나 입는 팁. 상품명·사진으로 확인한 사실만
  (소재, 기장, 굽 높이 등). 모르는 기능을 지어내지 않는다.

## 캡션
- `intro`: profile `copy.intro` 가 true 면 한 줄 필수. 그날 날씨·요일·상황을 열고 오늘 아이템을 권한다.
- 이모지: voice.md 규칙대로 (개수는 `copy.max_emoji`, 한 줄에 `copy.max_emoji_per_line`). 표지 제목·상품 장에는 없음.
- `points`: `copy.points` 줄 수. 한 줄에 한 가지 (색 조합, 핏, 소재, 입는 방법).
- 광고 고지, 제품 목록, 링크 안내, AI 고지는 `tools.plan` 이 profile `copy` 문구로 붙인다.
  직접 쓰지 않는다. `monetize: manual_link` 면 `#광고` 도 도구가 맨 앞에 붙인다.
- 해시태그: 주제·아이템·상황·계절 해시태그. 기본 묶음과 합쳐 `copy.hashtags` 개수 안.

## 여러 벌 (plan.yaml 에 `looks`, 20단계)
- 표지 제목: 여러 벌 전체를 한 문장으로 (예: "출근룩 고민될 때 / 가을 코디 여섯 벌"). 한 벌만 말하지 않는다.
- 상품 `name`: 벌 장 아래쪽에 "카테고리 이름 스토어" 한 줄로 들어간다. 12자 안팎으로 짧게. `comment` 는 쓰지 않는다.
- `looks[].point`: 캡션에 "LOOK n" 뒤에 붙는 그 벌 한 줄. 그 벌의 포인트(색 조합, 입는 법, 상황) 하나.
  `copy.comment_max_chars` 이하, 이모지 규칙은 캡션과 같다(합계에 들어감).
- `text.points` 는 비워 둔다 (벌마다 point 가 대신). `intro`, `hashtags` 는 같다.

## 마지막 확인
`python -m tools.plan caption out/$DATE/plan.yaml` 로 완성된 캡션을 읽어 보고,
voice.md 의 금지 표현(체형 비하, 과장, 가격 단정)이 없는지 한 번 더 본다.
