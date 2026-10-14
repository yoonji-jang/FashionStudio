<!--
하루 코디컷 지시문의 짧은 본문 (tools.outfit_gen, profile outfit_image.prompt). 상품 참고판을 번호 순서로 함께 보낸다.
$items 번호별 상품 설명과 실제 크기, $styling 입는 방법, $parts 그 벌에 해당하는 조각(prompts/outfit/, 고르는 기준은
prompts/outfit/parts.yaml), $layout 고정 구도(profile looks.composition), $scene 바닥·빛(profile outfit_image.scene).
2026-10-12 피드백 반영: 소매·바지 밑단 접지 않기, 와이드·부츠컷 밑단 유지, 허리 말지 않기, 신발은 짧은 하의 밑에.
2026-10-12 정리(스레드 "코디컷 이미지 피드백"): 2,500단어까지 길어져 이미지 AI 가 지시를 놓치고, 그 벌에 없는 치마·스카프
문장까지 매번 가고, 구도 문장이 서로 부딪혔다 → 본문은 짧게, 하의·신발·겹쳐 입기 같은 규칙은 해당할 때만 조각으로.
사용자 참고 코디컷(/mnt/project-files/image-feedback/참고_구도_1012/)처럼 반듯하고 단정하게: 다리 벌리기·휘기, 밑단 말림,
부위별 볼륨, 치마 밑단 들림 문장과 숫자 비율(허리 30%, 바지 1.6배, 여백 %)은 뺐다. 착용컷과 cm 가 비율을 정한다.
그동안의 규칙(9/30~10/12)은 git 기록과 /mnt/project-files/image-feedback/피드백_정리.md 에 있다.
사용자: 바지 기장을 길게, 모델컷과 비슷한 핏을 가장 중요하게 → 'Most important' 두 가지.
새 규칙은 덧붙이기보다 해당 조각의 문장을 고친다. 본문과 조각을 합친 지시문은 모든 조각을 합쳐도 1,000단어를 넘지 않는다(테스트가 본다).
이 주석은 전송되지 않는다.
2026-10-14 사용자 참고(/mnt/project-files/image-feedback/참고_1013/, wyeun_ AI 플랫레이): "자연스럽고 부피감 있게, 구도도"
→ 입은 모양 그대로 눕힌 입체감(never puffed up 뺌), 옷이 화면을 크게 채우기(왼쪽 아래 빈자리 규칙 뺌), 재질 결,
허리는 착용컷 높이(natural waist 는 패션 용어로 높은 허리라 뺌), 상품을 숫자 대신 "first sheet" 로 불러 숫자 그림 방지.

-->
Create one photorealistic top-down flat lay photograph of a complete outfit, laid out by a stylist
on the floor and photographed from directly above, like a premium brand's styling post.

The attached images are reference sheets of the exact items, in this order. Each sheet shows shop
photos of one item, flat or worn by a model; draw each item once and ignore any other clothes or
props in the photos:
$items

Most important, three things: (1) every item has the same fit as on the model in the worn photos:
the same silhouette, length, width, volume and drape. (2) Long trousers and long skirts are long:
exactly as long as on the model, covering the ankles, clearly the longest part of the outfit, with
the hem near the bottom edge of the frame; never cropped or shortened. (3) The clothes look full and
three-dimensional, as if a person had just been wearing them and they were laid down in that worn
shape: a rounded chest and shoulders, sleeves filled with air and falling softly with a slight bend,
trouser legs and skirts with real depth, thickness and soft vertical folds, knits thick and soft.
Keep every item identical to its photos: color, fabric and its texture (knit stitches, wool nap,
leather sheen, denim twill), pattern, cut, collar, neckline, number and position of buttons,
pockets, hardware and shoe style; never redesign a detail. The real sizes are for proportion only:
bottoms longer than tops, bag and shoes in true proportion to the clothes.

How the outfit is worn: $styling
Sleeves and collars hang as soft as in the photos: sleeves are never rolled or folded up.
Layering is physical: an inner piece lies under the piece worn over it and shows only at the
neckline, the cuffs and the hem. The waistband of the bottoms sits exactly at the height where it
sits on the model in the worn photos, never higher and never lower; the top keeps its full body
length from shoulder to waistband and is never shortened or squashed. A shirt worn tucked in has its
whole hem hidden inside the waistband with a soft blouse of fabric above it; a knit or T-shirt worn
tucked in has only the front tucked, relaxed and slightly bloused.
$parts

The garments lie neatly, never crumpled or twisted, never paper-flat.

Layout, the same for every image: $layout

Scene: $scene. One soft light for everything; every item casts a soft natural contact shadow.
Vertical 4:5 image, high detail. No people, mannequin or hanger. Never add text, letters, numbers,
labels or logos that are not on the product itself (the sizes are never drawn). Use only the
listed items, each exactly once: a pair of shoes is two shoes, a pair of earrings is two earrings,
and if no jewelry or accessory is listed, draw none.
