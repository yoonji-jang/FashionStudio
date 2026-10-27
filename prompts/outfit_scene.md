<!--
장면 모델컷 지시문 (2026-10-14 사용자, profile flow.scene_prompt, tools.outfit_gen, 기획 shot: model + scene_image).
주제마다 사용자가 장면 사진 1장(스트리트, 스튜디오 촬영컷, 거울 셀카 등)을 보내면, 그 사진의 무드로 벌마다 한 장씩 그린다.
10/15 스트리트 게시물 지시문(experiments/street_1015)을 정리했다. 그때는 벌마다 다른 장면 사진을 그대로 다시 만들었고,
이제는 한 장의 무드 안에서 벌마다 자리·포즈를 다르게 한다 ($pose, daily-feed 가 벌마다 씀).
상품 참고판을 번호 순서로, 장면 사진을 마지막 그림으로 보낸다 (여러 장을 받았으면 이 벌에 고른 한 장, $mood 는 공통 무드). 장면 속 사람의 얼굴·신원은 따르지 않는다 (새 AI 인물).
얼굴은 거의 보이지 않게 (2026-10-14 사용자: 모자를 넣거나 바꿔도 됨, 손으로 머리 넘기기 등으로 가리되 뭉개지 않게).
2026-10-15 사용자: 모자·선글라스처럼 얼굴을 가리는 패션 소품은 상품에 없어도 어울릴 때 그려도 됨 (꼭 그릴 필요는 없음). 모양은 사용자가 보낸 핀터레스트 예시
(/mnt/project-files/image-feedback/ref_1015_hats/): 무지 볼캡을 깊게 눌러 쓰고 머리는 낮게 묶음, 검은 울 뉴스보이캡,
작은 타원형 검은 선글라스, 볼캡 + 선글라스. 로고 없이, 코디 색(검정·차콜·회색·네이비·버건디)에 맞춘다.
2026-10-27 사용자: 상품과 다른 디테일(주름·카라·단추)·기장·두께, 통짜 허리, 넙데데한 얼굴을 고침.
긴바지는 발목을 살짝 덮는 기장, 랩·끈 디자인 상의는 하의 위로 꺼내 입기, 긴 다리 (2026-10-14 10/16 게시물 /fix).
$person 인물과 얼굴 (기획에 face 가 있으면 prompts/scene_face_shown.md, 없으면 scene_face_hidden.md),
$items 번호별 상품 설명과 실제 크기·몸에서 끝나는 자리 (2026-10-27 사용자: 기장이 길게 그려짐),
$worn 기준 코디 사진 설명 (따라 한 참고 코디가 있으면, 2026-10-27 사용자: 참고 코디와 똑같이 입기. 없으면 빈칸), $styling 입는 방법, $pose 이 벌의 자리·포즈·얼굴 가리는 방법,
$mood 장면 사진에서 읽은 무드 (촬영 종류, 장소, 빛, 색감, 카메라), $season 게시 날짜의 계절.
이 주석은 전송되지 않는다.
-->
Create one realistic photo of a young woman wearing this exact outfit, the kind of photo people save on Pinterest.

The attached images are, in order, reference sheets of the exact items (each sheet shows shop photos of
one item; draw each item once and ignore other clothes or people in those photos):
$items
$worn
The last attached image is the scene photo, a photo of a different person that sets the mood.

Make a new photo in the same mood as the scene photo: the same kind of shot (street snapshot, studio
shoot, mirror selfie or whatever it is), the same kind of place and background, the same light, color
tone, camera style, framing distance and photo quality. It is not a copy: $mood. This photo's spot and
pose: $pose. Season: $season.

$person

She wears exactly the listed items. How they are worn: $styling

Every item must look like the real product, not a generic version of it. Check each item against its
sheet: color, fabric and its surface (leather shine, wool fuzz, boucle loops, denim wash, knit rib),
pattern, cut, collar shape, number and kind of buttons, zips, belts and pockets, fit and length. Never add
details the product does not have: a plain skirt gets no pleats, a collarless jacket gets no collar. The
length on her body above is a hard rule: a mini skirt or shorts stay that short, a cropped jacket stays
cropped, a half coat is never drawn as a long coat, and long trousers, maxi skirts and long boots keep
their full length. Thick, heavy pieces (wool coats, padded or quilted jackets, fur, chunky knits, thick
denim) keep their real thickness, weight and volume and are never drawn thin or flimsy. Full-length
trousers fall onto the shoes with the hem just covering the ankle, never cropped above it. A top with a
front design (wrap, ties, draping, peplum) hangs over the waistband so the design shows, unless the
styling says it is tucked.

Her body is slim with a clearly shaped waist and long legs (legs about half her height or a little more).
The clothes sit on that body the way real fabric does: fitted and tucked pieces show the curve of the
waist and hips, the waistband sits at her real waist, and even a boxy or oversized outer hangs from her
shoulders with her slim figure visible inside it. Never a straight boxy column from chest to hip. Each
fabric folds and drapes by its own weight: soft knits and satin flow, stiff wool and leather hold their
shape. Show her full outfit from the collar to the shoes unless the scene photo is cropped tighter, and
never hide the listed items behind props.

Real camera photo quality, natural skin, real hair and fabric with natural creases. Vertical 4:5 photo
filling the whole canvas: no black or white border, no rounded corners, no phone screen frame. No text,
watermark, captions or logos that are not on the products.
