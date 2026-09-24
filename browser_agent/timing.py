"""可调运行时延迟（全局单例）。

`browser_agent` 里所有「等待/超时/拟人停顿」的时长都集中在这里，默认值与历史
硬编码值完全一致（因此不配置时行为不变）。app 层在每次浏览器工具调用前，
按全局设置调用 :func:`configure` 覆盖这些值，实现「改完保存，下次调用即生效」。

设计要点：
- 调用时读取（``get().nav.grace_seconds``），不是 import 时快照，故修改即时生效。
- 只覆盖已知键、忽略未知键，便于版本升级时旧配置向后兼容。
- 数值统一 ``float`` 且不小于 0；动作类延迟是 ``[最小, 最大]`` 二元组。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class NavTiming:
    """导航与稳定化（对交互延迟影响最大）。"""

    # 可能触发跳转的元素（a[href]/表单提交/role=link…）点击后，等导航“开始”的上限。
    grace_seconds: float = 4.0
    # 不可能触发跳转的元素（普通按钮/图标等）点击后，等导航“开始”的短宽限。
    # 这类元素几乎不会真正导航，等满 grace_seconds 纯属浪费；短宽限 + 判稳兜底即可。
    short_grace_seconds: float = 0.6
    # 导航已“开始”后，等待新文档提交（Page.frameNavigated）的上限（通常瞬时）。
    commit_timeout: float = 2.0
    # 新文档提交后，额外等待 loadEventFired 的上限；load 已触发则立即返回。
    # 用于给异步内容一点时间，但不再像旧版那样为“load 迟迟不来”死等 15s。
    load_grace_seconds: float = 1.5
    # 新标签页首次就绪 / 空壳页面等待正文的超时（站点很慢时可调大）。
    load_timeout: float = 15.0
    quiet_seconds: float = 0.7
    quiet_timeout: float = 2.0
    probe_interval: float = 0.15
    settle_poll_interval: float = 0.05


@dataclass
class ReadyTiming:
    """页面就绪与标签页相关的等待。"""

    timeout: float = 15.0
    poll_interval: float = 0.15
    new_tab_poll_interval: float = 0.1
    close_timeout: float = 3.0
    close_poll_interval: float = 0.1


@dataclass
class CdpTiming:
    """CDP 命令超时。"""

    command_timeout: float = 30.0
    probe_timeout: float = 3.0
    outer_html_timeout: float = 20.0
    # 输入事件（鼠标移动/按下/松开）的等待上限。输入事件是“发了就算数”的
    # 最佳努力操作：个别事件 ack 延迟（在部分机器上曾观察到单次卡 3~5s）
    # 时不应把整个点击拖成几十秒，超时就跳过等待继续下一步。
    input_timeout: float = 2.0


@dataclass
class LaunchTiming:
    """浏览器启动与端口探测。"""

    ready_timeout: float = 25.0
    devtools_poll_interval: float = 0.1
    cdp_poll_interval: float = 0.2
    reuse_timeout: float = 2.0
    port_probe_timeout: float = 1.0


@dataclass
class ActionsTiming:
    """拟人交互微延迟（均为 [最小, 最大] 秒，随机取值）。"""

    move_step: list[float] = field(default_factory=lambda: [0.008, 0.025])
    node_box: list[float] = field(default_factory=lambda: [0.05, 0.12])
    click_hover: list[float] = field(default_factory=lambda: [0.04, 0.12])
    click_press: list[float] = field(default_factory=lambda: [0.05, 0.12])
    input_focus: list[float] = field(default_factory=lambda: [0.08, 0.18])
    input_clear: list[float] = field(default_factory=lambda: [0.05, 0.10])
    type_char: list[float] = field(default_factory=lambda: [0.03, 0.12])
    drag_press: list[float] = field(default_factory=lambda: [0.05, 0.12])
    drag_move: list[float] = field(default_factory=lambda: [0.01, 0.03])
    scroll_settle: list[float] = field(default_factory=lambda: [0.15, 0.35])


@dataclass
class Timing:
    nav: NavTiming = field(default_factory=NavTiming)
    ready: ReadyTiming = field(default_factory=ReadyTiming)
    cdp: CdpTiming = field(default_factory=CdpTiming)
    launch: LaunchTiming = field(default_factory=LaunchTiming)
    actions: ActionsTiming = field(default_factory=ActionsTiming)


# 默认值（供设置面板展示「恢复默认」）。
DEFAULTS: dict[str, Any] = asdict(Timing())

_current: Timing = Timing()


def get() -> Timing:
    """返回当前生效的延迟配置单例。"""
    return _current


def defaults() -> dict[str, Any]:
    """返回默认值的深拷贝（修改它不影响运行中的配置）。"""
    return deepcopy(DEFAULTS)


def _coerce(value: Any, fallback: float) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return fallback


def _apply(obj: Any, overrides: dict) -> None:
    for key, value in (overrides or {}).items():
        if not hasattr(obj, key):
            continue
        current = getattr(obj, key)
        if isinstance(current, list):
            if isinstance(value, (list, tuple)) and len(value) == len(current):
                setattr(obj, key, [_coerce(v, c) for v, c in zip(value, current)])
        elif hasattr(current, "__dataclass_fields__"):
            if isinstance(value, dict):
                _apply(current, value)
        else:
            setattr(obj, key, _coerce(value, current))


def configure(overrides: dict) -> None:
    """按覆盖字典更新当前配置（只覆盖已知键，数值强制为非负 float）。"""
    if isinstance(overrides, dict):
        _apply(_current, overrides)


def reset() -> None:
    """恢复全部默认值。"""
    global _current
    _current = Timing()
