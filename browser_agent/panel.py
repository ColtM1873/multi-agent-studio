# 本模块于BID109引入
"""面板状态判定：给「这个控件的这个面板」一个**身份**，而不是拿全页聚合量反推。

## 为什么单独成模块

BID089–BID108 二十轮把「面板开/关」的判据散在 ``controller`` 的十几个函数里，每个函数
比较的量还不一样（浮层指纹 / 全页盒子数 ``count`` / 贴控件的候选面板数 ``near`` /
几何锚定 ``anchored``）。于是每加一条兜底就要再加一条否决（BID093/101/102 就是在加否决），
到第 20 轮仍在出新问题。根因是**测量对象错了**：要回答的是「这一个面板开着吗」，
测量的却是「整页有多少浮动盒子」。

这里把两件事收口：

* ``PanelState``：一次观测的**全部**原始量 + 面板句柄状态（``tracked`` / ``alive``）；
* ``opened_since`` / ``closed_since`` / ``still_present``：两次观测之间的**方向**判定。

controller 只负责在正确的时机调用它们，不再各自比较 ``count`` / ``near``。

## 关于 ``aria-expanded``（写给后来者，别高估它）

``aria`` 是**最像**状态通道、实际**最不可靠**的字段，历史实现没有拿它当运行时判据是对的：

* 它常常挂在**包装层**（``.ant-select`` / 自绘外壳）而不是真正的触发器上，读到的可能是
  另一个控件的状态；
* 受控组件的 ``blur`` 处理经常先把它置 ``false``，面板却还画在屏幕上；
* 有些组件库只在首次交互时写一次，之后再也不更新；有些是纯无障碍镜像（视觉隐藏）；
* 有些页面把它写成字符串/布尔不一致（``aria-expanded=""``、``"TRUE"``）。

因此本模块对它的用法只有一条：**记录，进诊断字段，不参与任何 open/closed 结论**。
真正的判据是「面板句柄」（``alive``）：点击/展开的那一刻把面板元素本身记下来
（CDP objectId），之后只问这一个节点还在不在、是不是 ``display:none``。
它不依赖任何框架约定，也不受页面上其它浮层影响。
"""

from __future__ import annotations

from typing import Any, Optional

# ``alive`` 的三态。**不要合并**：``-1``（没跟踪）与 ``0``（跟丢了/不在）语义完全不同，
# 前者只能退回旧口径，后者是「这个节点确实不在了」的强证据（BID109）。
ALIVE_UNKNOWN = -1  # BID109
ALIVE_GONE = 0  # BID109
ALIVE_ALIVE = 1  # BID109


def _as_int(value: Any, default: int = -1) -> int:  # BID109
    try:  # BID109
        return int(value)  # BID109
    except (TypeError, ValueError):  # BID109
        return default  # BID109


def _state(value: Any) -> dict:  # BID109
    return value if isinstance(value, dict) else {}  # BID109


def tracked(state: Any) -> bool:  # BID109
    return bool(_state(state).get("tracked"))  # BID109


def alive_n(state: Any) -> int:  # BID109
    """被跟踪的候选面板里「此刻仍在场且可见」的个数；没跟踪时返回 ``-1``。"""
    data = _state(state)  # BID109
    if not data.get("tracked"):  # BID109
        return -1  # BID109
    return _as_int(data.get("alive_n", 0), 0)  # BID109


def tracked_n(state: Any) -> int:  # BID109
    """动作前认领到的候选个数（基线），没跟踪时返回 ``0``。"""
    data = _state(state)  # BID109
    if not data.get("tracked"):  # BID109
        return 0  # BID109
    return _as_int(data.get("tracked_n", 0), 0)  # BID109


def alive(state: Any) -> int:  # BID109
    """被跟踪候选面板的**粗粒度**三态：``1`` 还有 / ``0`` 全没了 / ``-1`` 未跟踪。

    精细判据用 :func:`alive_n`（几个还在）；这里只是给「只要一个布尔」的地方
    一个统一的读法，避免调用方各自写 ``alive_n > 0`` 而漏掉 ``-1`` 的语义。
    """  # BID109
    count = alive_n(state)  # BID109
    if count < 0:  # BID109
        return ALIVE_UNKNOWN  # BID109
    return ALIVE_ALIVE if count > 0 else ALIVE_GONE  # BID109


def aria_expanded(state: Any) -> Optional[bool]:  # BID109
    """触发器上的 ``aria-expanded`` 三态：``True``/``False``/``None``（缺失或不可解析）。

    **只用于诊断字段**（``panel.hints.aria``），不参与任何判定。理由见模块 docstring：
    这个字段可能挂在包装层、可能 stale、可能是 a11y 镜像。把它当判据是本模块从
    BID089 起的错误示范的重演（用「像状态的字段」代替「真实状态」）。
    """  # BID109
    data = _state(state)  # BID109
    value = data.get("aria_expanded", None)  # BID109
    if value is True or value is False:  # BID109
        return bool(value)  # BID109
    if value in (1, 0):  # BID109
        return bool(value)  # BID109
    return None  # BID109


def page_hidden(state: Any) -> bool:  # BID109
    return bool(_state(state).get("page_hidden", False))  # BID109


def opened_since(before: Any, after: Any) -> bool:  # BID109
    """相对动作前基线，判断「这个控件旁**刚刚出现**了面板」。

    证据按强弱排序，任一成立即 ``True``：

    1. **句柄**：被认领的候选面板「活着」的个数变多（``after.alive_n > before.alive_n``）
       ——例如面板被点关后又挂回，原来的节点重新可见。这是唯一能区分「面板新开了」
       与「页面上一直有别的浮层」的证据。
    2. 贴本控件的候选面板数 ``near`` 变多（BID092）。
    3. 同口径浮层盒子数 ``count`` 变多（BID091），且**没有**被「``near`` 变少」否决
       （BID093：同一击关掉面板又插入两个无关浮层时总数会变多，兜底不得否决主判据）。
    4. 锚定面板从无到有（``anchored`` 假→真，BID089/BID091）。

    取不到数（``count == -1``）或基线本来就锚定且计数没增加时返回 ``False``：
    宁可少一次「已打开」的断言，也不要把反向操作说成成功（BID060/BID090）。
    """  # BID109
    after_data = _state(after)  # BID109
    if not after_data:  # BID109
        return False  # BID109
    before_data = _state(before)  # BID109
    # 1) 句柄：被认领候选的「存活个数」变多。只有两端都有句柄时才可比较。
    if tracked(before_data) and tracked(after_data):  # BID109
        if alive_n(after_data) > alive_n(before_data):  # BID109
            return True  # BID109
    before_n = _as_int(before_data.get("count", -1))  # BID109
    after_n = _as_int(after_data.get("count", -1))  # BID109
    before_near = _as_int(before_data.get("near", -1))  # BID109
    after_near = _as_int(after_data.get("near", -1))  # BID109
    near_decreased = (  # BID109
        before_near >= 0 and after_near >= 0 and after_near < before_near  # BID109
    )  # BID109
    if (  # BID109
        before_n >= 0  # BID109
        and after_n >= 0  # BID109
        and after_n > before_n  # BID109
        and not near_decreased  # BID109
    ):  # BID109
        return True  # BID109
    if before_near >= 0 and after_near >= 0 and after_near > before_near:  # BID109
        return True  # BID109
    return bool(after_data.get("anchored")) and not bool(before_data.get("anchored"))  # BID109


def still_present(after: Any, before_near: int = -1, after_near: int = -1) -> bool:  # BID109
    """面板**仍在**的直接证据（用于否决「被关掉」）。

    两级：

    * 句柄：被认领的候选里还有活着的（``alive_n > 0``）——最强，与 ``count`` /
      ``near``、候选行数都无关；
    * ``near`` 可观测且一个没少（BID101，``before_near > 0 and after_near >= before_near``）。

    只在能观测到时返回 ``True``；``near == 0`` / ``-1`` 是探测盲区，不能否决
    （BID101 的原始边界：面板太窄/太远时 ``near`` 本来就看不见它）。
    """  # BID109
    count = alive_n(after)  # BID109
    if count > 0:  # BID109
        return True  # BID109
    return before_near > 0 and after_near >= before_near  # BID109


def closed_since(before: Any, after: Any) -> bool:  # BID109
    """相对动作前基线，判断「这一击把面板关掉了」（补点闸门）。

    句柄优先，**它可以单独下结论**（这是与旧口径最大的区别）：

    * 被认领的候选里**活着的个数没减少** ⇒ ``False``：面板就是其中之一，``count``
      变少只能说明别的浮层消失了（BID101 的 toast/校验提示、BID102 的「筛到只剩 1 行」
      都属于这一类）。注意这里数的是**节点个数**，不是候选行数——筛掉两行不会让
      任何一个节点消失，这正是 BID102 想要的语义；
    * 活着的个数**减少**（哪怕还剩一个常驻提示）且页面可见 ⇒ ``True``：真面板不在
      了，``count`` 变多也无关（BID093 的反向情形）；
    * 页面不可见（``page_hidden``）时句柄的「可见」不可信 ⇒ 不下结论，退回旧口径
      （BID072：不可观测 ⇒ 保守）。

    没有句柄时退回 BID093/101/102 的老口径（``count`` 变少必须被 ``near`` 背书）。
    """  # BID109
    before_data = _state(before)  # BID109
    after_data = _state(after)  # BID109
    if tracked(after_data) and not page_hidden(after_data):  # BID109
        before_count = alive_n(before_data)  # BID109
        after_count = alive_n(after_data)  # BID109
        if after_count == 0:  # BID109
            return True  # BID109
        if before_count >= 0 and after_count < before_count:  # BID109
            return True  # BID109
        return False  # BID109
    before_n = _as_int(before_data.get("count", -1))  # BID109
    after_n = _as_int(after_data.get("count", -1))  # BID109
    before_near = _as_int(before_data.get("near", -1))  # BID109
    after_near = _as_int(after_data.get("near", -1))  # BID109
    if still_present(after_data, before_near, after_near):  # BID109
        # 候选面板一个没少：``count`` 的下降来自无关浮层，不是面板被关。
        return False  # BID109
    if before_n >= 0 and after_n >= 0 and after_n < before_n:  # BID109
        return True  # BID109
    return before_near >= 0 and after_near >= 0 and after_near < before_near  # BID109


def verdict(before: Any, after: Any, *, fallback: str = "unknown") -> str:  # BID109
    """给「这次动作之后面板是什么状态」一个**唯一的**结论，供返回的 ``panel`` 字段使用。

    只在句柄能下定论时给出 ``open``/``closed``；其余一律 ``unknown``（含探测盲区、
    页面上/下不可观测、没有句柄）。调用方可以用 ``fallback`` 表达它自己的先验
    （例如「按契约应当打开却没观测到」时传 ``"unknown"``，而不是 ``"closed"``）。
    """  # BID109
    after_data = _state(after)  # BID109
    count = alive_n(after_data)  # BID109
    if count > 0:  # BID109
        return "open"  # BID109
    if count == 0 and not page_hidden(after_data):  # BID109
        return "closed"  # BID109
    return fallback  # BID109
