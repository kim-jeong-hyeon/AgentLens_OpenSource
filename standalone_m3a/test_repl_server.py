"""Long-running WebSocket server with a file-based command REPL.

Once you connect the AgentLens app to this server, the connection stays alive
forever. To trigger actions, write a command to /tmp/genui_cmd.txt and the
server will execute it. The server never auto-exits.

Commands:
  launch <package>           Launch package on the VD via backend ADB.
  partial <y1> <y2>          Send a hardcoded show_element with bounds
                              (x1=0, y1=<y1>, x2=1080, y2=<y2>).
  full                        Send show_app (mirror entire VD).
  dismiss                     Dismiss any active overlay.
  tree                        Dump UI tree XML for the current VD.
  tap <x> <y>                 Inject a tap at VD coordinates.
  speak <text>                Send a speak command.
  ask <text>                  Send an ask command (no overlay).
  quit                        Stop the server.
"""
import asyncio
import logging
import os
import sys
sys.path.insert(0, '.')

# Load .env (project-local API key) before importing things that might use it.
def _load_dotenv():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.isfile(p):
        return
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            os.environ[k.strip()] = v.strip()
_load_dotenv()

from m3a_agent.server import AppConnection
from m3a_agent.infer import Gpt4Wrapper
from m3a_agent import genui_agent

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

CMD_FILE = '/tmp/genui_cmd.txt'


async def command_loop(app_conn: AppConnection):
    print(f'\nWatching {CMD_FILE} — write a command to it to trigger actions.')
    print('Commands: launch <pkg> / partial <y1> <y2> / full / dismiss / tree / tap <x> <y> / speak <text> / ask <text> / quit\n')
    while True:
        await asyncio.sleep(0.3)
        if not os.path.exists(CMD_FILE):
            continue
        try:
            with open(CMD_FILE) as f:
                line = f.read().strip()
            os.remove(CMD_FILE)
            if not line:
                continue
            print(f'\n[CMD] {line}')
            parts = line.split(maxsplit=1)
            cmd = parts[0]
            arg = parts[1] if len(parts) > 1 else ''

            if cmd == 'launch':
                await app_conn._handle_launch_app_request(
                    {'type': 'launch_app_request', 'package': arg, 'activity': ''},
                    app_conn._ws)
            elif cmd == 'partial':
                bits = arg.split()
                if len(bits) != 2:
                    print('  usage: partial <y1> <y2>')
                    continue
                y1, y2 = int(bits[0]), int(bits[1])
                await app_conn.send_command({
                    'type': 'show_element',
                    'text': f'Partial y={y1}-{y2}',
                    'interactive': True,
                    'bounds': {'x1': 0, 'y1': y1, 'x2': 1080, 'y2': y2},
                })
            elif cmd == 'full':
                await app_conn.send_command({
                    'type': 'show_app',
                    'text': 'Full app mirror',
                    'interactive': False,
                })
            elif cmd == 'dismiss':
                await app_conn.send_command({'type': 'dismiss'})
            elif cmd == 'tree':
                xml = await app_conn.get_ui_tree(timeout=10.0)
                with open('/tmp/genui_tree.xml', 'w') as f:
                    f.write(xml)
                print(f'  UI tree ({len(xml)} chars) written to /tmp/genui_tree.xml')
            elif cmd == 'tap':
                bits = arg.split()
                if len(bits) != 2:
                    print('  usage: tap <x> <y>')
                    continue
                x, y = int(bits[0]), int(bits[1])
                await app_conn._handle_touch({'action': 'down', 'x': x, 'y': y})
                await app_conn._handle_touch({'action': 'up', 'x': x, 'y': y})
            elif cmd == 'genui':
                # Generate an HTML "alternative UI" for the given instruction
                # via the LLM and ship it to the device as show_genui.
                if not arg:
                    print('  usage: genui <instruction>')
                    continue
                if app_conn._llm is None:
                    print('  error: LLM not initialized')
                    continue
                print(f'  generating HTML for: {arg[:120]}')
                html = genui_agent.generate_html(app_conn._llm, arg)
                if not html:
                    print('  HTML generation failed')
                    continue
                print(f'  got HTML ({len(html)} chars), sending show_genui')
                await app_conn.send_command({
                    'type': 'show_genui',
                    'text': arg[:80],
                    'html': html,
                    'interactive': True,
                })
            elif cmd == 'speak':
                await app_conn.send_command({'type': 'speak', 'text': arg})
            elif cmd == 'ask':
                await app_conn.send_command({'type': 'ask', 'text': arg})
            elif cmd == 'quit':
                print('  quitting')
                return
            else:
                print(f'  unknown command: {cmd}')
        except Exception as e:
            print(f'  error: {e}')


async def main():
    print('Initializing LLM (gpt-5.4)...')
    llm = Gpt4Wrapper(model_name='gpt-5.4')
    app_conn = AppConnection(port=8765, serial='emulator-5554', llm=llm)
    print('Waiting for AgentLens app to connect...')
    display_id = await app_conn.start_and_wait_for_app(timeout=36000.0)
    print(f'Connected. display_id={display_id}')
    await command_loop(app_conn)


if __name__ == '__main__':
    asyncio.run(main())
