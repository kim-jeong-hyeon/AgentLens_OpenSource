# Copyright 2026 The android_world Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tools for processing and representing accessibility trees."""

import dataclasses
from typing import Any, Optional
import xml.etree.ElementTree as ET


@dataclasses.dataclass
class BoundingBox:
  """Class for representing a bounding box."""

  x_min: float | int
  x_max: float | int
  y_min: float | int
  y_max: float | int

  @property
  def center(self) -> tuple[float, float]:
    """Gets center of bounding box."""
    return (self.x_min + self.x_max) / 2.0, (self.y_min + self.y_max) / 2.0

  @property
  def width(self) -> float | int:
    """Gets width of bounding box."""
    return self.x_max - self.x_min

  @property
  def height(self) -> float | int:
    """Gets height of bounding box."""
    return self.y_max - self.y_min

  @property
  def area(self) -> float | int:
    return self.width * self.height


@dataclasses.dataclass
class UIElement:
  """Represents a UI element."""

  text: Optional[str] = None
  content_description: Optional[str] = None
  class_name: Optional[str] = None
  bbox: Optional[BoundingBox] = None
  bbox_pixels: Optional[BoundingBox] = None
  hint_text: Optional[str] = None
  is_checked: Optional[bool] = None
  is_checkable: Optional[bool] = None
  is_clickable: Optional[bool] = None
  is_editable: Optional[bool] = None
  is_enabled: Optional[bool] = None
  is_focused: Optional[bool] = None
  is_focusable: Optional[bool] = None
  is_long_clickable: Optional[bool] = None
  is_scrollable: Optional[bool] = None
  is_selected: Optional[bool] = None
  is_visible: Optional[bool] = None
  package_name: Optional[str] = None
  resource_name: Optional[str] = None
  tooltip: Optional[str] = None
  resource_id: Optional[str] = None
  metadata: Optional[dict[str, Any]] = None


def _normalize_bounding_box(
    node_bbox: BoundingBox,
    screen_width_height_px: tuple[int, int],
) -> BoundingBox:
  width, height = screen_width_height_px
  return BoundingBox(
      node_bbox.x_min / width,
      node_bbox.x_max / width,
      node_bbox.y_min / height,
      node_bbox.y_max / height,
  )


def _parse_ui_hierarchy(xml_string: str) -> dict[str, Any]:
  """Parses the UI hierarchy XML into a dictionary structure."""
  root = ET.fromstring(xml_string)

  def parse_node(node):
    result = node.attrib
    result['children'] = [parse_node(child) for child in node]
    return result

  return parse_node(root)


def xml_dump_to_ui_elements(xml_string: str) -> list[UIElement]:
  """Converts a UI hierarchy XML dump from uiautomator dump to UIElements."""
  parsed_hierarchy = _parse_ui_hierarchy(xml_string)
  ui_elements = []

  def process_node(node, is_root):
    bounds = node.get('bounds')
    if bounds:
      x_min, y_min, x_max, y_max = map(
          int, bounds.strip('[]').replace('][', ',').split(',')
      )
      bbox = BoundingBox(x_min, x_max, y_min, y_max)
    else:
      bbox = None

    ui_element = UIElement(
        text=node.get('text'),
        content_description=node.get('content-desc'),
        class_name=node.get('class'),
        bbox=bbox,
        bbox_pixels=bbox,
        is_checked=node.get('checked') == 'true',
        is_checkable=node.get('checkable') == 'true',
        is_clickable=node.get('clickable') == 'true',
        is_enabled=node.get('enabled') == 'true',
        is_focused=node.get('focused') == 'true',
        is_focusable=node.get('focusable') == 'true',
        is_long_clickable=node.get('long-clickable') == 'true',
        is_scrollable=node.get('scrollable') == 'true',
        is_selected=node.get('selected') == 'true',
        package_name=node.get('package'),
        resource_id=node.get('resource-id'),
        is_visible=True,
    )
    if not is_root:
      ui_elements.append(ui_element)

    for child in node.get('children', []):
      process_node(child, is_root=False)

  process_node(parsed_hierarchy, is_root=True)
  return ui_elements
