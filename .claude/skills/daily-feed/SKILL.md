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
상품 구성은 사람이 고른 것이라 바꾸지 않는다. **6번 사진 확인에서도 상품을 다른 상품으로 바꾸지 않는다**
(2026-10-27 사용자: run349 에서 사진을 못 받아 숏 코트가 롱코트로, 캉캉 미니가 플리츠 미니로 바뀌어 상세페이지와
다른 옷이 그려짐). `tools.plan photos` 가 "사진을 다 받지 못한 상품"을 알리거나 photo-checker 가 `missing: true` 를
주면 `tools.plan photos` 를 한 번 더 돌린다 (받은 사진은 건너뜀). 그래도 고른 색 사진이 없으면 상품은 그대로 두고
후보 때 쓴 사진(`cutout_photo`)으로 그리며, 보고 맨 위에 "사람 확인: <벌> <상품> 고른 색 사진 없음"을 적는다.
상품 페이지가 없어진 경우(판매 종료)만 바꿀 수 있고, 그때도 보고 맨 위에 적는다. 코디컷 배치 값(`desc` 의 영어 핏·기장, `wear_with`·`wear_modes`·
`wear_how`, `cm`)은 `pick-outfit` 스킬의 "코디컷 배치" 기준으로 다듬는다 (후보에 빠져 있을 수 있다).

## 여러 벌 게시물 (profile `looks.enabled`, 20단계)
plan.yaml 에 `items` 대신 `looks`(벌 목록)가 있다. 사람이 고른 벌만 들어 있으니 벌을 더하거나 빼지 않는다.
순서는 같고, 아래만 다르다.
- 5. 상품 조합 (고르기 없이 처음부터 만들 때만): `outfit-candidates` 스킬의 "여러 벌" 절대로 `looks.count` 벌을 짠다.
- 6. 사진 확인: 확인표가 벌마다 `photo_check_<n>.jpg` 로 나온다. `photo-checker` 에게 plan.yaml 과
  확인표들을 한 번에 넘기고, 돌려받은 `cutout_photo`·`ref_photos` 를 그 상품이 있는 벌마다 채운다
  (상품 장이 없어 `photo` 는 안 써도 됨). 이어서 6-1 실측도 한다 (`tools.plan measure` 가 같은 상품이 든 벌마다 적는다). 돌려받은 `fit` 으로 그 상품 `desc` 영어의 핏·기장·소재 말을 고친다
  (사진이 이름보다 맞다. 기장 말이 바뀌면 `cm` 도 맞춘다: 오버핏 아우터 75, 미디스커트 75, 롱스커트 85). 사람이 고른 상품은 바꾸지 않는다 (위 "고른 후보로 시작").
- 7. 문구: `write-copy` 의 "여러 벌" 절 (제목, 도입, 벌마다 `point`, 상품 `name`).
- 9. 만들기: 벌마다 `out/$DATE/looks/<n>/` 에 코디컷 후보(`looks.candidates` 장)가 생긴다.
- 10. 검수: 벌 폴더마다 따로 한다. `python -m tools.review prep out/$DATE` 가 모든 벌을 prep 한다. 그다음
  벌마다(`out/$DATE/looks/<n>`) `image-reviewer` → `scores.yaml` → `tools.review apply out/$DATE/looks/<n>`,
  종료 코드 3이면 그 벌만 `tools.review more out/$DATE/looks/<n>` 하고 그 벌만 다시 검수한다.
  모든 벌이 끝나면 **표지 벌을 고른다** (profile `looks.cover_collage.enabled` 가 false 일 때, 2026-10-12 사용자).
  단 profile `looks.cover_first` 가 켜져 있으면 고르지 않는다: 사람이 고른 순서 그대로 두고 `cover_look: 1`
  (첫 벌 = 표지 = LOOK 1, 2026-10-16 사용자). 벌 순서도 바꾸지 않는다. 그 밖에는:
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
- **계정 얼굴 사진** (2026-10-29 사용자: 모든 코디컷을 같은 얼굴로, 얼굴 보이게): `out/$DATE/src/face.jpg` 가 있으면
  도구가 그 사진을 같이 보내 그 얼굴 그대로 그린다. 그러면 얼굴을 가리지 않으므로 벌 `pose` 에 얼굴 가리는 방법·모자·선글라스를
  쓰지 않는다 (카메라를 보거나 옆을 보는 등 얼굴이 보이는 포즈). Read 로 얼굴 사진을 보고 검수에 "얼굴 사진: out/$DATE/src/face.jpg.
  모든 벌이 이 얼굴과 같은 사람인지 본다"를 말한다. `face` 문장은 쓰지 않아도 된다.
- **얼굴 참고** (2026-10-26 사용자): scene.txt 가 몇 장은 얼굴 참고라고 하면 (예: "앞에 3장은 장면, 뒤에 3장은 얼굴")
  그 사진은 벌 `scene_image` 로 쓰지 않는다 (장면 사진만 고르게 나눠 쓴다). 얼굴 사진과 scene.txt 의 말을 보고
  plan 의 `face` 에 모든 벌 공통 얼굴을 영어 한 문장으로 쓴다: 인상, 눈매·쌍꺼풀, 눈썹, 화장, 입술, 피부 톤, 헤어.
  얼굴형 말(heart-shaped, round, oval)은 쓰지 않는다: 지시문이 늘 작고 갸름한 V라인 얼굴로 정한다 (2026-10-27 사용자: 넙데데함).
  사진 속 사람을 닮게 하는 말(이름, "like the woman in photo 4", 점·흉터 같은 그 사람만의 특징)은 쓰지 않고
  여러 장에 공통인 일반 특징만 쓴다 (실제 사람을 닮게 하지 않는다: 특징은 같고 사람은 새 인물).
  `face` 가 있으면 얼굴은 가리지 않으므로 `pose` 에 얼굴 가리는 방법·모자·선글라스를 쓰지 않는다
  (사진 설명이 정한 헤어는 모든 벌 `pose` 에 넣는다). 보고에 face 문장과 장면·얼굴로 나눈 사진 번호를 적는다.
- 8번(검사) 전에 plan.yaml 에 쓴다 (모두 영어).
  - `shot: model`, `scene_image: <SCENE 값>`.
  - `mood`: 장면 사진에서 읽은 무드 한 문장. 촬영 종류(street snapshot, studio lookbook shoot, mirror selfie …),
    장소·배경의 종류, 빛(흐림·한낮·스튜디오 조명·창빛), 색감, 카메라(폰, 필름 느낌, 플래시, 렌즈 거리).
    실제 가게·사람 이름은 쓰지 않는다.
  - 벌마다 `pose`: 같은 무드 안에서 벌마다 다른 자리와 포즈, 얼굴 가리는 방법을 한 문장으로
    (예: "walking past the shop window, looking down at her phone, hair falling over her face").
    얼굴 가리기는 고개 숙임·돌림·머리카락·손·모자 챙·선글라스·거울 셀카의 폰을 섞는다.
    모자·선글라스는 상품에 없어도 쓸 수 있다 (2026-10-15 사용자): 무지 볼캡을 깊게 + 머리 낮게 묶기, 검은 울 뉴스보이캡,
    작은 타원형 검은 선글라스, 볼캡 + 선글라스. 색은 코디에 맞추고(검정·차콜·회색·네이비·버건디) 로고는 없다.
    꼭 쓰는 것이 아니라 그 벌 코디와 장면에 어울릴 때만 쓴다 (사용자: "어울릴 경우 그려도 된다").
    쓰는 벌의 `pose` 에 영어로 적고, 안 쓰는 벌은 고개·머리카락·손으로 가린다.
    여섯 벌이 한 장소의 다른 컷처럼 보이게: 서기·걷기·기대기·앉기, 정면·옆·뒤돌아보기, 거리(전신 위주)를 섞는다.
    포즈는 장면 사진의 포즈를 따르되 경직된 차렷 자세(정면으로 똑바로 서서 두 팔을 내림)는 쓰지 않는다. 짝다리, 한 손 주머니,
    고개 기울임처럼 힘을 뺀 자연스러운 포즈로 쓴다 (2026-10-27 사용자: 너무 단정함, 차렷 피하기, 포즈는 장면 사진 = B).
    포즈는 제한하지 않는다 (2026-10-27 사용자: 팔을 들어도 기장·여밈은 요청·참고대로 그리면 됨). 지시문이 "포즈가 입은 모양을
    바꾸지 않게"를 붙인다.
  - 후보 벌 `requests`(사용자가 후보 /fix 로 미리 정한 그리기 요청: 상품 아닌 소품, 포즈, 여밈 정도 등)는 그대로 옮긴다.
    지우거나 줄이지 않고, `pose`·`styling` 을 그 요청과 어긋나지 않게 쓴다 (요청에 포즈가 있으면 `pose` 도 그대로).
    장면이 거울 셀카면 모두 거울 셀카로, 폰이 얼굴을 가리고 거울 속 전신(신발까지)이 보이게 쓴다.
    전신이 보이게 쓴다 (앉은 포즈도 신발까지 보이게).
  - 벌 `styling`: 사람이 입은 모양으로 ("the shirt is tucked into the front of the skirt; the jacket is worn open").
    펼친 코디컷 말("laid", "flat")은 쓰지 않는다.
  - **기준** (2026-10-27 사용자): 1. 참고 코디컷과 최대한 "동일"한 핏·코디 느낌, 2. 실제 제품의 재질·기장·색을
    절대 왜곡하지 않기. 상세페이지 사진과 다르게 그려지면 다시 그리는 비용이 크다. 2026-10-28 사용자 기준: 입는 방법(넣어 입기, 단추·여밈, 카라,
    이너 삐져나온 정도, 언발란스)만 참고 코디, 옷의 길이·재질·품·핏은 상세 사진이 정답이다. 이미지 AI 에는 사진이 정답이고, 글은 어느 사진이 어떤 상품인지 알려 주는 정도다.
  - 6번 사진 확인에서 `tools.plan photos` 가 벌마다 참고 코디 사진을 `out/$DATE/src/refs/look_<n>.jpg` 로 받는다.
    photo-checker 에게 이 사진도 보라고 말한다. photo-checker 가 참고 코디와 가장 비슷하게 입은 착용컷(예: 코트를
    벨트로 묶은 컷), 기장이 보이는 전신 착용컷, 정면컷, 재질 확대컷을 벌마다 `ref_photos` 2~4장으로 골라 준다.
    같은 상품이라도 벌마다 받은 대로 채운다.
  - 참고 코디 사진(`out/$DATE/src/refs/look_<n>.jpg`)이 없으면 그 벌은 그리지 않는다 (도구도 멈춘다). 지어낸 글로 그리면
    참고와 다른 핏이 나와 다시 그리는 비용만 든다 (2026-10-27 run356). 보고 맨 위에 "사람 확인: 참고 코디 사진 없음"을 적는다.
  - 벌에 따라 한 참고 코디(`ref_photo`)가 있으면 그 사진이 이미지 AI 에 같이 가고,
    입는 모양(여밈, 지퍼 올린 정도, 넣어 입기, 삐져나온 정도, 어깨에 걸치기, 한쪽 흘러내림, 소매 걷음)은 그 사진이 정한다
    (2026-10-27 사용자: 글로 강제하지 말고 참고 코디컷과 최대한 같은 실루엣·기장·룩). 그래서 `styling` 에 이런 것을
    글로 옮기지 않는다. 글이 사진과 조금만 달라도 AI 는 글을 따라 다르게 그린다 (run356: "엉덩이 위 크롭", "티를 치마에
    넣음"이 둥근 오버핏 점퍼를 짧은 일자 점퍼로 만듦, 10/23: "아우터 밑으로 살짝" 같은 글이 참고와 다른 비율을 만듦).
    `styling` 은 비워 두거나(도구가 "참고 코디 사진대로"를 넣음) 사진이 알려 줄 수 없는 것만 영어로 짧게 쓴다:
    상품이 아닌데 사용자가 요청한 것(예: 비치는 검은 시스루 스타킹, 적어야 그려진다), 사용자가 참고와 다르게 해 달라고
    한 것. 참고 사진의 무지 스타킹·양말은 적지 않아도 따라 신는다.
  - 아이템 `desc` 는 짧게: 한국어 이름 (영어로 종류, 색, photo-checker 의 `material`). 상품을 서로 구분하는 말이지
    입는 법을 정하는 말이 아니다. 기장 말("hem ends at …", "cropped", "long", "mini")은 어느 벌에도 쓰지 않는다
    (2026-10-28 사용자: 긴바지 기장 문장 때문에 반바지·치마가 길게 그려짐, 기장은 상세페이지와 똑같이만.
    도구가 "착용컷 모델 몸에서 끝나는 자리"를 넣는다). 핏 말("oversized", "slim")도 쓰지 않는다
    (품·핏·기장은 상세 사진, 입는 방법은 참고 사진이 정한다). 사진을 보기 전에 쓴 핏·기장·디테일 말은 지운다.
    상품에 없는 디테일(주름, 카라, 단추)은 쓰지 않는다.
    기장 cm 는 실측표에서 읽은 것만 쓴다 (장면 모델컷 지시문은 실측이 없으면 cm 대신 "착용컷 모델 몸에서 끝나는 자리"를 쓴다).
  - **그리기 전 확인** (9번 전, 이미지 AI 를 부르지 않음): 벌마다 참고 코디 사진과 그 벌 상품의 `ref_photos` 사진을
    Read 로 나란히 보고 확인한다. (1) `styling`·`desc` 에 사진과 다르거나 입는 모양을 정하는 말이 없는지, (2) 참고 코디처럼 입을 수 있는
    상품인지 (예: 참고는 짧은 코트를 묶었는데 상품은 끈 없는 롱코트). (2) 가 크게 어긋나면 상품은 그대로 두고
    그대로 그리고 보고 맨 위에 "사람 확인: <벌> 참고 코디와 상품이 다름 (<무엇>)"을 적는다 (글로 입는 법을 덧붙이지 않는다).
- 10번 검수에서 `image-reviewer` 에게 "장면 모델컷: image-reviewer.md 의 모델컷·장면 모델컷 기준을 본다"고 말한다.
  plan 에 `face` 가 있으면 "얼굴 참고: face = <문장>. 얼굴이 보여도 되고, 이 특징과 맞는지·벌마다 같은 사람인지 본다"도 말한다.
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
