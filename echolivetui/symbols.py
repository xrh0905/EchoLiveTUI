"""Shared presets for message quotes and speaker brackets."""
QUOTES = {"en": ('"', '"'), "cn": ("“", "”"), "jp": ("「", "」")}
BRACKETS = {"square": ("【", "】"), "round": ("（", "）"), "corner": ("「", "」")}


def bracket_symbols(config):
    return BRACKETS.get(config.get("username_bracket_style", "square"),
                        (config.get("username_bracket_open", ""), config.get("username_bracket_close", "")))
