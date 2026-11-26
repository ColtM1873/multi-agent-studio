"""组件库（vendor）适配层 —— 解析/交互双层结构中的「库特征层」。

本模块于BID111引入。**本模块只做识别与安全方向的否决，不搬动既有判据**：它是
后续真实站点排查沉淀「某个库特有形状」的落点，本轮先落地编辑器这一类。

## 为什么需要这一层

网页解析的异构程度极高：同一种交互语义在不同组件库里是完全不同的 DOM 形状，而
同一个类名词在不同库里含义相反。把「某个库特有的形状」写进通用判据，等于让下一
次的陌生页面替上一次的修复买单：

* ``_is_widget_mirror``（BID050）为了丢弃 AntD/ATSX 可搜索下拉旁的「测宽镜像
  span」，用了通用判据「class 以 ``mirror`` 结尾」。tiptap 的编辑器根类名恰好是
  ``ProseMirror``，于是**整个富文本编辑器被当成测量脚手架剪掉**——页面上所有
  ``contenteditable``（项目职责 / 项目成果 / 自我评价 / 职责和业绩）在序列化里
  彻底消失，LLM 只能看到工具栏按钮，判定「工具抓不到可输入元素」并中止填表
  （2026-11-26 国聘简历页，BID111）。

## 两层结构的分工（设计哲学）

1. **通用层（generic）**：只认 Web 标准语义 —— ``tag`` / ``role`` / ``aria-*`` /
   ``contenteditable`` / ``label`` / ``disabled`` / 结构关系。不出现任何具体组件库
   的类名。它对任何页面都成立，因此只允许做**保守的判定**和**安全的否决**。
   落地位置：``dom/classify.py::is_input``、``dom/serialize.py::_has_editable_descendant``。
2. **组件库层（vendor，本模块）**：只列**具名**库的类名 / data 属性特征。它的结论
   **只往「不剪枝、不降级、不误报」的安全方向** override 通用启发式，绝不把通用层
   已认定为「可交互 / 可编辑」的东西剪掉。每个真实站点排查得到的新知识落在这里的
   某个具名条目上，而不是塞进通用判据。

命中的方向性很关键：本模块只用于回答「这是不是某个已知库的特征标志」，而且只被
用来**阻止**一次剪枝/降级，或用于 debug 提示。因此识别宽一点只会少剪、不会多剪。

## debug 集成

``page_vendors()`` 给出「这一页识别到了哪些编辑器组件库」，写入 debug 日志头部；
``describe_prune_override()`` 让 vendor 层在**否决**一次通用剪枝时留痕。下次排障
能直接看出「这条输出是被通用层判的，还是被某个 vendor override 过」。
"""

from __future__ import annotations

from typing import Iterable

# ---------------------------------------------------------------------------
# 编辑器组件库：它们的根节点 / 包装层就是「可编辑区域」。
# 任何「剪枝 / 降级为文本 / 当作装饰」的启发式都必须为它们让路。
#
# 匹配方式：class/id 的小写串里出现下列任一 token 即认定。用「包含」而不是
# 「精确等于」，因为同一库的类名带后缀（``ProseMirror-focused``、``cm-editor``）。
# 方向是安全的（命中结果 = 不剪枝），所以略宽可接受。
# ---------------------------------------------------------------------------
_EDITOR_VENDOR_TOKENS: dict[str, tuple[str, ...]] = {
    "tiptap": ("tiptap", "prosemirror"),
    "codemirror": ("codemirror", "cm-editor", "cm-content", "cm-scroller"),
    "quill": ("ql-editor", "ql-container"),
    "wangeditor": ("w-e-text", "w-e-text-container"),
    "draftjs": ("drafteditor-root", "public-drafteditor-content"),
    "slate": ("slate-editor", "slate-editable"),
    "lexical": ("lexical",),
    "ckeditor": ("ck-editor", "cke_editable", "ck-content"),
    "tinymce": ("mce-content-body", "tox-tinymce"),
    "froala": ("fr-element", "fr-view"),
    "summernote": ("note-editable",),
    "trumbowyg": ("trumbowyg-editor",),
    "medium": ("medium-editor",),
}

# 这些组件库把「可编辑」语义挂在 data 属性上（根节点可能没有 contenteditable）。
_EDITOR_DATA_ATTRS: dict[str, str] = {
    "data-lexical-editor": "lexical",
    "data-slate-editor": "slate",
}

# 测宽镜像（mirror）类名形状：真实 mirror 是「与输入框配对、纯文本、不可交互」的
# 测量脚手架。名字形状本身仍是通用启发式，但**只有通用层没有认定为可编辑、且不
# 属于任何编辑器组件库时**才允许生效（见 ``dom/serialize.py::_is_widget_mirror``）。
_MIRROR_CLASS_HINTS: tuple[str, ...] = ("mirror",)


def _class_id_text(node) -> str:
    """节点 class + id 的小写串（vendor 识别的唯一输入）。"""
    attributes = getattr(node, "attributes", None) or {}
    return f"{attributes.get('class', '')} {attributes.get('id', '')}".lower()


def detect_editor_vendor(node, max_hops: int = 6) -> str:
    """返回 ``node`` 自身或祖先（≤``max_hops`` 层）所属的编辑器组件库名。

    返回空串表示「不认识这个编辑器 / 这里不是编辑器」。识别是**安全方向**的：
    命中只会让剪枝类判据放行，不会新增任何剪枝。
    """
    current = node
    hops = 0
    while current is not None and hops <= max_hops:
        if getattr(current, "is_element", False):
            attributes = getattr(current, "attributes", None) or {}
            for attr, vendor in _EDITOR_DATA_ATTRS.items():
                if attr in attributes:
                    return vendor
            raw = _class_id_text(current)
            if raw.strip():
                for vendor, tokens in _EDITOR_VENDOR_TOKENS.items():
                    if any(token in raw for token in tokens):
                        return vendor
        current = getattr(current, "parent", None)
        hops += 1
    return ""


def is_editor_vendor_node(node) -> bool:
    """True 若节点属于某个编辑器组件库（自身或近祖先命中类名/data 特征）。"""
    return bool(detect_editor_vendor(node))


def is_measure_mirror_class(node) -> bool:
    """True 若节点的类名**形状**像测宽镜像（不含任何结构护栏）。

    调用方必须先用通用层护栏（可编辑区域 / 含可编辑子树）与编辑器 vendor 否决
    之后再使用本函数，见 ``dom/serialize.py::_is_widget_mirror``。
    """
    attributes = getattr(node, "attributes", None) or {}
    classes = attributes.get("class", "")
    if not classes or not any(hint in classes.lower() for hint in _MIRROR_CLASS_HINTS):
        return False
    for token in classes.split():
        low = token.lower()
        if low.endswith("mirror") or "-mirror" in low or "_mirror" in low:
            return True
    return False


def describe_prune_override(node) -> str:
    """描述「vendor 层为什么要否决一次通用剪枝」（给 debug 备注用，可为空串）。"""
    vendor = detect_editor_vendor(node)
    if not vendor:
        return ""
    attributes = getattr(node, "attributes", None) or {}
    return f"vendor:editor={vendor} class={attributes.get('class', '')[:60]!r}"


def _iter_nodes(tree) -> Iterable:
    """遍历 ``EnhancedTree`` 的全部节点（``nodes`` 列表优先，回退递归 root）。"""
    nodes = getattr(tree, "nodes", None)
    if nodes:
        return list(nodes)
    root = getattr(tree, "root", None)
    out: list = []
    stack = [root] if root is not None else []
    limit = 20000
    while stack and len(out) < limit:
        cur = stack.pop()
        out.append(cur)
        stack.extend(getattr(cur, "children", None) or [])
    return out


def page_vendors(tree) -> list[str]:
    """识别整页出现的编辑器组件库，仅用于 debug 提示。

    绝不参与任何判定；识别失败一律返回空列表。
    """
    found: set[str] = set()
    try:
        for node in _iter_nodes(tree):
            if not getattr(node, "is_element", False):
                continue
            attributes = getattr(node, "attributes", None) or {}
            for attr, vendor in _EDITOR_DATA_ATTRS.items():
                if attr in attributes:
                    found.add(vendor)
            raw = _class_id_text(node)
            if not raw.strip():
                continue
            for vendor, tokens in _EDITOR_VENDOR_TOKENS.items():
                if any(token in raw for token in tokens):
                    found.add(vendor)
    except Exception:  # noqa: BLE001 - debug 提示绝不打断工具调用
        return []
    return sorted(found)
