---
name: daily-feed
description: 하루치 인스타 코디 게시물을 처음부터 끝까지 만든다 (주제 → 검색어·수집 → 상품 조합 → 사진 확인 → 문구 → 검사 → 코디컷·캐러셀·캡션·manifest → 이미지 검수). "오늘 게시물 만들어", "/daily-feed", GitHub Actions daily 워크플로에서 쓴다.
---

# daily-feed: 하루 게시물 만들기

입력: 환경변수 `ACCOUNT` (없으면 `women`), `DATE` (없으면 내일, `tools.plan` 기본값),
`OUTFIT` (`auto`·`generate`·`photoroom`·`none`, 없으면 `auto` = profile `outfit_image.engine`),
`COLLECT` (`1` 이면 주제 검색어로 새 상품 수집), `SCENE` (있으면 장면 모델컷, 아래 절).
결과: `out/<DATE>/plan.yaml` 과 `out/<DATE>/manifest.json` (core.models.Manifest, 검수 점수 포함), 슬라이드, 캡션.

모든 판단은 `accounts/$ACCOUNT/profile.yaml` 과 `accounts/$ACCOUNT/voice.md` 를 따른다.
계정마다 다른 값(스타일, 색, 문구 규칙)을 이 문서에서 가정하지 말고 매번 파일에서 읽는다.
게시·삭제·외부 전송은 하지 않는다. 판매자 이름·연락처 같은 개인정보는 읽거나 저장하지 않는다.

## 고른 후보로 시작 (9단계)
`out/$DATE/plan.yaml` 이 이미 있고 `text` 가 없으면 사람이 텔레그램에서 고른 코디 후보다
(`outfit-candidates` 스킬이 만든 것). 1~5번을 건너뛰고 6번(사진 확인)부터 한다.
상품 구성은 사람이 고른 것이라 바꾸지 않는다. 6번에서 맞는 색 사진이 없는 상품만 같은 카테고리의 비슷한
상품으로 바꾸고, 바꾼 것을 보고에 적는다. 코디컷 배치 값(`desc` 의 영어 핏·기장, `wear_with`·`wear_modes`·
`wear_how`, `cm`)은 `pick-outfit` 스킬의 "코디컷 배치" 기준으로 다듬는다 (후보에 빠져 있을 수 있다).

## 여러 벌 게시물 (profile `looks.enabled`, 20단계)
plan.yaml 에 `items` 대신 `looks`(벌 목록)가 있다. 사람이 고른 벌만 들어 있으니 벌을 더하거나 빼지 않는다.
순서는 같고, 아래만 다르다.
- 5. 상품 조합 (고르기 없이 처음부터 만들 때만): `outfit-candidates` 스킬의 "여러 벌" 절대로 `looks.count` 벌을 짠다.
- 6. 사진 확인: 확인표가 벌마다 `photo_check_<n>.jpg` 로 나온다. `photo-checker` 에게 plan.yaml 과
  확인표들을 한 번에 넘기고, 돌려받은 `cutout_photo`·`ref_photos` 를 그 상품이 있는 벌마다 채운다
  (상품 장이 없어 `photo` 는 안 써도 됨). 이어서 6-1 실측도 한다 (`tools.plan measure` 가 같은 상품이 든 벌마다 적는다). 돌려받은 `fit` 으로 그 상품 `desc` 영어의 핏·기장·소재 말을 고친다
  (사진이 이름보다 맞다. 기장 말이 바뀌면 `cm` 도 맞춘다: 오버핏 아우터 75, 미디스커트 75, 롱스커트 85). 바꿀 상품은 같은 벌 안에서 같은 카테고리로 바꾼다.
- 7. 문구: `write-copy` 의 "여러 벌" 절 (제목, 도입, 벌마다 `point`, 상품 `name`).
- 9. 만들기: 벌마다 `out/$DATE/looks/<n>/` 에 코디컷 후보(`looks.candidates` 장)가 생긴다.
- 10. 검수: 벌 폴더마다 따로 한다. `python -m tools.review prep out/$DATE` 가 모든 벌을 prep 한다. 그다음
  벌마다(`out/$DATE/looks/<n>`) `image-reviewer` → `scores.yaml` → `tools.review apply out/$DATE/looks/<n>`,
  종료 코드 3이면 그 벌만 `tools.review more out/$DATE/looks/<n>` 하고 그 벌만 다시 검수한다.
  모든 벌이 끝나면 **표지 벌을 고른다** (profile `looks.cover_collage.enabled` 가 false 일 때, 2026-10-12 사용자):
  벌마다 고른 코디컷(`looks/<n>/review/covers.jpg`)을 보고, 피드 첫 화면에서 가장 눈에 띄고 제목과 가장 잘
  맞는 한 벌을 plan.yaml `cover_look: <n>` (1부터)에 쓴다. 기준: 검수 통과·높은 점수, 제목이 말하는
  아이템·색이 보이는 벌, 아래 왼쪽 제목 자리(흰 글자)를 옷이 크게 가리지 않는 벌, 밝기·색이 단조롭지 않은 벌.
  최근 며칠 표지와 같은 아이템 종류(예: 매일 트렌치)만 이어지지 않게 한다. 사람이 텔레그램 "/ok 3" 으로 바꿀 수 있다.
  그다음 `python -m tools.review finish out/$DATE` 로 고른 코디컷으로 슬라이드·manifest 를
  다시 만든다 (종료 코드 4 = 통과 못 한 벌이 있어 사람 확인, 그대로 11로).
  벌 하나만 계속 떨어지면 그 벌의 상품을 바꾸지 말고 사람 확인으로 둔다 (사람이 고른 구성).

## 장면 모델컷 (환경변수 `SCENE` 이 있을 때, 차례 흐름 profile `flow`, 2026-10-14 사용자)
`SCENE`(보통 `out/$DATE/src/scene.jpg`)은 사용자가 이 주제에 맞춰 텔레그램으로 보낸 장면 사진 한 장이다
(스트리트, 스튜디오 촬영컷, 거울 셀카 등). 여섯 벌 모두 이 사진의 무드로, 벌마다 자리·포즈만 다르게 그린다.
코디컷은 `prompts/outfit_scene.md` 지시문으로 그린다. 순서는 위와 같고 아래만 다르다.
- 1번 다음에 `SCENE` 사진을 Read 로 본다. 사진은 무드 참고일 뿐: 그 사람의 얼굴·신원·옷은 따르지 않는다.
- **여러 장** (`SCENES` 에 경로가 둘 이상, 2026-10-15 사용자 "섞어서 무드"): 모두 Read 로 보고
  - `mood` 는 여러 장에 공통인 무드 한 문장 (촬영 종류·장소 종류·빛·색감·카메라).
  - 벌마다 그 벌 옷에 가장 어울리는 사진 하나를 골라 벌 `scene_image` 에 쓰고, `pose` 는 그 사진의 자리·포즈를
    따라 새로 쓴다 (사진을 그대로 베끼지 않고 같은 종류의 자리·포즈로). 사진은 고르게 쓰고, 한 사진은 두 벌까지.
  - plan 의 `scene_image` 는 첫 장(`SCENE`) 그대로 둔다. 보고에 벌마다 고른 사진 번호를 적는다.
  `out/$DATE/src/scene.txt` 가 있으면 사용자가 장면 사진에 붙인 요청이다 (예: "슬릭번" = 모든 벌 머리를 낮게 묶은
  슬릭번으로). 여섯 벌 모두의 `pose`(또는 `mood`)에 영어로 분명히 넣는다. 보고에 어떻게 넣었는지 적는다.
- 8번(검사) 전에 plan.yaml 에 쓴다 (모두 영어).
  - `shot: model`, `scene_image: <SCENE 값>`.
  - `mood`: 장면 사진에서 읽은 무드 한 문장. 촬영 종류(street snapshot, studio lookbook shoot, mirror selfie …),
    장소·배경의 종류, 빛(흐림·한낮·스튜디오 조명·창빛), 색감, 카메라(폰, 필름 느낌, 플래시, 렌즈 거리).
    실제 가게·사람 이름은 쓰지 않는다.
  - 벌마다 `pose`: 같은 무드 안에서 벌마다 다른 자리와 포즈, 얼굴 가리는 방법을 한 문장으로
    (예: "walking past the shop window, looking down at her phone, hair falling over her face").
    얼굴 가리기는 고개 숙임·돌림·머리카락·손·모자 챙·거울 셀카의 폰을 섞는다. 모자를 쓰는 벌은 두 벌 이하.
    여섯 벌이 한 장소의 다른 컷처럼 보이게: 서기·걷기·기대기·앉기, 정면·옆·뒤돌아보기, 거리(전신 위주)를 섞는다.
    장면이 거울 셀카면 모두 거울 셀카로, 폰이 얼굴을 가리고 거울 속 전신(신발까지)이 보이게 쓴다.
    전신이 보이게 쓴다 (앉은 포즈도 신발까지 보이게).
  - 벌 `styling`: 사람이 입은 모양으로 ("the shirt is tucked into the front of the skirt; the jacket is worn open").
    펼친 코디컷 말("laid", "flat")은 쓰지 않는다.
- 10번 검수에서 `image-reviewer` 에게 "장면 모델컷: image-reviewer.md 의 모델컷·장면 모델컷 기준을 본다"고 말한다.
- 11번 보고에 읽은 mood 와 벌마다 pose 를 적는다.

## 에이전트 부르기
`photo-checker`, `size-reader`, `image-reviewer` 에이전트는 반드시 앞에서(`run_in_background: false`) 불러 결과를 받은 뒤
다음 단계로 간다. 백그라운드로 부르면 결과를 기다리는 동안 실행이 끝나 버려 게시물이 만들어지지 않는다.
report.md 를 쓰기 전에는 끝내지 않는다.

## 순서

1. **기준 읽기**: `accounts/$ACCOUNT/profile.yaml`, `accounts/$ACCOUNT/voice.md`, `accounts/$ACCOUNT/styling.md`(코디 기준, 있으면) 를 읽는다.
2. **오늘 정보**: `python -m tools.plan context --account $ACCOUNT --date $DATE`
   날짜·요일·계절·날씨 예보, 정해 둔 주제, 최근 게시, 상품 창고 재고가 나온다.
3. **주제**: 정해 둔 주제가 있으면 그대로 쓴다. 없으면 `plan-themes` 스킬의 "하루 주제" 방법으로 하나 정한다.
   주제에는 `search_terms` (카테고리별 지그재그 검색어 1~3개)를 꼭 넣는다.
4. **새 상품 수집 (COLLECT=1 일 때만)**: 주제 검색어마다
   `python -m tools.collect --account $ACCOUNT --category <카테고리> --query <검색어> --report out/$DATE/collect`
   를 돌린다. 지그재그는 GitHub Actions 에서만 열린다. 실패해도 창고에 있는 상품으로 계속한다.
5. **상품 조합**: `pick-outfit` 스킬을 따른다. `out/$DATE/plan.yaml` 에 theme, items, reason 을 쓴다.
6. **사진 확인**: `python -m tools.plan photos out/$DATE/plan.yaml` 로 사진 확인표(`photo_check.jpg`)를 만들고
   `photo-checker` 에이전트에게 plan.yaml 과 확인표를 넘긴다. 에이전트가 돌려준 번호로
   items 의 `photo`, `cutout_photo`, `ref_photos` 를 채운다. 맞는 색 사진이 없는 상품은 같은 카테고리 다른 상품으로 바꾸고
   이 단계를 다시 한다. 옷은 돌려받은 `fit` 으로 `desc` 영어의 핏·기장·소재 말을 고친다 (사진이 이름보다 맞다,
   기장 말이 바뀌면 `cm` 도: 오버핏 아우터 75, 미디스커트 75, 롱스커트 85).
   돌려받은 `styled_with` 에 이너(티·나시·얇은 터틀넥)가 있거나 "needs a plain inner" 인데 그 벌에 이너가 없으면
   `tools.plan candidates --category 상의 --query "나시"` 등으로 베이직 이너를 찾아 넣는다 (`wear_with: 상의`,
   styling.md 3번). 사람이 고른 구성이어도 이너 추가는 된다. 보고에 적는다.
6-1. **실측** (2026-10-06): `python -m tools.plan sizes out/$DATE/plan.yaml` 로 옷 상품의 상세 본문 이미지를
   조각내고(`out/$DATE/src/sizes/`, 결과 브랜치에 올라가지 않음), `size-reader` 에이전트에게 `sizes.json` 을 넘긴다.
   돌려받은 YAML 을 `out/$DATE/sizes.yaml` 로 저장하고 `python -m tools.plan measure out/$DATE/plan.yaml --file
   out/$DATE/sizes.yaml` 로 옷마다 `cm`·`size`(총장·어깨·가슴·소매 cm, 영어)를 적는다. 실측이 `fit` 이나 `desc` 의
   기장 말과 어긋나면 실측을 믿고 `desc` 를 고친다 (예: 총장 74cm 자켓인데 desc 가 cropped). 상품을 바꾸면 다시 한다.
   실측표를 못 찾은 상품은 그대로 둔다 (이름의 기장 말·기본값으로 그림).
7. **문구**: `write-copy` 스킬로 plan.yaml 의 `text` 와 items 의 `name`, `comment` 를 쓴다.
8. **검사**: `python -m tools.plan check out/$DATE/plan.yaml`.
   오류가 있으면 고치고 다시 검사한다 (최대 3번). 경고는 이유가 있으면 두어도 된다.
9. **만들기**: `python -m tools.plan build out/$DATE/plan.yaml --outfit $OUTFIT`
   코디컷 후보(profile `review.candidates` 장), 슬라이드, `caption.txt`, `preview.jpg`, `manifest.json`,
   `review/review.json` 이 생긴다. 검수 전에는 첫 후보가 표지다.
   코디컷 이미지 서비스 한도(종료 코드 2, OpenRouter 크레딧 부족 등)면 `--outfit none` 으로 다시 만들고
   보고에 적는다. 생성 비용은 report.md 에 자동으로 남는다.
10. **이미지 검수**: 아래를 통과하거나 사람 확인으로 끝날 때까지 한다.
    1. `python -m tools.review prep out/$DATE` → `review/input.md`, `review/covers.jpg`, `review/slides.jpg`
    2. `image-reviewer` 에이전트에게 `out/$DATE` 폴더와 `review/input.md` 를 넘긴다.
       돌려받은 YAML 을 그대로 `out/$DATE/review/scores.yaml` 에 쓴다 (점수를 고치지 않는다).
    3. `python -m tools.review apply out/$DATE --scores out/$DATE/review/scores.yaml`
       - 종료 코드 0: 통과. 가장 좋은 후보가 표지가 되고 manifest 에 점수가 남는다. 11로.
       - 3: 표지 후보가 모두 기준 미달. `python -m tools.review more out/$DATE` 로 후보를 더 만들고 1부터.
         (생성형 코디컷은 가장 나은 후보를 검수 결함·메모대로 고쳐 그린다. 0점 항목이 있으면 처음부터 다시 그린다.
         그래서 `notes` 에 어느 상품의 무엇이 틀렸는지 분명히 적어야 한다.)
         (`more` 가 2면 이미지 서비스 한도, 3이면 최대 회차: 그대로 11로)
       - 4: 사람 확인 필요. 캐러셀 장 문제가 이유면 한 번만 고쳐 본다: plan.yaml 의 `photo` 번호를
         다른 사진으로 바꾸거나 이름·코멘트를 줄이고, `cover:` 에 `review/review.json` 의 표지(chosen) 파일
         경로(저장소 기준, 예: `out/$DATE/outfit/results/C_2.jpg`)를 적은 뒤 8~9를 다시 하고 1부터.
         코디컷은 다시 만들지 않고 이미 매긴 점수도 그대로 남는다. 그래도 4면 11로.
11. **확인·보고**: `out/$DATE/preview.jpg` 를 직접 보고, 표지 제목이 잘리거나 상품 장 사진이 이상하면
    plan.yaml 을 고쳐 8~10을 다시 한다. 마지막에 `out/$DATE/report.md` 에 짧게 덧붙인다:
    주제와 이유, 고른 상품과 이유, 바꾼 것, 검수 결과(표지 후보 점수, 남은 문제), 남은 경고.

## 지키는 것
- 상품 창고에 없는 상품은 쓰지 않는다 (id 는 `tools.plan candidates` 결과에서만 복사).
- 상품의 색·소재·기능은 상품명과 사진으로 확인한 것만 쓴다. 모르면 쓰지 않는다.
- 검수 점수는 image-reviewer 가 준 그대로 쓴다. 통과시키려고 점수를 올리거나 후보를 빼지 않는다.
- plan.yaml 외에 `data/` 를 고치는 것은 주제 저장(`tools.plan themes`)과 수집(`tools.collect`)뿐이다.
