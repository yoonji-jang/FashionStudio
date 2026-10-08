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
같은 날: 오프숄더 니트의 넓은 깃이 가죽자켓 어깨 위로 덮임, 니트가 자켓 어깨 위로 올라옴(10/3 /fix)
→ 안에 입은 옷은 겉옷 위로 올라오지 않는다는 겹침 순서 문장.
2026-10-06 피드백(10/7 게시물, 스레드 "이미지 피드백"): 오버핏 자켓·가디건이 크롭처럼 짧고 작게, 흘러내리는
오버핏 셔츠가 빳빳하게, 플리츠 치마의 재질·주름이 다르게, 미디스커트가 발목 기장으로 그려짐, 스카프를 옷 위 목에 묶음
→ '상의·아우터는 작고 compact 하게' 문장을 빼고 상품의 핏·기장·천의 느낌을 지키는 문장, 치마 재질·주름·미디 기장 문장,
스카프 같은 목 소품은 옷에 두르지 않고 목 높이 옆 빈자리에 따로 두는 문장.
2026-10-07 사용자: 펼친 옷이 납작해 보임 → 과하지 않게 실제 옷을 펼친 것처럼 살짝 볼륨(천의 두께, 안쪽 공기, 부드러운 그림자).
같은 날 10/8 게시물(run167): 여전히 납작함 → 'Keep it subtle' 이 볼륨 문장을 약하게 만들고, 고른 빛이라 그림자가 없음.
부위별 볼륨(팔꿈치 들림, 가슴 품, 밑단 말림, 니트 두께, 하의 둥근 주름)과 옆빛 그림자로 보이게 하는 문장으로.
어깨에 두른 가디건이 상의 가슴을 망토처럼 덮음 → 두른 니트는 몸판을 상의 뒤로 숨기고 소매만 앞으로 묶는 문장.
같은 날 사용자: 하의, 특히 치마가 납작함(새틴 롱스커트·미니스커트가 주름 없는 판), 코디가 너무 위에 붙음
→ 치마는 깊은 주름과 물결치는 밑단·광택 명암 문장, 깃은 위 끝에서 8% 아래, 짧은 코디는 아래로 내려 위아래 여백을 맞춤.
2026-10-02·03 피드백: 단추가 상세페이지는 3개인데 2개로, 귀걸이 디자인이 다름, 단추 없는 니트 조끼에 단추가 생김
→ 단추 개수·위치와 주얼리 디자인을 사진 그대로 지키는 문장.
2026-10-09 피드백(10/9 게시물 /fix 5건): 크롭 자켓이 길게, 와이드 바지 기장·밑단이 좁고 짧게, 단추를 다 잠금, 가디건이 이너를 가림,
목걸이가 작고 오른쪽에 치우침 → 모델이 입은 모양(크롭 기장, 푼 단추, 열린 가디건) 문장, 와이드 밑단 문장, 목걸이는 크게 가운데.
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
remove or redesign any detail, do not add logos or text. Count the buttons in the product photos and
draw exactly that many in the same places (a three-button jacket never gets two; a vest without
buttons gets none); draw earrings and other jewelry with the exact design, shape and size of their
product photos, never a similar-looking one. Use every item exactly once and add no
other clothing or objects.

Styling: $styling
Layering order is physical, exactly as on a real body: an item worn inside lies under the item
worn over it everywhere they overlap. An inner top never lies on top of an outer's shoulders,
collar, lapels, front panels or sleeves; it shows only where it really would: at the neckline inside
the outer's front opening, a little at the cuffs and at the hem. A wide neckline, off-shoulder
neckline, big collar or puffy sleeve of an inner top is hidden under the outer and only shows inside
the front opening, folded or tucked under the outer's edges; never copy its shape from the shop
photo over the outer.
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
opening). The top's hem never covers the waistband design of the bottoms (waistband, belt, belt
loops, buttons): the top is only lightly tucked in at the front or ends just above it, and the
bottoms sit a little lower so their whole waistband shows. Below it the bottoms are the biggest and longest part of the figure, about 60 percent of
its height: full-length trousers run from that waistline all the way down near the bottom edge of
the frame, clearly longer than any jacket or top (an item stated as 100 cm is about 1.6 times as
long as one stated as 62 cm), and midi or long skirts flow well below where the knees would be.
Style the bottoms like a fashion stylist, not like a stiff product shot: keep the exact cut seen in
the worn photos (never change straight or wide legs into flared, bootcut or skinny, and never make
them wider), but give the fabric real body and drape. The two trouser legs are not two rigid
parallel tubes: they open slightly apart from the hips, one leg is gently curved or turned inward,
pressed creases run down the front, and at the hem the fabric breaks into a few soft folds as if
resting on the foot. A skirt keeps the exact fabric and pleats of its photos: the same material and
sheen (matte wool, glossy satin, sheer chiffon, crisp cotton), the same pleat type, pleat width and
pleat count (soft unpressed gathers stay soft, sharp knife or accordion pleats stay sharp, a skirt
without pleats gets none). A skirt is never a flat smooth panel or trapezoid: it is laid as if just
taken off a body, with the waistband slightly rounded, the fabric gathering into several deep soft
folds that run from the hips down and open into a rippling, wavy hem that lifts off the floor in
places; an A-line or flared skirt fans out with rolling waves along the hem, a mini skirt still has a
few soft folds and a slightly flipped hem, and satin or other glossy fabric shows bright highlights
on the raised folds and soft shadows between them. A skirt never looks pressed or stuck flat to the
floor: the hip area stays gently rounded as if a body had just stepped out of it, the front panel
curves with depth, and at the hem the inside back layer shows a little where the front lifts, so
the skirt clearly has a front and a back with air between them. A skirt ends exactly where its stated cm and worn
photos show: a midi skirt ends at mid-calf unless its stated length and worn photos put it at the
knee (then it stays at the knee), and it is never drawn as an ankle-length maxi skirt. A straight
H-line, pencil or bias-cut satin skirt stays narrow and falls straight like on the model, only its
very hem loosening a little; it is never fanned out into an A-line or flare.
Every item keeps its exact product design: neckline, ties and straps, button count, pockets, belt,
pleats and hem shape are copied from its photos and never redesigned. Fitted tops look the way they
fit on the model: slim and close to the body, with their point details (off-shoulder neckline,
halter tie, layered inner) clearly shown.
Tops and outers keep the true fit, length and fabric of their worn photos. A cropped jacket stays
cropped (ending at the waist, not the hips). Buttons are fastened or left open exactly as on the
model: a shirt or cardigan worn with the upper buttons open is drawn that way, and a cardigan worn
open over a layered inner top hangs open wide enough to show the inner top's neckline and body.
Wide-leg trousers keep a wide, straight hem that reaches the instep, never narrowed or shortened. An oversized or loose
jacket, cardigan or shirt stays big and roomy: dropped shoulders, a wide body, long sleeves that
reach the knuckles and a hem at the hips; never shrink it into a cropped, short or fitted piece.
Soft drapey fabrics (thin cotton or rayon shirts, knits, satin) fall in soft folds and natural
creases, never crisp, stiff or freshly pressed. Sleeves relax slightly bent, never spread out stiffly.
Every garment has natural volume, like real clothes just laid down on the floor, never a flattened
cutout or a pressed paper shape: the fabric keeps its own thickness and there is a little air inside
the body and sleeves so they rise off the floor. Show it in each part: sleeves lie gently bent with
the elbow slightly lifted and a soft hollow at the cuff opening; the body of tops and outers is
slightly rounded at the chest with soft wrinkles at the waist; hems and cuffs curl or flip a little;
collars and lapels roll and stand slightly; knits look plump with visible thickness at their edges;
skirts and trousers keep soft rounded folds instead of being pressed flat. The side light makes this
visible: every garment casts a soft shadow along its edges on the floor and its folds have light and
shade. Keep it natural: no stuffed, puffy or inflated look, and the outline and size of each item
stay the same.
A knit or cardigan draped over the shoulders is laid the way a stylist does it: its body lies
hidden under the top, behind the shoulders, with only a narrow band of it showing above the top's
shoulders like a collar; its two sleeves come forward over the shoulders, cross on the upper chest
and tie in one loose knot, with the sleeve ends hanging a little. The top's front stays fully
visible below the knot; the draped knit's front opening, buttons and hem are never laid on top of
the top like a cape or a bib.

Composition like a professional outfit flat lay account: the clothes form one continuous figure from
the collar about 8 percent below the top edge to the trouser hem near the bottom edge, filling about
90 percent of the frame height, centered horizontally, with small even margins and no large empty
areas. The outfit never hugs the top edge: with a short skirt, shorts or a short outfit, the whole
figure (clothes and shoes together) moves down so the empty floor above the collar and below the
lowest item is about equal. The bag
lies close to the left side of the clothes at waist height, slightly overlapping the edge of the
clothes, and never lower than the knees. The shoes are one neat pair laid side by side,
parallel and pointing the same way, never splayed in a V, crossed or overlapping each other. With
full-length trousers or a long skirt they sit in the lower right beside the hem, not overlapping it;
with a short or knee-length skirt, shorts or any bottom that ends above the ankle they sit centered
on the empty floor directly below the hem. Only if jewelry or small accessories are in
the numbered list above, they sit in a neat vertical column on the right side at chest height (except a necklace, which is
drawn clearly visible, a little larger than the other jewelry, centered below the collar); if
the list has none, draw no jewelry, watch, rings, earrings or any other accessory at all. A scarf,
bandana or other neck accessory is never worn, tied or draped on the clothes: it lies on its own
like an accessory, flat or loosely folded in the shape of its product photos, in the empty floor
beside the top at neck height, not touching the clothes. Keep the lower left area of the frame (the bottom third, left of
the trouser legs) empty floor with no shoes, bag or accessories, because a title is placed there later.

Scene: $scene.
Every item casts a soft contact shadow on the floor, one consistent light direction for everything.
Vertical 4:5 image, high detail, no people, no mannequin, no hanger, no text, no numbers, no labels,
no watermark. Draw exactly the number of pieces stated for each item (a pair of earrings is two
earrings, a pair of shoes is two shoes), never an extra pair.
