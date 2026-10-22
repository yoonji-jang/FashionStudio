<!--
/fix 고쳐 그리기 지시문 (tools.outfit_gen.edit_prompt, 2026-10-13 사용자: /fix 는 새로 그리지 말고 고쳐 그리기).
첫 그림은 지난 코디컷, 그 뒤는 상품 참고판(번호 순서). $request 고칠 점(영어, fix-feed 스킬이 씀), $items 2번부터의 상품 설명.
$kind 그림 종류, $keep 그대로 둘 배경 (펼친 코디컷·장면 모델컷마다 다름, tools.outfit_gen.EDIT_KINDS).
$extra 상품 참고판 뒤에 붙는 기준 코디 사진·사용자 참고 사진 설명 (없으면 빈칸, 2026-10-22).
괜찮던 부분이 그대로 남도록 고칠 곳 말고는 바꾸지 말라고 한다. 이 주석은 전송되지 않는다.
-->
Edit the first attached image, $kind.
Change only this: $request

Make the change fully and clearly, even if it means moving or redrawing whole items: a change to the
layout or proportions really moves the items to their new places. Keep everything the change does not
touch exactly as it is: every item's color, pattern, fabric and details, $keep. Do not add or remove any item unless the change asks for it.

The other attached images are reference sheets of the exact items, in this order. Use them only to keep
each item true to the real product while making the change:
$items
$extra

No text, letters, logos, labels or watermarks anywhere in the image.
