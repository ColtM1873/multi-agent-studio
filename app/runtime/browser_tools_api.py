from app.runtime.browser_tools_wrap_up import _apply_browser_timing


def browser_status(agent_id: str | None = None, thread_id: str | None = None) -> tuple[bool, int | None]:
    """返回 (是否已连接, port)。

    有 ``agent_id + thread_id`` 时读该会话自己的浏览器实例；否则保持旧单实例口径
    （``config.json`` 的 ``last_port``），供未升级的调用方兼容。
    """
    if agent_id and thread_id:
        try:
            from app.deps import browser_session_manager

            state = browser_session_manager.status(agent_id, thread_id)
            return bool(state["connected"]), state.get("port")
        except Exception:  # noqa: BLE001
            return False, None

    from browser_agent.config import load_config
    from browser_agent.launcher import BrowserLauncher

    cfg = load_config()
    port = cfg.get("last_port")
    if isinstance(port, int) and BrowserLauncher._is_port_ready(port):
        return True, port
    return False, (port if isinstance(port, int) else None)


def warmup_browser(agent_id: str | None = None, thread_id: str | None = None) -> None:
    """提前完成「首次浏览器工具调用」所需的加载与准备工作。

    多实例下只预热当前会话的 controller；没有会话参数时走旧的单实例 controller。
    """
    import browser_agent  # noqa: F401
    import browser_agent.actions  # noqa: F401
    import browser_agent.cdp  # noqa: F401
    import browser_agent.dom  # noqa: F401
    import browser_agent.launcher  # noqa: F401
    import browser_agent.timing as ba_timing
    from app.runtime.browser_sessions import use_browser_session

    try:
        _apply_browser_timing()
        if agent_id and thread_id:
            with use_browser_session(agent_id, thread_id):
                from app.runtime.browser_sessions import current_browser_controller

                ctrl = current_browser_controller()
                _warm_controller(ctrl, ba_timing)
        else:
            from browser_agent.controller import BrowserController

            _warm_controller(BrowserController.instance(), ba_timing)
    except Exception:  # noqa: BLE001 — 预热失败绝不影响「打开浏览器」本身
        pass


def _warm_controller(ctrl, ba_timing) -> None:
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
