"""Validated shared settings, with atomic persistence and optimistic drafts."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from dataclasses import dataclass
import tempfile
import yaml


@dataclass(frozen=True)
class Field:
    default: object
    label: str
    choices: tuple = ()
    minimum: int | None = None
    maximum: int | None = None

    def effect(self, key):
        if key.startswith("listen."):
            return "保存后重建监听" if key != "listen.public_host" else "仅改变显示地址"
        if key == "typing.enable":
            return "立即生效；托管网页需刷新"
        return "下一条消息生效" if key.startswith("message.") else "立即生效"


FIELDS = {
    "input.interrupt_guard": Field(True, "防止 Ctrl+C 退出"),
    "listen.host": Field("127.0.0.1", "监听地址"),
    "listen.port": Field(3000, "监听端口", minimum=1, maximum=65535),
    "listen.public_host": Field("", "显示地址"),
    "message.username": Field("Someone", "说话人"),
    "message.username_brackets": Field(True, "姓名括号【】"),
    "message.quote": Field(True, "引号"),
    "message.quote_style": Field("en", "引号样式", ("en", "cn", "jp", "custom")),
    "message.quote_open": Field("", "自定义左引号"),
    "message.quote_close": Field("", "自定义右引号"),
    "message.suffix": Field(False, "后缀"),
    "message.suffix_value": Field("喵", "后缀内容"),
    "message.typewriting": Field(True, "模拟打字"),
    "message.typewriting_scheme": Field("pinyin", "模拟打字方案", ("pinyin", "zhuyin")),
    "message.print_speed": Field(10, "打印间隔 ms", minimum=1, maximum=60000),
    "message.autopause": Field(False, "自动停顿"),
    "message.autopausestr": Field(",，.。;；:：!！", "停顿字符"),
    "message.autopausetime": Field(10, "停顿时长", minimum=0, maximum=60000),
    "typing.enable": Field(False, "输入提示"),
    "log.level": Field("error", "日志级别", ("error", "info", "debug")),
    "history.hide_latest": Field(False, "暂存最新一条历史"),
    "osc.enable": Field(False, "VRChat OSC"),
    "osc.host": Field("127.0.0.1", "OSC 地址"),
    "osc.port": Field(9000, "OSC 端口", minimum=1, maximum=65535),
}


def coerce(key: str, value):
    if key not in FIELDS:
        raise ValueError(f"未知设置：{key}")
    f = FIELDS[key]
    if isinstance(f.default, bool):
        if isinstance(value, str) and value.lower() in ("on", "off", "true", "false"):
            value = value.lower() in ("on", "true")
        if type(value) is not bool:
            raise ValueError(f"{key} 需要 on/off")
    elif isinstance(f.default, int):
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                raise ValueError(f"{key} 需要整数") from None
        if type(value) is not int or not f.minimum <= value <= f.maximum:
            raise ValueError(f"{key} 范围 {f.minimum}–{f.maximum}")
    elif not isinstance(value, str):
        raise ValueError(f"{key} 需要文字")
    if f.choices and value not in f.choices:
        raise ValueError(f"{key} 可选：{', '.join(f.choices)}")
    if key in ("listen.host", "osc.host") and not value.strip():
        raise ValueError(f"{key} 不能为空")
    return value


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.values = {k: f.default for k, f in FIELDS.items()}
        self.routing = {"targets": [], "exclude": [], "overrides": {}}
        self.load_error = ""
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if not isinstance(data, dict):
                    raise ValueError("配置根节点必须为映射")
                for group, entries in data.items():
                    if group == "routing":
                        if not isinstance(entries, dict):
                            raise ValueError("routing 必须为映射")
                        self.routing.update(entries)
                        # Pre-release prototype used live-source history bindings.
                        # History now has its own delivery service.
                        self.routing.pop("history", None)
                        continue
                    if not isinstance(entries, dict):
                        raise ValueError(f"{group} 必须为映射")
                    for key, value in entries.items():
                        if group == "message" and key == "paren":
                            # Removed prototype-only body parentheses; username_brackets
                            # remains the sole persistent setting for /paren.
                            continue
                        self.values[f"{group}.{key}"] = coerce(f"{group}.{key}", value)
                self.validate(self.values)
                self.validate_routing(self.routing)
            except (ValueError, OSError, yaml.YAMLError) as exc:
                self.load_error = str(exc)
                self.values = {k: f.default for k, f in FIELDS.items()}
                self.routing = {"targets": [], "exclude": [], "overrides": {}}

    def __getitem__(self, key):
        return self.values[key]

    def group(self, name):
        return {k.split(".", 1)[1]: v for k, v in self.values.items() if k.startswith(name + ".")}

    def validate(self, values):
        for key, value in values.items():
            coerce(key, value)
        if values["message.quote_style"] == "custom" and not all(values[k] for k in ("message.quote_open", "message.quote_close")):
            raise ValueError("自定义引号需要左右两个符号")

    def validate_routing(self, routing):
        if set(routing) != {"targets", "exclude", "overrides"}:
            raise ValueError("未知 routing 设置")
        for key in ("targets", "exclude"):
            if not isinstance(routing[key], list) or not all(isinstance(s, str) for s in routing[key]):
                raise ValueError(f"routing.{key} 必须为选择器列表")
        overrides = routing["overrides"]
        if not isinstance(overrides, dict):
            raise ValueError("routing.overrides 必须为映射")
        for selector, override in overrides.items():
            if not isinstance(selector, str) or not isinstance(override, dict):
                raise ValueError("无效的能力覆盖")
            for key, value in override.items():
                if key == "typing":
                    if type(value) is not bool:
                        raise ValueError("typing 能力必须为布尔值")
                elif key not in {"role", "version", "software", "container"} or not isinstance(value, str):
                    raise ValueError("未知客户端覆盖字段")
                elif key == "role" and value not in {"live", "history", "character", "server", "client", "unknown"}:
                    raise ValueError("未知客户端职责")

    def save(self, values=None, routing=None):
        if self.load_error:
            raise ValueError(f"原配置无效，未覆盖：{self.load_error}")
        values = dict(self.values if values is None else values)
        self.validate(values)
        routing = copy.deepcopy(self.routing if routing is None else routing)
        self.validate_routing(routing)
        data = {}
        for key, value in values.items():
            group, name = key.split(".")
            data.setdefault(group, {})[name] = value
        data["routing"] = routing
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".echolivetui-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                yaml.safe_dump(data, stream, allow_unicode=True, sort_keys=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.values, self.routing = values, routing
