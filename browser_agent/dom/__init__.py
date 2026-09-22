"""DOM capture / build / classify / serialize / registry / diff."""

from .build import EnhancedNode, EnhancedTree, build_enhanced_tree
from .capture import capture_raw, viewport_from_metrics
from .classify import classify, is_clickable, is_draggable, is_input, is_scrollable
from .diff import compute_diff
from .registry import NameRegistry
from .serialize import DOMSerializer

__all__ = [
    "EnhancedNode",
    "EnhancedTree",
    "build_enhanced_tree",
    "capture_raw",
    "viewport_from_metrics",
    "classify",
    "is_clickable",
    "is_draggable",
    "is_input",
    "is_scrollable",
    "compute_diff",
    "NameRegistry",
    "DOMSerializer",
]
