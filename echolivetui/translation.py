"""Async Edge translation, shared requests and a bounded successful-result cache."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass

import aiohttp

from .message import parse_message

EDGE_TRANSLATE = "https://edge.microsoft.com/translate/translatetext"


class TranslationError(ValueError):
    pass


def editor_text(text):
    return "".join(part.get("text", "") for part in parse_message(text))


def paragraph_text(message):
    """Read actual wire text, excluding media metadata and typing phonetics."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return "".join(paragraph_text(part) for part in message)
    if isinstance(message, dict):
        return message.get("text", "") if isinstance(message.get("text"), str) else ""
    return ""


@dataclass(frozen=True)
class TranslationResult:
    translations: tuple[tuple[str, ...], ...]
    detected_languages: tuple[str | None, ...]

    def tracks(self, index=0):
        return [{"message": [{"text": text}]} for text in self.translations[index]]


@dataclass
class Pending:
    task: asyncio.Task
    users: int = 0


class TranslationService:
    def __init__(self, *, endpoint=EDGE_TRANSLATE, timeout=8):
        self.endpoint, self.timeout = endpoint, timeout
        self.session = None
        self.cache = OrderedDict()
        self.pending = {}
        self.closed = False

    async def translate(self, texts, source, targets):
        if self.closed:
            raise TranslationError("翻译服务已关闭")
        key = tuple(texts), source, tuple(targets)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if not targets:
            raise TranslationError("请添加至少一种子轨语言")
        if not texts:
            return TranslationResult((), ())
        pending = self.pending.get(key)
        if pending is None:
            pending = self.pending[key] = Pending(asyncio.create_task(self._request(key)))
        pending.users += 1
        try:
            return await asyncio.shield(pending.task)
        finally:
            pending.users -= 1
            if not pending.users:
                if not pending.task.done():
                    pending.task.cancel()
                if self.pending.get(key) is pending:
                    self.pending.pop(key)

    async def _request(self, key):
        texts, source, targets = key
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout), trust_env=True,
                                                 headers={"User-Agent": "EchoLiveTUI", "Content-Type": "application/json"})
        params = [("from", source), *(("to", code) for code in targets), ("isEnterpriseClient", "false")]
        try:
            async with self.session.post(self.endpoint, params=params, json=list(texts)) as response:
                if response.status == 429:
                    raise TranslationError("翻译请求受限（HTTP 429），请稍后重试")
                if response.status != 200:
                    raise TranslationError(f"翻译服务返回 HTTP {response.status}")
                data = await response.json(content_type=None)
        except TranslationError:
            raise
        except TimeoutError:
            raise TranslationError("翻译请求超时，请重试") from None
        except (aiohttp.ClientError, ValueError) as exc:
            raise TranslationError(f"翻译请求失败：{exc}") from exc
        if not isinstance(data, list) or len(data) != len(texts):
            raise TranslationError("翻译响应缺少对应段落")
        translations, detected = [], []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("translations"), list):
                raise TranslationError("翻译响应格式无效")
            mapping = {part.get("to"): part.get("text") for part in item["translations"]
                       if isinstance(part, dict) and isinstance(part.get("to"), str)}
            if any(not isinstance(mapping.get(code), str) or not mapping[code].strip() for code in targets):
                raise TranslationError("翻译响应缺少目标语言译文")
            translations.append(tuple(mapping[code] for code in targets))
            language = item.get("detectedLanguage")
            code = language.get("language") if isinstance(language, dict) else None
            detected.append(code if isinstance(code, str) else None)
        result = TranslationResult(tuple(translations), tuple(detected))
        self.cache[key] = result
        self.cache.move_to_end(key)
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return result

    async def close(self):
        self.closed = True
        tasks = [pending.task for pending in self.pending.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        if self.session:
            await self.session.close()
