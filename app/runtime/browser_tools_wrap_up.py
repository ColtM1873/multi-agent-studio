import asyncio

from browser_agent import bid065_trace  # BID065
from app.runtime.browser_tools_privacy_mask import make_sensitive_masker,_mask_deep
from app.runtime.browser_tools_debug_log import _debug_enabled,_write_debug_log,_invoke_debug

bid065_trace.set_provider(_debug_enabled)  # BID065: 时间戳探针只在「浏览器工具debug」开启时工作


_paused: bool = False
PAUSE_PROMPT = "用户已中止你的浏览器操作。请立即中止任何形式的工具调用，迅速结束。"

def is_paused() -> bool:
    return _paused


def set_paused(value: bool) -> None:
    """设置全局暂停标志（用户点「停止」为 True，点「继续」为 False）。"""
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


async def _wrap_up(sync_func , **kwargs) -> str:
    if _paused:
        return PAUSE_PROMPT
    _bid065_t0 = bid065_trace.perf()  # BID065
    bid065_trace.mark(f"_wrap_up START {getattr(sync_func, '__name__', sync_func)}")  # BID065
    # 按全局「浏览器交互延迟设置」覆盖运行时延迟（调用时现读，改完即生效）。
    _apply_browser_timing()
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