"""Searchable language choices and an ordered target list for settings drafts."""
from textual import on
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Select, Static

from .languages import language_label, language_options


class LanguageChoice(Vertical):
    class Changed(Message):
        def __init__(self, control):
            super().__init__()
            self._control, self.value = control, control.value

        @property
        def control(self):
            return self._control

    def __init__(self, value="", *, automatic=True, **kwargs):
        super().__init__(**kwargs)
        self.automatic, self._value = automatic, value

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._value = value
        if self.is_mounted:
            self.refresh_choices()

    def compose(self):
        yield Input(placeholder="搜索语言名称或代码", classes="language-search", compact=True)
        yield Select(language_options(automatic=self.automatic), value=self._value, allow_blank=False,
                     classes="language-choice", compact=True)

    def refresh_choices(self):
        choice = self.query_one(Select)
        options = language_options(self.query_one(Input).value, automatic=self.automatic)
        if self._value and self._value not in {code for _, code in options}:
            options.append((language_label(self._value), self._value))
        with choice.prevent(Select.Changed):
            choice.set_options(options)
            choice.value = self._value

    @on(Input.Changed)
    def searched(self, event):
        event.stop()
        self.refresh_choices()

    @on(Select.Changed)
    def selected(self, event):
        event.stop()
        if isinstance(event.value, str) and event.value != self._value:
            self._value = event.value
            self.post_message(self.Changed(self))


class TargetLanguages(Vertical):
    class Changed(Message):
        def __init__(self, control):
            super().__init__()
            self._control, self.value = control, control.value

        @property
        def control(self):
            return self._control

    def __init__(self, value, **kwargs):
        super().__init__(**kwargs)
        self._value = list(value)

    @property
    def value(self):
        return self._value.copy()

    @value.setter
    def value(self, value):
        self._value = list(value)
        if self.is_mounted:
            self.refresh_rows()

    def compose(self):
        yield Vertical(classes="target-rows")
        yield LanguageChoice("zh-Hans", automatic=False, classes="target-picker")
        yield Button("添加语言", name="add", classes="target-add")

    def on_mount(self):
        self.refresh_rows()

    def refresh_rows(self):
        container = self.query_one(".target-rows", Vertical)
        container.remove_children()
        rows = []
        for index, code in enumerate(self._value):
            rows.append(Horizontal(Static(language_label(code)),
                                   Button("↑", name=f"up:{index}", disabled=index == 0),
                                   Button("↓", name=f"down:{index}", disabled=index == len(self._value) - 1),
                                   Button("删除", name=f"remove:{index}"), classes="target-row"))
        container.mount(*rows)
        self.query_one(".target-add", Button).disabled = self.query_one(LanguageChoice).value in self._value

    @on(LanguageChoice.Changed)
    def choice_changed(self, event):
        event.stop()
        self.query_one(".target-add", Button).disabled = event.value in self._value

    @on(Button.Pressed)
    def change(self, event):
        event.stop()
        action = event.button.name
        if action == "add":
            code = self.query_one(LanguageChoice).value
            if code in self._value:
                return
            self._value.append(code)
        else:
            action, raw_index = action.split(":")
            index = int(raw_index)
            if action == "remove":
                self._value.pop(index)
            else:
                other = index + (1 if action == "down" else -1)
                if not 0 <= other < len(self._value):
                    return
                self._value[index], self._value[other] = self._value[other], self._value[index]
        self.refresh_rows()
        self.post_message(self.Changed(self))
