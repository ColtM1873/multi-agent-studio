from datetime import datetime
from pathlib import Path
import json
import threading


# debug 日志根目录：程序根目录（app/runtime/browser_tools_debug_log.py 上溯三层）。
_ROOT_DIR = Path(__file__).resolve().parents[2]
_DEBUG_LOG_DIR = _ROOT_DIR / "browser_tools_DEBUG_logs"

# 同一秒内的并发工具调用（同一个 AI 轮可并发多个 tool_call）会落到同一文件名而
# 互相覆盖（实测 3 个并发 `tool_05_type_in_select` 只留 1 份日志）。文件名追加一个
# 进程内递增的唯一后缀，保证每次调用一份日志。
_log_seq = 0
_log_seq_lock = threading.Lock()


def _unique_suffix() -> str:
    global _log_seq
    with _log_seq_lock:
        _log_seq += 1
        return f"{_log_seq:04d}"

def _load_settings_safe():
    """读取全局设置；失败返回 None（不干扰工具调用）。"""
    try:
        from app.config.settings import load_settings
        from app.deps import config_store

        return load_settings(config_store._dir)
    except Exception:  # noqa: BLE001
        return None

def _debug_enabled() -> bool:
    """调用时实时读取「浏览器工具 debug 模式」开关。"""
    settings = _load_settings_safe()
    return bool(settings is not None and settings.browser_takeover_debug)


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


def _write_debug_log(tool_name: str, llm_input: dict, content: str, capture) -> None:
    """把一次工具调用写成 ``browser_takeover_debug_logs/<日期-时-时>/<工具名-时分秒>``。"""
    try:
        now = datetime.now()
        end_hour = (now.hour + 1) % 24
        folder = _DEBUG_LOG_DIR / f"{now:%Y%m%d}-{now.hour:02d}-{end_hour:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{now:%H%M%S}-{tool_name}-{_unique_suffix()}"

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