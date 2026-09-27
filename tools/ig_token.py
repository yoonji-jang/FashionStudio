r"""인스타 장기 토큰 연장 (9단계). 외부로 보내는 도구라 Claude 허용 목록에 없다.

Instagram API with Instagram Login 장기 토큰은 약 60일 뒤 끝난다. 발급 24시간 뒤부터 연장할 수 있고,
연장하면 다시 60일이 된다. token 워크플로가 매주(profile schedule.token_refresh_at) 부른다.

연장 결과로 토큰 문자열이 바뀌면 GitHub Secret 을 새 값으로 바꿔야 한다.
Secret SECRETS_TOKEN (이 저장소 Secrets 쓰기 권한이 있는 fine-grained 토큰)이 있으면
자동으로 바꾸고,
없으면 텔레그램으로 알린다. 토큰 값은 어디에도 출력하거나 보내지 않는다.

사용법:
  python -m tools.ig_token refresh --account women
환경 변수: IG_TOKEN (지금 토큰), GITHUB_REPOSITORY, SECRETS_TOKEN(선택),
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from datetime import date, timedelta
from urllib.parse import urlsplit

import httpx

from core.profile import load_profile
from tools import notify
from tools.ig_publish import IGError


def refresh(token: str, api_base: str, client: httpx.Client | None = None) -> tuple[str, int]:
    """(새 토큰, 남은 초)."""
    parts = urlsplit(api_base)
    if parts.netloc != "graph.instagram.com":
        raise ValueError("토큰 연장은 Instagram Login 토큰(graph.instagram.com)만 지원합니다")
    client = client or httpx.Client(timeout=30)
    r = client.get(
        f"{parts.scheme}://{parts.netloc}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
    )
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 300 or "access_token" not in body:
        err = body.get("error") or {}
        msg = (err.get("message") or r.text[:200]).replace(token, "***")
        raise IGError(f"토큰 연장 실패 ({r.status_code}): {msg}", err.get("code"))
    return body["access_token"], int(body.get("expires_in") or 0)


def put_secret(
    repo: str, name: str, value: str, gh_token: str, client: httpx.Client | None = None
) -> None:
    """GitHub Actions Secret 을 바꾼다 (저장소 공개 키로 암호화해서 보냄)."""
    from nacl import encoding, public

    client = client or httpx.Client(timeout=30)
    headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}
    base = f"https://api.github.com/repos/{repo}/actions/secrets"
    r = client.get(f"{base}/public-key", headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"저장소 공개 키를 읽지 못했습니다 ({r.status_code})")
    key = r.json()
    box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    sealed = base64.b64encode(box.encrypt(value.encode())).decode()
    r = client.put(
        f"{base}/{name}",
        headers=headers,
        json={"encrypted_value": sealed, "key_id": key["key_id"]},
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Secret {name} 을 바꾸지 못했습니다 ({r.status_code})")


def run(account: str, env: dict | None = None, client: httpx.Client | None = None) -> str:
    """연장하고 결과 한 줄을 돌려준다."""
    env = env if env is not None else dict(os.environ)
    profile = load_profile(account)
    secret = profile.instagram.token_secret
    token = env.get("IG_TOKEN") or env.get(secret, "")
    if not token:
        raise ValueError(f"인스타 토큰이 없습니다 (Secret {secret})")
    api_base = (
        notify.publish_config()
        .get("instagram", {})
        .get("api_base", "https://graph.instagram.com/v23.0")
    )
    new, seconds = refresh(token, api_base, client)
    until = date.today() + timedelta(seconds=seconds)
    if new == token:
        return f"[{account}] 인스타 토큰을 연장했어요 (만료 {until})"
    gh_token, repo = env.get("SECRETS_TOKEN", ""), env.get("GITHUB_REPOSITORY", "")
    if gh_token and repo:
        put_secret(repo, secret, new, gh_token, client)
        return (
            f"[{account}] 인스타 토큰을 연장하고 Secret {secret} 을 새 값으로 바꿨어요 "
            f"(만료 {until})"
        )
    raise RuntimeError(
        f"[{account}] 인스타 토큰을 연장했지만 새 값이라 Secret {secret} 을 바꿔야 해요. "
        "SECRETS_TOKEN 을 등록하면 다음부터 자동으로 바꿔요 (README 참고). "
        f"지금 Secret 의 토큰은 원래 만료일까지 쓸 수 있어요."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("refresh")
    s.add_argument("--account", required=True)
    args = ap.parse_args(argv)
    tg = None
    try:
        tg = notify.Telegram.from_env()
    except ValueError:
        pass
    try:
        print(run(args.account))
        return 0
    except (ValueError, RuntimeError, IGError, httpx.HTTPError) as e:
        msg = str(e)
        if isinstance(e, IGError) and e.token_problem:
            msg += "\n토큰이 이미 만료됐거나 취소됐어요. 새 토큰을 Secret 에 다시 등록해 주세요."
        print(msg, file=sys.stderr)
        if tg is not None:
            try:
                tg.send(msg)
            except (RuntimeError, httpx.HTTPError):
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
