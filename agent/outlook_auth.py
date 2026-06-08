import html
import os
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .secret_store import SecretStore, SecretStoreError


OUTLOOK_CALLBACK_HOST = "127.0.0.1"
OUTLOOK_CALLBACK_PORT = 53682
OUTLOOK_CALLBACK_PATH = "/auth/outlook/callback"
OUTLOOK_REDIRECT_URI = f"http://localhost:{OUTLOOK_CALLBACK_PORT}{OUTLOOK_CALLBACK_PATH}"
OUTLOOK_SCOPES = ["User.Read", "Mail.Read", "Mail.ReadWrite", "offline_access"]
OUTLOOK_AUTH_REQUEST_SCOPES = ["User.Read", "Mail.Read", "Mail.ReadWrite"]
OUTLOOK_CLIENT_ID_ENV = "NJORDHR_OUTLOOK_CLIENT_ID"
OUTLOOK_TENANT_ENV = "NJORDHR_OUTLOOK_TENANT_ID"
OUTLOOK_TOKEN_CACHE_KEY = "outlook_msal_cache"


def _load_msal():
    try:
        import msal
    except ImportError as exc:
        raise RuntimeError("msal is required for Outlook PKCE auth. Install the 'msal' package.") from exc
    return msal


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class OutlookAuthManager:
    def __init__(self, settings_store, secret_store=None):
        self.settings_store = settings_store
        self.secret_store = secret_store or SecretStore(service_name="NjordHR.OutlookIntake")
        self._lock = threading.RLock()
        self._server = None
        self._server_thread = None
        self._active_flow = None

    def _client_id(self):
        cfg = self.settings_store.get()
        return str(cfg.get("outlook_client_id", "")).strip() or os.getenv(OUTLOOK_CLIENT_ID_ENV, "").strip()

    def _tenant_id(self):
        cfg = self.settings_store.get()
        return str(cfg.get("outlook_tenant_id", "")).strip() or os.getenv(OUTLOOK_TENANT_ENV, "organizations").strip() or "organizations"

    def _authority(self):
        return f"https://login.microsoftonline.com/{self._tenant_id()}"

    def _new_cache(self):
        msal = _load_msal()
        cache = msal.SerializableTokenCache()
        try:
            serialized = self.secret_store.get(OUTLOOK_TOKEN_CACHE_KEY)
        except SecretStoreError:
            serialized = None
        if serialized:
            cache.deserialize(serialized)
        return cache

    def _save_cache(self, cache):
        if cache and cache.has_state_changed:
            self.secret_store.set(OUTLOOK_TOKEN_CACHE_KEY, cache.serialize())

    def _delete_cache(self):
        try:
            self.secret_store.delete(OUTLOOK_TOKEN_CACHE_KEY)
        except SecretStoreError:
            return

    def _public_client_app(self, cache):
        msal = _load_msal()
        return msal.PublicClientApplication(
            client_id=self._client_id(),
            authority=self._authority(),
            token_cache=cache,
        )

    def _cached_account(self):
        client_id = self._client_id()
        if not client_id:
            return None
        try:
            cache = self._new_cache()
            app = self._public_client_app(cache)
            accounts = app.get_accounts()
        except Exception:
            return None
        return accounts[0] if accounts else None

    def acquire_access_token(self):
        client_id = self._client_id()
        if not client_id:
            raise RuntimeError("outlook_client_id is required before Outlook auth can start.")
        cache = self._new_cache()
        app = self._public_client_app(cache)
        accounts = app.get_accounts()
        if not accounts:
            raise RuntimeError("Outlook mailbox is not connected.")
        result = app.acquire_token_silent(OUTLOOK_AUTH_REQUEST_SCOPES, account=accounts[0])
        self._save_cache(cache)
        if not result:
            raise RuntimeError("Outlook token refresh failed. Disconnect and connect the mailbox again.")
        if "access_token" not in result:
            message = result.get("error_description") or result.get("error") or "Outlook token refresh failed."
            raise RuntimeError(str(message))
        return result["access_token"]

    def status(self):
        cfg = self.settings_store.get()
        account = self._cached_account()
        with self._lock:
            flow = dict(self._active_flow or {})
        return {
            "configured": bool(self._client_id()),
            "client_id_present": bool(self._client_id()),
            "tenant_id": self._tenant_id(),
            "authority": self._authority(),
            "redirect_uri": OUTLOOK_REDIRECT_URI,
            "scopes": list(OUTLOOK_SCOPES),
            "keyring_available": self.secret_store.available(),
            "connected": bool(account),
            "connected_account": (
                cfg.get("outlook_connected_account", "")
                or (account or {}).get("username", "")
            ),
            "mailbox": cfg.get("email_intake_mailbox", ""),
            "enabled": bool(cfg.get("email_intake_enabled", False)),
            "monitored_folder": cfg.get("email_intake_monitored_folder", ""),
            "processed_folder": cfg.get("email_intake_processed_folder", ""),
            "failed_folder": cfg.get("email_intake_failed_folder", ""),
            "poll_interval_seconds": cfg.get("email_intake_poll_interval_seconds", 60),
            "auth_in_progress": bool(flow),
            "auth_started_at": flow.get("started_at", ""),
            "auth_url": flow.get("auth_url", ""),
            "last_error": cfg.get("outlook_last_auth_error", ""),
        }

    def health_summary(self):
        status = self.status()
        return {
            "configured": status["configured"],
            "keyring_available": status["keyring_available"],
            "connected": status["connected"],
            "connected_account": status["connected_account"],
            "mailbox": status["mailbox"],
            "enabled": status["enabled"],
            "poll_interval_seconds": status["poll_interval_seconds"],
            "monitored_folder": status["monitored_folder"],
            "processed_folder": status["processed_folder"],
            "failed_folder": status["failed_folder"],
            "auth_in_progress": status["auth_in_progress"],
            "last_error": status["last_error"],
            "redirect_uri": status["redirect_uri"],
        }

    def _clear_active_flow(self):
        with self._lock:
            self._active_flow = None

    def _stop_server(self):
        with self._lock:
            server = self._server
            thread = self._server_thread
            self._server = None
            self._server_thread = None
        if server:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1)

    def _build_callback_server(self):
        manager = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != OUTLOOK_CALLBACK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Not found")
                    return
                params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                flat = {key: values[-1] for key, values in params.items()}
                result = manager.complete_auth_response(flat)
                body = manager._callback_html(result)
                code = 200 if result.get("success") else 400
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                threading.Thread(target=manager._stop_server, daemon=True).start()

        return ThreadingHTTPServer((OUTLOOK_CALLBACK_HOST, OUTLOOK_CALLBACK_PORT), Handler)

    def _serve_callback_once(self, server):
        with self._lock:
            self._server = server
            self._server_thread = threading.current_thread()
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            try:
                server.server_close()
            except Exception:
                pass

    def _callback_html(self, result):
        title = "NjordHR Outlook Intake Connected" if result.get("success") else "NjordHR Outlook Intake Failed"
        message = result.get("message") or ("You can return to NjordHR." if result.get("success") else "Authentication failed.")
        detail = ""
        if not result.get("success"):
            detail = (
                "Return to NjordHR, open Download > Mailbox Intake, and use Connect Mailbox again. "
                "If this repeats, check that Microsoft Entra allows the redirect URI "
                f"{OUTLOOK_REDIRECT_URI} and that local secure token storage is available."
            )
        safe_title = html.escape(title)
        safe_message = html.escape(message)
        safe_detail = html.escape(detail)
        detail_html = f"<p>{safe_detail}</p>" if safe_detail else ""
        return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>{safe_title}</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 32px; color: #1f2937; }}
      .card {{ max-width: 640px; padding: 24px; border: 1px solid #d1d5db; border-radius: 12px; }}
      h1 {{ margin-top: 0; }}
    </style>
  </head>
  <body>
    <div class="card">
      <h1>{safe_title}</h1>
      <p>{safe_message}</p>
      {detail_html}
    </div>
  </body>
</html>""".encode("utf-8")

    def start_auth_flow(self, open_browser=False):
        self._stop_server()
        self._clear_active_flow()

        client_id = self._client_id()
        if not client_id:
            return {"success": False, "message": "outlook_client_id is required before Outlook auth can start."}
        if not self.secret_store.available():
            return {"success": False, "message": "Secure secret storage is unavailable. Install/configure keyring first."}

        try:
            cache = self._new_cache()
            app = self._public_client_app(cache)
            flow = app.initiate_auth_code_flow(
                scopes=OUTLOOK_AUTH_REQUEST_SCOPES,
                redirect_uri=OUTLOOK_REDIRECT_URI,
            )
        except OSError as exc:
            return {"success": False, "message": f"Could not start Outlook callback listener: {exc}"}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

        auth_url = flow.get("auth_uri", "")
        with self._lock:
            self._active_flow = {
                "app": app,
                "cache": cache,
                "flow": flow,
                "auth_url": auth_url,
                "started_at": _now_iso(),
        }
        self.settings_store.update({"outlook_last_auth_error": ""})

        try:
            server = self._build_callback_server()
        except OSError as exc:
            self._clear_active_flow()
            return {"success": False, "message": f"Could not start Outlook callback listener: {exc}"}

        thread = threading.Thread(
            target=self._serve_callback_once,
            args=(server,),
            daemon=True,
            name="outlook-auth-callback",
        )
        thread.start()

        opened = False
        if open_browser and auth_url:
            opened = bool(webbrowser.open(auth_url))

        return {
            "success": True,
            "auth_url": auth_url,
            "redirect_uri": OUTLOOK_REDIRECT_URI,
            "opened_browser": opened,
            "message": "Open the Microsoft sign-in page and complete consent to connect the mailbox.",
        }

    def complete_auth_response(self, query_params):
        with self._lock:
            active = dict(self._active_flow or {})
        if not active:
            return {"success": False, "message": "No active Outlook auth flow is waiting for a callback."}

        try:
            result = active["app"].acquire_token_by_auth_code_flow(active["flow"], query_params)
        except Exception as exc:
            self.settings_store.update({"outlook_last_auth_error": str(exc)})
            self._clear_active_flow()
            return {"success": False, "message": str(exc)}

        if "error" in result:
            message = result.get("error_description") or result.get("error") or "Microsoft login failed."
            self.settings_store.update({"outlook_last_auth_error": message})
            self._clear_active_flow()
            return {"success": False, "message": message}

        claims = result.get("id_token_claims") or {}
        connected_account = claims.get("preferred_username") or claims.get("upn") or ""
        try:
            self._save_cache(active["cache"])
        except Exception as exc:
            self.settings_store.update({"outlook_last_auth_error": str(exc)})
            self._clear_active_flow()
            return {"success": False, "message": str(exc)}

        self.settings_store.update({
            "outlook_connected_account": connected_account,
            "outlook_last_auth_error": "",
        })
        self._clear_active_flow()
        return {
            "success": True,
            "connected_account": connected_account,
            "message": f"Connected {connected_account or 'Microsoft account'} to Outlook intake.",
        }

    def disconnect(self):
        self._stop_server()
        self._clear_active_flow()
        self._delete_cache()
        self.settings_store.update({
            "outlook_connected_account": "",
            "outlook_last_auth_error": "",
            "email_intake_enabled": False,
        })
        return {"success": True, "message": "Outlook mailbox disconnected."}

    def shutdown(self):
        self._stop_server()
