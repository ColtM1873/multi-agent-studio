from app.runtime.browser_tools_wrap_up import _apply_browser_timing

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

def warmup_browser() -> None:
    """提前完成「首次浏览器工具调用」所需的加载与准备工作。

    在用户点击「打开浏览器」后调用：预导入 ``browser_agent`` 全部子模块、应用
    全局延迟配置、确保已连接、attach 当前聚焦标签页并跑一次轻量 ``Runtime.evaluate``。
    这样真正由 LLM 发起第一次互动时，不再额外支付这些一次性开销。
    """
    import browser_agent  # noqa: F401
    import browser_agent.actions  # noqa: F401
    import browser_agent.cdp  # noqa: F401
    import browser_agent.dom  # noqa: F401
    import browser_agent.launcher  # noqa: F401
    import browser_agent.timing as ba_timing
    from browser_agent.controller import BrowserController

    try:
        _apply_browser_timing()
        ctrl = BrowserController.instance()
        ctrl.ensure_connected()
        target_id = ctrl._resolve_focused_target()
        if not target_id or ctrl.client is None:
            return
        session = ctrl.client.attach(target_id)
        ctrl.client.enable_page_domains(session)
        ctrl.client.send(
            "Runtime.evaluate",
            {"expression": "document.readyState", "returnByValue": True},
            session_id=session,
            timeout=ba_timing.get().cdp.probe_timeout,
        )
    except Exception:  # noqa: BLE001 — 预热失败绝不影响「打开浏览器」本身
        pass