import copy
from unittest.mock import Mock

import pytest

from echolivetui.config import Settings
from echolivetui.core import Core
from echolivetui.hub import Peer
from echolivetui.message import typewrite_incoming
from echolivetui.pipeline import prepare
from echolivetui.protocol import Profile


@pytest.fixture
def settings(tmp_path):
    settings = Settings(tmp_path / "s.yaml")
    settings.values.update({"message.quote": False, "message.typewriting": False})
    return settings


def test_optional_tracks_keep_original_call_compatible(settings):
    assert "track" not in prepare("main", settings).data["messages"][0]
    assert prepare("main", settings, tracks=[]).data["messages"][0]["track"] == []


def test_tracks_preserve_wire_fields_without_main_text_enhancements(settings):
    settings.values.update({"message.quote": True, "message.suffix": True,
                            "message.suffix_value": "!", "message.autopause": True})
    tracks = [{"message": "@^literal，", "custom": ["extension"]},
              {"message": [{"text": "中文", "style": {"sub": True}, "class": "custom",
                            "speed": 42, "pause": 3, "event": "ignored-by-upstream",
                            "data": {"emoji": "smile"}}], "data": {"printSpeed": 90}}]
    before = copy.deepcopy(tracks)
    prepared = prepare("main", settings, tracks=tracks)
    children = prepared.data["messages"][0]["track"]
    assert prepared.plain == "「main!」"
    assert children[0] == {**tracks[0], "data": {"printSpeed": settings["message.print_speed"]}}
    assert children[1] == tracks[1]
    children[0]["custom"].append("new")
    children[1]["message"][0]["style"]["sub"] = False
    assert tracks == before


@pytest.mark.parametrize("scheme", ["pinyin", "zhuyin"])
def test_typing_visits_main_and_one_level_of_subtracks(settings, scheme):
    settings.values.update({"message.typewriting": True, "message.typewriting_scheme": scheme})
    source = {"username": "editor", "messages": [{"message": "主轨", "track": [
        {"message": "中文————……👩‍💻再见", "data": {"printSpeed": 42}},
        {"message": [{"text": "现有", "typewrite": "editor", "style": {"super": True}},
                     {"text": "注音", "style": {"sub": True}, "speed": 60},
                     {"text": "", "pause": 3}, {"text": "", "data": {"image": {"url": "test.png"}}}]}]}]}
    before = copy.deepcopy(source)
    incoming = typewrite_incoming(source, scheme)
    local = prepare("主轨", settings, tracks=source["messages"][0]["track"])
    for children in (incoming["messages"][0]["track"], local.data["messages"][0]["track"]):
        parts = children[0]["message"]
        assert [p["text"] for p in parts] == ["中文", "————……👩‍💻", "再见"]
        assert parts[0]["typewrite"] and parts[2]["typewrite"]
        assert "typewrite" not in parts[1]
        assert children[0]["data"]["printSpeed"] == 42
        styled = children[1]["message"]
        assert styled[0] == before["messages"][0]["track"][1]["message"][0]
        assert styled[1]["style"]["sub"] and styled[1]["speed"] == 60 and styled[1]["typewrite"]
        assert styled[2:] == before["messages"][0]["track"][1]["message"][2:]
    assert incoming["messages"][0]["message"][0]["typewrite"]
    assert source == before


def test_delay_uses_slowest_parallel_track_with_segment_speed_and_pauses(settings):
    tracks = [{"message": [{"text": "abc", "speed": 50, "pause": 2}], "data": {"printSpeed": 100}},
              {"message": "ab", "data": {"printSpeed": 200}}]
    assert prepare("x", settings, tracks=tracks).delay == .4
    tracks[0]["message"][0]["pause"] = 10
    assert prepare("x", settings, tracks=tracks).delay == 1.15
    # Phonetics can take much longer than the single committed character.
    assert prepare("x", settings, tracks=[{"message": {"text": "中", "typewrite": "abcdefghij", "speed": 100}}]).delay == 1.1


@pytest.mark.parametrize("tracks", ["bad", ["bad"], [{}], [{"message": None}],
                                  [{"message": "child", "track": []}],
                                  [{"message": "child", "data": "bad"}]])
def test_invalid_tracks_fail_before_accepting_or_consuming_state(settings, tmp_path, tracks):
    core = Core(settings, tmp_path)
    core.paren_once = True
    live = Peer(None, Profile("live", role="live"))
    core.hub.peers["live"] = live
    with pytest.raises(ValueError):
        core.submit("main", tracks=tracks)
    assert core.paren_once and live.queue.empty()


def test_submit_broadcasts_tracks_but_history_and_osc_receive_main_only(settings, tmp_path, monkeypatch):
    settings.values.update({"osc.enable": True, "history.hide_latest": False})
    core = Core(settings, tmp_path)
    osc = Mock()
    monkeypatch.setattr("echolivetui.osc.SimpleUDPClient", lambda *args: osc)
    for uid in ("one", "two", "history"):
        core.hub.peers[uid] = Peer(None, Profile(uid, role="history" if uid == "history" else "live"))
    receipts = []
    tracks = [{"message": "child"}]
    core.submit("main", tracks=tracks, playback=receipts)
    assert len(receipts) == 2 and all(r.message == "main" for r in receipts)
    for uid in ("one", "two"):
        envelope, delay = core.hub.peers[uid].queue.get_nowait()
        assert envelope["data"]["messages"][0]["track"][0]["message"] == "child"
    history, _ = core.hub.peers["history"].control.get_nowait()
    assert history["data"]["message"] == "main"
    assert core.hub.peers["history"].control.empty()
    osc.send_message.assert_called_once_with("/chatbox/input", ["main", True])
