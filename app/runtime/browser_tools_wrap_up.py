import asyncio

from browser_agent import bid065_trace  # BID065
from app.runtime.browser_tools_privacy_mask import make_sensitive_masker,_mask_deep
from app.runtime.browser_tools_debug_log import _debug_enabled,_write_debug_log,_invoke_debug

bid065_trace.set_provider(_debug_enabled)  # BID065: 时间戳探针只在「浏览器工具debug」开启时工作


_paused: bool = False
PAUSE_PROMPT = "用户已中止你的浏览器操作。请立即中止任何形式的工具调用，迅速结束。"

def _legacy_is_paused() -> bool:
    """单实例兼容路径的暂停标志（没有会话上下文时使用）。"""
    return _paused


def is_paused() -> bool:
    """当前上下文是否暂停。

    有会话上下文（ID91 多实例）时读该会话自己的暂停标志；否则回退旧的全局标志。
    """
    try:
        from app.deps import browser_session_manager

        return browser_session_manager.is_current_paused()
    except Exception:  # noqa: BLE001
        return _paused


def set_paused(value: bool) -> None:
    """设置全局暂停标志（单实例兼容路径；按会话暂停走 BrowserSessionManager）。"""
    global _paused
    _paused = bool(value)

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
        from browser_agent import timing as browser_timing

        settings = _load_settings_safe()
        if settings is not None:
            browser_timing.configure(getattr(settings, "browser_timing", None) or {})
    except Exception:  # noqa: BLE001
        pass


# 本函数于BID080引入
def _apply_browser_image_tags() -> None:
    """按全局「浏览器工具解析图片标签」开关覆盖 browser_agent 的输出偏好。

    关闭（默认）时，图片标签以及其独子父链会在层级压缩前被删除。每次工具调用前
    现读设置，因此改完保存后下一次调用即生效；browser_agent 默认也是关闭。
    """
    try:
        from browser_agent import prefs as browser_prefs

        settings = _load_settings_safe()
        if settings is not None:
            browser_prefs.configure_image_tags(
                bool(getattr(settings, "browser_image_tags", False))
            )
    except Exception:  # noqa: BLE001
        pass


# 本函数于BID084引入
def _apply_browser_interactive_prefs() -> None:
    """按全局「可交互元素整合 / 英文渲染」设置覆盖 browser_agent 的输出偏好。

    每次工具调用前现读设置，因此改完保存后下一次调用即生效；工具描述在
    图编译时读取，所以设置页保存这类改动时还需要 invalidate_all（见
    ``app/api/settings.py``）。
    """
    try:
        from browser_agent import prefs as browser_prefs

        settings = _load_settings_safe()
        if settings is not None:
            browser_prefs.configure_interactive(
                merge_enabled=bool(
                    getattr(settings, "browser_interactive_merge", True)
                ),
                separator=getattr(
                    settings, "browser_interactive_merge_separator", ":"
                ),
                english_enabled=bool(
                    getattr(settings, "browser_interactive_english", False)
                ),
            )
    except Exception:  # noqa: BLE001
        pass


# 本函数于BID087引入
def _apply_browser_sensitive_names() -> None:
    """按全局「敏感信息替换列表」把条目名称交给 browser_agent 的输出偏好。

    整合层的符号替换据此保留 ``<名称>`` 占位符（不改成 ``[名称]``），与「隐私遮蔽」
    生成占位符、以及列表说明里「用 ``<>`` 包裹名称」的口径保持一致。
    每次工具调用前现读，改表后下一次调用即生效。

    受 ``browser_agent.prefs.SENSITIVE_PLACEHOLDER_GUARD`` 总开关控制（默认关闭）：
    关闭时第一行就返回，连设置都不读——不产生任何额外开销。
    """
    try:
        from browser_agent import prefs as browser_prefs

        if not browser_prefs.SENSITIVE_PLACEHOLDER_GUARD:  # BID087
            return  # BID087
        settings = _load_settings_safe()
        if settings is not None:
            browser_prefs.configure_sensitive_names(
                [
                    getattr(entry, "name", "")
                    for entry in (getattr(settings, "sensitive_info", None) or [])
                ]
            )
    except Exception:  # noqa: BLE001
        pass


async def _wrap_up(sync_func , **kwargs) -> str:
    if is_paused():
        return PAUSE_PROMPT
    _bid065_t0 = bid065_trace.perf()  # BID065
    bid065_trace.mark(f"_wrap_up START {getattr(sync_func, '__name__', sync_func)}")  # BID065
    # 按全局「浏览器交互延迟设置」覆盖运行时延迟（调用时现读，改完即生效）。
    _apply_browser_timing()
    _apply_browser_image_tags()  # BID080
    _apply_browser_interactive_prefs()  # BID084
    _apply_browser_sensitive_names()  # BID087
    # 记录 LLM 原始输入（敏感信息替换前的 kwargs）。
    llm_input = dict(kwargs)

    debug_on = _debug_enabled()
    bid065_trace.mark_elapsed("_wrap_up 设置读取+应用", _bid065_t0)  # BID065
    capture = None
    error_text = ""

    # call the sync_func
    if debug_on:
        result, capture, error_text = await asyncio.to_thread(_invoke_debug, sync_func, kwargs)
        bid065_trace.mark_elapsed("_wrap_up to_thread(_invoke_debug)", _bid065_t0)  # BID065
    else:
        try:
            result = await asyncio.to_thread(sync_func, **kwargs)
        except Exception as exc:  # noqa: BLE001
            error_text = f"{type(exc).__name__}: {exc}"
            result = None
        bid065_trace.mark_elapsed("_wrap_up to_thread(sync_func)", _bid065_t0)  # BID065

    # make log_content , and result if error_text

    # 如果失败了，就没有必要mask
    if error_text:
        result = f"浏览器工具执行失败：{error_text}"
        log_content = result
    else:  # 函数必然已经调用成功
        mask = make_sensitive_masker()
        if mask:
            result = mask(result) if isinstance(result, str) else _mask_deep(result, mask)
        bid065_trace.mark_elapsed("_wrap_up 隐私遮蔽", _bid065_t0)  # BID065

        if isinstance(result, str):
            log_content = result
        elif isinstance(result, dict):
            log_content = str(result["content"]) if result.get("content") else str(result)

    if debug_on:
        _write_debug_log(sync_func.__name__, llm_input, log_content, capture)
        bid065_trace.mark_elapsed("_wrap_up 写debug日志", _bid065_t0)  # BID065

    bid065_trace.mark_elapsed("_wrap_up TOTAL", _bid065_t0)  # BID065
    return result