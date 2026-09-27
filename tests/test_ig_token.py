import base64
import json

import httpx
import pytest
from nacl import public

from tools import ig_token
from tools.ig_publish import IGError


class Apis:
    def __init__(self, new_token: str):
        self.new_token = new_token
        self.key = public.PrivateKey.generate()
        self.put: dict | None = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.host == "graph.instagram.com":
            assert req.url.params["grant_type"] == "ig_refresh_token"
            if req.url.params["access_token"] == "DEAD":
                err = {"message": "Error validating access token DEAD", "code": 190}
                return httpx.Response(400, json={"error": err})
            return httpx.Response(200, json={"access_token": self.new_token, "expires_in": 5184000})
        if req.url.path.endswith("/public-key"):
            pk = base64.b64encode(bytes(self.key.public_key)).decode()
            return httpx.Response(200, json={"key_id": "k1", "key": pk})
        self.put = json.loads(req.content)
        return httpx.Response(201)


def client(api):
    return httpx.Client(transport=httpx.MockTransport(api))


def test_same_token_just_extends():
    api = Apis("OLD")
    msg = ig_token.run("women", {"IG_TOKEN": "OLD"}, client(api))
    assert "연장했어요" in msg and api.put is None


def test_new_token_updates_secret_encrypted():
    api = Apis("NEW")
    env = {"IG_TOKEN": "OLD", "SECRETS_TOKEN": "gh", "GITHUB_REPOSITORY": "me/repo"}
    msg = ig_token.run("women", env, client(api))
    assert "IG_WOMEN_TOKEN" in msg and "NEW" not in msg
    sealed = base64.b64decode(api.put["encrypted_value"])
    assert public.SealedBox(api.key).decrypt(sealed) == b"NEW"


def test_new_token_without_secrets_token_asks_user():
    with pytest.raises(RuntimeError, match="SECRETS_TOKEN") as e:
        ig_token.run("women", {"IG_TOKEN": "OLD"}, client(Apis("NEW")))
    assert "NEW" not in str(e.value)


def test_expired_token_hides_value():
    with pytest.raises(IGError) as e:
        ig_token.run("women", {"IG_TOKEN": "DEAD"}, client(Apis("X")))
    assert e.value.token_problem and "DEAD" not in str(e.value)
