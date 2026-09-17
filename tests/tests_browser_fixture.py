"""Real loopback fixture shutdown after a Windows peer-reset cleanup failure."""
from __future__ import annotations

import asyncio
import http.client
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.browser_fixture import BrowserFixture  # noqa: E402


class BrowserFixtureShutdownTests(unittest.TestCase):
    def test_http_fixture_keeps_default_subprocess_support(self):
        policy = asyncio.get_event_loop_policy()

        async def child_output():
            child = await asyncio.create_subprocess_exec(
                sys.executable, '-I', '-c', 'print("fixture-child-ok")',
                stdout=asyncio.subprocess.PIPE)
            output, _ = await asyncio.wait_for(child.communicate(), timeout=5)
            self.assertEqual(child.returncode, 0)
            return output.strip()

        with BrowserFixture() as fixture:
            self.assertIs(asyncio.get_event_loop_policy(), policy)
            self.assertEqual(asyncio.run(child_output()), b'fixture-child-ok')
        self.assertFalse(fixture.thread.is_alive())
        self.assertFalse(fixture.root.exists())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows transport regression')
    def test_peer_reset_does_not_leave_http_host_waiting_for_closed_connection(self):
        fixture = BrowserFixture().__enter__()
        client = http.client.HTTPConnection('127.0.0.1', fixture.port, timeout=3)
        transport = None
        original_shutdown = socket.socket.shutdown
        failures = []
        callback_finished = threading.Event()

        def reset_during_shutdown(sock, how):
            if how == socket.SHUT_RDWR and sock.getsockname()[1] == fixture.port:
                raise ConnectionResetError(10054, 'Peer reset during transport shutdown')
            return original_shutdown(sock, how)

        try:
            client.request('GET', '/api/health')
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection = next(iter(fixture.server.server_state.connections))
            transport = connection.transport
            loop = transport._loop

            def record_failure(_loop, context):
                failures.append(type(context.get('exception')).__name__)
                _loop.default_exception_handler(context)

            def disconnect():
                loop.set_exception_handler(record_failure)
                transport.close()
                loop.call_soon(callback_finished.set)

            # Inject only the OS socket error from the CI traceback. Protocol,
            # transport callbacks, server connection tracking and shutdown are real.
            with patch.object(socket.socket, 'shutdown', reset_during_shutdown):
                loop.call_soon_threadsafe(disconnect)
                self.assertTrue(callback_finished.wait(3), 'transport callback did not run')
            print(json.dumps({'loop': type(loop).__name__, 'callback_errors': failures,
                'connections': len(fixture.server.server_state.connections),
                'tasks': len(fixture.server.server_state.tasks),
                'attached_transports': [server._active_count for server in fixture.server.servers]}), flush=True)
            client.close()
            fixture.close()
            self.assertFalse(fixture.thread.is_alive())
            self.assertFalse(failures)
            with socket.socket() as probe:
                probe.settimeout(1)
                self.assertNotEqual(probe.connect_ex(('127.0.0.1', fixture.port)), 0)
        finally:
            client.close()
            # The unfixed Proactor callback skipped _detach(). Repair only this
            # captured test transport so a red run does not leave a daemon behind.
            if (transport is not None and fixture.thread.is_alive()
                    and getattr(transport, '_server', None) is not None
                    and hasattr(transport, '_call_connection_lost')):
                transport._loop.call_soon_threadsafe(transport._call_connection_lost, None)
                fixture.thread.join(timeout=3)
            fixture.close()


if __name__ == '__main__':
    unittest.main()
