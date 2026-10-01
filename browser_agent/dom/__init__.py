"""DOM capture / build / classify / serialize / registry / diff."""

from .build import (
    PAGE_SCROLL_TAG,
    EnhancedNode,
    EnhancedTree,
    build_enhanced_tree,
    in_collapsed_overlay_portal,
)
from .capture import capture_raw, read_outer_html, viewport_from_metrics
from .classify import (
    classify,
    has_svg_descendant,
    has_text,
    is_clickable,
    is_control_icon,
    is_cursor_pointer_only,
    is_draggable,
    is_input,
    is_range_picker_end,
    is_range_picker_entry,
    is_scrollable,
    is_select,
    may_navigate,
    picker_range_position,
)
from .diff import changed_lines, compute_diff, compute_lost, format_lines, format_lost
from .registry import NameCounter, NameRegistry
from .serialize import DOMSerializer, OutLine

__all__ = [
    "PAGE_SCROLL_TAG",
    "EnhancedNode",
    "EnhancedTree",
    "build_enhanced_tree",
    "in_collapsed_overlay_portal",
    "capture_raw",
    "read_outer_html",
    "viewport_from_metrics",
    "classify",
    "has_svg_descendant",
    "has_text",
    "is_clickable",
    "is_control_icon",
    "is_cursor_pointer_only",
    "is_draggable",
    "is_input",
    "is_range_picker_end",
    "is_range_picker_entry",
    "picker_range_position",
    "is_scrollable",
    "is_select",
    "may_navigate",
    "compute_diff",
    "changed_lines",
    "format_lines",
    "compute_lost",
    "format_lost",
    "NameRegistry",
    "NameCounter",
    "DOMSerializer",
    "OutLine",
]
