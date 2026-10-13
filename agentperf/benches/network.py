"""Network (the Reach score): DNS, TLS, and time to first byte against the model
APIs, plus bulk download/upload. Sends no credentials and calls no paid API."""

from __future__ import annotations

import http.client
import socket
import ssl

from ..registry import Skip, bench
from ..util import now

API_HOSTS = {"anthropic": "api.anthropic.com", "openai": "api.openai.com"}
SPEED_HOST = "speed.cloudflare.com"
DOWNLOAD_BYTES = 25_000_000
UPLOAD_BYTES = 8_000_000


def _context_for(ctx, host: str, bench_id: str) -> ssl.SSLContext:
    """A TLS context for *host*, built and probed independently per host.

    A blocked Anthropic endpoint must not disable the OpenAI tests, and the
    verification outcome is recorded under the real benchmark id.
    """
    def make():
        verified = ssl.create_default_context()
        try:
            with socket.create_connection((host, 443), timeout=10) as raw:
                with verified.wrap_socket(raw, server_hostname=host):
                    pass
            return verified, True
        except ssl.SSLCertVerificationError:
            # timing only; no data is trusted
            return ssl._create_unverified_context(), False
        except OSError as exc:
            raise Skip("cannot reach {}: {}".format(host, exc))

    context, tls_verified = ctx.cached("tls-context-" + host, make)
    # The note is per benchmark, the context per host: a cached context
    # must still record the verification outcome under every benchmark
    # that uses it, not only the first one.
    ctx.note(bench_id, tls_verified=tls_verified)
    return context


def _handshake_ms(host: str, context: ssl.SSLContext) -> float:
    t0 = now()
    raw = socket.create_connection((host, 443), timeout=10)
    tls = context.wrap_socket(raw, server_hostname=host)  # new socket: no session reuse
    elapsed = (now() - t0) * 1000.0
    tls.close()
    return elapsed


def _ttfb_ms(host: str, context: ssl.SSLContext) -> float:
    raw = socket.create_connection((host, 443), timeout=10)
    tls = context.wrap_socket(raw, server_hostname=host)
    try:
        request = "GET / HTTP/1.1\r\nHost: {}\r\nUser-Agent: agentperf\r\nConnection: close\r\n\r\n"
        t0 = now()
        tls.sendall(request.format(host).encode("ascii"))
        first = tls.recv(1)
        elapsed = (now() - t0) * 1000.0
        if not first:
            raise RuntimeError("no response from " + host)
        # Validate the response after timing the first byte.
        line = first
        while not line.endswith(b"\r\n") and len(line) < 200:
            chunk = tls.recv(1)
            if not chunk:
                break
            line += chunk
        if not line.startswith(b"HTTP/"):
            raise RuntimeError("unexpected response from {}: {!r}".format(host, line[:60]))
        return elapsed
    finally:
        tls.close()


@bench("network.dns", title="DNS lookup of the model API hosts", unit="ms", lower_is_better=True,
       models="First request of every session", reps=5)
def dns(ctx):
    hosts = list(API_HOSTS.values()) + ["registry.npmjs.org"]

    def sample():
        t0 = now()
        for host in hosts:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return (now() - t0) * 1000.0 / len(hosts)

    return sample


def _api_benches(name: str, host: str):
    @bench("network.tls_" + name, title="TCP + TLS handshake to " + host, unit="ms",
           lower_is_better=True, models="New connection to the model API", reps=7)
    def handshake(ctx):
        context = _context_for(ctx, host, "network.tls_" + name)
        return lambda: _handshake_ms(host, context)

    @bench("network.ttfb_" + name, title="Time to first byte from " + host, unit="ms",
           lower_is_better=True, models="Round trip floor for every model turn", reps=7)
    def ttfb(ctx):
        context = _context_for(ctx, host, "network.ttfb_" + name)
        return lambda: _ttfb_ms(host, context)


for _name, _host in API_HOSTS.items():
    _api_benches(_name, _host)


def _conn(ctx, bench_id: str) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(
        SPEED_HOST, 443, timeout=60, context=_context_for(ctx, SPEED_HOST, bench_id))


@bench("network.download", title="Download 25 MB (Cloudflare speed test)", unit="Mbit/s",
       lower_is_better=False, models="Package installs, git clones, model downloads", reps=3,
       budget_s=40)
def download(ctx):
    _context_for(ctx, SPEED_HOST, "network.download")

    def sample():
        conn = _conn(ctx, "network.download")
        try:
            t0 = now()
            conn.request("GET", "/__down?bytes={}".format(DOWNLOAD_BYTES))
            resp = conn.getresponse()
            if resp.status != 200:
                resp.read()
                raise RuntimeError("download HTTP {}".format(resp.status))
            got = 0
            while True:
                data = resp.read(1 << 20)
                if not data:
                    break
                got += len(data)
            elapsed = now() - t0
        finally:
            conn.close()
        if got < DOWNLOAD_BYTES:
            raise RuntimeError("short download: {} bytes".format(got))
        return got * 8 / 1e6 / elapsed

    return sample


@bench("network.upload", title="Upload 8 MB (Cloudflare speed test)", unit="Mbit/s",
       lower_is_better=False, models="git push, artifact and log uploads", reps=3, budget_s=40)
def upload(ctx):
    _context_for(ctx, SPEED_HOST, "network.upload")
    body = b"0" * UPLOAD_BYTES

    def sample():
        conn = _conn(ctx, "network.upload")
        try:
            t0 = now()
            conn.request("POST", "/__up", body=body,
                         headers={"Content-Type": "application/octet-stream"})
            resp = conn.getresponse()
            resp.read()
            elapsed = now() - t0
            if resp.status != 200:
                raise RuntimeError("upload HTTP {}".format(resp.status))
        finally:
            conn.close()
        return UPLOAD_BYTES * 8 / 1e6 / elapsed

    return sample


@bench("network.registry", title="npm registry metadata request", unit="ms", lower_is_better=True,
       models="Dependency resolution during installs", reps=5)
def registry(ctx):
    context = _context_for(ctx, "registry.npmjs.org", "network.registry")

    def sample():
        conn = http.client.HTTPSConnection("registry.npmjs.org", 443, timeout=20, context=context)
        try:
            t0 = now()
            conn.request("GET", "/left-pad",
                         headers={"Accept": "application/vnd.npm.install-v1+json"})
            resp = conn.getresponse()
            body = resp.read()
            elapsed = (now() - t0) * 1000.0
            if resp.status != 200:
                raise RuntimeError("registry HTTP {}".format(resp.status))
            if b"left-pad" not in body:
                raise RuntimeError("registry response does not describe left-pad")
            return elapsed
        finally:
            conn.close()

    return sample
