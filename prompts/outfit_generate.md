<!--
하루 표지 코디컷 지시문 (tools.outfit_gen, profile outfit_image.prompt). 상품 참고판을 번호 순서로 함께 보낸다.
$items: 번호별 상품 설명과 실제 크기, $styling: 입는 방법, $scene: 바닥·빛 (profile outfit_image.scene).
2026-09-30 실험(gen-3, gen-4)에서 나온 실수를 막는 문장을 넣었다: 귀걸이 두 쌍, 깃·신발 모양 바뀜, 글자·숫자.
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
trousers, jewelry is tiny).

Composition like a professional outfit flat lay account: the clothes form one continuous figure from
the collar to the hem and fill about 85 percent of the frame height, centered slightly left, with
only small even margins and no large empty areas. The bag lies close to the left side of the bottoms,
slightly overlapping the edge of the clothes. The shoes sit as a pair at an angle in the lower left,
close to the hem. Small accessories sit in a neat vertical column on the right side at chest height.

Scene: $scene.
Every item casts a soft contact shadow on the floor, one consistent light direction for everything.
Vertical 4:5 image, high detail, no people, no mannequin, no hanger, no text, no numbers, no labels,
no watermark. Draw exactly the number of pieces stated for each item (a pair of earrings is two
earrings, a pair of shoes is two shoes), never an extra pair.
