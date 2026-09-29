from __future__ import annotations
import argparse
import logging
from pathlib import Path
import yaml
import jieba
from .config import Settings, FIELDS


def import_legacy(settings, path):
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("旧配置必须为映射")
    aliases = {"host": "listen.host", "port": "listen.port", "inhibit_ctrl_c": "input.interrupt_guard", "auto_parentheses": "message.paren", "auto_suffix": "message.suffix", "auto_suffix_value": "message.suffix_value", "quote_custom_left": "message.quote_open", "quote_custom_right": "message.quote_close", "osc_enabled": "osc.enable"}
    values = dict(settings.values)
    for key, value in data.items():
        target = aliases.get(key, "message." + key)
        if target in FIELDS:
            values[target] = value
    if data.get("quote_style") == "none":
        values.update({"message.quote": False, "message.quote_style": "en"})
    if "osc_address" in data:
        host, port = str(data["osc_address"]).rsplit(":", 1)
        values.update({"osc.host": host, "osc.port": int(port)})
    settings.save(values)


def main():
    parser = argparse.ArgumentParser(description="EchoLiveTUI · Echo Live 本地字幕广播")
    parser.add_argument("--config", type=Path, default=Path.cwd() / "echolivetui.yaml")
    parser.add_argument("--import-legacy", type=Path, metavar="CONFIG_YAML")
    parser.add_argument("--version", action="version", version="EchoLiveTUI 0.1.0")
    args = parser.parse_args()
    settings = Settings(args.config)
    if args.import_legacy:
        try:
            import_legacy(settings, args.import_legacy)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            parser.error(str(exc))
    jieba.setLogLevel(logging.ERROR)
    from .ui import EchoApp
    EchoApp(settings, Path.cwd()).run()


if __name__ == "__main__":
    main()
