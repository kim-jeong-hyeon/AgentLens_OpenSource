"""One-shot: launch Calendar on the VD, then push show_app overlay.

Usage:
  cd standalone_m3a
  python test_show_app_calendar.py

Then on the device: open AgentLens app, tap Start Display.
"""
import asyncio
import logging
import sys

sys.path.insert(0, '.')
from m3a_agent.server import AppConnection

logging.basicConfig(
    level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

PACKAGE = 'com.google.android.calendar'


async def main():
  app_conn = AppConnection(port=8765, serial='emulator-5554')
  display_id = await app_conn.start_and_wait_for_app(timeout=36000.0)
  print(f'App connected. display_id={display_id}')

  # Launch Calendar on the VD via the existing handler (also enables pin_loop).
  await app_conn._handle_launch_app_request(
      {'type': 'launch_app_request', 'package': PACKAGE, 'activity': ''},
      app_conn._ws,
  )
  print('Calendar launched. Waiting 2.5s for splash...')
  await asyncio.sleep(2.5)

  # Push the full-VD mirror overlay.
  print('Sending show_app overlay command...')
  await app_conn.send_command({
      'type': 'show_app',
      'text': 'Calendar (test show_app)',
      'interactive': False,
  })

  print('Overlay sent. Holding the WebSocket open for 60s '
        '(close window or Ctrl+C when done).')
  await asyncio.sleep(60)
  await app_conn.send_command({'type': 'dismiss'})
  await app_conn.close()
  print('Done.')


if __name__ == '__main__':
  asyncio.run(main())
