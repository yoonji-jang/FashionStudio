<!--
하루 표지 코디컷 지시문 (tools.outfit_gen, profile outfit_image.prompt). 상품 참고판을 번호 순서로 함께 보낸다.
$items: 번호별 상품 설명과 실제 크기, $styling: 입는 방법, $scene: 바닥·빛 (profile outfit_image.scene).
2026-09-30 실험(gen-3, gen-4)에서 나온 실수를 막는 문장을 넣었다: 귀걸이 두 쌍, 깃·신발 모양 바뀜, 글자·숫자.
2026-09-30 첫 게시(run40) 피드백: 바지가 자켓에 비해 짧게 그려짐, 왼쪽 아래 신발이 표지 제목에 가려짐
→ 기장 비율 문장, 신발은 오른쪽 아래, 왼쪽 아래(제목 자리)는 비우기.
이 주석은 전송되지 않는다.
-->
Create one photorealistic top-down flat lay photograph of a complete outfit, as if a stylist laid
the real clothes on the floor and photographed them from directly above with a camera.

The attached images are reference sheets of the exact items to use, in this order. Each sheet shows
one or more shop photos of the same single item (sometimes flat, sometimes worn by a model); use
them together to get its true details, and draw the item only once. Shop photos may show other
clothes or props worn with the item: ignore everything except the named item:
$items

Keep every item identical to its product photos: same color, fabric texture, pattern, cut, length,
fit, collar or lapel type, neckline, buttons, pockets, hardware, toe shape and proportions. Do not
change a collar into a different collar type, do not change the style of the shoes, do not add,
remove or redesign any detail, do not add logos or text. Use every item exactly once and add no
other clothing or objects.

Styling: $styling
Lay the outfit out like a person wearing it: the top sits at the shoulders, the bottoms continue
below the top with the waist tucked slightly under the hem, garments overlap naturally, sleeves and
trouser legs relax with soft real fabric folds and a slight natural bend, never stiff or symmetric.
Every item is drawn at its real size given above, all at one common scale, so the size ratios
between items are exactly true to life (shoes are about a quarter of the length of full-length
trousers, jewelry is tiny). Lengths must follow the stated cm exactly: full-length trousers are the
longest piece, clearly longer than any jacket or top (an item stated as 100 cm is about 1.6 times as
long as one stated as 62 cm), and they are drawn at their full length down to where the ankles would
be, never shortened, cropped or ankle-length unless the product itself is cropped.

Composition like a professional outfit flat lay account: the clothes form one continuous figure from
the collar near the top edge to the trouser hem near the bottom edge, filling about 90 percent of the
frame height, centered horizontally, with only small even margins and no large empty areas. The bag
lies close to the left side of the clothes at waist height, slightly overlapping the edge of the
clothes, and never lower than the knees. The shoes sit as a pair at an angle in the lower right,
beside the trouser hem, not overlapping the trousers. Small accessories sit in a neat vertical column
on the right side at chest height. Keep the lower left area of the frame (the bottom third, left of
the trouser legs) empty floor with no shoes, bag or accessories, because a title is placed there later.

Scene: $scene.
Every item casts a soft contact shadow on the floor, one consistent light direction for everything.
Vertical 4:5 image, high detail, no people, no mannequin, no hanger, no text, no numbers, no labels,
no watermark. Draw exactly the number of pieces stated for each item (a pair of earrings is two
earrings, a pair of shoes is two shoes), never an extra pair.
