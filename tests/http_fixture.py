"""Run a loopback test HTTP host without changing the process event-loop policy."""
from __future__ import annotations

import asyncio


def run_http_server(server, sockets):
    # Python 3.12.9 Proactor can skip transport detach after a Windows peer reset,
    # leaving server.wait_closed() stuck. Only this HTTP thread uses Selector;
    # Playwright's Windows subprocess pipes still need the default Proactor.
    # Runner also works with Uvicorn 0.30, before Config accepted loop factories.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(server.serve(sockets=sockets))
