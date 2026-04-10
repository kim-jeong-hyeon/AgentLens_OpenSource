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

"""Utilties to interact with the environment using adb."""

import json
import os
import re
import time
from typing import Any, Callable, Collection, Iterable, Literal, Optional, TypeVar
import unicodedata
from absl import logging
from m3a_agent.env.adb_controller import AdbController, AdbResult, AdbError
import immutabledict

T = TypeVar('T')

_DEFAULT_TIMEOUT_SECS = 10

# pylint: disable=line-too-long
# Maps app names to the activity that should be launched to open the app.
_PATTERN_TO_ACTIVITY = immutabledict.immutabledict({
    'google chrome|chrome': (
        'com.android.chrome/com.google.android.apps.chrome.Main'
    ),
    'google chat': (
        'com.google.android.apps.dynamite/com.google.android.apps.dynamite.startup.StartUpActivity'
    ),
    'settings|system settings': 'com.android.settings/.Settings',
    'youtube|yt': (
        'com.google.android.youtube/com.google.android.apps.youtube.app.WatchWhileActivity'
    ),
    'google play|play store|gps': (
        'com.android.vending/com.google.android.finsky.activities.MainActivity'
    ),
    'gmail|gemail|google mail|google email|google mail client': (
        'com.google.android.gm/.ConversationListActivityGmail'
    ),
    'google maps|gmaps|maps|google map': (
        'com.google.android.apps.maps/com.google.android.maps.MapsActivity'
    ),
    'google photos|gphotos|photos|google photo|google pics|google images': (
        'com.google.android.apps.photos/com.google.android.apps.photos.home.HomeActivity'
    ),
    'google calendar|gcal': (
        'com.google.android.calendar/com.android.calendar.AllInOneActivity'
    ),
    'camera': 'com.android.camera2/com.android.camera.CameraLauncher',
    'audio recorder': (
        'com.dimowner.audiorecorder/com.dimowner.audiorecorder.app.welcome.WelcomeActivity'
    ),
    'google drive|gdrive|drive': (
        'com.google.android.apps.docs/.drive.startup.StartupActivity'
    ),
    'google keep|gkeep|keep': (
        'com.google.android.keep/.activities.BrowseActivity'
    ),
    'grubhub': (
        'com.grubhub.android/com.grubhub.dinerapp.android.splash.SplashActivity'
    ),
    'tripadvisor': (
        'com.tripadvisor.tripadvisor/com.tripadvisor.android.ui.launcher.LauncherActivity'
    ),
    'starbucks': 'com.starbucks.mobilecard/.main.activity.LandingPageActivity',
    'google docs|gdocs|docs': (
        'com.google.android.apps.docs.editors.docs/com.google.android.apps.docs.editors.homescreen.HomescreenActivity'
    ),
    'google sheets|gsheets|sheets': (
        'com.google.android.apps.docs.editors.sheets/com.google.android.apps.docs.editors.homescreen.HomescreenActivity'
    ),
    'google slides|gslides|slides': (
        'com.google.android.apps.docs.editors.slides/com.google.android.apps.docs.editors.homescreen.HomescreenActivity'
    ),
    'google voice|voice': (
        'com.google.android.apps.googlevoice/com.google.android.apps.googlevoice.SplashActivity'
    ),
    'clock': 'com.google.android.deskclock/com.android.deskclock.DeskClock',
    'google search|google': (
        'com.google.android.googlequicksearchbox/com.google.android.googlequicksearchbox.SearchActivity'
    ),
    'contacts': (
        'com.google.android.contacts/com.android.contacts.activities.PeopleActivity'
    ),
    'facebook|fb': 'com.facebook.katana/com.facebook.katana.LoginActivity',
    'whatsapp|wa': 'com.whatsapp/com.whatsapp.Main',
    'instagram|ig': (
        'com.instagram.android/com.instagram.mainactivity.MainActivity'
    ),
    'twitter|tweet': 'com.twitter.android/com.twitter.app.main.MainActivity',
    'snapchat|sc': 'com.snapchat.android/com.snap.mushroom.MainActivity',
    'telegram|tg': 'org.telegram.messenger/org.telegram.ui.LaunchActivity',
    'linkedin': (
        'com.linkedin.android/com.linkedin.android.authenticator.LaunchActivity'
    ),
    'spotify|spot': 'com.spotify.music/com.spotify.music.MainActivity',
    'netflix': (
        'com.netflix.mediaclient/com.netflix.mediaclient.ui.launch.UIWebViewActivity'
    ),
    'amazon shopping|amazon|amzn': (
        'com.amazon.mShop.android.shopping/com.amazon.mShop.home.HomeActivity'
    ),
    'tiktok|tt': (
        'com.zhiliaoapp.musically/com.ss.android.ugc.aweme.splash.SplashActivity'
    ),
    'discord': 'com.discord/com.discord.app.AppActivity$Main',
    'reddit': 'com.reddit.frontpage/com.reddit.frontpage.MainActivity',
    'pinterest': 'com.pinterest/com.pinterest.activity.PinterestActivity',
    'android world': 'com.example.androidworld/.MainActivity',
    'files': (
        'com.google.android.documentsui/com.android.documentsui.files.FilesActivity'
    ),
    'markor': 'net.gsantner.markor/net.gsantner.markor.activity.MainActivity',
    'clipper': 'ca.zgrs.clipper/ca.zgrs.clipper.Main',
    'messages': (
        'com.google.android.apps.messaging/com.google.android.apps.messaging.ui.ConversationListActivity'
    ),
    'simple sms messenger|simple sms': (
        'com.simplemobiletools.smsmessenger/com.simplemobiletools.smsmessenger.activities.MainActivity'
    ),
    'dialer|phone': (
        'com.google.android.dialer/com.google.android.dialer.extensions.GoogleDialtactsActivity'
    ),
    'simple calendar pro|simple calendar': (
        'com.simplemobiletools.calendar.pro/com.simplemobiletools.calendar.pro.activities.MainActivity'
    ),
    'simple gallery pro|simple gallery': (
        'com.simplemobiletools.gallery.pro/com.simplemobiletools.gallery.pro.activities.MainActivity'
    ),
    'miniwob': (
        'com.google.androidenv.miniwob/com.google.androidenv.miniwob.app.MainActivity'
    ),
    'simple draw pro': (
        'com.simplemobiletools.draw.pro/com.simplemobiletools.draw.pro.activities.MainActivity'
    ),
    'pro expense|pro expense app': (
        'com.arduia.expense/com.arduia.expense.ui.MainActivity'
    ),
    'broccoli|broccoli app|broccoli recipe app|recipe app': (
        'com.flauschcode.broccoli/com.flauschcode.broccoli.MainActivity'
    ),
    'caa|caa test|context aware access': (
        'com.google.ccc.hosted.contextawareaccess.thirdpartyapp/.ChooserActivity'
    ),
    'osmand': 'net.osmand/net.osmand.plus.activities.MapActivity',
    'tasks|tasks app|tasks.org:': (
        'org.tasks/com.todoroo.astrid.activity.MainActivity'
    ),
    'open tracks sports tracker|activity tracker|open tracks|opentracks': (
        'de.dennisguse.opentracks/de.dennisguse.opentracks.TrackListActivity'
    ),
    'joplin|joplin app': 'net.cozic.joplin/.MainActivity',
    'vlc|vlc app|vlc player': 'org.videolan.vlc/.gui.MainActivity',
    'retro music|retro|retro player': (
        'code.name.monkey.retromusic/.activities.MainActivity'
    ),
})
# pylint: enable=line-too-long

_ORIENTATIONS = {
    'portrait': '0',
    'landscape': '1',
    'portrait_reversed': '2',
    'landscape_reversed': '3',
}

# Special app names that will trigger opening the default app.
_DEFAULT_URIS: dict[str, str] = {
    'calendar': 'content://com.android.calendar',
    'browser': 'http://',
    'contacts': 'content://contacts/people/',
    'email': 'mailto:',
    'gallery': 'content://media/external/images/media/',
}


def _input_cmd_prefix(display_id: Optional[int] = None) -> list[str]:
  """Returns the ADB shell input command prefix, optionally targeting a display.

  Args:
    display_id: If set, target input to this display via `-d <displayId>`.

  Returns:
    List of ADB command args, e.g. ['shell', 'input', '-d', '2'] or
    ['shell', 'input'].
  """
  if display_id is not None:
    return ['shell', 'input', '-d', str(display_id)]
  return ['shell', 'input']


def check_ok(result: AdbResult, message=None) -> None:
  """Check an ADB result and raise RuntimeError if not successful.

  Args:
    result: AdbResult to check.
    message: Error message to raise on failure. If not specified, a
      generic "ADB command failed" error message is used.

  Raises:
    RuntimeError: If result was not successful.
  """
  if not result.success:
    if message is not None:
      raise RuntimeError(message)
    else:
      raise RuntimeError(
          f'ADB command failed (rc={result.returncode}): {result.output}'
      )


def start_activity(
    activity: str,
    extra_args: Optional[Collection[str]],
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
    display_id: Optional[int] = None,
) -> AdbResult:
  """Issues an ADB command to launch the given activity.

  Args:
    activity: The activity to launch in standard android_package/activity_name
      format.
    extra_args: Optional set of arguments to be issued with the ABD broadcast.
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.
    display_id: If set, launch activity on this display.

  Returns:
    The ADB result.
  """
  logging.info('Attempting to launch %r', activity)
  cmd = ['shell', 'am', 'start', '-n', activity]
  if extra_args:
    cmd.extend(list(extra_args))
  if display_id is not None:
    cmd.extend(['--display', str(display_id), '--windowingMode', '1'])
  result = issue_generic_request(cmd, env, timeout_sec)
  if not result.success:
    logging.error('Failed to launch activity: %r', activity)
    return result
  logging.debug('Launch package output %r', result.output)
  return result


def get_current_activity(
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
) -> tuple[Optional[str], AdbResult]:
  """Returns the full activity name that is currently opened to the user.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.

  Returns:
    A tuple (current_activity_name, adb_result) containing the string with
      the current activity or None if no current activity can be
      extracted, and the ADB result.
  """
  result = issue_generic_request(
      'shell dumpsys activity activities', env, timeout_sec
  )
  if not result.success:
    logging.warning('Failed to obtain visible task.')
    return (None, result)

  # Parse mResumedActivity line to extract the activity name.
  match = re.search(
      r'mResumedActivity:.*\{[^ ]+ [^ ]+ ([^ ]+) ', result.output
  )
  if match:
    return (match.group(1), result)
  return (None, result)


def tap_screen(
    x: int,
    y: int,
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
    display_id: Optional[int] = None,
) -> AdbResult:
  """Issues an ADB command to tap the screen at the specified point.

  Args:
    x: X coordinate on the screen, in pixels.
    y: Y coordinate on the screen, in pixels.
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.
    display_id: If set, target input to this display.

  Returns:
    The ADB result.
  """
  logging.info('Attempting to tap the screen at (%d, %d)', x, y)
  result = issue_generic_request(
      _input_cmd_prefix(display_id) + ['tap', str(x), str(y)],
      env,
      timeout_sec,
  )
  if not result.success:
    logging.error('Failed to tap the screen')
  return result


def double_tap(
    x: int,
    y: int,
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
    display_id: Optional[int] = None,
) -> AdbResult:
  """Issues two ADB commands to double tap the screen at the specified point.

  Args:
    x: X coordinate on the screen, in pixels.
    y: Y coordinate on the screen, in pixels.
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.
    display_id: If set, target input to this display.

  Returns:
    The ADB result from the second tap.
  """
  logging.info('Attempting to double tap the screen at (%d, %d)', x, y)
  first_tap = tap_screen(x, y, env, timeout_sec=0, display_id=display_id)
  second_tap = tap_screen(x, y, env, timeout_sec=timeout_sec, display_id=display_id)
  logging.info('First tap: %s', first_tap)
  logging.info('Second tap: %s', second_tap)
  return second_tap


def long_press(
    x: int,
    y: int,
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
    display_id: Optional[int] = None,
) -> AdbResult:
  """Issues an ADB command to long press the screen at the specified point.

  Args:
    x: X coordinate on the screen, in pixels.
    y: Y coordinate on the screen, in pixels.
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.
    display_id: If set, target input to this display.

  Returns:
    The ADB result.
  """
  logging.info('Attempting to long press the screen at (%d, %d)', x, y)
  return issue_generic_request(
      _input_cmd_prefix(display_id) + ['swipe', str(x), str(y), str(x), str(y), '1000'],
      env,
      timeout_sec,
  )


def press_home_button(
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
    display_id: Optional[int] = None,
) -> AdbResult:
  """Issues an ADB command to press the HOME button.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.
    display_id: If set, target input to this display.

  Returns:
    The ADB result.
  """
  logging.info('Attempting to press the HOME button')
  result = issue_generic_request(
      _input_cmd_prefix(display_id) + ['keyevent', '3'],
      env,
      timeout_sec,
  )
  if not result.success:
    logging.error('Failed to press the HOME button')
  return result


def press_back_button(
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
    display_id: Optional[int] = None,
) -> AdbResult:
  """Issues an ADB command to press the BACK button.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.
    display_id: If set, target input to this display.

  Returns:
    The ADB result.
  """
  logging.info('Attemting to press the BACK button')
  result = issue_generic_request(
      _input_cmd_prefix(display_id) + ['keyevent', '4'],
      env,
      timeout_sec,
  )
  if not result.success:
    logging.error('Failed to press the BACK button')
  return result


def press_enter_button(
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
    display_id: Optional[int] = None,
) -> AdbResult:
  """Issues an ADB command to press the ENTER button.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.
    display_id: If set, target input to this display.

  Returns:
    The ADB result.
  """
  logging.info('Attemting to press the ENTER button')
  result = issue_generic_request(
      _input_cmd_prefix(display_id) + ['keyevent', '66'],
      env,
      timeout_sec,
  )
  if not result.success:
    logging.error('Failed to press the ENTER button')
  return result


def press_keyboard_generic(
    keycode: str,
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
    display_id: Optional[int] = None,
) -> AdbResult:
  """Issues an ADB command to press any button in the keyboard.

  Args:
    keycode: The keycode to press.
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.
    display_id: If set, target input to this display.

  Returns:
    The ADB result.
  """
  logging.info('Attemting to press the keyboard button: %s', keycode)

  result = issue_generic_request(
      _input_cmd_prefix(display_id) + ['keyevent', keycode],
      env,
      timeout_sec,
  )

  if not result.success:
    logging.error('Failed to press the keyboard button: %s', keycode)

  return result


def _adb_text_format(text: str) -> str:
  """Prepares text for use with adb."""
  to_escape = [
      '\\',
      ';',
      '|',
      '`',
      '\r',
      ' ',
      "'",
      '"',
      '&',
      '<',
      '>',
      '(',
      ')',
      '#',
      '$',
  ]
  for char in to_escape:
    text = text.replace(char, '\\' + char)
  normalized_text = unicodedata.normalize('NFKD', text)
  return normalized_text.encode('ascii', 'ignore').decode('ascii')


def _split_words_and_newlines(text: str) -> Iterable[str]:
  """Split lines of text into individual words and newline chars."""
  lines = text.split('\n')
  for i, line in enumerate(lines):
    words = line.split(' ')
    for j, word in enumerate(words):
      if word:
        yield word
      if j < len(words) - 1:
        yield '%s'
    if i < len(lines) - 1:
      yield '\n'


def type_text(
    text: str,
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
    display_id: Optional[int] = None,
) -> None:
  """Issues ADB commands to type the specified text string word-by-word.

  It types word-by-word to fix issue where sometimes long text strings can be
  typed out of order at the character level. Additionally, long strings can time
  out and word-by-word fixes this, while allowing us to keep a lot timeout per
  word.

  Args:
    text: The text string to be typed.
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation. Note: For longer texts,
      this should be longer as it takes longer to type.
    display_id: If set, target input to this display.
  """
  words = _split_words_and_newlines(text)
  for word in words:
    if word == '\n':
      logging.info('Found \\n, pressing enter button.')
      press_enter_button(env, display_id=display_id)
      continue
    formatted = _adb_text_format(word)
    logging.info('Attempting to type word: %r', formatted)
    result = issue_generic_request(
        _input_cmd_prefix(display_id) + ['text', formatted],
        env,
        timeout_sec,
    )
    if not result.success:
      logging.error('Failed to type word: %r', formatted)


def issue_generic_request(
    args: Collection[str] | str,
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
) -> AdbResult:
  """Issues an adb command.

  Example:
  ~~~~~~~

  issue_generic_request(['shell', 'ls'], env)
  # or
  issue_generic_request('shell ls', env)

  Args:
    args: Set of arguments to be issued with the ADB command. Can also be a
      string.
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.

  Returns:
    The ADB result.
  """
  if isinstance(args, str):
    args_str = args
  else:
    args_str = ' '.join(args)

  result = env.run(args, timeout_sec)
  if not result.success:
    logging.error('Failed to issue generic adb request: %r', args_str)

  return result


def get_adb_activity(app_name: str) -> Optional[str]:
  """Get a mapping of regex patterns to ADB activities top Android apps."""
  for pattern, activity in _PATTERN_TO_ACTIVITY.items():
    if re.match(pattern.lower(), app_name.lower()):
      return activity


def get_all_package_names(
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
) -> list[str]:
  """Returns all packages installed on the device.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.

  Returns:
    A list of installed package names.
  """
  result = issue_generic_request('shell pm list packages', env, timeout_sec)
  if not result.success:
    logging.error('Failed to issue package manager request.')
    return []

  package_names = []
  for line in result.output.strip().split('\n'):
    line = line.strip()
    if line.startswith('package:'):
      package_names.append(line[len('package:'):])
  return package_names


def get_all_apps(
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
) -> list[str]:
  """Returns all apps installed on the device.

  Note: the output list will not be exhaustive as it is currently based on a
  mapping we define, so any apps not included in that mapping will not be
  output here.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation. If not set the default
      timeout will be used.

  Returns:
    A list of app names.
  """
  packages = get_all_package_names(env, timeout_sec)
  package_to_app = {
      v.split('/')[0]: k.split('|')[0] for k, v in _PATTERN_TO_ACTIVITY.items()
  }
  app_names = []
  for package in packages:
    if package in package_to_app:
      app_names.append(package_to_app[package])

  return app_names


def _launch_default_app(
    app_key: str,
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
) -> AdbResult:
  """Launches a default application with a predefined data URI."""
  if app_key not in _DEFAULT_URIS:
    raise ValueError(
        f'Unrecognized app key: {app_key}. Must be one of'
        f' {list(_DEFAULT_URIS.keys())}'
    )
  data_uri = _DEFAULT_URIS[app_key]
  adb_command = [
      'shell',
      'am',
      'start',
      '-a',
      'android.intent.action.VIEW',
      '-d',
      data_uri,
  ]
  result = issue_generic_request(adb_command, env, timeout_sec)
  return result


def launch_app(
    app_name: str,
    env: AdbController,
    display_id: Optional[int] = None,
) -> Optional[str]:
  """Uses regex and ADB activity to try to launch an app.

  Args:
    app_name: The name of the app, as represented as a key in
      _PATTERN_TO_ACTIVITY.
    env: The ADB controller.
    display_id: If set, launch app on this display.

  Returns:
    The name of the app that is launched.
  """

  if app_name in _DEFAULT_URIS:
    _launch_default_app(app_name, env)
    return app_name

  activity = get_adb_activity(app_name)
  if activity is None:
    #  If the app name is not in the mapping, assume it is a package name.
    result = issue_generic_request(
        ['shell', 'monkey', '-p', app_name, '1'], env, timeout_sec=5
    )
    logging.info('Launching app by package name, response: %r', result)
    return app_name
  start_activity(activity, extra_args=[], env=env, timeout_sec=5, display_id=display_id)
  return app_name


def extract_package_name(activity: str) -> str:
  """Extract the package name from the activity string."""
  return activity.split('/')[0]


def close_recents(env: AdbController):
  """Closes all recent apps."""
  result = issue_generic_request('shell dumpsys activity recents', env)
  if not result.success:
    return
  recents_ids = re.findall(r'id=(\d+)', result.output)
  for recents_id in recents_ids:
    issue_generic_request(['shell', 'am', 'stack', 'remove', recents_id], env)


def close_app(
    app_name: str,
    env: AdbController,
    timeout_sec: Optional[float] = _DEFAULT_TIMEOUT_SECS,
) -> Optional[str]:
  """Uses regex and ADB package name to try to directly close an app.

  Args:
    app_name: The name of the app, as represented as a key in
      _PATTERN_TO_ACTIVITY.
    env: The ADB controller.
    timeout_sec: The timeout.

  Returns:
    The app name that is closed.
  """
  activity = get_adb_activity(app_name)
  if activity is None:
    logging.error('Failed to close app: %r', app_name)
    return None
  package_name = extract_package_name(activity)
  issue_generic_request(
      ['shell', 'am', 'force-stop', package_name], env, timeout_sec
  )
  return app_name


def generate_swipe_command(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration_ms: Optional[int] = None,
    display_id: Optional[int] = None,
) -> list[str]:
  """Sends a swipe action to the simulator.

  Args:
    start_x: The x-coordinate of the start of the swipe.
    start_y: The y-coordinate of the start of the swipe.
    end_x: The x-coordinate of the end of the swipe.
    end_y: The y-coordinate of the end of the swipe.
    duration_ms: If given, the duration of time in milliseconds to take to
      complete the swipe. This value can differentiate a swipe from a fling.
    display_id: If set, target input to this display.

  Returns:
    List of adb arguments.
  """
  cmd = _input_cmd_prefix(display_id) + [
      'swipe',
      str(start_x),
      str(start_y),
      str(end_x),
      str(end_y),
  ]
  if duration_ms:
    cmd.append(str(duration_ms))
  return cmd


def generate_drag_and_drop_command(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration_ms: Optional[int] = None,
    display_id: Optional[int] = None,
) -> list[str]:
  """Sends a drag and drop action to the simulator.

  Args:
    start_x: The x-coordinate of the start of the drag and drop.
    start_y: The y-coordinate of the start of the drag and drop.
    end_x: The x-coordinate of the end of the drag and drop.
    end_y: The y-coordinate of the end of the drag and drop.
    duration_ms: If given, the duration of time in milliseconds to take to
      complete the drag and drop.
    display_id: If set, target input to this display.

  Returns:
    List of adb arguments.
  """
  cmd = _input_cmd_prefix(display_id) + [
      'draganddrop',
      str(start_x),
      str(start_y),
      str(end_x),
      str(end_y),
  ]
  if duration_ms:
    cmd.append(str(duration_ms))
  return cmd


def send_android_intent(
    command: str,
    action: str,
    env: AdbController,
    data_uri: str | None = None,
    mime_type: str | None = None,
    extras: dict[str, Any] | None = None,
    timeout_sec: int = _DEFAULT_TIMEOUT_SECS,
) -> AdbResult:
  """Sends an intent to Android device using adb.

  Args:
    command: Either "start" for start activity intents or "broadcast" for
      broadcast intents.
    action: The broadcast action (e.g. "android.intent.action.VIEW").
    env: The ADB controller.
    data_uri: Optional intent data URI (e.g. "content://contacts/people/1").
    mime_type: Optional mime type (e.g. "image/png").
    extras: Dictionary containing keys and values to be sent as extras.
    timeout_sec: The maximum time in seconds to wait for the broadcast to
      complete.

  Returns:
    AdbResult object.
  """
  if command not in ['start', 'broadcast']:
    raise ValueError('Intent command must be either "start" or "broadcast"')

  adb_command = ['shell', 'am', command, '-a', action]

  if data_uri:
    adb_command.extend(['-d', f'"{data_uri}"'])

  if mime_type:
    adb_command.extend(['-t', f'"{mime_type}"'])

  if extras:
    for key, value in extras.items():
      if isinstance(value, tuple):
        type_override, value = value
        if type_override == 'str':
          adb_command.extend(['--es', key, f'"{value}"'])
        elif type_override == 'bool':
          adb_command.extend(['--ez', key, f'"{value}"'])
        elif type_override == 'int':
          adb_command.extend(['--ei', key, f'"{value}"'])
        elif type_override == 'long':  # long type only available via override.
          adb_command.extend(['--el', key, f'"{value}"'])
        elif type_override == 'float':
          adb_command.extend(['--ef', key, f'"{value}"'])
        elif type_override == 'string array':
          array_str = ','.join(value)
          adb_command.extend(['--esa', key, f'"{array_str}"'])
      elif isinstance(value, str):
        adb_command.extend(['--es', key, f'"{value}"'])
      elif isinstance(value, bool):
        adb_command.extend(['--ez', key, f'"{value}"'])
      elif isinstance(value, int):
        adb_command.extend(['--ei', key, f'"{value}"'])
      # long type only available via override above.
      elif isinstance(value, float):
        adb_command.extend(['--ef', key, f'"{value}"'])
      elif isinstance(value, list):
        array_str = ','.join(value)
        adb_command.extend(['--esa', key, f'"{array_str}"'])
      else:
        raise ValueError(f'Unrecognized extra type for {key}')

  return issue_generic_request(adb_command, env, timeout_sec)


def get_api_level(env: AdbController) -> int:
  """Gets the API level of the device.

  Args:
    env: The ADB controller.

  Returns:
    The API level.

  Raises:
    RuntimeError: If adb command does not successfully execute.
  """
  result = issue_generic_request(
      ['shell', 'getprop ro.build.version.sdk'], env
  )
  if not result.success:
    raise RuntimeError('Failed to get API level.')
  return int(result.output.strip())


def _toggle_svc(
    service: str,
    on_or_off: Literal['on', 'off'],
    env: AdbController,
) -> AdbResult:
  """Toggles a system service on or off using svc.

  Args:
    service: The name of the service to toggle.
    on_or_off: The state to set ('on' or 'off').
    env: The ADB controller.

  Returns:
    adb result.

  Raises:
    ValueError: If invalid on_or_off is provided.
  """
  if on_or_off not in ('on', 'off'):
    raise ValueError('Must be one of on or off.')

  cmd = 'enable' if on_or_off == 'on' else 'disable'
  return issue_generic_request(['shell', 'svc', service, cmd], env)


def toggle_wifi(
    env: AdbController, on_or_off: Literal['on', 'off']
) -> AdbResult:
  """Toggles wifi on or off.

  Args:
    env: The ADB controller.
    on_or_off: Whether to turn it on or off.

  Returns:
    adb result.
  """
  return _toggle_svc('wifi', on_or_off, env)


def toggle_bluetooth(
    env: AdbController, on_or_off: Literal['on', 'off']
) -> AdbResult:
  """Toggles Bluetooth on or off.

  Args:
    env: The ADB controller.
    on_or_off: Whether to turn it on or off.

  Returns:
    adb result.
  """
  return _toggle_svc('bluetooth', on_or_off, env)


def set_brightness(
    max_or_min: str, env: AdbController
) -> AdbResult:
  """Sets screen brightness to maximum or minimum.

  Args:
    max_or_min: Whether to set it to maximum or minimum.
    env: The ADB controller.

  Returns:
    The adb result.

  Raises:
    ValueError: If invalid max_or_min is provided.
  """
  if max_or_min not in ('max', 'min'):
    raise ValueError('Must be one of max or min.')

  brightness_level = '255' if max_or_min == 'max' else '1'

  return issue_generic_request(
      [
          'shell',
          'settings',
          'put',
          'system',
          'screen_brightness',
          brightness_level,
      ],
      env,
  )


def clear_app_data(
    package_name: str, env: AdbController
) -> AdbResult:
  """Clears all data for a given package.

  Args:
    package_name: The package name of the app whose data is to be cleared.
    env: The ADB controller.

  Returns:
    adb result.
  """
  try:
    return issue_generic_request(['shell', 'pm', 'clear', package_name], env)
  except AdbError as exc:
    raise AdbError(
        f'Failed to clear app data for package {package_name}. Is the app'
        ' installed?'
    ) from exc


def toggle_airplane_mode(
    on_or_off: Literal['on', 'off'], env: AdbController
) -> AdbResult:
  """Toggles airplane mode on or off.

  Args:
    on_or_off: Whether to turn it on or off.
    env: The ADB controller.

  Returns:
    adb result.

  Raises:
    ValueError: If invalid on_or_off is provided.
  """
  if on_or_off not in ('on', 'off'):
    raise ValueError('Must be one of on or off.')
  state = '1' if on_or_off == 'on' else '0'
  return issue_generic_request(
      ['shell', 'settings', 'put', 'global', 'airplane_mode_on', state], env
  )


def install_apk(
    apk_location: str, env: AdbController
) -> None:
  """Installs an APK.

  Args:
    apk_location: Location of apk.
    env: The ADB controller.

  Raises:
    ValueError: If apk location does not exist.
  """
  if not os.path.exists(apk_location):
    raise ValueError('APK does not exist.')
  issue_generic_request(['install', apk_location], env, timeout_sec=30.0)


def check_airplane_mode(env: AdbController) -> bool:
  """Checks if airplane mode is enabled.

  Args:
    env: The ADB controller.

  Returns:
    True if airplane mode is enabled, False otherwise.

  Raises:
    RuntimeError: If cannot execute airplane mode check.
  """
  result = issue_generic_request(
      ['shell', 'settings', 'get', 'global', 'airplane_mode_on'], env
  )

  if not result.success:
    raise RuntimeError(
        f'ADB command failed (rc={result.returncode}): {result.output}'
    )

  return result.output.replace('\r', '').strip('\n') == '1'


def extract_broadcast_data(raw_output: str) -> Optional[str]:
  """Extracts the data from an adb broadcast command output.

  Args:
    raw_output: The adb command output.

  Returns:
    Extracted data as a string, or None if the result is 0.
  """
  if 'Broadcast completed: result=-1, data=' in raw_output:
    return raw_output.split('data=')[1].strip('"\r\n')
  elif 'Broadcast completed: result=0' in raw_output:
    return None
  else:
    raise ValueError(f'Unexpected broadcast output: {raw_output}')


def _extract_clipper_output(raw_output: str) -> str:
  """Parses the clipper output from the adb command.

  Args:
    raw_output: The adb command output.

  Returns:
    The clipboard content as a string.

  Raises:
    RuntimeError: If the adb command does not successfully execute or if the
      app is not in the foreground.
  """
  parsed_data = extract_broadcast_data(raw_output)
  if parsed_data is not None:
    return parsed_data
  else:
    raise RuntimeError(
        'Clipper app must be in the foreground to access clipboard. '
        'Additionally, app privileges must be granted manually by opening the '
        'clipper app and granting them.'
    )


def get_clipboard_contents(env: AdbController) -> str:
  """Gets the clipboard content from the Android device.

  Args:
    env: The ADB controller.

  Returns:
    The clipboard content as a string.

  Raises:
    RuntimeError: If the adb command does not successfully execute or if the
      app is not in the foreground.
  """
  if launch_app('clipper', env) is None:
    raise RuntimeError(
        'Clipper app must be in the foreground to access clipboard. You may'
        ' need to install clipper app.'
    )

  time.sleep(0.5)
  res = issue_generic_request(
      ['shell', 'am', 'broadcast', '-a', 'clipper.get'], env
  )

  if not res.success:
    raise RuntimeError('Failed to get clipboard content.')

  result = _extract_clipper_output(res.output)

  press_back_button(env)
  return result


def change_orientation(
    orientation: str, env: AdbController
) -> None:
  """Changes the screen orientation.

  Args:
    orientation: str, The new orientation. Can be portrait, landscape,
      reverse_portrait, or reverse_landscape.
    env: The ADB controller.

  Raises:
    ValueError if invalid orientation is provided.
  """
  if orientation not in _ORIENTATIONS:
    raise ValueError(
        f'Unknown orientation provided: {orientation} not in'
        f' {_ORIENTATIONS.keys()}'
    )
  command = [
      'shell',
      'settings',
      'put',
      'system',
  ]
  # Turn off accelerometer.
  issue_generic_request(command + ['accelerometer_rotation', '0'], env)
  issue_generic_request(
      command + ['user_rotation', _ORIENTATIONS[orientation]], env
  )


def set_clipboard_contents(
    content: str, env: AdbController
) -> None:
  """Sets the clipboard content on the Android device.

  NOTE: If using an Emulator, the contents of your clipboard on your local
  machine may transfer to the emulator when focused on the emulator. Thus the
  result of this function can be overwritten just by switching windows.

  Args:
    content: Content to put into clipboard.
    env: The ADB controller.

  Raises:
    RuntimeError: If the adb command does not successfully execute or if the
    app is not in the foreground.
  """
  if launch_app('clipper', env) is None:
    raise RuntimeError(
        'Clipper app must be in the foreground to access clipboard. You may'
        ' need to install clipper app.'
    )

  time.sleep(0.5)
  content = _adb_text_format(content)
  output_str = issue_generic_request(
      ['shell', 'am', 'broadcast', '-a', 'clipper.set', '-e', 'text', content],
      env,
  ).output
  _extract_clipper_output(output_str)
  press_back_button(env)


def grant_permissions(
    activity_name: str,
    permission: str,
    env: AdbController,
) -> None:
  """Grants permissions on an activity.

  This is useful because it prevents pop-ups prompting user/agent for
  permission.

  See https://developer.android.com/reference/android/Manifest.permission for
  available permissions to grant.

  Args:
    activity_name: The name of the activity.
    permission: The permission to grant.
    env: The ADB controller.
  """
  issue_generic_request(
      ['shell', 'pm', 'grant', activity_name, permission],
      env,
  )


def execute_sql_command(
    db_path: str,
    sql_command: str,
    env: AdbController,
) -> AdbResult:
  """Execute an arbitrary SQL command on a SQLite database file via ADB.

  Args:
    db_path: The path to the SQLite database on the Android device.
    sql_command: The SQL command to execute.
    env: The ADB controller.

  Returns:
    The ADB result.
  """
  adb_command = ['shell', f'sqlite3 {db_path} "{sql_command}"']
  adb_result = issue_generic_request(adb_command, env)
  return adb_result


def get_call_state(
    env: AdbController,
    timeout_sec: float = _DEFAULT_TIMEOUT_SECS,
) -> str:
  """Query the call state and the dialed number of the phone through ADB.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout for the ADB operation.

  Returns:
    The call state as a string.
  """
  adb_args = ['shell', 'dumpsys', 'telephony.registry']
  result = issue_generic_request(adb_args, env, timeout_sec)

  state_match = re.search(r'mCallState=(\d)', result.output)

  state = 'UNKNOWN'

  if state_match:
    state_code = state_match.group(1)
    if state_code == '0':
      state = 'IDLE'
    elif state_code == '1':
      state = 'RINGING'
    elif state_code == '2':
      state = 'OFFHOOK'

  return state


def call_emulator(
    env: AdbController,
    phone_number: str,
    timeout_sec: float = _DEFAULT_TIMEOUT_SECS,
) -> AdbResult:
  """Simulate an incoming call in an emulator using ADB.

  Args:
    env: The ADB controller.
    phone_number: The incoming phone number.
    timeout_sec: A timeout for the ADB operation.

  Returns:
    AdbResult.
  """
  escaped_phone_number = re.sub(r'[^0-9+]', '', phone_number)
  adb_args = ['emu', 'gsm', 'call', f'{escaped_phone_number}']
  result = issue_generic_request(adb_args, env, timeout_sec)
  return result


def end_call_if_active(
    env: AdbController,
    timeout_sec: float = _DEFAULT_TIMEOUT_SECS,
) -> None:
  """Ends phone call if on an active call."""
  current_state = get_call_state(env, timeout_sec)

  # This check is crucial. Otherwise pressing endcall key results in black
  # screen, potentially because it's simulating turning display off?
  if current_state in ('OFFHOOK', 'RINGING'):
    adb_args = ['shell', 'input', 'keyevent', 'KEYCODE_ENDCALL']
    issue_generic_request(adb_args, env, timeout_sec)


def clear_android_emulator_call_log(
    env: AdbController,
    timeout_sec: float = _DEFAULT_TIMEOUT_SECS,
) -> None:
  """Clears the call log of a specific Android emulator.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout for the ADB operation.
  """
  adb_args = ['shell', 'content', 'delete', '--uri', 'content://call_log/calls']
  issue_generic_request(adb_args, env, timeout_sec)


def call_phone_number(
    env: AdbController,
    phone_number: str,
    timeout_sec: float = _DEFAULT_TIMEOUT_SECS,
) -> AdbResult:
  """Initiate a phone call using ADB.

  Args:
    env: The ADB controller.
    phone_number: The phone number to dial.
    timeout_sec: A timeout for the ADB operation.

  Returns:
    AdbResult.
  """
  escaped_phone_number = re.sub(r'[^0-9]', '', phone_number)
  adb_args = [
      'shell',
      'am',
      'start',
      '-a',
      'android.intent.action.CALL',
      '-d',
      f'tel:{escaped_phone_number}',
  ]
  return issue_generic_request(adb_args, env, timeout_sec)


def text_emulator(
    env: AdbController,
    phone_number: str,
    message: str,
    timeout_sec: float = _DEFAULT_TIMEOUT_SECS,
) -> AdbResult:
  """Simulate an incoming text message in an emulator using ADB.

  Args:
    env: The ADB controller.
    phone_number: The sender's phone number.
    message: The text message content.
    timeout_sec: A timeout for the ADB operation.

  Returns:
    AdbResult.
  """
  escaped_phone_number = re.sub(r'[^0-9+]', '', phone_number)
  adb_args = [
      'emu',
      'sms',
      'send',
      f'{escaped_phone_number}',
      f'{message}',
  ]
  result = issue_generic_request(adb_args, env, timeout_sec)
  return result


def set_default_app(
    setting_key: str,
    package_name: str,
    env: AdbController,
    timeout_sec: float = _DEFAULT_TIMEOUT_SECS,
) -> AdbResult:
  """Set the default application for a given type using ADB.

  Args:
    setting_key: The setting key for the default application type (e.g.,
      'sms_default_application').
    package_name: The package name of the application to be set as default.
    env: The ADB controller.
    timeout_sec: A timeout for the ADB operation.

  Returns:
    AdbResult.
  """
  adb_args = ['shell', 'settings', 'put', 'secure', setting_key, package_name]
  result = issue_generic_request(adb_args, env, timeout_sec)
  return result


def disable_headsup_notifications(
    env: AdbController,
    timeout_sec: float = _DEFAULT_TIMEOUT_SECS,
) -> AdbResult:
  """Disables the heads up notifications.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout for the ADB operation.

  Returns:
    AdbResult.
  """
  adb_args = [
      'shell',
      'settings',
      'put',
      'global',
      'heads_up_notifications_enabled',
      '0',
  ]
  result = issue_generic_request(adb_args, env, timeout_sec)
  return result


def enable_headsup_notifications(
    env: AdbController,
    timeout_sec: float = _DEFAULT_TIMEOUT_SECS,
) -> AdbResult:
  """Enables the heads up notifications.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout for the ADB operation.

  Returns:
    AdbResult.
  """
  adb_args = [
      'shell',
      'settings',
      'put',
      'global',
      'heads_up_notifications_enabled',
      '1',
  ]
  result = issue_generic_request(adb_args, env, timeout_sec)
  return result


def put_settings(
    namespace: str,
    key: str,
    value: str,
    env: AdbController,
) -> AdbResult:
  """Change a setting in the Android system via ADB.

  Args:
    namespace: The namespace ('system', 'secure', or 'global').
    key: The key of the setting to change.
    value: The new value for the setting.
    env: The ADB controller.

  Returns:
    The ADB result.
  """
  if not key:
    raise ValueError('Key must be provided.')
  if not value:
    raise ValueError('Value must be provided.')
  return issue_generic_request(
      ['shell', 'settings', 'put', namespace, key, value], env
  )


def _post_process_settings(settings: dict[str, str]) -> dict[str, Any]:
  """Post process settings to remove non-deterministic fields."""

  # Remove theme timestamp
  theme_key = 'theme_customization_overlay_packages'
  if theme_key in settings:
    theme = json.loads(settings[theme_key])
    theme.pop('_applied_timestamp')
    settings[theme_key] = theme

  # Remove zen_duration
  settings.pop('zen_duration', None)

  return settings


def get_all_settings(env: AdbController) -> dict[str, str]:
  """Get all settings from the Android system via ADB."""
  adb_commands = [
      'shell settings list secure',
      'shell settings list global',
      'shell settings list system',
  ]
  settings = {}
  for adb_command in adb_commands:
    result = issue_generic_request(adb_command, env)
    lines = result.output.split('\n')
    for line in lines:
      if not line:
        continue
      key, value = line.split('=', 1)
      settings[key] = value
  return _post_process_settings(settings)


def delete_contacts(
    env: AdbController,
    timeout_sec: float = _DEFAULT_TIMEOUT_SECS,
) -> AdbResult:
  """Deletes all contacts.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout for the ADB operation.

  Returns:
    AdbResult.
  """
  adb_args = [
      'shell',
      'pm',
      'clear',
      'com.android.providers.contacts',
  ]
  result = issue_generic_request(adb_args, env, timeout_sec)
  return result


def _parse_screen_size_response(response: str) -> tuple[int, int]:
  """Parse the adb response to extract screen size.

  Args:
    response: The adb response string.

  Returns:
    The screen width and height in pixels.
  """
  match = re.search(r'Physical size: (\d+)x(\d+)', response)
  if match:
    width, height = map(int, match.groups())
    return width, height
  else:
    raise ValueError(
        f'Screen size information not found in adb response: "{response}"'
    )


def get_screen_size(env: AdbController) -> tuple[int, int]:
  """Get the screen size in pixels of an Android device via ADB.

  Args:
    env: The ADB controller.

  Returns:
    The screen width and height in pixels.
  """
  adb_command = ['shell', 'wm size']
  adb_result = issue_generic_request(adb_command, env)
  return _parse_screen_size_response(adb_result.output)


def get_logical_screen_size(
    env: AdbController,
) -> tuple[int, int]:
  """Returns the logical screen size.

  The logical screen size is the screen size that applications use to render
  their interfaces which might be different than the physical screen size when
  orientation/resolution changes. The coordinates we get from A11y tree are
  based on the logical screen size.

  Args:
    env: The ADB controller.

  Returns:
    The logical screen size in (width, height).
  """
  result = issue_generic_request(
      'shell dumpsys input | grep logicalFrame', env
  )
  if result.success:
    pattern = r'logicalFrame=\[0, 0, (\d+), (\d+)\]'
    matches = re.findall(pattern, result.output)
    for m in matches:
      if int(m[0]) == 0 and int(m[1]) == 0:
        continue
      width, height = (int(m[0]), int(m[1]))
      return (width, height)
  raise ValueError('Failed to get logical screen size.')


def get_physical_frame_boundary(
    env: AdbController,
) -> tuple[int, int, int, int]:
  """Returns the physical frame boundary.

  Args:
    env: The ADB controller.

  Returns:
    First two integers are the coordinates for top left corner, last two are for
    lower right corner. All coordinates are given in portrait orientation.
  """
  result = issue_generic_request(
      'shell dumpsys input | grep physicalFrame', env
  )
  if result.success:
    pattern = r'physicalFrame=\[(\d+), (\d+), (\d+), (\d+)\]'
    matches = re.findall(pattern, result.output)
    for m in matches:
      if (
          int(m[0]) == 0
          and int(m[1]) == 0
          and int(m[2]) == 0
          and int(m[3]) == 0
      ):
        continue
      orientation = get_orientation(env)
      if orientation == 0 or orientation == 2:
        return (int(m[0]), int(m[1]), int(m[2]), int(m[3]))
      return (int(m[1]), int(m[0]), int(m[3]), int(m[2]))
  raise ValueError('Failed to get physical frame boundary.')


def get_orientation(
    env: AdbController,
) -> int:
  """Returns the current screen orientation.

  The returned value follows the normal convention, 0 for portrait, 1 for
  landscape, 2 for reverse portrait, 3 for reverse landscape.

  Args:
    env: The ADB controller.

  Returns:
    The screen orientation.
  """
  result = issue_generic_request(
      'shell dumpsys window | grep mCurrentRotation', env
  )
  if result.success:
    pattern = r'mCurrentRotation=ROTATION_(\d+)'
    matches = re.findall(pattern, result.output)
    for m in matches:
      return int(m) // 90
  raise ValueError('Failed to get orientation.')


def set_screen_size(
    width: int,
    height: int,
    env: AdbController,
) -> AdbResult:
  """Sets the (logical) screen size (resolution) of the Android device via ADB.

  Args:
    width: The desired screen width.
    height: The desired screen height.
    env: The ADB controller.

  Returns:
    The ADB result.
  """
  # Command will fail if width equals height.
  if width <= 0 or height <= 0 or width == height:
    raise ValueError(
        'Screen size not valid (need to be positive, width can not equal'
        ' height).'
    )
  # Construct the ADB command for setting screen size
  adb_command = ['shell', f'wm size {width}x{height}']

  # Issue the command and return the result
  return issue_generic_request(adb_command, env)


def retry(n: int) -> Callable[[Any], Any]:
  """Decorator to retry ADB commands."""

  def decorator(func: Callable[..., T]) -> Callable[..., T]:
    def wrapper(*args: Any, **kwargs: Any) -> T:
      attempts = 0
      while attempts < n:
        try:
          return func(*args, **kwargs)
        except AdbError:
          attempts += 1
          if attempts >= n:
            raise
          print(f'Could not execute {func}. Retrying...')
          time.sleep(2)
        except Exception as exc:
          raise exc

    return wrapper

  return decorator


def uiautomator_dump(
    env: AdbController,
    timeout_sec: Optional[float] = 30,
    display_id: Optional[int] = None,
) -> str:
  """Issues a uiautomator dump request and returns the UI hierarchy.

  Args:
    env: The ADB controller.
    timeout_sec: A timeout to use for this operation.
    display_id: If set, focus and dump from this display.
  """
  if display_id is not None:
    # `uiautomator dump --display N` requires the input dispatcher to know
    # the target display, otherwise it silently falls back to display 0 and
    # returns the launcher's hierarchy. We previously achieved this with a
    # `tap 0 0` on the display, but that tap dismissed any open transient
    # popup (Calendar overflow menu, FAB quick-create, etc.) and left the
    # agent in an infinite reopen-the-menu loop.
    #
    # Send a no-op key event instead. KEYCODE_UNKNOWN (0) is routed through
    # the input dispatcher with `-d <displayId>` and is enough to update
    # the dispatcher's per-display focus, but it does not generate a
    # dispatched key down/up to the focused window, so popup menus are not
    # dismissed.
    issue_generic_request(
        ['shell', 'input', '-d', str(display_id), 'keyevent', '0'],
        env,
        timeout_sec=timeout_sec,
    )
    dump_args = [
        'shell', 'uiautomator', 'dump',
        '--display', str(display_id),
        '/sdcard/window_dump.xml',
    ]
  else:
    dump_args = 'shell uiautomator dump /sdcard/window_dump.xml'
  issue_generic_request(dump_args, env, timeout_sec=timeout_sec)

  read_args = 'shell cat /sdcard/window_dump.xml'
  result = issue_generic_request(read_args, env, timeout_sec=timeout_sec)

  return result.output
