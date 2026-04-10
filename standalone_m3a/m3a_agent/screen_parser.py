"""Hierarchical screen parser for AgentLens-style UI grouping.

Parses uiautomator XML into semantic groups that preserve parent-child
relationships, enabling the LLM to select meaningful UI regions (e.g.,
an entire card) rather than individual leaf nodes.

Based on the DOM-based parsing approach described in:
  AgentLens: Adaptive Visual Modalities for Human-Agent Interaction
  in Mobile GUI Agents (UIST '26)
"""

import dataclasses
import xml.etree.ElementTree as ET
from typing import Optional

from m3a_agent.env.representation_utils import BoundingBox


# Classes considered layout wrappers (no semantic meaning)
_LAYOUT_CLASSES = frozenset([
    'android.widget.FrameLayout',
    'android.widget.LinearLayout',
    'android.widget.RelativeLayout',
    'android.view.ViewGroup',
    'android.view.View',
    'android.widget.ScrollView',
    'android.widget.HorizontalScrollView',
    'androidx.recyclerview.widget.RecyclerView',
    'androidx.constraintlayout.widget.ConstraintLayout',
    'androidx.coordinatorlayout.widget.CoordinatorLayout',
    'androidx.appcompat.widget.LinearLayoutCompat',
])

# Classes considered meaningful content nodes
_CONTENT_CLASSES = frozenset([
    'android.widget.TextView',
    'android.widget.EditText',
    'android.widget.ImageView',
    'android.widget.Button',
    'android.widget.CheckBox',
    'android.widget.RadioButton',
    'android.widget.Switch',
    'android.widget.ToggleButton',
    'android.widget.Spinner',
    'android.widget.ProgressBar',
    'android.widget.RatingBar',
    'android.widget.SeekBar',
    'android.widget.ImageButton',
])


@dataclasses.dataclass
class UIGroup:
  """A semantic group of UI elements with a combined bounding box."""

  index: int
  label: str  # human-readable summary
  bbox: BoundingBox
  depth: int  # depth in the original tree
  children_texts: list[str]  # text contents of child nodes
  is_clickable: bool
  is_scrollable: bool
  class_name: str
  resource_id: str
  child_count: int  # number of meaningful children

  @property
  def description(self) -> str:
    """Short description for the LLM prompt."""
    parts = []
    if self.label:
      parts.append(self.label)
    if self.children_texts:
      texts = [t for t in self.children_texts if t != self.label]
      if texts:
        parts.append(' | '.join(texts[:5]))
    tags = []
    if self.is_clickable:
      tags.append('clickable')
    if self.is_scrollable:
      tags.append('scrollable')
    if tags:
      parts.append(f'[{", ".join(tags)}]')
    return ' — '.join(parts) if parts else self.class_name.split('.')[-1]


def _parse_bounds(bounds_str: str) -> Optional[BoundingBox]:
  """Parse '[x1,y1][x2,y2]' into a BoundingBox."""
  if not bounds_str:
    return None
  try:
    coords = list(map(int, bounds_str.strip('[]').replace('][', ',').split(',')))
    if len(coords) == 4:
      return BoundingBox(coords[0], coords[2], coords[1], coords[3])
  except (ValueError, IndexError):
    pass
  return None


def _has_content(node: ET.Element) -> bool:
  """Check if a node has meaningful content (text, desc, or is interactive)."""
  if node.get('text', '').strip():
    return True
  if node.get('content-desc', '').strip():
    return True
  if node.get('clickable') == 'true':
    return True
  if node.get('checkable') == 'true':
    return True
  return False


def _collect_texts(node: ET.Element) -> list[str]:
  """Recursively collect all text/content-desc from descendants."""
  texts = []
  text = node.get('text', '').strip()
  if text:
    texts.append(text)
  desc = node.get('content-desc', '').strip()
  if desc and desc != text:
    texts.append(desc)
  for child in node:
    texts.extend(_collect_texts(child))
  return texts


def _count_content_nodes(node: ET.Element) -> int:
  """Count nodes with meaningful content (text, desc, or interactive)."""
  count = 0
  if _has_content(node):
    count = 1
  for child in node:
    count += _count_content_nodes(child)
  return count


def _is_visible(node: ET.Element, screen_w: int = 1080, screen_h: int = 1920) -> bool:
  """Check if a node is visible on screen."""
  bbox = _parse_bounds(node.get('bounds', ''))
  if bbox is None:
    return False
  if bbox.width <= 0 or bbox.height <= 0:
    return False
  if bbox.x_max <= 0 or bbox.y_max <= 0:
    return False
  if bbox.x_min >= screen_w or bbox.y_min >= screen_h:
    return False
  return True


def _merge_bounds(boxes: list[BoundingBox]) -> BoundingBox:
  """Merge multiple bounding boxes into one encompassing box."""
  return BoundingBox(
      x_min=min(b.x_min for b in boxes),
      x_max=max(b.x_max for b in boxes),
      y_min=min(b.y_min for b in boxes),
      y_max=max(b.y_max for b in boxes),
  )


def _is_semantic_group(node: ET.Element) -> bool:
  """Determine if a node is a semantic group worth indexing.

  A semantic group is a non-leaf node that:
  - Has multiple content-bearing children (or is itself interactive)
  - Is not the root/full-screen wrapper
  - Has reasonable size (not full-screen, not tiny)
  """
  cls = node.get('class', '')
  children = list(node)
  bbox = _parse_bounds(node.get('bounds', ''))

  if bbox is None:
    return False

  # Skip full-screen containers (likely just wrappers)
  if bbox.width >= 1060 and bbox.height >= 1880:
    return False

  # Skip tiny elements
  if bbox.area < 2000:
    return False

  content_count = _count_content_nodes(node)

  # A group needs at least 2 content children to be meaningful
  if content_count >= 2:
    return True

  # Interactive node with at least 1 content child
  if node.get('clickable') == 'true' and content_count >= 1:
    return True

  return False


def parse_ui_groups(
    xml_string: str,
    screen_width: int = 1080,
    screen_height: int = 1920,
    min_area: int = 5000,
    max_groups: int = 30,
) -> list[UIGroup]:
  """Parse uiautomator XML into semantic UI groups.

  Walks the accessibility tree and identifies nodes that represent
  semantically coherent groups (e.g., cards, list items, toolbars).
  Each group gets an index that the LLM can reference.

  Args:
    xml_string: Raw XML from uiautomator dump.
    screen_width: Virtual display width in pixels.
    screen_height: Virtual display height in pixels.
    min_area: Minimum bounding box area to include a group.
    max_groups: Maximum number of groups to return.

  Returns:
    Sorted list of UIGroup objects, indexed from 0.
  """
  root = ET.fromstring(xml_string)
  groups: list[UIGroup] = []

  def walk(node: ET.Element, depth: int = 0):
    # Skip visibility check for nodes without bounds (e.g., <hierarchy> root)
    if node.get('bounds') and not _is_visible(node, screen_width, screen_height):
      return

    if _is_semantic_group(node):
      bbox = _parse_bounds(node.get('bounds', ''))
      if bbox and bbox.area >= min_area:
        texts = _collect_texts(node)
        label = texts[0] if texts else ''
        cls = node.get('class', '')

        groups.append(UIGroup(
            index=-1,  # assigned later
            label=label,
            bbox=bbox,
            depth=depth,
            children_texts=texts,
            is_clickable=node.get('clickable') == 'true',
            is_scrollable=node.get('scrollable') == 'true',
            class_name=cls,
            resource_id=node.get('resource-id', ''),
            child_count=_count_content_nodes(node),
        ))

    for child in node:
      walk(child, depth + 1)

  walk(root)

  # Remove groups that are fully contained within a smaller group
  # (prefer the tightest fit)
  filtered = _remove_redundant_groups(groups)

  # Sort by vertical position (top to bottom), then left to right
  filtered.sort(key=lambda g: (g.bbox.y_min, g.bbox.x_min))

  # Limit and assign indices
  filtered = filtered[:max_groups]
  for i, group in enumerate(filtered):
    group.index = i

  return filtered


def _remove_redundant_groups(groups: list[UIGroup]) -> list[UIGroup]:
  """Remove near-duplicate and redundant wrapper groups.

  1. Merge groups with nearly identical bounds (keep the one with more info).
  2. Remove groups whose bounds match a parent that also has the same
     content count (pure wrapper).
  """
  if not groups:
    return groups

  # Step 1: Remove near-duplicates (similar bounds within 30px)
  keep = []
  for g in groups:
    is_dup = False
    for other in keep:
      if (abs(g.bbox.x_min - other.bbox.x_min) < 30 and
          abs(g.bbox.y_min - other.bbox.y_min) < 30 and
          abs(g.bbox.x_max - other.bbox.x_max) < 30 and
          abs(g.bbox.y_max - other.bbox.y_max) < 30):
        # Keep the one with a better label
        if not other.label and g.label:
          keep.remove(other)
          keep.append(g)
        is_dup = True
        break
    if not is_dup:
      keep.append(g)

  # Step 2: Remove pure wrappers — groups that contain exactly one
  # child group covering most of their area
  filtered = []
  for g in keep:
    is_wrapper = False
    for other in keep:
      if g is other:
        continue
      # Check if 'other' is inside 'g' and covers most of g's area
      if (other.bbox.x_min >= g.bbox.x_min - 10 and
          other.bbox.y_min >= g.bbox.y_min - 10 and
          other.bbox.x_max <= g.bbox.x_max + 10 and
          other.bbox.y_max <= g.bbox.y_max + 10 and
          other.bbox.area > g.bbox.area * 0.8 and
          other is not g):
        # g is a wrapper around other — but only discard g if
        # g has no unique label
        if not g.label or g.label == other.label:
          is_wrapper = True
          break
    if not is_wrapper:
      filtered.append(g)

  return filtered


def format_groups_for_llm(groups: list[UIGroup]) -> str:
  """Format UI groups into a text representation for the LLM prompt.

  Returns a numbered list like:
    [0] Search bar — "Search here" [clickable] (29,18)-(923,90)
    [1] Mountain View card — "Mountain View" (40,1394)-(1040,1449)
    [2] Image carousel — [scrollable] (0,1482)-(1080,1920)
  """
  lines = []
  for g in groups:
    bbox = g.bbox
    coords = f'({bbox.x_min},{bbox.y_min})-({bbox.x_max},{bbox.y_max})'
    lines.append(f'[{g.index}] {g.description} {coords}')
  return '\n'.join(lines)
