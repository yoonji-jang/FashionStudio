"""인스타그램 게시 API (8단계). 캐러셀(2~10장) 또는 사진 한 장과 캡션을 올린다.

순서: 사진마다 캐러셀 항목 만들기 → 캐러셀 묶기(캡션) → 처리 끝날 때까지 기다리기
→ 게시 → 게시물 주소.
공개 작업이라 Claude 허용 목록에서 막혀 있다. 게시 job(tools.publish)이 부른다.
토큰은 Instagram API with Instagram Login 장기 토큰 (약 60일).
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx


class IGError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code

    @property
    def token_problem(self) -> bool:
        return self.code == 190  # 토큰 만료·취소


class Instagram:
    def __init__(
        self,
        user_id: str,
        token: str,
        api_base: str = "https://graph.instagram.com/v23.0",
        client: httpx.Client | None = None,
        wait_sec: float = 5,
        wait_tries: int = 36,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not user_id or not token:
            raise ValueError("인스타 사용자 ID 와 토큰이 필요합니다 (Secrets)")
        self.user_id, self.token = str(user_id), token
        self.base = api_base.rstrip("/")
        self.client = client or httpx.Client(timeout=60)
        self.wait_sec, self.wait_tries, self.sleep = wait_sec, wait_tries, sleep

    def _call(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        if method == "GET":
            r = self.client.get(f"{self.base}/{path}", params=params)
        else:
            r = self.client.post(f"{self.base}/{path}", data=params)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 300 or "error" in body:
            err = body.get("error") or {}
            msg = err.get("error_user_msg") or err.get("message") or r.text[:200]
            # 응답에 토큰이 되돌아오는 일은 없지만 혹시 몰라 가린다
            raise IGError(
                f"인스타 {path} 실패 ({r.status_code}): {msg}".replace(self.token, "***"),
                err.get("code"),
            )
        return body

    def publish_limit(self) -> tuple[int, int] | None:
        """(최근 24시간 게시 수, 한도). 못 읽으면 None."""
        try:
            data = self._call(
                "GET", f"{self.user_id}/content_publishing_limit", fields="quota_usage,config"
            )["data"][0]
            return int(data["quota_usage"]), int(data["config"]["quota_total"])
        except (IGError, KeyError, IndexError, ValueError):
            return None

    def _container(self, **params) -> str:
        return self._call("POST", f"{self.user_id}/media", **params)["id"]

    def wait(self, container: str) -> None:
        for _ in range(self.wait_tries):
            status = self._call("GET", container, fields="status_code,status").get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                detail = self._call("GET", container, fields="status").get("status", "")
                raise IGError(f"인스타가 사진을 처리하지 못했습니다 ({status}) {detail}")
            self.sleep(self.wait_sec)
        raise IGError("인스타 처리가 너무 오래 걸립니다 (잠시 뒤 다시 실행하세요)")

    def publish(self, image_urls: list[str], caption: str) -> str:
        """게시하고 게시물 id 를 돌려준다."""
        if not 1 <= len(image_urls) <= 10:
            raise ValueError(f"사진은 1~10장이어야 합니다 (지금 {len(image_urls)}장)")
        if len(image_urls) == 1:
            container = self._container(image_url=image_urls[0], caption=caption)
        else:
            children = [self._container(image_url=u, is_carousel_item="true") for u in image_urls]
            for c in children:
                self.wait(c)
            container = self._container(
                media_type="CAROUSEL", children=",".join(children), caption=caption
            )
        self.wait(container)
        return self._call("POST", f"{self.user_id}/media_publish", creation_id=container)["id"]

    def permalink(self, media_id: str) -> str | None:
        try:
            return self._call("GET", media_id, fields="permalink").get("permalink")
        except IGError:
            return None
