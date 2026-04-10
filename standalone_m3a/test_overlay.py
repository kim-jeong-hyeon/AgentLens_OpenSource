"""One-shot test: dump UI tree from VD and send it as Full UI overlay."""
import asyncio
import logging
import sys
sys.path.insert(0, '.')
from m3a_agent.server import AppConnection
from m3a_agent.env import representation_utils

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def ui_elements_to_dicts(elements):
    out = []
    for i, elem in enumerate(elements):
        if not elem.is_visible or not elem.bbox_pixels:
            continue
        if not (elem.text or elem.content_description):
            continue
        out.append({
            'index': i,
            'text': elem.text or '',
            'subtext': elem.content_description or '',
            'clickable': bool(elem.is_clickable),
            'bounds': {
                'x1': int(elem.bbox_pixels.x_min),
                'y1': int(elem.bbox_pixels.y_min),
                'x2': int(elem.bbox_pixels.x_max),
                'y2': int(elem.bbox_pixels.y_max),
            },
        })
    return out


async def main():
    app_conn = AppConnection(port=8765, serial='emulator-5554')
    display_id = await app_conn.start_and_wait_for_app(timeout=36000.0)
    print(f'Connected. display_id={display_id}')

    # Make sure DoorDash is running on the VD before we try to dump UI tree.
    print('Pre-launching com.dd.doordash on the VD...')
    await app_conn._handle_launch_app_request(
        {'type': 'launch_app_request', 'package': 'com.dd.doordash', 'activity': ''},
        app_conn._ws)
    print('Waiting 5s for splash → main screen...')
    await asyncio.sleep(5)

    print('Sending show_app command (mirrors live VD content into the overlay)...')
    await app_conn.send_command({
        'type': 'show_app',
        'text': 'Live VD mirror — DoorDash',
        'interactive': False,
    })
    print('Overlay command sent. The actual rendered VD content should appear.')

    print('Sleeping 60s before exit (overlay stays up).')
    await asyncio.sleep(60)
    await app_conn.close()


if __name__ == '__main__':
    asyncio.run(main())
