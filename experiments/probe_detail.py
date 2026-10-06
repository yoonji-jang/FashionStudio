"""상세 본문(실측표) 구조 확인용 임시 스크립트. 구조(키 이름·이미지 개수·이미지 주소)만 출력하고
페이지 원본이나 글 내용은 저장·출력하지 않는다 (판매자 정보가 섞일 수 있음)."""

import json
import re
import sys

from sources import zigzag
from tools import scout

IMG = re.compile(r"https?://[^\s\"'<>\\]+?\.(?:jpe?g|png|webp|gif)", re.I)


def walk(obj, path, out, depth=0):
    if depth > 6:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{path}.{k}", out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            walk(v, f"{path}[{i}]", out, depth + 1)
    elif isinstance(obj, str):
        urls = IMG.findall(obj)
        if urls:
            key = re.sub(r"\[\d+\]", "[]", path)
            out.setdefault(key, []).extend(urls)


c = scout.client()
for url in sys.argv[1:]:
    code, html = scout.fetch_page(c, url)
    print("==", url, code, "html", len(html), "img tags", html.count("<img"))
    m = zigzag._NEXT_DATA.search(html)
    nd = json.loads(m.group(1))
    queries = nd["props"]["pageProps"]["dehydratedState"]["queries"]
    for q in queries:
        print(" query", json.dumps(q.get("queryKey"), ensure_ascii=False)[:120])
    data = zigzag._page_data(html)
    print(" product keys", sorted(data["product"].keys()))
    found = {}
    walk(nd, "", found)
    for key, urls in found.items():
        uniq = list(dict.fromkeys(urls))
        print(f" {key}: {len(uniq)} images, e.g. {uniq[:3]}")
    # NEXT_DATA 밖의 HTML 속 이미지 (본문이 따로 렌더링될 때)
    outside = list(dict.fromkeys(IMG.findall(html.replace(m.group(1), ""))))
    print(" html outside next_data:", len(outside), outside[:5])
