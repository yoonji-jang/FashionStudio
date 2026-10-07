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
2026-10-03 사용자: 깨끗하고 빈 배경이 AI 같다 → 핀터레스트처럼 실제 생활 장소, 들고 있는 소품, 필름 느낌.
2026-10-03 사용자 2차: 깔린 낙엽도 인위적, 책으로 얼굴 전체를 가리는 건 어색 → 얼굴 일부는 보여도 됨, 낙엽 금지.
2026-10-07 사용자 3차 (10/8 모델컷이 화보 같음, 핀터레스트 저장 사진과 비교): 5장이 같은 거리 전신·같은 긴 생머리·
같은 골든아워 필름 톤·다림질한 옷·모델 포즈·사람 없는 예쁜 골목이라 인위적 → 컷 종류·빛·헤어를 포즈마다 다르게,
신발·가방이 잘려도 됨, 글자 자리(왼쪽 아래) 비우기 없앰(글자 뒤 그라데이션이 받침), 입던 옷처럼 자연스러운 구김,
흐릿한 행인·생활 물건 허용, 힙한 핀터레스트 느낌. 바지는 실제 기장보다 짧게 그리지 않는다 (LOOK 4 와이드 팬츠가 짧아 보임).
이 주석은 전송되지 않는다.
-->
Create one candid, effortlessly cool everyday photo of a person wearing this exact outfit, the kind of
hip street-style snapshot people save on Pinterest: taken quickly by a friend or as a selfie in a real
place, not a polished AI image, a lookbook, an ad or a studio shoot.

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
the worn photos (never flared, cropped or wider than in the photos), and trousers and skirts are never
drawn shorter than their real size: long trousers break over the shoes or graze the ground.
The person wears or carries every item in the list and nothing else: no hat, cap, scarf, sunglasses,
glasses, belt, watch, rings, necklace, earrings or other jewelry unless it is in the numbered list,
and no other bag or clothing on top. Hair and skin are the only other things on the body (a plain hair tie or
plain claw clip in the hair is fine): bare fingers with no rings. Everyday props named in the pose (a phone, a paperback book, a paper takeaway
coffee cup, a small wrapped bouquet) are held or placed nearby, never worn, and never cover the
listed items.

Styling: $styling
Person and pose: $pose
The face may be partly visible the way it is in a candid photo (looking down, turned away, half
behind her hair, the cup or the phone), but she never looks at the camera and her full face is never
shown clearly from the front. Nothing is held up unnaturally just to hide the face. The person is
fictional and does not resemble the models in the reference sheets.

Framing: as the pose describes, like a real snapshot rather than a catalog shot: the camera distance,
height and angle follow the pose (a mirror selfie, a shot from above, a low angle, a waist-up crop or a
far-away shot), and the frame may cut off the top of the head, the feet, the shoes or part of the bag
when the pose calls for it. The outer, top and bottoms stay clearly visible and are never hidden by props.
Do not center the person like a product photo; a slightly tilted, imperfect handheld frame is fine.

Mood and place: $mood. Season: $season, with light and any trees or plants matching the season.
Light, camera and place come from the pose (it may be flat overcast light, harsh midday sun with hard
shadows, direct on-camera flash at night, warm indoor lamp light or soft window light); do not turn it
into golden-hour light unless the pose says so.
Make it look like a real photo, not AI: an ordinary real place exactly as it is, lived in and a little
messy (old walls, worn wood, cups and things on tables, parked cars or bikes, a few blurred passers-by far
in the background are fine), uneven light and shadow, visible grain or slight phone-camera noise and
slight softness or motion blur. The clothes look worn, not new from the shop: natural creases at the
elbows and waist, sleeves a little pushed up or bunched, an outer that hangs or slips naturally, with
the shape, length and color of every item unchanged. Natural skin texture, little makeup, real hair with
flyaways. The pose is relaxed and a little imperfect, never a model pose. The ground is plain pavement
or floor: no carpet of fallen leaves, no props arranged for the photo. No glossy perfect surfaces, no
empty symmetrical corridors, no spotless streets, no HDR or over-sharpened look, no sharp people near
the person. Colors of the listed items stay true to their product photos. Vertical 4:5, no text,
no numbers, no labels, no signs with writing, no watermark. Draw exactly the number of pieces stated
for each item (a pair of earrings is two earrings, a pair of shoes is two shoes).
