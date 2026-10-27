<!--
장면 모델컷 지시문 (2026-10-14 사용자, profile flow.scene_prompt, tools.outfit_gen, 기획 shot: model + scene_image).
주제마다 사용자가 장면 사진 1장(스트리트, 스튜디오 촬영컷, 거울 셀카 등)을 보내면, 그 사진의 무드로 벌마다 한 장씩 그린다.
10/15 스트리트 게시물 지시문(experiments/street_1015)을 정리했다. 그때는 벌마다 다른 장면 사진을 그대로 다시 만들었고,
이제는 한 장의 무드 안에서 벌마다 자리·포즈를 다르게 한다 ($pose, daily-feed 가 벌마다 씀).
기준 코디 사진(있으면 맨 앞, 2026-10-27 사용자: 참고와 입은 느낌이 다름 → 상품 사진보다 앞에), 상품 참고판을 번호 순서로, 장면 사진을 마지막 그림으로 보낸다 (여러 장을 받았으면 이 벌에 고른 한 장, $mood 는 공통 무드). 장면 속 사람의 얼굴·신원은 따르지 않는다 (새 AI 인물).
얼굴은 거의 보이지 않게 (2026-10-14 사용자: 모자를 넣거나 바꿔도 됨, 손으로 머리 넘기기 등으로 가리되 뭉개지 않게).
2026-10-15 사용자: 모자·선글라스처럼 얼굴을 가리는 패션 소품은 상품에 없어도 어울릴 때 그려도 됨 (꼭 그릴 필요는 없음). 모양은 사용자가 보낸 핀터레스트 예시
(/mnt/project-files/image-feedback/ref_1015_hats/): 무지 볼캡을 깊게 눌러 쓰고 머리는 낮게 묶음, 검은 울 뉴스보이캡,
작은 타원형 검은 선글라스, 볼캡 + 선글라스. 로고 없이, 코디 색(검정·차콜·회색·네이비·버건디)에 맞춘다.
2026-10-27 사용자: 상품과 다른 디테일(주름·카라·단추)·기장·두께, 통짜 허리, 넙데데한 얼굴을 고침.
같은 날 run356 원인: 일반 몸 규칙(허리 굴곡·통짜 금지)이 둥근 오버핏을 일자로 만들고, 카탈로그 차렷 포즈가 단정해 보임 → 기준 코디가 있으면 몸 규칙 대신 그 실루엣, 차렷 금지 (포즈는 장면 사진, 사용자 B).
2026-10-27 사용자 (게시 뒤): 핏·재질·기장·입는 모양을 글로 정해 두지 말고 참고 코디컷과 최대한 같은 실루엣·기장·룩으로.
우선순위: 기준 코디 사진(실루엣·핏·몸에서 보이는 기장·입는 모양: 여밈, 지퍼 올린 정도, 넣어 입기, 삐져나온 정도, 어깨에 걸치기,
한쪽 흘러내림) > 상품 참고판(무엇인지: 재질·색·무늬·디테일·두께) > 장면 사진(장소·빛·포즈만). 기준 코디가 없을 때의 일반 입는
규칙(긴바지는 발목을 살짝 덮는 기장, 랩·끈 디자인 상의는 하의 위로, 2026-10-14 10/16 /fix)은 scene_body.md 로 옮김.
같은 날 /fix 규칙 반영: 앞코·굽 확인, 새 옷처럼 매끈하게. 안에 입은 옷은 살짝만, 바지·소매는 상품이 접혔을 때만 접기는
기준 코디가 없을 때만 (scene_body.md, 있으면 그 사진대로).
$person 인물과 얼굴 (기획에 face 가 있으면 prompts/scene_face_shown.md, 없으면 scene_face_hidden.md),
$items 번호별 상품 설명과 실측 (2026-10-27 사용자: 기장은 cm 기본값이 아니라 상품 착용컷의 모델 몸에서 끝나는 자리를 따름),
$body 몸과 실루엣 (기준 코디 사진이 있으면 prompts/scene_body_ref.md, 없으면 scene_body.md), $sheets 상품 참고판 설명 (기준 코디가 있으면 상품 사진 속 모델이 입은 방식은 따르지 않게), $worn 기준 코디 사진 설명 (따라 한 참고 코디가 있으면, 2026-10-27 사용자: 참고 코디와 똑같이 입기. 없으면 빈칸), $styling 입는 방법, $pose 이 벌의 자리·포즈·얼굴 가리는 방법,
$must 사용자가 /fix·후보 /fix 로 정한 요청 (맨 위, 모든 것보다 앞섬. 2026-10-27 run378: 요청이 맨 끝 styling 에 있고
"글보다 사진" 문장에 눌려 니트 기장이 그대로 → 맨 위로), $mood 장면 사진에서 읽은 무드 (촬영 종류, 장소, 빛, 색감, 카메라), $season 게시 날짜의 계절.
이 주석은 전송되지 않는다.
-->
Create one realistic photo of a young woman wearing this exact outfit, the kind of photo people save on Pinterest.
$must

$worn
$sheets Draw each item once and ignore other clothes or people in those photos:
$items

The last attached image is the scene photo, a photo of a different person that sets the mood. Take only
the kind of shot, place, light and mood from it, never that person's clothes, face or body.

What decides what, in this order: the reference outfit photo (if attached) decides the whole look on
her body: the silhouette, how each piece fits, how long each piece looks on her and how it is worn
(open or closed, how far a zip is done up, tucked or loose, what peeks out and by how much, a coat on the
shoulders or slipping off one shoulder, pushed-up sleeves). The item sheets decide what each product is:
its fabric, color, pattern, details and thickness. The scene photo decides only the place, light and
pose. Apart from the user's requests at the top, the text only tells the items apart: where other
text and the photos differ, follow the photos.

Make a new photo in the same mood as the scene photo: the same kind of shot (street snapshot, studio
shoot, mirror selfie or whatever it is), the same kind of place and background, the same light, color
tone, camera style, framing distance and photo quality. It is not a copy: $mood. This photo's spot and
pose: $pose. Season: $season.

$person

She wears exactly the listed items. How they are worn: $styling

Every item must look like the real product, not a generic version of it. Check each item against its
sheet: color, fabric and its surface (leather shine, wool fuzz, boucle loops, denim wash, knit rib, satin
sheen, velvet pile), pattern, cut, collar shape, number and kind of buttons, zips, belts and pockets,
shoe toe shape (pointed, round or square) and heel height. Never add details the product does not have:
a plain skirt gets no pleats, a collarless jacket gets no collar, a coat without a belt gets no belt.
Each piece keeps its kind of length: a mini skirt or shorts stay short, a cropped jacket stays cropped,
a half coat never becomes a long coat. Thick, heavy pieces (wool coats, padded or quilted jackets, fur,
chunky knits, thick denim) keep their real thickness, weight and volume and are never drawn thin or
flimsy; thin pieces stay thin.

$body Each fabric folds and drapes by its own weight: soft knits and satin flow, stiff wool and leather
hold their shape. Show her full outfit from the collar to the shoes unless the scene photo is cropped
tighter, and never hide the listed items behind props. Her pose is relaxed and natural, with some
attitude: never a stiff, straight upright stance with both arms hanging at her sides.

Real camera photo quality, natural skin, real hair and fabric that looks new and smooth, with only a few
light creases where the body bends. Vertical 4:5 photo filling the whole canvas: no black or white border, no rounded corners, no phone screen frame. No text,
watermark, captions or logos that are not on the products.
