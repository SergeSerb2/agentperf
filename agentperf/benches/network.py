"""Network (the Reach score): DNS, TLS, and time to first byte against the model
APIs, plus bulk download/upload. Sends no credentials and calls no paid API."""

from __future__ import annotations

import http.client
import socket
import ssl

from ..registry import bench
from ..util import now

API_HOSTS = {"anthropic": "api.anthropic.com", "openai": "api.openai.com"}
SPEED_HOST = "speed.cloudflare.com"
DOWNLOAD_BYTES = 25_000_000
UPLOAD_BYTES = 8_000_000


def _context(ctx) -> ssl.SSLContext:
    """A verifying TLS context; falls back to no verification only for timing (no secrets sent)."""
    def make():
        verified = ssl.create_default_context()
        try:
            with socket.create_connection((API_HOSTS["anthropic"], 443), timeout=10) as raw:
                with verified.wrap_socket(raw, server_hostname=API_HOSTS["anthropic"]):
                    pass
            ctx.note("net.tls_anthropic", tls_verified=True)
            return verified
        except ssl.SSLCertVerificationError:
            ctx.note("net.tls_anthropic", tls_verified=False)
            return ssl._create_unverified_context()  # timing only; no data is trusted
    return ctx.cached("tls-context", make)


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
        tls.recv(1)
        return (now() - t0) * 1000.0
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
        context = _context(ctx)
        return lambda: _handshake_ms(host, context)

    @bench("network.ttfb_" + name, title="Time to first byte from " + host, unit="ms",
           lower_is_better=True, models="Round trip floor for every model turn", reps=7)
    def ttfb(ctx):
        context = _context(ctx)
        return lambda: _ttfb_ms(host, context)


for _name, _host in API_HOSTS.items():
    _api_benches(_name, _host)


def _conn(ctx) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(SPEED_HOST, 443, timeout=60, context=_context(ctx))


@bench("network.download", title="Download 25 MB (Cloudflare speed test)", unit="Mbit/s",
       lower_is_better=False, models="Package installs, git clones, model downloads", reps=3,
       budget_s=40)
def download(ctx):
    def sample():
        conn = _conn(ctx)
        try:
            t0 = now()
            conn.request("GET", "/__down?bytes={}".format(DOWNLOAD_BYTES))
            resp = conn.getresponse()
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
    body = b"0" * UPLOAD_BYTES

    def sample():
        conn = _conn(ctx)
        try:
            t0 = now()
            conn.request("POST", "/__up", body=body,
                         headers={"Content-Type": "application/octet-stream"})
            conn.getresponse().read()
            elapsed = now() - t0
        finally:
            conn.close()
        return UPLOAD_BYTES * 8 / 1e6 / elapsed

    return sample


@bench("network.registry", title="npm registry metadata request", unit="ms", lower_is_better=True,
       models="Dependency resolution during installs", reps=5)
def registry(ctx):
    context = _context(ctx)

    def sample():
        conn = http.client.HTTPSConnection("registry.npmjs.org", 443, timeout=20, context=context)
        try:
            t0 = now()
            conn.request("GET", "/left-pad",
                         headers={"Accept": "application/vnd.npm.install-v1+json"})
            conn.getresponse().read()
            return (now() - t0) * 1000.0
        finally:
            conn.close()

    return sample
