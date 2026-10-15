<!--
장면 모델컷 지시문 (2026-10-14 사용자, profile flow.scene_prompt, tools.outfit_gen, 기획 shot: model + scene_image).
주제마다 사용자가 장면 사진 1장(스트리트, 스튜디오 촬영컷, 거울 셀카 등)을 보내면, 그 사진의 무드로 벌마다 한 장씩 그린다.
10/15 스트리트 게시물 지시문(experiments/street_1015)을 정리했다. 그때는 벌마다 다른 장면 사진을 그대로 다시 만들었고,
이제는 한 장의 무드 안에서 벌마다 자리·포즈를 다르게 한다 ($pose, daily-feed 가 벌마다 씀).
상품 참고판을 번호 순서로, 장면 사진을 마지막 그림으로 보낸다 (여러 장을 받았으면 이 벌에 고른 한 장, $mood 는 공통 무드). 장면 속 사람의 얼굴·신원은 따르지 않는다 (새 AI 인물).
얼굴은 거의 보이지 않게 (2026-10-14 사용자: 모자를 넣거나 바꿔도 됨, 손으로 머리 넘기기 등으로 가리되 뭉개지 않게).
긴바지는 발목을 살짝 덮는 기장, 랩·끈 디자인 상의는 하의 위로 꺼내 입기, 긴 다리 (2026-10-14 10/16 게시물 /fix).
$items 번호별 상품 설명과 실제 크기, $styling 입는 방법, $pose 이 벌의 자리·포즈·얼굴 가리는 방법,
$mood 장면 사진에서 읽은 무드 (촬영 종류, 장소, 빛, 색감, 카메라), $season 게시 날짜의 계절.
이 주석은 전송되지 않는다.
-->
Create one realistic photo of a young woman wearing this exact outfit, the kind of photo people save on Pinterest.

The attached images are, in order, reference sheets of the exact items (each sheet shows shop photos of
one item; draw each item once and ignore other clothes or people in those photos):
$items
The last attached image is the scene photo, a photo of a different person that sets the mood.

Make a new photo in the same mood as the scene photo: the same kind of shot (street snapshot, studio
shoot, mirror selfie or whatever it is), the same kind of place and background, the same light, color
tone, camera style, framing distance and photo quality. It is not a copy: $mood. This photo's spot and
pose: $pose. Season: $season.

The person is a fictional young Korean woman: never copy the face or identity of the person in the scene
photo or in the item sheets. Her face is barely visible: turned away, looking down, behind her hair or a
hand, under a cap brim, or behind the phone in a mirror selfie. What shows of her face is natural and
clear, never blurred, smudged or pixelated. She may wear one plain hat or cap to help hide her face; apart
from that she wears nothing that is not listed: no scarf, sunglasses, socks, belt, jewelry or other bag
unless listed. A held prop from the scene photo (coffee cup, phone, newspaper) is fine.

She wears exactly the listed items. How they are worn: $styling Keep every item identical to its product
photos: color, fabric texture, pattern, cut, length, fit, collar, buttons, hardware and shoe style. A mini
skirt stays a mini skirt, long trousers and maxi skirts keep their full length, long boots keep their
real shaft height. Full-length trousers fall onto the shoes with the hem just covering the ankle, never
cropped above it. A top with a front design (wrap, ties, draping, peplum) hangs over the waistband so the
design shows, unless the styling says it is tucked. Her legs are long for her height (legs about half
her height or a little more). Show her full outfit from the collar to the shoes unless the scene photo is cropped
tighter, and never hide the listed items behind props.

Real camera photo quality, natural skin, real hair and fabric with natural creases. Vertical 4:5 photo
filling the whole canvas: no black or white border, no rounded corners, no phone screen frame. No text,
watermark, captions or logos that are not on the products.
