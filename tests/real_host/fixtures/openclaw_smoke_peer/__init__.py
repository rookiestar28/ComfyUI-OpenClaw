"""A minimal peer custom node used only by the real-host smoke lane.

The sidebar mount handover this lane has to observe is a transition from one
*custom* tab to another custom tab with no outgoing destroy callback. A stock
host has exactly one custom tab in this lane, and code under a reference checkout
may not be executed, so the second tab has to be one this repository owns.

This node registers nothing but a sidebar tab that renders a single element. It
declares no inputs, no outputs, and no execution, it never reaches the product
package, and no product code imports it.
"""

WEB_DIRECTORY = "./web"

NODE_CLASS_MAPPINGS: dict[str, type] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
