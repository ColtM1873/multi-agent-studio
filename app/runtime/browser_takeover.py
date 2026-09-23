"""浏览器接管工具：把 ``browser_agent`` 的同步工具转换成 LangChain StructuredTool。

设计要点（见 inner_docs/ID56）：
- **惰性 import** ``browser_agent``：未安装 websocket-client 时不应导致 studio 启动失败。
- **暂停短路**：全局 ``_paused`` 为真时，所有工具**最先**返回 ``STOP_TEXT``，
  不做任何连接/校验/输入检查（满足「直接 shortcut」要求）。
- **不阻塞事件循环**：browser_agent 全同步（websocket-client + sleep），
  统一用 ``asyncio.to_thread`` 在线程池执行。
- 工具返回 dict 时序列化为 JSON 字符串（``ToolMessage.content`` 需为文本）。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

STOP_TEXT = "用户已暂停你的浏览器操作，请立即停止所有浏览器动作，等待用户的后续指示。"

# debug 日志根目录：程序根目录（app/runtime/browser_takeover.py 上溯三层）。
_ROOT_DIR = Path(__file__).resolve().parents[2]
_DEBUG_LOG_DIR = _ROOT_DIR / "browser_takeover_debug_logs"

_paused: bool = False


def set_paused(value: bool) -> None:
    """设置全局暂停标志（用户点「停止」为 True，点「继续」为 False）。"""
    global _paused
    _paused = bool(value)


def is_paused() -> bool:
    return _paused


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


def _preprocess_interact(kwargs: dict) -> dict:
    """tool-2：只替换可填入元素的 fill。"""
    if "fill" in kwargs:
        kwargs["fill"] = apply_sensitive_replacement(kwargs.get("fill") or "")
    return kwargs


def _preprocess_interact_many(kwargs: dict) -> dict:
    """tool-11：只替换可填入元素的 fill_list。"""
    fills = kwargs.get("fill_list")
    if isinstance(fills, list):
        kwargs["fill_list"] = [apply_sensitive_replacement(str(f) if f is not None else "") for f in fills]
    return kwargs


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


# ── 有参工具的 args_schema（描述照搬 browser_agent/schemas.py）─────────────
class NoArgs(BaseModel):
    """无参工具的空 schema（避免从 **kwargs 推断出错误的 kwargs 参数）。"""


class InteractArgs(BaseModel):
    name: str = Field(..., description="互动元素名称，如 'e4'（来自序列化标签）。")
    fill: str = Field("", description="仅用于『可输入』元素的填充内容；其他情况传空串 ''。")
    drag_pct: int = Field(
        0, ge=0, le=100,
        description="仅用于『可拖动』元素的目标位置百分比(0-100)；其他情况传 0。",
    )
    scroll_delta: int = Field(
        0, ge=-6, le=6,
        description=(
            "仅用于『可滚动』元素：正数向下滚、负数向上滚，单位为步"
            "（每步约 0.7 个可见高度），绝对值上限 6；其他情况传 0"
            "（对可滚动元素传 0 等同向下 6 步）。"
        ),
    )


class SwitchTabArgs(BaseModel):
    tab_id: str = Field(..., description="目标标签页名称，如 '百度001'（来自 tool_4/tool_10）。")


class CloseTabArgs(BaseModel):
    tab_id: str = Field(..., description="要关闭的标签页名称，如 '百度001'。")


class NavigateArgs(BaseModel):
    url: str = Field(..., description="目标地址，如 'example.com' 或完整 URL。")


class InteractManyArgs(BaseModel):
    name_list: list[str] = Field(
        ..., description="互动元素名称列表，如 ['e4','e5','e6']；最后一个可选为可点击元素。"
    )
    fill_list: list[str] = Field(
        ...,
        description="填入内容列表；与 name_list 等长，或比它少一个（少一个时末位为收尾点击）。",
    )


# ── debug 模式：LLM 每次调用浏览器工具都落盘一份人类可读日志 ────────────────
def _load_settings_safe():
    """读取全局设置；失败返回 None（不干扰工具调用）。"""
    try:
        from app.config.settings import load_settings
        from app.deps import config_store

        return load_settings(config_store._dir)
    except Exception:  # noqa: BLE001
        return None


def _apply_browser_timing() -> None:
    """按全局「浏览器交互延迟设置」覆盖 browser_agent 的运行时延迟。

    每次工具调用前现读设置并覆盖（只覆盖已知键），因此改完保存后下一次调用即生效，
    无需 invalidate/重建 runtime。读取失败时保持 browser_agent 的默认值。
    """
    try:
        from browser_agent import timing as ba_timing

        settings = _load_settings_safe()
        if settings is not None:
            ba_timing.configure(getattr(settings, "browser_timing", None) or {})
    except Exception:  # noqa: BLE001
        pass


def _debug_enabled() -> bool:
    """调用时实时读取「浏览器工具 debug 模式」开关。"""
    settings = _load_settings_safe()
    return bool(settings is not None and settings.browser_takeover_debug)


def _humanize(text: str) -> str:
    """把字面量 ``\\t`` / ``\\n`` / ``\\r`` 还原成真正的 tab / 换行，便于阅读。"""
    if not text:
        return ""
    return (
        text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
        .replace("\\t", "\t")
    )


def _invoke_debug(fn, kwargs: dict):
    """在 worker 线程里带着 debug 采集器执行底层工具。

    返回 ``(result, capture, error_text)``；异常不抛出，转成 ``error_text``。
    """
    from browser_agent import debug as ba_debug

    capture = ba_debug.begin_capture()
    try:
        result = fn(**kwargs)
        return result, capture, ""
    except Exception as exc:  # noqa: BLE001
        return None, capture, f"{type(exc).__name__}: {exc}"
    finally:
        ba_debug.end_capture()


def _write_debug_log(tool_name: str, llm_input: dict, content: str, capture) -> None:
    """把一次工具调用写成 ``browser_takeover_debug_logs/<日期-时-时>/<工具名-时分秒>``。"""
    try:
        now = datetime.now()
        end_hour = (now.hour + 1) % 24
        folder = _DEBUG_LOG_DIR / f"{now:%Y%m%d}-{now.hour:02d}-{end_hour:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{tool_name}-{now:%H%M%S}"

        raw_dom = capture.raw_dom if capture is not None else ""
        processed_dom = capture.processed_dom if capture is not None else ""
        url = capture.url if capture is not None else ""
        title = capture.title if capture is not None else ""
        try:
            input_text = json.dumps(llm_input, ensure_ascii=False, indent=2, default=str)
        except Exception:  # noqa: BLE001
            input_text = str(llm_input)

        sep = "=" * 70
        thin = "-" * 70
        lines = [
            sep,
            "浏览器工具调用 debug 日志",
            f"工具：{tool_name}",
            f"时间：{now:%Y-%m-%d %H:%M:%S}",
            f"URL：{url}",
            f"标题：{title}",
            sep,
            "",
            thin,
            "一、整个 viewport 的原始 DOM（未处理的真实 HTML）",
            thin,
            _humanize(raw_dom) or "（本次调用没有抓取 DOM）",
            "",
            thin,
            "二、处理后的整个 viewport DOM（已标记可互动元素等）",
            thin,
            _humanize(processed_dom) or "（本次调用没有抓取 DOM）",
            "",
            thin,
            "三、LLM 本次调用的输入",
            thin,
            _humanize(input_text) or "（无）",
            "",
            thin,
            "四、工具本次返回的 content",
            thin,
            _humanize(content) or "（无）",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _wrap(fn, description: str, args_schema=None, preprocess=None) -> StructuredTool:
    """把一个 browser_agent 同步工具包成 LangChain StructuredTool。

    ``preprocess`` 可选：在参数校验之后、真正调用底层工具之前，对 kwargs 做变换
    （如把可填入元素的 fill 里的 ``<名称>`` 替换为敏感信息真实值）。
    """

    async def _call(**kwargs) -> str:
        if _paused:
            return STOP_TEXT
        # 按全局「浏览器交互延迟设置」覆盖运行时延迟（调用时现读，改完即生效）。
        _apply_browser_timing()
        # 记录 LLM 原始输入（敏感信息替换前的 kwargs）。
        llm_input = dict(kwargs)
        if preprocess is not None:
            try:
                kwargs = preprocess(dict(kwargs))
            except Exception:  # noqa: BLE001
                pass

        debug_on = _debug_enabled()
        capture = None
        error_text = ""
        if debug_on:
            result, capture, error_text = await asyncio.to_thread(_invoke_debug, fn, kwargs)
        else:
            try:
                result = await asyncio.to_thread(fn, **kwargs)
            except Exception as exc:  # noqa: BLE001
                error_text = f"{type(exc).__name__}: {exc}"
                result = None

        if error_text:
            return_text = f"浏览器工具执行失败：{error_text}"
            log_content = return_text
        elif isinstance(result, str):
            return_text = result
            log_content = result
        else:
            try:
                return_text = json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError):
                return_text = str(result)
            if isinstance(result, dict):
                raw_content = result.get("content")
                log_content = "" if raw_content is None else str(raw_content)
            else:
                log_content = return_text

        # 隐私遮蔽模式：返回给 LLM 之前，把真实敏感值反向替换为 <名称>。
        mask = make_sensitive_masker()
        if mask is not None:
            if error_text or isinstance(result, str):
                return_text = mask(return_text)
                log_content = mask(log_content)
            elif isinstance(result, (dict, list)):
                masked = _mask_deep(result, mask)
                try:
                    return_text = json.dumps(masked, ensure_ascii=False)
                except (TypeError, ValueError):
                    return_text = mask(str(result))
                raw_content = masked.get("content") if isinstance(masked, dict) else None
                log_content = "" if raw_content is None else str(raw_content)

        if debug_on:
            _write_debug_log(fn.__name__, llm_input, log_content, capture)
        return return_text

    return StructuredTool.from_function(
        coroutine=_call,
        name=fn.__name__,
        description=description,
        args_schema=args_schema,
    )


def build_browser_tools() -> list[StructuredTool]:
    """构建 11 个浏览器接管工具（惰性 import browser_agent）。"""
    import browser_agent as ba

    descriptions = {t["name"]: t["description"] for t in ba.TOOLS}

    def desc(name: str) -> str:
        return descriptions.get(name, "")

    return [
        _wrap(ba.tool_1_open_browser, desc("tool_1_open_browser"), NoArgs),
        _wrap(ba.tool_2_interact, desc("tool_2_interact"), InteractArgs, _preprocess_interact),
        _wrap(ba.tool_3_get_viewport_dom, desc("tool_3_get_viewport_dom"), NoArgs),
        _wrap(ba.tool_4_list_tabs, desc("tool_4_list_tabs"), NoArgs),
        _wrap(ba.tool_5_switch_tab, desc("tool_5_switch_tab"), SwitchTabArgs),
        _wrap(ba.tool_6_go_back, desc("tool_6_go_back"), NoArgs),
        _wrap(ba.tool_7_refresh, desc("tool_7_refresh"), NoArgs),
        _wrap(ba.tool_8_close_tab, desc("tool_8_close_tab"), CloseTabArgs),
        _wrap(ba.tool_9_navigate, desc("tool_9_navigate"), NavigateArgs),
        _wrap(ba.tool_10_tab_url_map, desc("tool_10_tab_url_map"), NoArgs),
        _wrap(ba.tool_11_interact_many, desc("tool_11_interact_many"), InteractManyArgs, _preprocess_interact_many),
    ]


def browser_status() -> tuple[bool, int | None]:
    """返回 (是否已连接, port)。

    连接判定：``config.json`` 的 ``last_port`` 能应答 CDP 的 ``/json/version``。
    """
    from browser_agent.config import load_config
    from browser_agent.launcher import BrowserLauncher

    cfg = load_config()
    port = cfg.get("last_port")
    if isinstance(port, int) and BrowserLauncher._is_port_ready(port):
        return True, port
    return False, (port if isinstance(port, int) else None)
