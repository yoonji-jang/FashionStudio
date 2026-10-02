<!--
모델컷 지시문 (16단계, tools.outfit_gen, 기획 shot: model, profile model_shot.prompt).
schedule.post_days 의 model 날 게시물은 전날 올린 벌들을 옷을 입은 얼굴 없는 AI 모델컷으로 다시 그린다.
상품 참고판을 번호 순서로 함께 보낸다. 얼굴이 보이지 않는 가상 인물. 실제 인물 사진을 넣지 않는다.
$items: 번호별 상품 설명과 실제 크기, $styling: 입는 방법, $pose: 인물·포즈·얼굴 가리는 방법
(기획 pose, profile model_shot.poses 에서 벌마다 하나. 인물 묘사를 같게 써서 한 사람처럼),
$mood: 장소·분위기 (기획 mood, 없으면 profile model_shot.mood),
$season: 게시 날짜의 계절 (영어).
2026-09-30 비교(gen-3 사람컷)와 펼친 코디컷에서 나온 실수를 막는 문장을 넣었다:
지시에 없는 버킷햇을 씌움, 귀걸이 두 쌍, 깃·신발 모양 바뀜, 바지 핏, 걷는 컷에서 옆얼굴이 보임.
2026-10-02 모델컷 시안(gen-23·24): 창가 컷 옆얼굴, 폰 든 손에 반지.
제목·상품 줄은 왼쪽 아래(가로 45%, 세로 35%)에 얹으므로 인물은 오른쪽으로, 그 자리는 배경만.
이 주석은 전송되지 않는다.
-->
Create one photorealistic lifestyle fashion photo in a candid Pinterest mood of a person wearing this
exact outfit, like a real outfit-of-the-day photo taken with a good camera.

The attached images are reference sheets of the exact items the person wears or carries, in this
order. Each sheet shows one or more shop photos of the same single item (sometimes flat, sometimes
worn by a model whose identity must not be copied); use them together to get its true details, and
draw the item only once. Shop photos may show other clothes or props worn with the item: ignore
everything except the named item:
$items

Keep every item identical to its product photos: same color, fabric texture, pattern, cut, length,
fit, collar or lapel type, neckline, buttons, pockets, hardware, toe shape and proportions. Do not
change a collar into a different collar type, do not change the style of the shoes, do not add,
remove or redesign any detail, no logos or text. Trousers keep the leg silhouette and length seen in
the worn photos (never flared, cropped or wider than in the photos).
The person wears or carries every item in the list and nothing else: no hat, cap, scarf, sunglasses,
glasses, belt, watch, rings, necklace, earrings or other jewelry unless it is in the numbered list,
and no other bag or clothing on top. Hair, skin and the phone are the only other things on the body: bare fingers with no rings.

Styling: $styling
Person and pose: $pose
The face is never visible: it is hidden naturally by the pose or the object described above, not by
cropping the photo, and no eyes, nose or mouth can be seen, not even in profile. The person is
fictional and does not resemble the models in the reference sheets.

Framing: full body from the top of the head to the shoes, every item clearly visible, the shoes fully
in the frame with a little floor below them, the person filling about 80 percent of the frame
height. Text is written on the photo later in the lower left, so the person stands in the right part
of the frame (body centered at about 60 percent of the width from the left), and the whole lower-left
area (the left 45 percent of the width, the bottom 35 percent of the height) shows only calm floor,
wall or street with no person, bag, shoes or objects.

Mood and place: $mood. Season: $season, with light and any trees or plants matching the season.
Natural light, true-to-life colors, shallow depth of field, subtle film grain, vertical 4:5, no text,
no numbers, no labels, no signs with writing, no watermark. Draw exactly the number of pieces stated
for each item (a pair of earrings is two earrings, a pair of shoes is two shoes).
