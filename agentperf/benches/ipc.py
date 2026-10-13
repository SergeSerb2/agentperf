"""IPC: capturing tool output through pipes and talking to local servers."""

from __future__ import annotations

import os
import socket
import threading

from ..registry import bench
from ..util import now

PIPE_MB = 128
STREAM_MB = 128
RTT_COUNT = 2000


@bench("ipc.pipe", title="Pipe 128 MB between threads", unit="MB/s", lower_is_better=False,
       models="Harness reading a tool's stdout", reps=5)
def pipe(ctx):
    block = os.urandom(64 * 1024)
    total = PIPE_MB * 1024 * 1024

    def sample():
        r, w = os.pipe()

        def writer():
            try:
                for _ in range(total // len(block)):
                    os.write(w, block)
            finally:
                os.close(w)

        thread = threading.Thread(target=writer)
        t0 = now()
        thread.start()
        got = 0
        while True:
            data = os.read(r, 65536)
            if not data:
                break
            got += len(data)
        thread.join()
        elapsed = now() - t0
        os.close(r)
        return PIPE_MB / elapsed

    return sample


def _server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    return srv


@bench("ipc.tcp_rtt", title="Loopback TCP round trip (64 bytes)", unit="us", lower_is_better=True,
       models="Local dev servers, LSP/MCP over sockets, test HTTP calls", reps=7)
def tcp_rtt(ctx):
    def sample():
        srv = _server()

        def echo():
            conn, _ = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with conn:
                while True:
                    data = conn.recv(64)
                    if not data:
                        break
                    conn.sendall(data)

        thread = threading.Thread(target=echo)
        thread.start()
        cli = socket.create_connection(srv.getsockname())
        cli.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        msg = b"x" * 64
        t0 = now()
        for _ in range(RTT_COUNT):
            cli.sendall(msg)
            need = 64
            while need:
                need -= len(cli.recv(need))
        elapsed = now() - t0
        cli.close()
        thread.join()
        srv.close()
        return elapsed * 1e6 / RTT_COUNT

    return sample


@bench("ipc.tcp_stream", title="Loopback TCP stream 128 MB", unit="MB/s", lower_is_better=False,
       models="Local file servers, Docker port forwards, dev proxies", reps=5)
def tcp_stream(ctx):
    block = os.urandom(1024 * 1024)

    def sample():
        srv = _server()
        done = {}

        def sink():
            conn, _ = srv.accept()
            got = 0
            with conn:
                while True:
                    data = conn.recv(1 << 20)
                    if not data:
                        break
                    got += len(data)
            done["t"] = now()
            done["bytes"] = got

        thread = threading.Thread(target=sink)
        thread.start()
        cli = socket.create_connection(srv.getsockname())
        t0 = now()
        for _ in range(STREAM_MB):
            cli.sendall(block)
        cli.close()
        thread.join()
        srv.close()
        return STREAM_MB / (done["t"] - t0)

    return sample
