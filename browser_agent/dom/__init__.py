"""DOM capture / build / classify / serialize / registry / diff."""

from .build import PAGE_SCROLL_TAG, EnhancedNode, EnhancedTree, build_enhanced_tree
from .capture import capture_raw, read_outer_html, viewport_from_metrics
from .classify import classify, is_clickable, is_draggable, is_input, is_scrollable
from .diff import changed_lines, compute_diff, compute_lost, format_lines, format_lost
from .registry import NameRegistry
from .serialize import DOMSerializer, OutLine

__all__ = [
    "PAGE_SCROLL_TAG",
    "EnhancedNode",
    "EnhancedTree",
    "build_enhanced_tree",
    "capture_raw",
    "read_outer_html",
    "viewport_from_metrics",
    "classify",
    "is_clickable",
    "is_draggable",
    "is_input",
    "is_scrollable",
    "compute_diff",
    "changed_lines",
    "format_lines",
    "compute_lost",
    "format_lost",
    "NameRegistry",
    "DOMSerializer",
    "OutLine",
]
