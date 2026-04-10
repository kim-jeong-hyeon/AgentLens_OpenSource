"""WebSocket server for communication with the Android assistant app."""

import asyncio
import json
import logging
import math
from typing import Any, Optional

import websockets
from websockets.asyncio.server import serve, ServerConnection


class AppConnection:
  """Manages the WebSocket connection to the Android assistant app."""

  def __init__(self, port: int = 8765, llm=None, serial: str = None, adb_path: str = 'adb'):
    self._port = port
    self._llm = llm  # LLM instance for GenUI generation
    self._serial = serial  # ADB device serial (e.g. 'emulator-5554')
    self._adb_path = adb_path
    # Packages whose tasks should be force-pinned to the VD whenever they
    # migrate to the default display. Updated by _handle_launch_app_request.
    self._pinned_packages: set[str] = set()
    self._pin_task: Optional[asyncio.Task] = None
    # Queue of user-submitted goals from the chat UI on the Android app.
    # The agent runner waits on this queue and processes them serially.
    self._goal_queue: asyncio.Queue = asyncio.Queue()
    self._ws: Optional[ServerConnection] = None
    self._display_id: Optional[int] = None
    self._registered = asyncio.Event()
    self._server = None
    # Touch injection state
    self._touch_start: Optional[tuple[float, float]] = None
    self._interaction_event = asyncio.Event()
    self._last_interaction: Optional[dict] = None
    # UI tree via AccessibilityService
    self._ui_tree_event = asyncio.Event()
    self._last_ui_tree: str = ''
    # Screenshot via app
    self._screenshot_event = asyncio.Event()
    self._last_screenshot: str = ''

  @property
  def display_id(self) -> Optional[int]:
    return self._display_id

  async def start_and_wait_for_app(self, timeout: float = 120.0) -> int:
    """Start WebSocket server and wait for the Android app to register.

    Args:
      timeout: Seconds to wait for the app to connect and register.

    Returns:
      The display ID reported by the app.

    Raises:
      TimeoutError: If the app does not register within the timeout.
    """
    self._server = await serve(self._handler, '0.0.0.0', self._port)
    logging.info('WebSocket server listening on port %d', self._port)
    print(f'Waiting for Android app to connect on port {self._port}...')

    try:
      await asyncio.wait_for(self._registered.wait(), timeout=timeout)
    except asyncio.TimeoutError:
      raise TimeoutError(
          f'Android app did not register within {timeout}s'
      )

    print(f'Android app connected with display_id={self._display_id}')
    return self._display_id

  async def _handler(self, websocket: ServerConnection) -> None:
    """Handle incoming WebSocket connections."""
    if self._ws is not None:
      logging.info('Replacing existing connection with new client.')
      try:
        await self._ws.close()
      except Exception:
        pass
      self._ws = None
    try:
      async for raw in websocket:
        try:
          msg = json.loads(raw)
        except json.JSONDecodeError:
          logging.warning('Received non-JSON message: %s', raw)
          continue

        msg_type = msg.get('type')
        if msg_type == 'register':
          self._ws = websocket
          self._display_id = msg.get('display_id')
          self._registered.set()
          logging.info('App registered: display_id=%s', self._display_id)

        elif msg_type == 'touch':
          await self._handle_touch(msg)

        elif msg_type == 'genui_action':
          payload = msg.get('payload', '{}')
          logging.info('GenUI action: %s', payload)
          # If element_tap, inject touch on VD via ADB
          try:
            action_data = json.loads(payload) if isinstance(payload, str) else payload
            if action_data.get('type') == 'element_tap' and self._display_id is not None:
              bounds = action_data.get('bounds', {})
              cx = (bounds.get('x1', 0) + bounds.get('x2', 0)) // 2
              cy = (bounds.get('y1', 0) + bounds.get('y2', 0)) // 2
              cmd = [self._adb_path, 'shell', 'input', '-d', str(self._display_id),
                     'tap', str(cx), str(cy)]
              logging.info('Injecting tap at (%d, %d) on display %d', cx, cy, self._display_id)
              proc = await asyncio.create_subprocess_exec(*cmd)
              await proc.wait()
          except Exception as e:
            logging.warning('Failed to inject tap: %s', e)
          self._last_interaction = {
              'interaction_type': 'genui_action',
              'payload': payload,
          }
          self._interaction_event.set()

        elif msg_type == 'ui_tree':
          self._last_ui_tree = msg.get('xml', '')
          self._ui_tree_event.set()

        elif msg_type == 'screenshot':
          self._last_screenshot = msg.get('data', '')
          self._screenshot_event.set()

        elif msg_type == 'launch_app_request':
          await self._handle_launch_app_request(msg, websocket)

        elif msg_type == 'user_goal':
          goal = msg.get('text', '').strip()
          if goal:
            logging.info('user_goal received: %s', goal[:120])
            await self._goal_queue.put(goal)

        elif msg_type == 'user_interaction':
          self._last_interaction = msg
          self._interaction_event.set()
          logging.info('User interaction: %s', msg.get('interaction_type'))

        else:
          logging.debug('Received message: %s', msg)
    except websockets.ConnectionClosed:
      logging.info('Android app disconnected')
      if self._ws is websocket:
        self._ws = None

  async def _resolve_launcher_activity(self, package: str) -> Optional[str]:
    """Use `cmd package resolve-activity` to find the launcher class for a package.

    Returns the fully-qualified activity class name, or None on failure.
    """
    cmd = [self._adb_path]
    if self._serial:
      cmd += ['-s', self._serial]
    cmd += ['shell', 'cmd', 'package', 'resolve-activity', '--brief', '--user',
            '0', '-a', 'android.intent.action.MAIN',
            '-c', 'android.intent.category.LAUNCHER', package]
    try:
      proc = await asyncio.create_subprocess_exec(
          *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
      out, _ = await proc.communicate()
      if proc.returncode != 0:
        return None
      # Output is two lines: priority then "<pkg>/<class>"
      for line in out.decode().strip().splitlines():
        line = line.strip()
        if '/' in line and line.startswith(package + '/'):
          return line.split('/', 1)[1]
      return None
    except Exception as e:
      logging.warning('resolve_launcher_activity failed: %s', e)
      return None

  async def wait_for_goal(self) -> str:
    """Block until the chat UI submits a goal, then return it."""
    return await self._goal_queue.get()

  async def send_agent_state(self, state: str, detail: str = '') -> None:
    """Notify the chat UI about the agent's high-level state.

    state is one of: 'idle', 'thinking', 'executing', 'waiting', 'done', 'error'.
    """
    await self.send_command({
        'type': 'agent_state',
        'state': state,
        'detail': detail,
    })

  async def send_agent_message(self, text: str, role: str = 'agent') -> None:
    """Push a chat message into the Android chat history.

    role is one of: 'agent', 'user', 'system'.
    """
    await self.send_command({
        'type': 'agent_message',
        'role': role,
        'text': text,
    })

  async def _handle_launch_app_request(self, msg: dict, websocket) -> None:
    """Launch an app on the virtual display via ADB."""
    package = msg.get('package', '')
    activity = msg.get('activity', '')
    display_id = self._display_id
    if not package or display_id is None:
      logging.warning('launch_app_request: missing package or display_id')
      resp = {'type': 'launch_app_result', 'package': package, 'success': False}
      await websocket.send(json.dumps(resp))
      return

    if not activity:
      activity = await self._resolve_launcher_activity(package)
      if not activity:
        logging.warning('launch_app_request: could not resolve launcher for %s', package)
        resp = {'type': 'launch_app_result', 'package': package, 'success': False}
        await websocket.send(json.dumps(resp))
        return

    cmd = [self._adb_path]
    if self._serial:
      cmd += ['-s', self._serial]
    cmd += ['shell', 'am', 'start', '--display', str(display_id),
            '--windowingMode', '1', '-n', f'{package}/{activity}']
    logging.info('Launching app via ADB: %s', ' '.join(cmd))
    try:
      proc = await asyncio.create_subprocess_exec(
          *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
      _, stderr = await proc.communicate()
      success = proc.returncode == 0
      if not success:
        logging.warning('ADB launch failed: %s', stderr.decode())
      else:
        logging.info('ADB launch succeeded: %s on display %d', package, display_id)
    except Exception as e:
      logging.error('ADB launch exception: %s', e)
      success = False

    resp = {'type': 'launch_app_result', 'package': package, 'success': success}
    await websocket.send(json.dumps(resp))

    if success:
      self._pinned_packages.add(package)
      self._ensure_pin_loop_running()

  def _ensure_pin_loop_running(self) -> None:
    """Start the background task that pins migrated tasks back to the VD."""
    if self._pin_task is not None and not self._pin_task.done():
      return
    self._pin_task = asyncio.create_task(self._pin_loop())

  async def _pin_loop(self) -> None:
    """Poll dumpsys and move escaped tasks back to the virtual display.

    Third-party apps internally call startActivity with no launchDisplayId,
    so the new task lands on display 0 and the existing one is dragged
    along. This loop detects that and uses `cmd activity display move-stack`
    (works under shell uid) to push the task back. There is an unavoidable
    100-500 ms flash on the physical display while the task is on display 0.
    """
    import time
    poll_interval = 0.1
    move_cooldown = 0.4  # don't re-move the same task more than once per 400 ms
    last_moved: dict[int, float] = {}
    logging.info('pin_loop started; pinning packages=%s to display %s',
                 self._pinned_packages, self._display_id)
    while self._pinned_packages and self._display_id is not None:
      try:
        target_display = self._display_id
        adb_cmd = [self._adb_path]
        if self._serial:
          adb_cmd += ['-s', self._serial]
        proc = await asyncio.create_subprocess_exec(
            *(adb_cmd + ['shell', 'dumpsys', 'activity', 'activities']),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        text = out.decode(errors='ignore')

        # Walk Display sections looking for tasks belonging to a pinned
        # package that have escaped to a display other than the VD.
        #
        # We need to be strict here: an earlier version tracked the most
        # recent Task{} id loosely and matched any ActivityRecord whose line
        # contained the package — that incorrectly attributed unrelated
        # tasks (e.g. Chrome, t87) to Calendar and dragged them onto the VD.
        #
        # New approach: treat each Task{...} line as a hard boundary,
        # buffer the task's id, then determine its package from the FIRST
        # ActivityRecord line inside the same task block. Only enroll the
        # task in escaped_tasks if its detected package is pinned AND its
        # display is not the target VD.
        current_display = None
        in_task = False
        task_id: Optional[int] = None
        task_pkg: Optional[str] = None
        escaped_tasks: dict[int, str] = {}

        def _finalize_task() -> None:
          nonlocal in_task, task_id, task_pkg
          if (in_task
              and task_id is not None
              and task_pkg is not None
              and task_pkg in self._pinned_packages
              and current_display is not None
              and current_display != target_display):
            escaped_tasks.setdefault(task_id, task_pkg)
          in_task = False
          task_id = None
          task_pkg = None

        for line in text.splitlines():
          stripped = line.strip()
          if stripped.startswith('Display #'):
            _finalize_task()
            try:
              current_display = int(stripped.split('#', 1)[1].split(' ', 1)[0])
            except (IndexError, ValueError):
              current_display = None
            continue
          if 'Task{' in stripped and '#' in stripped:
            # New task — finalize the previous one before starting this.
            _finalize_task()
            in_task = True
            idx = stripped.find('#')
            tid_str = stripped[idx + 1:].split(' ', 1)[0]
            try:
              task_id = int(tid_str)
            except ValueError:
              task_id = None
            continue
          # Inside a task: detect its package from the first `Hist #N:`
          # ActivityRecord line — this is the actual activity backstack of
          # the task. We deliberately ignore other ActivityRecord references
          # like `mLastPausedActivity: ActivityRecord{...}` because those
          # can mention any package and would falsely tag e.g. the launcher
          # task as being a Calendar task.
          if (in_task
              and task_pkg is None
              and stripped.startswith('* Hist')
              and 'ActivityRecord' in stripped):
            for pkg in self._pinned_packages:
              if f' {pkg}/' in stripped:
                task_pkg = pkg
                break

        # Flush the final task at EOF.
        _finalize_task()

        now = time.monotonic()
        for task_id, pkg in escaped_tasks.items():
          # Throttle so we don't fire move-stack on every poll for the same
          # task — gives the system a moment to actually move it.
          last = last_moved.get(task_id, 0.0)
          if now - last < move_cooldown:
            continue
          last_moved[task_id] = now
          move_cmd = adb_cmd + [
              'shell', 'cmd', 'activity', 'display', 'move-stack',
              str(task_id), str(target_display),
          ]
          logging.info('pin_loop: moving %s task #%d back to display %d',
                       pkg, task_id, target_display)
          await asyncio.create_subprocess_exec(
              *move_cmd, stdout=asyncio.subprocess.DEVNULL,
              stderr=asyncio.subprocess.DEVNULL)
          # After move-stack the activity is recreated on the VD, but its
          # first paint isn't pushed into the TextureView — the overlay
          # turns blank white. A 1-pixel wm size toggle forces a
          # configuration change that makes every window relayout/redraw.
          async def _nudge_after_move(disp: int, _adb_cmd=adb_cmd) -> None:
            await asyncio.sleep(0.4)
            for w_arg in (f'1080x{1920 - 1}', '1080x1920'):
              p = await asyncio.create_subprocess_exec(
                  *(_adb_cmd + ['shell', 'wm', 'size', w_arg, '-d', str(disp)]),
                  stdout=asyncio.subprocess.DEVNULL,
                  stderr=asyncio.subprocess.DEVNULL)
              await p.wait()
          asyncio.create_task(_nudge_after_move(target_display))
      except Exception as e:
        logging.warning('pin_loop iteration failed: %s', e)
      await asyncio.sleep(poll_interval)
    logging.info('pin_loop exiting')

  async def _handle_touch(self, msg: dict) -> None:
    """Process touch events from the overlay and inject via ADB."""
    if self._display_id is None:
      return

    action = msg.get('action')
    x = msg.get('x', 0)
    y = msg.get('y', 0)

    if action == 'down':
      self._touch_start = (x, y)

    elif action == 'up':
      if self._touch_start is not None:
        sx, sy = self._touch_start
        dist = math.sqrt((x - sx) ** 2 + (y - sy) ** 2)
        if dist < 20:
          # Tap
          cmd = [
              self._adb_path, 'shell', 'input', '-d', str(self._display_id),
              'tap', str(int(x)), str(int(y)),
          ]
        else:
          # Swipe
          cmd = [
              self._adb_path, 'shell', 'input', '-d', str(self._display_id),
              'swipe', str(int(sx)), str(int(sy)),
              str(int(x)), str(int(y)), '300',
          ]
        logging.info('ADB inject: %s', ' '.join(cmd))
        proc = await asyncio.create_subprocess_exec(*cmd)
        await proc.wait()
      self._touch_start = None

    # 'move' events are ignored to avoid ADB latency issues

  async def _vd_top_resumed_activity(self) -> Optional[str]:
    """Return the top resumed activity component on the current VD, or None.

    Used to detect when the user has finished interacting with a
    show_element form: e.g. tapping Calendar's real Save button transitions
    the resumed activity from EditEventActivity back to AllInOneCalendar,
    which we treat as an implicit "user is done" signal.
    """
    if self._display_id is None:
      return None
    cmd = [self._adb_path]
    if self._serial:
      cmd += ['-s', self._serial]
    cmd += ['shell', 'dumpsys', 'activity', 'activities']
    try:
      proc = await asyncio.create_subprocess_exec(
          *cmd, stdout=asyncio.subprocess.PIPE,
          stderr=asyncio.subprocess.DEVNULL)
      out, _ = await proc.communicate()
    except Exception as e:
      logging.debug('vd_top_resumed_activity: %s', e)
      return None
    text = out.decode(errors='ignore')
    target = f'Display #{self._display_id}'
    in_section = False
    for line in text.splitlines():
      stripped = line.strip()
      if stripped.startswith('Display #'):
        in_section = stripped.startswith(target)
        continue
      if in_section and 'topResumedActivity=' in stripped:
        try:
          comp = stripped.split('topResumedActivity=', 1)[1]
          comp = comp.split(' u0 ', 1)[1].split(' ', 1)[0]
          return comp
        except (IndexError, ValueError):
          return None
    return None

  async def wait_for_interaction(self, timeout: float = 60.0) -> Optional[dict]:
    """Wait for a user interaction event from the overlay.

    Two channels can unblock the wait:
      1. An explicit `user_interaction` / `dismiss` WS message from the app
         (the user tapped X / Done on the overlay).
      2. The VD's top resumed activity changes — i.e. the user pressed Save
         inside the mirrored real form and the underlying app navigated to
         a different screen. We treat that as an implicit "I'm done" signal
         so the user doesn't have to manually close the overlay.

    Args:
      timeout: Seconds to wait.

    Returns:
      The interaction message dict, or None if timed out.
    """
    self._interaction_event.clear()
    self._last_interaction = None
    initial_top = await self._vd_top_resumed_activity()
    logging.info('wait_for_interaction: initial top activity=%s', initial_top)

    async def _watch_screen_change() -> None:
      # We only treat the activity change as "user is done" once the new
      # activity has been stable for STABILITY_SECONDS. This filters out
      # transient dialogs (date pickers, time pickers, dropdowns, share
      # sheets, etc.) that pop up while the user is still interacting with
      # the form. A real submit/back navigation lands on a non-form screen
      # that stays put long enough to clear the threshold.
      stability_seconds = 2.0
      poll_interval = 0.5
      candidate: Optional[str] = None
      stable_for = 0.0
      while True:
        await asyncio.sleep(poll_interval)
        current = await self._vd_top_resumed_activity()
        if not current or not initial_top or current == initial_top:
          # Back on the original screen — reset, keep waiting.
          candidate = None
          stable_for = 0.0
          continue
        if current == candidate:
          stable_for += poll_interval
        else:
          candidate = current
          stable_for = poll_interval
        if stable_for >= stability_seconds:
          logging.info(
              'wait_for_interaction: activity stable on %s for %.1fs '
              '(was %s), treating as implicit user-done signal',
              current, stable_for, initial_top,
          )
          self._last_interaction = {
              'interaction_type': 'screen_changed',
              'from': initial_top,
              'to': current,
          }
          self._interaction_event.set()
          return

    watcher = asyncio.create_task(_watch_screen_change())
    try:
      await asyncio.wait_for(self._interaction_event.wait(), timeout=timeout)
      return self._last_interaction
    except asyncio.TimeoutError:
      logging.info('wait_for_interaction timed out after %.0fs', timeout)
      return None
    finally:
      if not watcher.done():
        watcher.cancel()
        try:
          await watcher
        except (asyncio.CancelledError, Exception):
          pass

  async def get_ui_tree(self, timeout: float = 10.0) -> str:
    """Request UI tree XML from the Android app via AccessibilityService.

    Args:
      timeout: Seconds to wait for response.

    Returns:
      uiautomator-compatible XML string.
    """
    self._ui_tree_event.clear()
    self._last_ui_tree = ''
    await self.send_command({
        'type': 'get_ui_tree',
        'display_id': self._display_id or 0,
    })
    try:
      await asyncio.wait_for(self._ui_tree_event.wait(), timeout=timeout)
      return self._last_ui_tree
    except asyncio.TimeoutError:
      logging.warning('get_ui_tree timed out after %.0fs', timeout)
      return '<hierarchy rotation="0"></hierarchy>'

  async def capture_screenshot(self, timeout: float = 10.0) -> Optional[bytes]:
    """Request a screenshot from the Android app via WebSocket.

    Returns:
      PNG bytes, or None if timed out or failed.
    """
    self._screenshot_event.clear()
    self._last_screenshot = ''
    await self.send_command({'type': 'capture_screenshot'})
    try:
      await asyncio.wait_for(self._screenshot_event.wait(), timeout=timeout)
      if self._last_screenshot:
        import base64
        return base64.b64decode(self._last_screenshot)
      return None
    except asyncio.TimeoutError:
      logging.warning('capture_screenshot timed out after %.0fs', timeout)
      return None

  async def send_command(self, command: dict[str, Any]) -> None:
    """Send a JSON command to the connected Android app.

    Args:
      command: The command dict to send.
    """
    if self._ws is None:
      logging.warning('No app connected, cannot send command: %s', command)
      return
    # Log a compact preview so the operator can see what each step actually
    # sends to the device.
    preview = {k: v for k, v in command.items() if k != 'elements'}
    if 'elements' in command:
      preview['elements_count'] = len(command['elements'])
    logging.info('-> app: %s', json.dumps(preview, ensure_ascii=False))
    try:
      await self._ws.send(json.dumps(command))
    except websockets.ConnectionClosed:
      logging.warning('Connection lost while sending command')
      self._ws = None

  @staticmethod
  def _ui_element_to_dict(elem, index: int) -> dict:
    """Convert a UIElement to a dict for the overlay."""
    bounds = {}
    if elem.bbox_pixels:
      bounds = {
          'x1': int(elem.bbox_pixels.x_min),
          'y1': int(elem.bbox_pixels.y_min),
          'x2': int(elem.bbox_pixels.x_max),
          'y2': int(elem.bbox_pixels.y_max),
      }
    return {
        'index': index,
        'text': elem.text or '',
        'subtext': elem.content_description or '',
        'clickable': bool(elem.is_clickable),
        'bounds': bounds,
    }

  @staticmethod
  def _ui_group_to_dict(group) -> dict:
    """Convert a UIGroup to a dict for the overlay."""
    return {
        'index': group.index,
        'text': group.label or '',
        'subtext': ' | '.join(group.children_texts[:3]) if group.children_texts else '',
        'clickable': group.is_clickable,
        'scrollable': group.is_scrollable,
        'bounds': {
            'x1': int(group.bbox.x_min),
            'y1': int(group.bbox.y_min),
            'x2': int(group.bbox.x_max),
            'y2': int(group.bbox.y_max),
        },
    }

  async def send_visualization(
      self,
      action_type: str,
      text: str,
      visualization: Optional[dict],
      ui_elements: list,
      ui_groups: list = None,
  ) -> None:
    """Resolve visualization and send the appropriate command to the app.

    Uses parsed UI data to render native overlay (not VD mirroring).
    """
    interactive = action_type == 'ask'

    if visualization is None:
      await self.send_command({'type': action_type, 'text': text})
      return

    viz_type = visualization.get('visualization_type')

    if viz_type == 'none':
      await self.send_command({'type': action_type, 'text': text})

    elif viz_type == 'show_app':
      # Mirror the entire VD content as a popup overlay (live SurfaceView).
      await self.send_command({
          'type': 'show_app',
          'text': text,
          'interactive': interactive,
      })

    elif viz_type == 'show_element':
      indexes = visualization.get('index', [])
      if isinstance(indexes, int):
        indexes = [indexes]

      # Compute union bounds from the selected UI groups (preferred) or
      # fall back to flat UIElements. When the LLM sees ui_groups_text, its
      # indexes refer to group slots; otherwise they refer to flat elements.
      bboxes = []
      if ui_groups and indexes:
        group_map = {g.index: g for g in ui_groups}
        for i in indexes:
          g = group_map.get(i)
          if g:
            bb = g.bbox
            bboxes.append((bb.x_min, bb.y_min, bb.x_max, bb.y_max))
      if not bboxes and ui_elements and indexes:
        for i in indexes:
          if 0 <= i < len(ui_elements):
            bb = ui_elements[i].bbox_pixels
            if bb:
              bboxes.append((bb.x_min, bb.y_min, bb.x_max, bb.y_max))

      if bboxes:
        x1 = int(min(b[0] for b in bboxes))
        y1 = int(min(b[1] for b in bboxes))
        x2 = int(max(b[2] for b in bboxes))
        y2 = int(max(b[3] for b in bboxes))
        await self.send_command({
            'type': 'show_element',
            'text': text,
            'interactive': interactive,
            'bounds': {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2},
        })
      else:
        logging.warning('show_element: no valid bounds for indexes %s', indexes)
        await self.send_command({'type': action_type, 'text': text})

    elif viz_type == 'generate_ui':
      instruction = visualization.get('instruction', '')
      if instruction and self._llm:
        from m3a_agent import genui_agent
        logging.info('GenUI: generating HTML for: %s', instruction[:80])
        html = genui_agent.generate_html(self._llm, instruction)
        if html:
          await self.send_command({
              'type': 'show_genui',
              'text': text,
              'html': html,
              'interactive': interactive,
          })
        else:
          logging.warning('GenUI generation failed, falling back to speak')
          await self.send_command({'type': action_type, 'text': text})
      else:
        logging.warning('generate_ui with no instruction or no LLM')
        await self.send_command({'type': action_type, 'text': text})

    else:
      await self.send_command({'type': action_type, 'text': text})

  async def close(self) -> None:
    """Shut down the WebSocket server."""
    if self._server:
      self._server.close()
      await self._server.wait_closed()
      self._server = None
    self._ws = None
