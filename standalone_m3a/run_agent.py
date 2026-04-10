"""Standalone M3A agent runner.

Usage:
  # Classic mode (direct ADB, no WebSocket):
  python run_agent.py --goal "open the settings app"
  python run_agent.py --goal "turn off wifi" --max_steps 10 --model gpt-4.1

  # Server mode (WebSocket coordination with Android assistant app):
  python run_agent.py --server --goal "turn off wifi" --package com.android.settings
  python run_agent.py --server --port 9000 --goal "check email" --package com.google.android.gm
"""

import argparse
import asyncio
import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from m3a_agent.agent import M3A
from m3a_agent.infer import Gpt4Wrapper
from m3a_agent.env import adb_utils
from m3a_agent.env import env_launcher


def _load_dotenv(path: str = '.env') -> None:
  """Loads key=value pairs from a .env file into os.environ.

  When the file exists, its values OVERRIDE any pre-existing environment
  variables. This makes the project-local .env authoritative — useful when
  the user's shell rc has stale or rotated credentials exported globally.
  """
  env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
  if not os.path.isfile(env_path):
    return
  with open(env_path) as f:
    for line in f:
      line = line.strip()
      if not line or line.startswith('#') or '=' not in line:
        continue
      key, _, value = line.partition('=')
      key, value = key.strip(), value.strip()
      if value and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
      if key:
        prev = os.environ.get(key)
        if prev and prev != value:
          masked_prev = (prev[:8] + '…' + prev[-4:]) if len(prev) > 16 else '***'
          masked_new = (value[:8] + '…' + value[-4:]) if len(value) > 16 else '***'
          print(f'  .env override: {key} {masked_prev} -> {masked_new}')
        os.environ[key] = value


def _find_adb_directory() -> str:
  """Returns the path to the adb binary."""
  potential_paths = [
      os.path.expanduser('~/Library/Android/sdk/platform-tools/adb'),
      os.path.expanduser('~/Android/Sdk/platform-tools/adb'),
      os.path.expanduser('~/AppData/Local/Android/Sdk/platform-tools/adb.exe'),
  ]
  for path in potential_paths:
    if os.path.isfile(path):
      return path
  return '~/Android/Sdk/platform-tools/adb'


def _run_classic(args):
  """Original ADB-only mode (no WebSocket)."""
  print(f'Connecting to Android device (serial={args.serial})...')
  env = env_launcher.load_and_setup_env(
      adb_path=args.adb_path,
      serial=args.serial,
      display_id=args.display_id,
  )

  print(f'Initializing M3A agent with model {args.model}...')
  llm = Gpt4Wrapper(model_name=args.model)
  agent = M3A(env, llm)
  agent.reset(go_home_on_reset=False)
  env.hide_automation_ui()

  if args.package:
    print(f'Launching {args.package}...')
    cmd = ['shell', 'am', 'start']
    if args.display_id is not None:
      cmd.extend(['--display', str(args.display_id)])
    cmd.append(args.package)
    adb_utils.issue_generic_request(cmd, env.controller.adb)
    time.sleep(2)
    agent.history.append({'summary': f'Launched {args.package}.'})

  _run_agent_loop(agent, args)
  env.close()
  print('Done.')


## Hardcoded keyword → package mapping for the chat-driven demo. Each user
## goal is matched against these keyword lists in order; the first match wins.
##
## NOTE: order matters. Uber (rides) must come BEFORE the food entries so a
## goal like "uber to airport" doesn't get caught by the generic "order"
## keyword and routed to a food app.
GOAL_TO_PACKAGE: list[tuple[list[str], str]] = [
    (
        # Uber rides (com.ubercab) — checked FIRST so ride keywords win.
        'uber ride rides taxi cab car lyft pickup dropoff drop driver '
        'airport station home office destination trip route'.split(),
        'com.ubercab',
    ),
    (
        # Uber Eats — generic food / restaurant / delivery keywords.
        ['hungry', 'food', 'eat', 'meal', 'order', 'restaurant', 'delivery',
         'pizza', 'sandwich', 'burger', 'sushi', 'ubereats', 'eats', 'takeout',
         'snack', 'drink', 'coffee'],
        'com.ubercab.eats',
    ),
    (
        # Nouns
        'calendar schedule event events meeting meetings appointment reminder '
        'reminders agenda week today tomorrow yesterday plan plans plans '
        'planner deadline demo project task tasks time hour day '
        # Days of week (used a lot in calendar goals)
        'monday tuesday wednesday thursday friday saturday sunday '
        # Verbs
        'summarize summary list show show me check what when add create '
        'cancel delete remove move reschedule postpone shift book '
        # Phrases
        'busy free'.split(),
        'com.google.android.calendar',
    ),
    (
        # Android Settings — wifi, bluetooth, display, sound, battery, etc.
        'settings wifi wi-fi bluetooth battery display brightness sound '
        'volume ringtone notification notifications airplane mode network '
        'mobile data hotspot vpn nfc location gps permission permissions '
        'app apps storage memory language locale keyboard accessibility '
        'developer options reset factory update software backup restore '
        'screen timeout lock security pin password fingerprint biometric '
        'dark mode theme wallpaper font size zoom'.split(),
        'com.android.settings',
    ),
    (
        # Clock — timer, alarm, stopwatch, world clock
        'clock timer timers alarm alarms stopwatch countdown set ring '
        'wake wakeup morning snooze world time'.split(),
        'com.google.android.deskclock',
    ),
]


def pick_package_for_goal(goal: str, default: str | None) -> str | None:
  # Tokenize the goal into lowercase words so matching is word-boundary
  # accurate. Substring matching used to misroute "create" → "eat" → DoorDash.
  import re
  tokens = set(re.findall(r"[a-z]+", goal.lower()))
  for keywords, pkg in GOAL_TO_PACKAGE:
    for kw in keywords:
      kw_tokens = kw.split()
      if all(t in tokens for t in kw_tokens):
        return pkg
  return default


async def _run_server(args):
  """WebSocket server mode: coordinate with Android assistant app."""
  from m3a_agent.server import AppConnection

  # Pre-flight cleanup before every run. We force-stop:
  #   - The AgentLens app itself, to wipe leaked overlay windows, stale
  #     MediaProjection state, or in-memory caches from a previous run.
  #   - Every demo app in GOAL_TO_PACKAGE, because tasks belonging to
  #     these apps frequently "escape" from the untrusted virtual
  #     display onto the user's main screen and we cannot let stale
  #     instances confuse the next run (the agent would either see the
  #     wrong starting state or the user would see ghost windows).
  # After force-stopping we send KEYCODE_HOME so the launcher takes the
  # foreground on display 0, leaving the device in a known clean state.
  import subprocess
  pkgs_to_clean = ['com.marvis.agentlens'] + [p for _, p in GOAL_TO_PACKAGE]
  print(f'Pre-flight cleanup: force-stopping {pkgs_to_clean}...')
  for pkg in pkgs_to_clean:
    fs_cmd = [args.adb_path]
    if args.serial:
      fs_cmd += ['-s', args.serial]
    fs_cmd += ['shell', 'am', 'force-stop', pkg]
    try:
      subprocess.run(fs_cmd, check=False, capture_output=True, timeout=10)
    except Exception as e:
      print(f'  warning: force-stop {pkg} failed ({e}); continuing')
  # Kick the device back to the launcher so display 0 is clean.
  home_cmd = [args.adb_path]
  if args.serial:
    home_cmd += ['-s', args.serial]
  home_cmd += ['shell', 'input', 'keyevent', 'KEYCODE_HOME']
  try:
    subprocess.run(home_cmd, check=False, capture_output=True, timeout=5)
  except Exception:
    pass

  print(f'Initializing LLM with model {args.model}...')
  llm = Gpt4Wrapper(model_name=args.model)

  app_conn = AppConnection(port=args.port, llm=llm, serial=args.serial)
  display_id = await app_conn.start_and_wait_for_app(timeout=36000.0)

  print(f'Connecting to Android device (serial={args.serial})...')
  env = env_launcher.load_and_setup_env(
      adb_path=args.adb_path,
      serial=args.serial,
      display_id=display_id,
  )

  # For overlay VD: find SurfaceFlinger display ID for screencap
  import io, numpy as np, subprocess
  from PIL import Image

  adb_prefix = [args.adb_path] + (['-s', args.serial] if args.serial else [])
  sf_result = subprocess.run(
      adb_prefix + ['shell', 'dumpsys', 'SurfaceFlinger', '--display-id'],
      capture_output=True, text=True)
  sf_display_id = None
  for line in sf_result.stdout.splitlines():
    if 'Overlay' in line or '오버레이' in line:
      sf_display_id = line.split('Display ')[1].split(' ')[0]
      break

  if sf_display_id:
    print(f'Using SurfaceFlinger display ID: {sf_display_id} for screencap')
    def sf_screenshot():
      try:
        result = subprocess.run(
            adb_prefix + ['exec-out', 'screencap', '-d', sf_display_id, '-p'],
            capture_output=True, timeout=15)
        if result.returncode == 0 and len(result.stdout) > 100:
          return np.array(Image.open(io.BytesIO(result.stdout)).convert('RGB'))
      except Exception:
        pass
      return None
    env.controller._screenshot_fn = sf_screenshot
  else:
    # MediaProjection VD: ask the Android app to capture via WebSocket.
    print('No overlay VD detected — using WebSocket screenshot capture (MediaProjection VD).')
    loop = asyncio.get_running_loop()
    BLACK = np.zeros((1920, 1080, 3), dtype=np.uint8)

    def ws_screenshot():
      # Single attempt with a generous timeout. We used to retry on
      # failure, but the retry kicked off a SECOND captureScreenshot()
      # Thread on the Android side while the first was still running,
      # which raced on the VirtualDisplay surface assignment and left
      # both threads waiting for frames that never arrived. Heavy apps
      # like DoorDash with slow first paint hit this every time. One
      # request, longer wait — let the first capture actually finish.
      try:
        future = asyncio.run_coroutine_threadsafe(
            app_conn.capture_screenshot(timeout=25.0), loop)
        png_bytes = future.result(timeout=27.0)
        if png_bytes:
          print(f'  ws_screenshot got {len(png_bytes)} bytes', flush=True)
          return np.array(Image.open(io.BytesIO(png_bytes)).convert('RGB'))
        print('  ws_screenshot got 0 bytes — returning black image',
              flush=True)
      except Exception as e:
        print(f'  ws_screenshot failed: {type(e).__name__}: {e} '
              f'— returning black image', flush=True)
      return BLACK.copy()
    env.controller._screenshot_fn = ws_screenshot
    print(f'  set _screenshot_fn on {type(env.controller).__name__}', flush=True)

  print(f'Initializing M3A agent...')
  agent = M3A(env, llm)
  env.hide_automation_ui()
  await app_conn.send_agent_state('idle', 'Ready. Send a goal from the chat tab.')

  print('Server is ready. Waiting for chat goals from the AgentLens app.')
  print('-' * 60)

  current_pkg: str | None = None

  try:
    while True:
      if args.goal:
        # CLI-provided one-shot goal: run it once, then keep listening for
        # follow-up goals from the chat UI.
        goal = args.goal
        args.goal = None
      else:
        goal = await app_conn.wait_for_goal()
      print(f'\n=== New goal: {goal} ===')

      target_pkg = pick_package_for_goal(goal, args.package)
      if target_pkg is None:
        err = (f"No app mapping matched goal: {goal!r}. "
               f"Supported: ride/taxi (Uber), food/order (Uber Eats), "
               f"calendar/schedule (Google Calendar).")
        print(f'ERROR: {err}')
        await app_conn.send_agent_message(err, role='system')
        await app_conn.send_agent_state('error', err)
        raise SystemExit(2)

      await app_conn.send_agent_state('thinking', f'Working on: {goal[:80]}')
      await app_conn.send_agent_message(goal, role='user')

      # Always read the LIVE display id from the app connection — the user
      # may have stopped+started the projection since the server started, in
      # which case the VD has a new id. Using the stale `display_id` would
      # send the launch to a display that no longer exists, and the system
      # silently falls back to the main display.
      live_display_id = app_conn._display_id
      if live_display_id is None:
        await app_conn.send_agent_message(
            'No active virtual display. Please tap Start Display in the app.',
            role='system',
        )
        continue
      if live_display_id != display_id:
        print(f'Display id changed: {display_id} -> {live_display_id}')
        display_id = live_display_id
        # Re-bind the env controller to the new display.
        env._display_id = live_display_id  # type: ignore
        env.controller._display_id = live_display_id  # type: ignore

      # Pre-launch (or re-launch) the target package on the VD so the agent
      # has fresh content to look at.
      if target_pkg != current_pkg:
        print(f'Switching to package {target_pkg}')
        current_pkg = target_pkg
      print(f'Pre-launching {target_pkg} on display {display_id}...')
      await app_conn._handle_launch_app_request(
          {'type': 'launch_app_request', 'package': target_pkg, 'activity': ''},
          app_conn._ws)
      await asyncio.sleep(2.5)  # let the splash screen render

      # Reset the agent and seed history with the current app state.
      await asyncio.get_running_loop().run_in_executor(
          None, lambda: agent.reset(go_home_on_reset=False))
      agent.history.append({
          'summary': (
              f'The {target_pkg} app is already launched and visible on '
              f'virtual display {display_id}. Continue the task within this app.'
          )
      })

      try:
        await _run_agent_session(agent, app_conn, goal, args.max_steps)
        await app_conn.send_agent_state('idle', 'Done. Ready for the next goal.')
      except Exception as e:
        logging.exception('agent session failed')
        await app_conn.send_agent_state('error', f'{type(e).__name__}: {e}')
        await app_conn.send_agent_message(
            f'Sorry, the agent hit an error: {e}', role='system')
      finally:
        # Stop pin_loop polling once this goal is done. Otherwise it keeps
        # hammering dumpsys + move-stack every 0.5s forever even when the
        # agent is idle. The next goal will re-add the package via
        # _handle_launch_app_request.
        app_conn._pinned_packages.clear()
        current_pkg = None
  finally:
    env.close()
    await app_conn.close()
    print('Done.')


async def _run_agent_session(agent, app_conn, goal: str, max_steps: int) -> None:
  """Run the agent loop for a single user goal."""
  for step_n in range(max_steps):
    # Bail out if the AgentLens Android app has disconnected. We do NOT want to
    # keep burning OpenAI tokens with no way to actuate or surface results.
    if app_conn._ws is None:
      print('-' * 60)
      print('AgentLens app disconnected. Aborting current goal.')
      return

    print(f'Step {step_n + 1}...')
    await app_conn.send_agent_state('executing', f'Step {step_n + 1}')

    # UI tree comes from `uiautomator dump --display <id>` via ADB. The
    # AccessibilityService path was unreliable: MediaProjection-created VDs
    # are untrusted, so getWindowsOnAllDisplays() does not include their
    # windows and the service falls back to the host (AgentLens) app's tree —
    # producing a screenshot/UI mismatch that loops the agent forever.
    result = await asyncio.get_running_loop().run_in_executor(
        None, lambda: agent.step(goal))

    summary = result.data.get('summary', '')
    if summary:
      print(f'  Summary: {summary}')
      await app_conn.send_agent_message(summary, role='agent')

    # Dispatch visualization commands to the Android app
    action = result.data.get('action_output_json')
    if action:
      logging.info('agent action: type=%s viz=%s text=%s',
                   action.action_type,
                   getattr(action, 'visualization', None),
                   (action.text or '')[:80])
    if action and action.action_type in ('speak', 'ask'):
      ui_elements = result.data.get('before_ui_elements', [])
      ui_groups = result.data.get('ui_groups', [])
      await app_conn.send_visualization(
          action_type=action.action_type,
          text=action.text or '',
          visualization=action.visualization,
          ui_elements=ui_elements,
          ui_groups=ui_groups,
      )

      if action.action_type == 'ask' and action.visualization:
        print('  Waiting for user interaction...')
        await app_conn.send_agent_state('waiting', 'Waiting for your selection')

        # Snapshot the screen state at the moment we issued the ask, so
        # that after the user interacts we can diff before/after and tell
        # the LLM exactly what new content (text, fields, list entries)
        # the user introduced. Without this, the agent forgets it asked
        # and tends to restart the form on the next step.
        before_ui_elements = result.data.get('before_ui_elements') or []
        before_texts: set[str] = set()
        for elem in before_ui_elements:
          for s in (getattr(elem, 'text', None),
                    getattr(elem, 'content_description', None)):
            if s:
              s = str(s).strip()
              if s:
                before_texts.add(s)

        ask_question = action.text or '(no question text)'
        interaction = await app_conn.wait_for_interaction(timeout=60.0)

        if interaction:
          itype = interaction.get('interaction_type', 'unknown')
          print(f'  User interaction: {itype}')

          # Snapshot the screen AFTER the user finished interacting and
          # compute set-diff of visible texts. The diff is exactly what
          # the user typed, picked, or otherwise produced via the form.
          try:
            after_ui_elements = await asyncio.get_running_loop().run_in_executor(
                None, agent.env.controller.get_ui_elements)
          except Exception as e:
            logging.warning('after-state dump failed: %s', e)
            after_ui_elements = []
          after_texts: set[str] = set()
          for elem in after_ui_elements:
            for s in (getattr(elem, 'text', None),
                      getattr(elem, 'content_description', None)):
              if s:
                s = str(s).strip()
                if s:
                  after_texts.add(s)

          new_texts = sorted(after_texts - before_texts)
          gone_texts = sorted(before_texts - after_texts)

          MAX = 30
          new_preview = new_texts[:MAX]
          gone_preview = gone_texts[:MAX]

          if itype == 'screen_changed':
            transition = (
                f'(activity {interaction.get("from", "?")} -> '
                f'{interaction.get("to", "?")})'
            )
          else:
            transition = '(no activity transition recorded)'

          summary = (
              f'You asked: "{ask_question}". Result: {itype} {transition}. '
              f'New on screen (ground truth of what the user did): '
              f'{new_preview if new_preview else "(none)"}. '
              f'Gone: {gone_preview if gone_preview else "(none)"}. '
              f'Never re-issue this same ask. Look at the next screenshot '
              f'and continue the original goal: if it is now fully '
              f'satisfied, finish with a final speak + status=complete; '
              f'otherwise the user only completed an intermediate step, '
              f'so keep navigating from the new screen.'
          )
          agent.history.append({'summary': summary})
        else:
          print('  User interaction timed out, continuing.')
          agent.history.append({
              'summary': (
                  f'You issued an ASK ("{ask_question}") but the user '
                  f'did not interact within 60 seconds. Inspect the next '
                  f'screenshot to see if the form was filled silently.'
              ),
          })
        await app_conn.send_command({'type': 'dismiss'})
      elif action.action_type == 'speak' and action.visualization and action.visualization.get('visualization_type', 'none') != 'none':
        # Wait for the user to explicitly close the overlay (X / Done /
        # screen change). The TTS audio plays to completion on the
        # Android side regardless; we just keep the visual popup up
        # until the user is done reading. Long timeout so the popup
        # never disappears on its own mid-read.
        print('  Speak overlay shown — waiting for user to dismiss...')
        await app_conn.wait_for_interaction(timeout=600.0)
        await app_conn.send_command({'type': 'dismiss'})

    if result.done:
      print('-' * 60)
      print('Agent indicates task is done.')
      # Give the user a moment to read the final overlay (if any) before
      # tearing it down so the screen returns to a clean state.
      await asyncio.sleep(8.0)
      await app_conn.send_command({'type': 'dismiss'})
      await app_conn.send_agent_message('Task complete.', role='agent')
      return
  print('-' * 60)
  print('Reached max steps without agent completing the task.')
  await app_conn.send_command({'type': 'dismiss'})
  await app_conn.send_agent_message(
      f'Reached max steps ({max_steps}) without finishing.', role='system')


def _run_agent_loop(agent, args):
  """Shared agent step loop for classic mode."""
  print(f'Goal: {args.goal}')
  print(f'Max steps: {args.max_steps}')
  print('-' * 60)

  for step_n in range(args.max_steps):
    print(f'Step {step_n + 1}...')
    result = agent.step(args.goal)

    summary = result.data.get('summary', '')
    if summary:
      print(f'  Summary: {summary}')

    if result.done:
      print('-' * 60)
      print('Agent indicates task is done.')
      break
  else:
    print('-' * 60)
    print('Reached max steps without agent completing the task.')


def main():
  _load_dotenv()
  parser = argparse.ArgumentParser(description='Run M3A agent on an Android device.')
  parser.add_argument('--goal', type=str, default=None, help='Initial goal. Optional in --server mode (chat will provide goals).')
  parser.add_argument('--model', type=str, default='gpt-5.4', help='OpenAI model name (default: gpt-5.4)')
  parser.add_argument('--adb_path', type=str, default=_find_adb_directory(), help='Path to adb binary.')
  parser.add_argument('-s', '--serial', type=str, default=None, help='Device serial (e.g. emulator-5554, ABCD1234). Auto-selects if only one device is connected.')
  parser.add_argument('--package', type=str, default=None, help='Package name to launch before starting the agent (e.g. com.android.settings).')
  parser.add_argument('--max_steps', type=int, default=20, help='Maximum number of agent steps.')
  parser.add_argument('-d', '--display_id', type=int, default=None, help='Target display ID for input injection (for secondary displays).')
  parser.add_argument('--server', action='store_true', help='Enable WebSocket server mode for Android assistant app coordination.')
  parser.add_argument('--port', type=int, default=8765, help='WebSocket server port (default: 8765). Only used with --server.')
  args = parser.parse_args()

  if args.server:
    asyncio.run(_run_server(args))
  else:
    _run_classic(args)


if __name__ == '__main__':
  main()
