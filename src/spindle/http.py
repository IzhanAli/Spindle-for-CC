"""Minimal HTTP client built on ``urllib`` — the only network code in the tool.

Supports conditional requests (ETag / If-Modified-Since), gzip, a hard timeout
and a byte cap. It **never raises**: every failure (offline, timeout, 4xx/5xx,
malformed response, TLS error) is returned as a result object so callers can
simply move on. This code runs only during ``refresh`` — never at render time.

TLS self-heals the common macOS python.org problem where the interpreter ships
with **no CA certificates loaded** (every HTTPS request would otherwise fail
with CERTIFICATE_VERIFY_FAILED). We locate a CA bundle from, in order:
``ca_bundle`` arg → ``$SPINDLE_CA_BUNDLE`` / ``$SSL_CERT_FILE`` → the default
context (if it already has certs) → ``certifi`` → a known system bundle.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import ssl
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib import error, request

# Well-known CA bundle locations (macOS system, Homebrew OpenSSL, common Linux).
_SYSTEM_CA_CANDIDATES = (
    "/etc/ssl/cert.pem",
    "/private/etc/ssl/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
    "/usr/local/etc/openssl/cert.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/certs/ca-certificates.crt",
)

_CTX_CACHE: Dict[str, ssl.SSLContext] = {}


def _ssl_context(ca_bundle: Optional[str]) -> ssl.SSLContext:
    ca_bundle = (ca_bundle
                 or os.environ.get("SPINDLE_CA_BUNDLE")
                 or os.environ.get("SSL_CERT_FILE")
                 or "")
    if ca_bundle in _CTX_CACHE:
        return _CTX_CACHE[ca_bundle]

    if ca_bundle and os.path.exists(ca_bundle):
        ctx = ssl.create_default_context(cafile=ca_bundle)
        _CTX_CACHE[ca_bundle] = ctx
        return ctx

    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():           # interpreter has no trust store — heal it
        healed = False
        try:
            import certifi  # optional; present on many macOS python.org installs
            ctx.load_verify_locations(certifi.where())
            healed = bool(ctx.get_ca_certs())
        except Exception:
            pass
        if not healed:
            for cand in _SYSTEM_CA_CANDIDATES:
                try:
                    if os.path.exists(cand):
                        ctx.load_verify_locations(cand)
                        if ctx.get_ca_certs():
                            break
                except Exception:
                    continue
    _CTX_CACHE[ca_bundle] = ctx
    return ctx


@dataclass
class HttpResult:
    status: Optional[int]                 # None == transport/TLS failure
    body: Optional[bytes] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    final_url: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.body is not None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


def fetch(
    url: str,
    *,
    timeout: float,
    user_agent: str,
    max_bytes: int,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    ca_bundle: Optional[str] = None,
) -> HttpResult:
    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip",
        "Accept": "*/*",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    if extra_headers:
        headers.update(extra_headers)

    req = request.Request(url, headers=headers, method="GET")
    context = _ssl_context(ca_bundle) if url.lower().startswith("https") else None
    try:
        with request.urlopen(req, timeout=timeout, context=context) as resp:
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                try:
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                except OSError:
                    pass  # not actually gzipped / truncated — use as-is
            return HttpResult(
                status=getattr(resp, "status", 200) or 200,
                body=raw,
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
                final_url=resp.geturl(),
            )
    except error.HTTPError as e:
        if e.code == 304:                 # urllib surfaces 304 as an "error"
            return HttpResult(status=304)
        return HttpResult(status=e.code, error=f"HTTP {e.code}")
    except (error.URLError, TimeoutError, OSError, ValueError, ssl.SSLError) as e:
        return HttpResult(status=None, error=str(getattr(e, "reason", e)))


def fetch_json(url: str, **kwargs: Any) -> Optional[Any]:
    """Fetch and JSON-decode; returns ``None`` on any failure."""
    res = fetch(url, **kwargs)
    if not res.ok:
        return None
    try:
        return json.loads(res.body.decode("utf-8", "replace"))
    except ValueError:
        return None


def post_json(
    url: str,
    *,
    payload: Any,
    timeout: float,
    user_agent: str,
    max_bytes: int,
    headers: Optional[Dict[str, str]] = None,
    ca_bundle: Optional[str] = None,
) -> HttpResult:
    """POST a JSON body and return the raw ``HttpResult``.

    Same never-raises contract as :func:`fetch`: every failure (offline, timeout,
    4xx/5xx, TLS) comes back as a result object. On an HTTP error the response
    body is still returned when available, so callers can surface the API's error
    message. Used only by the AI layer during ``refresh`` — never at render time.
    """
    data = json.dumps(payload).encode("utf-8")
    hdrs = {
        "User-Agent": user_agent,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if headers:
        hdrs.update(headers)

    req = request.Request(url, data=data, headers=hdrs, method="POST")
    context = _ssl_context(ca_bundle) if url.lower().startswith("https") else None
    try:
        with request.urlopen(req, timeout=timeout, context=context) as resp:
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
            return HttpResult(
                status=getattr(resp, "status", 200) or 200,
                body=raw,
                final_url=resp.geturl(),
            )
    except error.HTTPError as e:
        body = None
        try:
            body = e.read()
        except Exception:
            pass
        return HttpResult(status=e.code, body=body, error=f"HTTP {e.code}")
    except (error.URLError, TimeoutError, OSError, ValueError, ssl.SSLError) as e:
        return HttpResult(status=None, error=str(getattr(e, "reason", e)))
