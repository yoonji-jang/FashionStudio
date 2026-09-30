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
- `visual.title_max_lines` 줄 (보통 2줄). 1줄은 상황·고민, 2줄은 해결 아이템이나 방법.
- voice.md 의 좋은 제목 예시와 같은 호흡으로. 예시 문장을 그대로 베끼지 않는다.
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

## 마지막 확인
`python -m tools.plan caption out/$DATE/plan.yaml` 로 완성된 캡션을 읽어 보고,
voice.md 의 금지 표현(체형 비하, 과장, 가격 단정)이 없는지 한 번 더 본다.
