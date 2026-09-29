"""인스타가 가져갈 사진을 잠시 올려 두는 공개 저장 위치 (8단계).

인스타 게시 API 는 사진 파일을 직접 받지 않고 공개 주소(https)로만 받는다. 이 저장소는 비공개라서
사진만 따로 공개 GitHub 저장소(기본: <이 저장소>-media)에 올리고, 게시가 끝나면 지운다.
설정은 config/publish.yaml 의 image_host. 외부로 올리는 도구라 Claude 허용 목록에 없다.

주소는 커밋 번호가 들어간 raw.githubusercontent.com 주소라서 나중에 파일을 지워도 인스타가 받는 동안
바뀌지 않는다. 지운 뒤에도 공개 저장소 기록에는 남지만, 이미 인스타에 공개한 사진이다.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

GITHUB_API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"


@dataclass
class Uploaded:
    path: str  # 저장소 안 경로
    url: str  # 인스타에 넘길 공개 주소
    sha: str  # 파일 sha (지울 때 필요)


class GitHubHost:
    def __init__(
        self,
        repo: str,
        token: str,
        branch: str = "main",
        client: httpx.Client | None = None,
        pages_base: str = "",
    ):
        if not repo or "/" not in repo:
            raise ValueError(
                f"사진 저장소 이름이 이상합니다: {repo!r} (예: owner/FashionStudio-media)"
            )
        if not token:
            raise ValueError("사진 저장소 토큰이 없습니다 (Secret MEDIA_REPO_TOKEN)")
        self.repo, self.branch = repo, branch
        self.pages_base = pages_base
        self.client = client or httpx.Client(timeout=60)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @classmethod
    def from_config(cls, cfg: dict, client: httpx.Client | None = None) -> GitHubHost:
        repo = cfg.get("repo") or ""
        if not repo and os.environ.get("GITHUB_REPOSITORY"):
            repo = os.environ["GITHUB_REPOSITORY"] + "-media"
        token = os.environ.get("MEDIA_REPO_TOKEN", "")
        return cls(repo, token, cfg.get("branch", "main"), client, cfg.get("pages_base") or "")

    def _check(self, r: httpx.Response, what: str) -> dict:
        if r.status_code >= 300:
            msg = r.json().get("message", "") if "json" in r.headers.get("content-type", "") else ""
            hint = ""
            if r.status_code in (401, 403, 404):
                hint = " (저장소 이름, 공개 여부, 토큰 권한 Contents: Read and write 를 확인하세요)"
            raise RuntimeError(f"사진 저장소 {what} 실패 ({r.status_code}) {msg}{hint}")
        return r.json() if r.content else {}

    def upload(self, files: list[Path], prefix: str) -> list[Uploaded]:
        done: list[Uploaded] = []
        for i, f in enumerate(files, 1):
            # 인스타가 받기 쉬운 ASCII 이름으로 올린다 (슬라이드 파일 이름은 한글)
            path = f"{prefix.strip('/')}/{i:02d}{f.suffix.lower()}"
            url = f"{GITHUB_API}/repos/{self.repo}/contents/{path}"
            body = {
                "message": f"게시용 사진 {path}",
                "content": base64.b64encode(f.read_bytes()).decode(),
                "branch": self.branch,
            }
            old = self.client.get(url, headers=self.headers, params={"ref": self.branch})
            if old.status_code == 200:  # 같은 게시물을 다시 올리는 경우
                body["sha"] = old.json()["sha"]
            data = self._check(self.client.put(url, headers=self.headers, json=body), "올리기")
            commit = data["commit"]["sha"]
            done.append(
                Uploaded(path, f"{RAW}/{self.repo}/{commit}/{path}", data["content"]["sha"])
            )
        return done

    def put(self, path: str, data: bytes, message: str) -> None:
        """파일 하나를 쓰거나 바꾼다 (상품 페이지)."""
        url = f"{GITHUB_API}/repos/{self.repo}/contents/{path}"
        body = {
            "message": message,
            "content": base64.b64encode(data).decode(),
            "branch": self.branch,
        }
        old = self.client.get(url, headers=self.headers, params={"ref": self.branch})
        if old.status_code == 200:
            if base64.b64decode(old.json().get("content", "")) == data:
                return  # 그대로
            body["sha"] = old.json()["sha"]
        self._check(self.client.put(url, headers=self.headers, json=body), "쓰기")

    def remove(self, path: str, message: str) -> bool:
        """파일 하나를 지운다 (상품 페이지 내리기). 없으면 False."""
        url = f"{GITHUB_API}/repos/{self.repo}/contents/{path}"
        old = self.client.get(url, headers=self.headers, params={"ref": self.branch})
        if old.status_code == 404:
            return False
        body = {"message": message, "sha": self._check(old, "확인")["sha"], "branch": self.branch}
        r = self.client.request("DELETE", url, headers=self.headers, json=body)
        if r.status_code == 404:
            return False
        self._check(r, "지우기")
        return True

    def pages_url(self, path: str = "") -> str:
        """GitHub Pages 주소. 개인 도메인을 연결했으면 config 의 pages_base 를 쓴다."""
        if self.pages_base:
            return f"{self.pages_base.rstrip('/')}/{path}"
        owner, name = self.repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/{path}"

    def delete(self, items: list[Uploaded]) -> None:
        for it in items:
            url = f"{GITHUB_API}/repos/{self.repo}/contents/{it.path}"
            body = {
                "message": f"게시 끝난 사진 지움 {it.path}",
                "sha": it.sha,
                "branch": self.branch,
            }
            r = self.client.request("DELETE", url, headers=self.headers, json=body)
            if r.status_code != 404:
                self._check(r, "지우기")
