"""Launches the environment for the standalone M3A agent."""

import platform

from absl import logging
from m3a_agent.env import android_world_controller
from m3a_agent.env import interface


def _get_env(
    adb_path: str,
    serial: str | None = None,
    display_id: int | None = None,
    app_connection=None,
) -> interface.AsyncEnv:
  """Creates an AsyncEnv by connecting to an existing Android device."""
  controller = android_world_controller.get_controller(
      adb_path=adb_path, serial=serial, display_id=display_id
  )
  return interface.AsyncAndroidEnv(controller, display_id=display_id, app_connection=app_connection)


def _increase_file_descriptor_limit(limit: int = 32768):
  """Increases the file descriptor limit to the given limit."""
  system_name = platform.system()
  if system_name == 'Windows':
    return

  try:
    import resource

    _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if limit > hard:
      logging.warning(
          (
              "Requested limit %d exceeds the system's hard limit %d. Setting"
              ' to the maximum allowed value.'
          ),
          limit,
          hard,
      )
      limit = hard

    current_soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if current_soft_limit < limit:
      resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
      logging.info('File descriptor limit set to %d.', limit)
  except ValueError as e:
    logging.exception('Failed to set file descriptor limit: %s', e)


def load_and_setup_env(
    adb_path: str = android_world_controller.DEFAULT_ADB_PATH,
    serial: str | None = None,
    display_id: int | None = None,
    app_connection=None,
) -> interface.AsyncEnv:
  """Create environment by connecting to an Android device via ADB.

  Works with emulators and real devices. If only one device is connected,
  the serial can be omitted and ADB will auto-select it.

  Args:
    adb_path: The location of the adb binary.
    serial: Device serial (e.g. 'emulator-5554', 'ABCD1234'). If None, adb
      auto-selects the only connected device.
    display_id: If set, target input events to this display.

  Returns:
    An interactable Android environment.
  """
  _increase_file_descriptor_limit()
  env = _get_env(adb_path, serial=serial, display_id=display_id, app_connection=app_connection)
  return env
