from functools import lru_cache
from collections import deque
import re

# ── 隐私遮蔽模式（把真实值反向替换回 <名称>，在返回给 LLM 之前）─────────────
# 与上面的「输入替换」互为逆操作：LLM 看到的是 <名称>，后台填的/读到的都是真实值。
# 需求要求「一切网页内容（DOM/diff/…）返回给 LLM 之前」都检测并替换，且网页较长、
# 表单较长时不能用 O(n×m) 暴力穷举，因此这里用 **Aho-Corasick 多模式匹配**：
# 构建一次自动机，对文本单次扫描即可找出所有模式的全部出现（O(n + 匹配数)）。
#
# 「智能边界」：纯 ASCII 字母/数字/下划线构成的值（如数字 17、英文名 Zhang）要求
# 前后不是 ASCII 字母数字（避免命中 2017、170、Zhangwei 等），减少误替换；含中文等
# 非 ASCII 字符的值（如姓名「张文远」）不做边界限制，因为中文没有可靠词边界。

def _is_ascii_word_char(ch: str) -> bool:
    return bool(ch) and ch.isascii() and (ch.isalnum() or ch == "_")

def _is_wordlike(value: str) -> bool:
    return bool(value) and all(
        ch.isascii() and (ch.isalnum() or ch == "_") for ch in value
    )


def _load_settings_safe():
    """读取全局设置；失败返回 None（不干扰工具调用）。"""
    try:
        from app.config.settings import load_settings
        from app.deps import config_store

        return load_settings(config_store._dir)
    except Exception:  # noqa: BLE001
        return None

def _boundary_ok(text: str, start: int, end: int, value: str) -> bool:
    """智能边界判定：非 wordlike（含中文等）一律通过；wordlike 要求前后非字母数字。"""
    if not _is_wordlike(value):
        return True
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not _is_ascii_word_char(before) and not _is_ascii_word_char(after)

class _AhoCorasick:
    """极简 Aho-Corasick 自动机：多模式一次扫描，按「最左最长」且过边界地替换。"""

    def __init__(self, patterns: tuple[tuple[str, str], ...]) -> None:
        # patterns: ((value, name), ...)
        self._goto: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._out: list[list[tuple[int, str, str]]] = [[]]  # (len, value, name)
        for value, name in patterns:
            node = 0
            for ch in value:
                nxt = self._goto[node].get(ch)
                if nxt is None:
                    nxt = len(self._goto)
                    self._goto.append({})
                    self._fail.append(0)
                    self._out.append([])
                    self._goto[node][ch] = nxt
                node = nxt
            self._out[node].append((len(value), value, name))

        queue: deque[int] = deque()
        for nxt in self._goto[0].values():
            queue.append(nxt)
        while queue:
            node = queue.popleft()
            for ch, nxt in self._goto[node].items():
                f = self._fail[node]
                while f and ch not in self._goto[f]:
                    f = self._fail[f]
                self._fail[nxt] = self._goto[f].get(ch, 0)
                self._out[nxt].extend(self._out[self._fail[nxt]])
                queue.append(nxt)

    def _search(self, text: str) -> list[tuple[int, int, str, str]]:
        """返回全部出现 (start, end, value, name)。"""
        matches: list[tuple[int, int, str, str]] = []
        node = 0
        for i, ch in enumerate(text):
            while node and ch not in self._goto[node]:
                node = self._fail[node]
            node = self._goto[node].get(ch, 0)
            for length, value, name in self._out[node]:
                matches.append((i - length + 1, i + 1, value, name))
        return matches

    def replace(self, text: str) -> str:
        matches = self._search(text)
        if not matches:
            return text
        # 同一位置优先最长匹配；再按最左贪心选出互不重叠的匹配。
        matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))
        parts: list[str] = []
        pos = 0
        for start, end, value, name in matches:
            if start < pos:
                continue
            if not _boundary_ok(text, start, end, value):
                continue
            parts.append(text[pos:start])
            parts.append(f"<{name}>")
            pos = end
        parts.append(text[pos:])
        return "".join(parts)

def _load_privacy_mask_enabled() -> bool:
    """读取全局设置里的「隐私遮蔽模式」开关。"""
    settings = _load_settings_safe()
    return bool(settings is not None and settings.privacy_mask_mode)

def _load_sensitive_reverse_map() -> tuple[tuple[str, str], ...]:
    """读取敏感信息表单，构建 ((真实值, 名称), ...)；真实值重复时保留首个名称。"""
    settings = _load_settings_safe()
    if settings is None:
        return ()
    seen: dict[str, str] = {}
    for entry in settings.sensitive_info:
        name = (entry.name or "").strip()
        value = entry.value or ""
        if name and value and value not in seen:
            seen[value] = name
    return tuple((value, name) for value, name in seen.items())

@lru_cache(maxsize=8)
def _build_sensitive_automaton(patterns: tuple[tuple[str, str], ...]) -> _AhoCorasick:
    return _AhoCorasick(patterns)

def make_sensitive_masker():
    """返回 ``mask(text) -> text``（隐私遮蔽模式开启且表单非空时），否则返回 None。

    每次调用现读设置与表单，故改表/改开关即时生效；表单不变时复用已编译的自动机。
    """
    if not _load_privacy_mask_enabled():
        return None
    patterns = _load_sensitive_reverse_map()
    if not patterns:
        return None
    automaton = _build_sensitive_automaton(patterns)
    return automaton.replace

def _mask_deep(obj, mask):
    """递归地对 dict / list / str 里的文本做遮蔽（用于工具返回的整个结果）。"""
    if isinstance(obj, str):
        return mask(obj)
    if isinstance(obj, dict):
        return {k: _mask_deep(v, mask) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_deep(v, mask) for v in obj]
    return obj


# ── 敏感信息脱敏（仅作用于「可填入」元素的 fill 内容）─────────────────────
# LLM 被要求把「名称」用 <> 包裹填入（如 <姓名>），这里在真正交给 browser_agent
# 之前，把 <名称> 替换成敏感信息表单里配置的真实值；表单里没有的名称原样保留。

_SENSITIVE_RE = re.compile(r"<([^<>\n]+)>")

def _load_sensitive_map() -> dict[str, str]:
    """读取全局设置里的敏感信息表单，构建 {名称: 真实值}。"""
    try:
        from app.config.settings import load_settings
        from app.deps import config_store

        settings = load_settings(config_store._dir)
    except Exception:  # noqa: BLE001
        return {}
    mapping: dict[str, str] = {}
    for entry in settings.sensitive_info:
        name = (entry.name or "").strip()
        if name:
            mapping[name] = entry.value
    return mapping

def apply_sensitive_replacement(text: str) -> str:
    """把 ``<名称>`` 替换为敏感信息表单中的真实值；无对应表项则原样保留。"""
    if not text or "<" not in text:
        return text
    mapping = _load_sensitive_map()
    if not mapping:
        return text

    def _repl(match: re.Match) -> str:
        key = match.group(1).strip()
        return mapping.get(key, match.group(0))

    return _SENSITIVE_RE.sub(_repl, text)