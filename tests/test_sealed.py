import base64

from tools import scout, sealed


def test_seal_roundtrip_and_ref_photo(tmp_path, monkeypatch):
    seed = "secret-seed"
    pub = sealed.private_key(seed).public_key
    box = sealed.seal(b"jpeg-bytes", pub)
    assert box != b"jpeg-bytes"
    assert sealed.unseal(box, seed) == b"jpeg-bytes"

    src = tmp_path / "src"
    src.mkdir()
    (src / "r1.sealed").write_bytes(box)
    (src / "bad.sealed").write_bytes(b"x")
    cache = tmp_path / "cache"
    assert sealed.open_dir(src, cache, seed) == 1

    monkeypatch.setenv("USER_REFS_DIR", str(cache))
    dest = tmp_path / "out" / "ref.jpg"
    assert scout.ref_photo("sealed:r1", [], dest) == dest
    assert dest.read_bytes() == b"jpeg-bytes"
    try:
        scout.ref_photo("sealed:nope", [], tmp_path / "x.jpg")
        raise AssertionError
    except scout.ImageUnavailable:
        pass
    assert scout.user_ref("sealed:r1") == "sealed:r1"
    assert scout.user_ref("abc") == "tg:abc"


def test_pubkey_cli(monkeypatch, capsys):
    monkeypatch.setenv(sealed.SEED_ENV, "s")
    assert sealed.main(["pubkey"]) == 0
    out = capsys.readouterr().out.strip()
    assert base64.b64decode(out) == bytes(sealed.private_key("s").public_key)
