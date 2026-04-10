"""Send a hardcoded show_element command to verify cropped overlay rendering."""
import asyncio
import logging
import sys
sys.path.insert(0, '.')
from m3a_agent.server import AppConnection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


async def main():
    app_conn = AppConnection(port=8765, serial='emulator-5554')
    display_id = await app_conn.start_and_wait_for_app(timeout=36000.0)
    print(f'Connected. display_id={display_id}')

    # Make sure DoorDash is on the VD.
    print('Pre-launching com.dd.doordash...')
    await app_conn._handle_launch_app_request(
        {'type': 'launch_app_request', 'package': 'com.dd.doordash', 'activity': ''},
        app_conn._ws)
    print('Waiting 6s for DoorDash to settle...')
    await asyncio.sleep(6)

    # Try the upper portion where DoorDash typically renders the category strip.
    bounds = {'x1': 0, 'y1': 300, 'x2': 1080, 'y2': 600}
    print(f'Sending show_element with bounds={bounds}')
    await app_conn.send_command({
        'type': 'show_element',
        'text': 'Pick a category (cropped)',
        'interactive': True,
        'bounds': bounds,
    })
    print('Command sent. The overlay should show ONLY the category row.')

    print('Sleeping 30s before exit.')
    await asyncio.sleep(30)
    await app_conn.close()


if __name__ == '__main__':
    asyncio.run(main())
