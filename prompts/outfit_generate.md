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
-->
Create one photorealistic top-down flat lay photograph of a complete outfit, laid out neatly on the
floor by a stylist and photographed from directly above.

The attached images are reference sheets of the exact items, in this order. Each sheet shows shop
photos of one item, flat or worn by a model; draw each item once and ignore any other clothes or
props in the photos:
$items

Most important, two things: (1) every item has the same fit as on the model in the worn photos: the
same silhouette, length, width, volume and drape, and the top meets the bottoms at the same place as
on the model. (2) Long trousers and long skirts are long: exactly as long as on the model, covering
the ankles, they are the longest part of the outfit, clearly longer than any top or jacket, with the
hem near the bottom edge of the frame; never cropped or shortened. Keep every item identical to its photos: color, fabric, pattern, cut, collar, neckline,
number and position of buttons, pockets, hardware and shoe style; never redesign a detail. Draw
every item at its real size given above, all at one common scale, so the bottoms keep their full
length and are never shortened.

How the outfit is worn: $styling
Sleeves and collars hang as soft as in the photos: sleeves are never rolled or folded up, a collar is never stiff.
Layering is physical: an inner piece lies under the piece worn over it and shows only at the
neckline, the cuffs and the hem. The waistband of the bottoms sits at the natural waist, where it
sits on the model, never pulled up to the chest. A top worn tucked in has its whole hem hidden inside
the waistband, with only a soft blouse of fabric above it and nothing hanging out at the sides; the waist is never rolled.
$parts

Lay it out like a clean product flat lay: the garments lie straight and nearly symmetric, sleeves
relaxed along the sides, with soft natural folds, real fabric thickness and soft shadows at the
edges; never crumpled, twisted, puffed up or paper-flat.

Layout, the same for every image: $layout

Scene: $scene. One light direction for everything; every item casts a soft contact shadow.
Vertical 4:5 image, high detail. No people, mannequin or hanger. Never add text, letters, numbers,
labels or logos that are not on the product itself (the cm values are never drawn). Use only the
listed items, each exactly once: a pair of shoes is two shoes, a pair of earrings is two earrings,
and if no jewelry or accessory is listed, draw none.
