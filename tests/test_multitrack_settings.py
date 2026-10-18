import copy

import pytest
from textual.widgets import Button, Input, Select

from echolivetui.commands import parse
from echolivetui.config import FIELDS, Settings, coerce
from echolivetui.language_controls import LanguageChoice, TargetLanguages
from echolivetui.languages import language_options
from echolivetui.ui import EchoApp


def test_language_order_search_and_config_isolation(tmp_path):
    assert [code for _, code in language_options()][:4] == ["zh-Hans", "en", "ja", "ru"]
    for search in ("ja", "日本語", "日语"):
        assert "ja" in [code for _, code in language_options(search)]
    assert "ja" not in [code for _, code in language_options("jp")]
    assert language_options(automatic=True)[0][1] == ""
    one, two = Settings(tmp_path / "one.yaml"), Settings(tmp_path / "two.yaml")
    one.values["multitrack.target_languages"].append("en")
    assert two["multitrack.target_languages"] == FIELDS["multitrack.target_languages"].default == []
    values = copy.deepcopy(one.values)
    values.update({"multitrack.enable": True, "multitrack.target_languages": ["ja", "en"]})
    one.save(values)
    values["multitrack.target_languages"].append("ru")
    assert one["multitrack.target_languages"] == ["ja", "en"]
    assert Settings(one.path)["multitrack.target_languages"] == ["ja", "en"]
    assert parse("/settings multitrack").args == ["multitrack"]
    assert coerce("multitrack.target_languages", "en,ja") == ["en", "ja"]
    with pytest.raises(ValueError, match="至少"):
        two.save({**two.values, "multitrack.enable": True})


@pytest.mark.parametrize("value", [["jp"], ["en", "en"], [1], None])
def test_invalid_language_lists_rejected(value):
    with pytest.raises(ValueError):
        coerce("multitrack.target_languages", value)


@pytest.mark.parametrize("width", [80, 40])
async def test_language_settings_add_order_delete_reset_and_save(tmp_path, width):
    settings = Settings(tmp_path / "s.yaml")
    app = EchoApp(settings, tmp_path, start_server=False)
    async with app.run_test(size=(width, 32)) as pilot:
        app.open_screen("settings", ["multitrack"])
        await pilot.pause()
        page = app.active_page
        source = page.query_one("#field-multitrack-source_language", LanguageChoice)
        source.query_one(Input).value = "日本語"
        await pilot.pause()
        source.query_one(Select).value = "ja"
        targets = page.query_one(TargetLanguages)
        picker = targets.query_one(LanguageChoice)
        for code in ("en", "ja"):
            picker.query_one(Select).value = code
            await pilot.pause()
            targets.query_one(".target-add", Button).press()
            await pilot.pause()
        assert targets.value == ["en", "ja"]
        assert settings["multitrack.target_languages"] == []
        assert app.settings_base["multitrack.target_languages"] == []
        next(b for b in targets.query(Button) if b.name == "up:1").press()
        await pilot.pause()
        assert targets.value == ["ja", "en"]
        next(b for b in targets.query(Button) if b.name == "remove:1").press()
        await pilot.pause()
        assert targets.value == ["ja"]
        page.query_one("#field-multitrack-enable", Select).value = True
        await pilot.pause()
        page.query_one("#save", Button).press()
        await pilot.pause()
        assert app.active_page is None
        assert settings["multitrack.source_language"] == "ja"
        assert Settings(settings.path)["multitrack.target_languages"] == ["ja"]
        app.open_screen("settings", ["multitrack"])
        await pilot.pause()
        app.active_page.query_one("#reset", Button).press()
        await pilot.pause()
        assert app.active_page.query_one(TargetLanguages).value == []
        assert app.settings_draft["multitrack.source_language"] == ""
        assert not app.settings_draft["multitrack.enable"]
        assert settings["multitrack.target_languages"] == ["ja"]
