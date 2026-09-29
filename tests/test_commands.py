from pathlib import Path
import pytest
from echolivetui.commands import parse, execute
from echolivetui.config import Settings
from echolivetui.core import Core


@pytest.mark.parametrize("text,expected", [("//settings", "/settings"), ("///x", "//x"), ("hello", "hello")])
def test_literal_slash(text, expected):
    assert parse(text).args == [expected]
    assert parse(text).name == "text"


async def test_quote_and_failed_once(tmp_path):
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path)
    await execute(core, parse("/quote jp"))
    await execute(core, parse("/quote off"))
    await execute(core, parse("/quote on"))
    assert core.settings["message.quote_style"] == "jp"
    await execute(core, parse("/paren once"))
    with pytest.raises(ValueError):
        core.submit("keep me")
    assert core.paren_once
    with pytest.raises(ValueError, match="阶段二"):
        parse("/skip")


async def test_script_preflight(tmp_path):
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path)
    script = tmp_path / "commands.txt"
    script.write_text("/name Changed\n/skip", encoding="utf-8")
    with pytest.raises(ValueError, match="未执行"):
        await execute(core, parse('/source "' + script.as_posix() + '"'))
    assert core.settings["message.username"] == "Someone"


async def test_setting_conflict(tmp_path):
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path)
    baseline = dict(core.settings.values)
    await core.apply({"message.username": "Other"})
    with pytest.raises(ValueError, match="冲突"):
        await core.apply({"message.username": "Draft"}, baseline)


async def test_paren_only_changes_username(tmp_path):
    from echolivetui.pipeline import prepare
    core = Core(Settings(tmp_path / "s.yaml"), tmp_path)
    core.settings.values["message.typewriting"] = False
    await execute(core, parse("/paren off"))
    assert prepare("正文", core.settings).data["username"] == "Someone"
    await execute(core, parse("/paren once"))
    message = prepare("正文", core.settings, core.paren_once)
    assert message.data["username"] == "【Someone】"
    assert message.plain == '"正文"'
    assert core.settings["message.username_brackets"] is False
    await execute(core, parse("/paren on"))
    assert core.settings["message.username_brackets"] is True
    assert "message.paren" not in core.settings.values
