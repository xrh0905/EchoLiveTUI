"""Echo Live's editable frontend font and read-only release checks."""
from __future__ import annotations

from pathlib import Path
import re

import aiohttp

from .config import FIELDS, coerce

DEFAULT_FONT = "思源黑体"
FONT_FILE = "res/style/live-common/font-family.css"
DEFAULT_REPOSITORY = FIELDS["echolive.repository"].default
GITHUB_API = "https://api.github.com"


def release_page(repository=DEFAULT_REPOSITORY):
    return f"https://github.com/{coerce('echolive.repository', repository)}/releases/latest"


RELEASE_PAGE = release_page()
FONT_DECLARATION = re.compile(r"(--echo-default-font-family\s*:\s*)([^;{}]+)(;)")
FONT_NAME = r'''(?:"[^"'\\\r\n]+"|'[^"'\\\r\n]+'|[\w.-]+(?:[ \t]+[\w.-]+)*)'''
FONT_VALUE = re.compile(FONT_NAME + r"(?:\s*,\s*" + FONT_NAME + r")*")
GENERIC_FONTS = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif", "ui-sans-serif", "ui-monospace", "ui-rounded", "emoji", "math", "fangsong"}


def font_names(value: str) -> list[str]:
    value = value.strip()
    if not FONT_VALUE.fullmatch(value) or any(char in value for char in ";{}\r\n\\"):
        raise ValueError("请输入字体名称或列表，例如 思源黑体, sans-serif")
    return [token[1:-1] if token.startswith(('"', "'")) else token for token in re.findall(FONT_NAME, value)]


def font_path(root: Path) -> Path:
    path = (root / FONT_FILE).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("字体文件不在 Echo Live 目录内")
    return path


def read_font(root: Path) -> str:
    match = FONT_DECLARATION.search(font_path(root).read_bytes().decode("utf-8-sig"))
    if not match:
        raise ValueError("字体文件中没有 --echo-default-font-family 变量")
    return ", ".join(font_names(match[2]))


def write_font(root: Path, value: str) -> None:
    names = font_names(value)
    value = ", ".join(name if name.lower() in GENERIC_FONTS else '"' + name + '"' for name in names)
    path = font_path(root)
    original = path.read_bytes()
    source = original.decode("utf-8-sig")
    if not FONT_DECLARATION.search(source):
        raise ValueError("字体文件中没有 --echo-default-font-family 变量")
    updated = FONT_DECLARATION.sub(lambda match: match[1] + value + match[3], source, count=1)
    path.write_bytes((b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + updated.encode("utf-8"))


def version_key(version: str | None):
    """Compare numeric versions, including semver prereleases, without strings."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([\w.-]+))?(?:\+[\w.-]+)?", version or "")
    if not match:
        return None
    prerelease = match[4]
    identifiers = tuple((0, int(part)) if part.isdigit() else (1, part) for part in prerelease.split(".")) if prerelease else ()
    return (*map(int, match.group(1, 2, 3)), 1 if prerelease is None else 0, identifiers)


def release_notice(local: str | None, latest: str) -> str:
    current, remote = version_key(local), version_key(latest)
    if remote is None:
        raise ValueError("GitHub 返回的版本号无法识别")
    if current is None:
        return f"最新版本 {latest}；本地版本未知，无法比较"
    if current < remote:
        return f"有更新：{local} → {latest} · 打开发布页查看更新"
    if current == remote:
        return f"已是最新版本：{local}"
    return f"本地版本 {local} 高于最新正式版 {latest}"


async def latest_release(repository=DEFAULT_REPOSITORY):
    repository = coerce("echolive.repository", repository)
    page = release_page(repository)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "EchoLiveTUI", "X-GitHub-Api-Version": "2026-03-10"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), headers=headers, trust_env=True) as session:
            async with session.get(f"{GITHUB_API}/repos/{repository}/releases/latest") as response:
                if response.status in {403, 429}:
                    raise ValueError("GitHub 请求受限，请稍后再试")
                if response.status != 200:
                    raise ValueError(f"检查更新失败：GitHub HTTP {response.status}")
                data = await response.json()
        tag = data.get("tag_name", "")
        if version_key(tag) is None:
            raise ValueError("GitHub 返回的版本号无法识别")
        url = data.get("html_url", page)
        if not isinstance(url, str) or not url.startswith(f"https://github.com/{repository}/releases/"):
            url = page
        return tag, url
    except (aiohttp.ClientError, TimeoutError, TypeError, AttributeError) as exc:
        raise ValueError("无法连接 GitHub，请检查网络后重试") from exc
