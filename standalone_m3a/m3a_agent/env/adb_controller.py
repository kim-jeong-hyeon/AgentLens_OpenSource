"""Lightweight ADB controller using subprocess calls."""

import dataclasses
import io
import subprocess
from typing import Optional

import numpy as np
from PIL import Image


@dataclasses.dataclass
class AdbResult:
  """Result of an ADB command."""

  output: str
  returncode: int

  @property
  def success(self) -> bool:
    return self.returncode == 0


class AdbError(RuntimeError):
  """Raised when an ADB operation fails."""


class AdbController:
  """Executes ADB commands via subprocess."""

  def __init__(
      self,
      adb_path: str = 'adb',
      serial: Optional[str] = None,
  ):
    self.adb_path = adb_path
    self.serial = serial

  def _base_cmd(self) -> list[str]:
    cmd = [self.adb_path]
    if self.serial:
      cmd += ['-s', self.serial]
    return cmd

  def run(
      self,
      args: list[str] | str,
      timeout: Optional[float] = 10,
  ) -> AdbResult:
    """Run an ADB command and return the result."""
    cmd = self._base_cmd()
    if isinstance(args, str):
      args = args.split(' ')
    cmd += list(args)
    try:
      result = subprocess.run(
          cmd,
          capture_output=True,
          timeout=timeout,
      )
    except subprocess.TimeoutExpired as e:
      raise AdbError(f'ADB command timed out: {" ".join(cmd)}') from e
    output = result.stdout.decode('utf-8', errors='replace')
    return AdbResult(output=output, returncode=result.returncode)

  def run_bytes(
      self,
      args: list[str] | str,
      timeout: Optional[float] = 10,
  ) -> bytes:
    """Run an ADB command and return raw stdout bytes."""
    cmd = self._base_cmd()
    if isinstance(args, str):
      args = args.split(' ')
    cmd += list(args)
    try:
      result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
      raise AdbError(f'ADB command timed out: {" ".join(cmd)}') from e
    if result.returncode != 0:
      raise AdbError(
          f'ADB command failed (rc={result.returncode}): '
          f'{result.stderr.decode("utf-8", errors="replace")}'
      )
    return result.stdout

  def screencap(self, display_id: Optional[int] = None) -> np.ndarray:
    """Capture a screenshot and return it as a numpy RGB array.

    Args:
      display_id: If set, capture from this display (e.g., virtual display).
    """
    if display_id is not None:
      cmd = ['exec-out', 'screencap', '-d', str(display_id), '-p']
    else:
      cmd = ['exec-out', 'screencap', '-p']
    png_bytes = self.run_bytes(cmd, timeout=15)
    image = Image.open(io.BytesIO(png_bytes)).convert('RGB')
    return np.array(image)
