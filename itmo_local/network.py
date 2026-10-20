from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from .common import SafeError


def retry_delay(value: str | None) -> int:
    if not value:
        return 0
    try:
        return max(0, min(86400, int(value)))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0, min(86400, int(parsed.timestamp() - time.time())))
        except (TypeError, ValueError, OverflowError):
            return 0


class HTTP:
    """No redirects, proxy environment, debug logging, response-body errors or TLS bypass."""
    def __init__(self, max_bytes: int = 4_000_000, transport: httpx.BaseTransport | None = None):
        self.max_bytes = max_bytes
        self.transport = transport

    def json(self, method: str, url: str, *, allowed_host: str,
             headers: dict[str, str] | None = None, params: dict[str, str] | None = None,
             data: dict[str, str] | None = None, token_request: bool = False) -> Any:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != allowed_host or parsed.port not in {None, 443}
                or parsed.username or parsed.password or parsed.fragment):
            raise SafeError("unsafe_outbound_url")
        request_headers = {"Accept": "application/json", "User-Agent": "Personal-ITMO-Calendar/0.1"}
        if headers:
            request_headers.update(headers)
        # A separate client per request avoids shared cookie/session mutation
        # across the worker and OAuth callback threads.
        try:
            with httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=False,
                              trust_env=False, transport=self.transport) as client:
                with client.stream(method, url, headers=request_headers, params=params, data=data) as response:
                    deadline = time.monotonic() + 30
                    if 300 <= response.status_code < 400:
                        raise SafeError("upstream_redirect_refused")
                    if response.status_code == 429:
                        raise SafeError("upstream_rate_limited", retry_after=retry_delay(response.headers.get("Retry-After")))
                    if response.status_code >= 500:
                        raise SafeError("upstream_unavailable", retry_after=retry_delay(response.headers.get("Retry-After")))
                    if response.status_code == 401 and not token_request:
                        raise SafeError("access_token_rejected")
                    if response.status_code == 403:
                        raise SafeError("upstream_access_forbidden")
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > self.max_bytes:
                            raise SafeError("upstream_response_too_large")
                        if time.monotonic() > deadline:
                            raise SafeError("upstream_request_deadline")
                    if token_request and response.status_code >= 400:
                        try:
                            error = json.loads(chunks).get("error")
                        except (ValueError, AttributeError):
                            error = None
                        if error in {"invalid_grant", "invalid_client", "unauthorized_client", "invalid_scope"}:
                            raise SafeError("oauth_" + error)
                        raise SafeError("oauth_exchange_failed")
                    if response.status_code != 200:
                        raise SafeError("unexpected_upstream_status")
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    if content_type != "application/json" and not content_type.endswith("+json"):
                        raise SafeError("upstream_not_json")
                    try:
                        return json.loads(chunks)
                    except (ValueError, UnicodeError):
                        raise SafeError("upstream_invalid_json") from None
        except httpx.HTTPError:
            # Never propagate an exception whose repr includes Authorization or URLs.
            raise SafeError("upstream_network_error") from None
