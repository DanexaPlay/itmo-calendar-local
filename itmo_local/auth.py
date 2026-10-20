"""Local OAuth Authorization Code + PKCE for a *registered, permitted* ITMO client.

Does not reuse Chrome cookies or impersonate ITMO's first-party client.
The redirect URI must be allowed in ITMO.ID; there is no fallback bypass.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import math
import secrets as random_secrets
import time
import webbrowser
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from .common import SafeError, file_lock
from .config import ISSUER, Settings
from .network import HTTP

PERMANENT_AUTH_ERRORS = {"oauth_invalid_grant", "oauth_invalid_client", "oauth_unauthorized_client", "oauth_invalid_scope",
                         "refresh_token_expired", "refresh_token_missing", "token_configuration_changed"}


def _number(value: Any) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise SafeError("invalid_token_expiry")
    return float(value)


def _secret(value: Any, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value) or len(value) > 20000:
        raise SafeError("invalid_token_response")
    if any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise SafeError("invalid_token_response")
    return value


class Auth:
    def __init__(self, settings: Settings, http: HTTP, protected):
        self.settings = settings
        self.http = http
        self.protected = protected
        self.lock = settings.data_dir / "auth.lock"

    def _require_client(self) -> None:
        if not self.settings.client_id:
            raise SafeError("permitted_itmo_oauth_client_required")

    def _read(self) -> dict[str, Any] | None:
        tokens = self.protected.get_tokens()
        if tokens is not None and (tokens.get("client_id") != self.settings.client_id or tokens.get("issuer") != ISSUER):
            raise SafeError("token_configuration_changed")
        return tokens

    def _client_fields(self) -> dict[str, str]:
        self._require_client()
        fields = {"client_id": self.settings.client_id}
        client_secret = self.protected.get("oauth_client_secret")
        if client_secret:
            fields["client_secret"] = _secret(client_secret)
        return fields

    def discovery(self) -> dict[str, str]:
        self._require_client()
        result = self.http.json("GET", ISSUER + "/.well-known/openid-configuration", allowed_host="id.itmo.ru")
        if not isinstance(result, dict) or result.get("issuer") != ISSUER:
            raise SafeError("oidc_issuer_mismatch")
        endpoints: dict[str, str] = {}
        for field in ("authorization_endpoint", "token_endpoint"):
            endpoint = result.get(field)
            parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
            if (parsed is None or parsed.scheme != "https" or parsed.netloc != "id.itmo.ru"
                    or parsed.query or parsed.fragment or not parsed.path.startswith("/auth/realms/itmo/")):
                raise SafeError("oidc_endpoint_not_allowed")
            endpoints[field] = endpoint
        methods = result.get("code_challenge_methods_supported")
        if methods is not None and "S256" not in methods:
            raise SafeError("oidc_pkce_s256_not_supported")
        return endpoints

    def _save_response(self, response: Any, now: float, *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(response, dict) or str(response.get("token_type", "Bearer")).lower() != "bearer":
            raise SafeError("invalid_token_response")
        access = _secret(response.get("access_token"))
        lifetime = _number(response.get("expires_in"))
        if lifetime < 10:
            raise SafeError("access_token_lifetime_too_short")
        refresh = _secret(response.get("refresh_token"), required=False)
        if previous is not None and not refresh:
            raise SafeError("replacement_refresh_token_missing")
        refresh_expiry = None
        if response.get("refresh_expires_in") is not None:
            duration = _number(response["refresh_expires_in"])
            if duration:
                refresh_expiry = now + duration
        tokens = {"issuer": ISSUER, "client_id": self.settings.client_id,
                  "access_token": access, "refresh_token": refresh,
                  "expires_at": now + lifetime, "refresh_expires_at": refresh_expiry,
                  "updated_at": now, "retry_not_before": 0, "blocked": False}
        self.protected.save_tokens(tokens)  # DPAPI-encrypted, atomic replace
        return tokens

    def _refresh_locked(self, tokens: dict[str, Any], now: float) -> dict[str, Any]:
        if tokens.get("blocked"):
            raise SafeError("reauthorization_required")
        if tokens.get("retry_not_before", 0) > now:
            raise SafeError("oauth_refresh_backoff", retry_after=math.ceil(tokens["retry_not_before"] - now))
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise SafeError("refresh_token_missing")
        if tokens.get("refresh_expires_at") is not None and tokens["refresh_expires_at"] <= now:
            tokens["blocked"] = True
            self.protected.save_tokens(tokens)
            raise SafeError("refresh_token_expired")
        fields = self._client_fields()
        fields.update({"grant_type": "refresh_token", "refresh_token": _secret(refresh)})
        try:
            # Never automatically retry a token exchange: ITMO refresh tokens
            # are one-time, and a timeout can mean the old token was consumed.
            response = self.http.json("POST", ISSUER + "/protocol/openid-connect/token",
                                      allowed_host="id.itmo.ru", data=fields, token_request=True)
            return self._save_response(response, time.time(), previous=tokens)
        except SafeError as error:
            if error.code in PERMANENT_AUTH_ERRORS or error.code == "replacement_refresh_token_missing":
                tokens["blocked"] = True
            else:
                tokens["retry_not_before"] = time.time() + max(300, error.retry_after)
            self.protected.save_tokens(tokens)
            raise

    def access_token(self, *, force_refresh: bool = False) -> str:
        with file_lock(self.lock):
            tokens = self._read()
            if tokens is None:
                raise SafeError("itmo_login_required")
            if tokens.get("blocked"):
                raise SafeError("reauthorization_required")
            now = time.time()
            if force_refresh or tokens["expires_at"] - now < 60:
                tokens = self._refresh_locked(tokens, now)
            return _secret(tokens.get("access_token"))

    def login(self, *, timeout: int = 180) -> None:
        """Interactive login over localhost, only for a registered redirect URI."""
        self._require_client()
        endpoints = self.discovery()
        state = random_secrets.token_urlsafe(32)
        verifier = random_secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {"client_id": self.settings.client_id, "response_type": "code", "scope": self.settings.scopes,
                  "redirect_uri": self.settings.redirect_uri, "state": state,
                  "code_challenge": challenge, "code_challenge_method": "S256"}
        authorize_url = endpoints["authorization_endpoint"] + "?" + urlencode(params)
        result: dict[str, str] = {}
        callback_path = urlsplit(self.settings.redirect_uri).path

        class Callback(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlsplit(self.path)
                values = parse_qs(parsed.query, keep_blank_values=True)
                supplied_state = values.get("state", [""])
                if parsed.path != callback_path or len(supplied_state) != 1 or not hmac.compare_digest(supplied_state[0], state):
                    self.send_error(400, "Invalid authorization callback")
                    return
                result["code"] = values.get("code", [""])[0]
                result["error"] = values.get("error", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write("<p>You may close this browser tab and return to the terminal.</p>".encode())

            def log_message(self, _format, *args):
                # Callback URL contains a sensitive OAuth code.
                pass

        port = urlsplit(self.settings.redirect_uri).port
        try:
            server = http.server.HTTPServer(("127.0.0.1", port), Callback)
        except OSError:
            raise SafeError("oauth_loopback_port_unavailable") from None
        with server:
            print("Открываю официальный ITMO.ID в браузере. Входи только на id.itmo.ru.")
            if not webbrowser.open(authorize_url):
                print("Браузер не открылся. Авторизационная ссылка (не публикуй её):\n" + authorize_url)
            deadline = time.monotonic() + timeout
            while not result and time.monotonic() < deadline:
                server.timeout = min(1, max(0.05, deadline - time.monotonic()))
                server.handle_request()
        if not result:
            raise SafeError("oauth_browser_timeout")
        if result.get("error"):
            raise SafeError("oauth_user_denied_or_client_not_permitted")
        code = result.get("code", "")
        if not code or len(code) > 10000:
            raise SafeError("invalid_oauth_code")
        fields = self._client_fields()
        fields.update({"grant_type": "authorization_code", "code": code,
                       "code_verifier": verifier, "redirect_uri": self.settings.redirect_uri})
        with file_lock(self.lock):
            response = self.http.json("POST", endpoints["token_endpoint"],
                                      allowed_host="id.itmo.ru", data=fields, token_request=True)
            self._save_response(response, time.time())

    def logout_local(self) -> None:
        with file_lock(self.lock):
            self.protected.forget("itmo_tokens")
        # This does not revoke ITMO.ID's issued tokens on its server.
