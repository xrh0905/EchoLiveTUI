import pytest
from echolivetui.config import Settings, coerce
from echolivetui.protocol import Profile, target_matches, validate_envelope
from echolivetui.pipeline import prepare


def test_settings_atomic_roundtrip_and_invalid_preservation(tmp_path):
    path = tmp_path / "settings.yaml"
    s = Settings(path)
    values = dict(s.values, **{"message.username": "名字"})
    s.save(values)
    assert Settings(path)["message.username"] == "名字"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        s.save(dict(values, **{"listen.port": 0}))
    assert path.read_bytes() == before
    path.write_text("bad: [", encoding="utf-8")
    s = Settings(path)
    assert s.load_error
    with pytest.raises(ValueError):
        s.save()
    assert path.read_text() == "bad: ["


@pytest.mark.parametrize("target,result", [(None, True), ("@__live", True), ("@__history", False), ("@Main", True), ("-other", True), (["-id", "@__live"], False), (["@__live", "-id"], True), (["-other", "missing"], False), ([], False)])
def test_ordered_targets(target, result):
    assert target_matches(target, Profile("id", "Main", "live")) == result


def test_targeted_and_unknown():
    p = Profile("id", "Main", "live", targeted=True)
    assert not target_matches(None, p)
    assert target_matches("@__live", p)
    assert target_matches("id", p)
    assert target_matches("@Main", p)
    assert not Profile("u").accepts("message_data")
    with pytest.raises(ValueError):
        validate_envelope({"action": "hello", "from": {"uuid": "x"}, "target": {"uuid": "x"}})


def test_profile_evidence():
    p = Profile("x")
    p.identify({"action": "ping", "from": {"type": "server"}})
    assert p.role == "server" and p.version is None
    p.identify({"action": "hello", "from": {"type": "live"}}, "1.6.6", "live")
    assert p.capabilities["typing"] is False
    p.identify({"action": "hello", "from": {"type": "live"}}, "1.8.12", "live")
    assert p.capabilities["typing"] is True


def test_pipeline_once_wrap(tmp_path):
    s = Settings(tmp_path / "s.yaml")
    s.values.update({"message.typewriting": False, "message.username_brackets": False})
    p = prepare("Hello", s, True)
    assert p.plain == '"Hello"'
    assert p.original == "Hello"
    assert p.data["username"] == "【Someone】"
    assert prepare("Hello", s).data["username"] == "Someone"


def test_strict_values():
    assert coerce("typing.enable", "off") is False
    for value in (True, 0, 65536, "oops"):
        with pytest.raises(ValueError):
            coerce("listen.port", value)
