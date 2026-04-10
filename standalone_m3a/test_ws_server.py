"""Minimal WebSocket server for testing app launch + click via backend."""
import asyncio
import logging
import sys
sys.path.insert(0, '.')
from m3a_agent.server import AppConnection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

async def command_poller(app_conn):
    """Poll /tmp/genui_cmd.txt for commands and execute them."""
    import os
    cmd_file = '/tmp/genui_cmd.txt'
    print(f"Watching {cmd_file} for commands (click <text>, tree)")
    while True:
        await asyncio.sleep(0.5)
        if os.path.exists(cmd_file):
            try:
                with open(cmd_file) as f:
                    line = f.read().strip()
                os.remove(cmd_file)
                if not line:
                    continue
                print(f"[CMD] {line}")
                if line == "tree":
                    xml = await app_conn.get_ui_tree(timeout=10.0)
                    print(xml[:3000])
                elif line.startswith("click "):
                    text = line[6:]
                    await app_conn.send_command({
                        'type': 'click_by_text',
                        'display_id': app_conn.display_id or 0,
                        'text': text,
                    })
                    print(f"Sent click_by_text '{text}'")
            except Exception as e:
                print(f"Error: {e}")

async def main():
    app_conn = AppConnection(port=8765, serial='emulator-5554')
    display_id = await app_conn.start_and_wait_for_app(timeout=36000.0)
    print(f'Connected! display_id={display_id}')
    await command_poller(app_conn)

if __name__ == '__main__':
    asyncio.run(main())
