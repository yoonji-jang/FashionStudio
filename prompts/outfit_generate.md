<!--
하루 표지 코디컷 지시문 (tools.outfit_gen, profile outfit_image.prompt). 상품 참고판을 번호 순서로 함께 보낸다.
$items: 번호별 상품 설명과 실제 크기, $styling: 입는 방법, $scene: 바닥·빛 (profile outfit_image.scene).
2026-09-30 실험(gen-3, gen-4)에서 나온 실수를 막는 문장을 넣었다: 귀걸이 두 쌍, 깃·신발 모양 바뀜, 글자·숫자.
2026-09-30 첫 게시(run40) 피드백: 바지가 자켓에 비해 짧게 그려짐, 왼쪽 아래 신발이 표지 제목에 가려짐
→ 기장 비율 문장, 신발은 오른쪽 아래, 왼쪽 아래(제목 자리)는 비우기.
다시 만든 run44 피드백: 바지가 여전히 짧고 펼친 핏이 펑퍼짐함 → 착용컷 핏(슬림 스트레이트, 발등 덮는 기장) 문장.
run46: 소품이 없는 날에도 '소품은 오른쪽 세로 줄' 문장 때문에 반지·시계를 그려 넣음 → 목록에 있을 때만.
2026-10-02 피드백(/fix 5건 + 스레드 "이미지 검토"): 바지·치마가 짧고 작고, 핏이 자로 잰 통짜처럼 정직함.
참고 계정(finleu.archive, lcod.kr)과 비교: 허리선이 높고(상의는 짧게·넣어 입고 벨트가 보임) 하의가 그림의 60%,
다리에 볼륨과 살짝 벌어진·휜 선, 밑단에 천이 접히는 주름 → '곧고 평행, 밑단 20cm' 문장을 빼고 비율·연출 문장으로.
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
below the top, garments overlap naturally, sleeves and
trouser legs relax with soft real fabric folds and a slight natural bend, never stiff or symmetric.
Every item is drawn at its real size given above, all at one common scale, so the size ratios
between items are exactly true to life (shoes are about a quarter of the length of full-length
trousers, jewelry is tiny). Lengths must follow the stated cm exactly and are never shortened,
cropped or ankle-length unless the product itself is cropped.

Proportions like a tall model with long legs: the waistline sits high, at about 40 percent of the
figure height from the collar, and stays visible (unless the styling says otherwise, the top is tucked in or ends at the
waist and any belt shows; an outer may fall over the hips but the waistband of the bottoms still shows in its
opening). Below it the bottoms are the biggest and longest part of the figure, about 60 percent of
its height: full-length trousers run from that waistline all the way down near the bottom edge of
the frame, clearly longer than any jacket or top (an item stated as 100 cm is about 1.6 times as
long as one stated as 62 cm), and midi or long skirts flow well below where the knees would be.
Style the bottoms like a fashion stylist, not like a stiff product shot: keep the exact cut seen in
the worn photos (never change straight or wide legs into flared, bootcut or skinny, and never make
them wider), but give the fabric real body and drape. The two trouser legs are not two rigid
parallel tubes: they open slightly apart from the hips, one leg is gently curved or turned inward,
pressed creases run down the front, and at the hem the fabric breaks into a few soft folds as if
resting on the foot. Skirts spread with soft natural folds and a slightly swaying hem. Tops and
outers look relaxed and compact: sleeves slightly bent, pushed up or folded at the cuff, never
spread out stiffly, so the bottoms read as the long, elegant line of the outfit.

Composition like a professional outfit flat lay account: the clothes form one continuous figure from
the collar near the top edge to the trouser hem near the bottom edge, filling about 95 percent of the
frame height, centered horizontally, with only small even margins and no large empty areas. The bag
lies close to the left side of the clothes at waist height, slightly overlapping the edge of the
clothes, and never lower than the knees. The shoes sit as a pair at an angle in the lower right,
beside the trouser hem, not overlapping the trousers. Only if jewelry or small accessories are in
the numbered list above, they sit in a neat vertical column on the right side at chest height; if
the list has none, draw no jewelry, watch, rings, earrings or any other accessory at all. Keep the lower left area of the frame (the bottom third, left of
the trouser legs) empty floor with no shoes, bag or accessories, because a title is placed there later.

Scene: $scene.
Every item casts a soft contact shadow on the floor, one consistent light direction for everything.
Vertical 4:5 image, high detail, no people, no mannequin, no hanger, no text, no numbers, no labels,
no watermark. Draw exactly the number of pieces stated for each item (a pair of earrings is two
earrings, a pair of shoes is two shoes), never an extra pair.
