"""Controller for Android that captures screenshots and UI trees via ADB."""

from typing import Optional

from absl import logging
from m3a_agent.env import adb_utils
from m3a_agent.env import representation_utils
from m3a_agent.env.adb_controller import AdbController
import numpy as np


DEFAULT_ADB_PATH = '~/Android/Sdk/platform-tools/adb'

OBSERVATION_KEY_UI_ELEMENTS = 'ui_elements'


class AndroidWorldController:
  """Controller for an Android instance using direct ADB commands."""

  def __init__(
      self,
      adb: AdbController,
      display_id: int | None = None,
  ):
    self._adb = adb
    self._display_id = display_id
    self._external_xml: str | None = None
    self._screenshot_fn = None  # Optional: callable returning np.ndarray

  @property
  def adb(self) -> AdbController:
    return self._adb

  @property
  def display_id(self) -> int | None:
    return self._display_id

  @property
  def device_screen_size(self) -> tuple[int, int]:
    """Returns the physical screen size of the device: (width, height)."""
    return adb_utils.get_screen_size(self._adb)

  @property
  def logical_screen_size(self) -> tuple[int, int]:
    return adb_utils.get_logical_screen_size(self._adb)

  def get_screenshot(self) -> np.ndarray:
    """Captures a screenshot and returns it as a numpy RGB array."""
    if self._screenshot_fn is not None:
      result = self._screenshot_fn()
      if result is not None:
        return result
    return self._adb.screencap(display_id=self._display_id)

  def set_external_xml(self, xml: str) -> None:
    """Set UI tree XML from an external source (e.g., AccessibilityService).

    The next call to get_ui_elements() will use this XML instead of
    uiautomator dump. The XML is consumed once.
    """
    self._external_xml = xml

  def get_ui_elements(self) -> list[representation_utils.UIElement]:
    """Returns the most recent UI elements from the device."""
    if self._external_xml:
      xml = self._external_xml
      self._external_xml = None
    else:
      try:
        xml = adb_utils.uiautomator_dump(self._adb, display_id=self._display_id)
      except Exception:
        # Fallback: return empty tree (e.g., uiautomator can't access private VD)
        xml = '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0"></hierarchy>'
    self._last_xml = xml
    return representation_utils.xml_dump_to_ui_elements(xml)

  @property
  def last_xml(self) -> str:
    """Returns the raw XML from the most recent uiautomator dump."""
    return getattr(self, '_last_xml', '')

  def close(self) -> None:
    pass


def get_controller(
    adb_path: str = DEFAULT_ADB_PATH,
    serial: str | None = None,
    display_id: int | None = None,
) -> AndroidWorldController:
  """Creates a controller by connecting to an existing Android device.

  Args:
    adb_path: Path to the adb binary.
    serial: Device serial (e.g. 'emulator-5554', 'ABCD1234', '192.168.1.5:5555').
      If None, adb auto-selects the only connected device.
    display_id: If set, target input to this display.
  """
  adb = AdbController(adb_path=adb_path, serial=serial)
  logging.info('Setting up AndroidWorldController (serial=%s).', serial)
  return AndroidWorldController(adb, display_id=display_id)
