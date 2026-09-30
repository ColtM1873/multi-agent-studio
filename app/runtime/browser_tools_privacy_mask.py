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


# ── 数字型敏感值的「分组容差」补充匹配 ─────────────────────────────────────
# 精确匹配只看字面相等，而网页/组件库常把同一个号码排版成带分隔符或国际区号的
# 形式：设置里配置 ``15683862160``，页面上却是 ``+86 156-8386-2160``，于是**真实
# 号码被明文返回给 LLM**（真实会话里就发生了，连模型自己都点出来「这是隐私问题」）。
# 这里为「≥6 位数字」的值再编一条正则：数字之间允许分隔符、允许 ``+86`` / ``0086``
# 这类国家码前缀；全角数字也算。所有条目合并成**一条**正则（单次扫描），并按
# 「最左最长」与精确匹配结果合并，故开销与值条数无关、只与文本长度线性相关。
_SEP_CLASS = r"[\s\-–—_.·:：/|()（）\[\]{}]{0,3}"
_ASCII_DIGITS = "0123456789"
_FULLWIDTH_DIGITS = "０１２３４５６７８９"
_DIGIT_LOOKAROUND = "[0-9０-９]"


def _digit_tolerant_atom(ch: str) -> str:
    """One digit of a pattern, accepting its full-width twin as well."""
    index = _ASCII_DIGITS.find(ch)
    if index >= 0:
        return "[%s%s]" % (ch, _FULLWIDTH_DIGITS[index])
    return re.escape(ch)


def _digit_tolerant_pattern(value: str) -> str:
    """A regex body matching ``value``'s digits with optional separators between."""
    digits = [ch for ch in value if ch.isdigit()]
    body = _SEP_CLASS.join(_digit_tolerant_atom(ch) for ch in digits)
    # An international prefix (``+86`` / ``0086``) is part of the number on screen.
    prefix = r"(?:(?:\+|00)[\s\-]{0,2}[0-9]{1,3}[\s\-]{0,2})?"
    return (
        r"(?<!" + _DIGIT_LOOKAROUND + r")"
        + prefix
        + body
        + r"(?!" + _DIGIT_LOOKAROUND + r")"
    )


def _should_tolerate_digits(value: str) -> bool:
    """Only long, mostly-numeric values get the tolerant pattern (avoid noise)."""
    digits = [ch for ch in value if ch.isdigit()]
    if len(digits) < 6:
        return False
    return len(digits) * 5 >= len(value) * 3


class _SensitiveMasker:
    """Exact (Aho-Corasick) plus digit-tolerant (one regex) sensitive masking."""

    def __init__(self, patterns: tuple[tuple[str, str], ...]) -> None:
        self._automaton = _AhoCorasick(patterns)
        tolerants = [
            (value, name) for value, name in patterns if _should_tolerate_digits(value)
        ]
        self._digit_names: dict[int, str] = {}
        self._digit_re = None
        if tolerants:
            pieces = []
            for index, (value, name) in enumerate(tolerants, start=1):
                self._digit_names[index] = name
                pieces.append(
                    "(?P<d%d>%s)" % (index, _digit_tolerant_pattern(value))
                )
            try:
                self._digit_re = re.compile("|".join(pieces))
            except re.error:
                # A pathological value must never break masking of the others.
                self._digit_re = None
                self._digit_names = {}

    def replace(self, text: str) -> str:
        matches = self._automaton._search(text)
        if self._digit_re is not None:
            for found in self._digit_re.finditer(text):
                name = self._digit_names.get(found.lastindex or 0)
                if name:
                    matches.append(
                        (found.start(), found.end(), found.group(0), name)
                    )
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

@lru_cache(maxsize=8)
def _build_masker(patterns: tuple[tuple[str, str], ...]) -> _SensitiveMasker:
    return _SensitiveMasker(patterns)

def make_sensitive_masker():
    """返回 ``mask(text) -> text``（隐私遮蔽模式开启且表单非空时），否则返回 None。

    每次调用现读设置与表单，故改表/改开关即时生效；表单不变时复用已编译的匹配器。
    """
    if not _load_privacy_mask_enabled():
        return None
    patterns = _load_sensitive_reverse_map()
    if not patterns:
        return None
    return _build_masker(patterns).replace

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