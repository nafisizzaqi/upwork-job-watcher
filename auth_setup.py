"""One-time Upwork OAuth login with a local callback server.

Jalankan sekali untuk login, token + refresh token disimpan di
.upwork_token.json (jangan di-commit, jangan dikirim ke chat).
Jalan kedua harusnya TIDAK minta login lagi kalau token masih bisa di-refresh.

Hanya read-only: list_accounts + 1x find_jobs (shopify, fixed).
Tidak ada submit / save / kirim pesan ke Upwork.
"""

import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx2
import requests
from dotenv import load_dotenv
from pydantic import AnyUrl

from mcp import Client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata

try:
    from importlib.metadata import version as _pkg_version
except ImportError:  # Python lama, tidak dipakai di sini
    _pkg_version = None

try:
    from mcp.shared.auth import AuthorizationCodeResult
except ImportError:
    try:
        from mcp.client.auth import AuthorizationCodeResult
    except ImportError:
        AuthorizationCodeResult = None

try:
    from mcp.shared.auth import OAuthToken as _TokenCls
except ImportError:
    try:
        from mcp.shared.auth import OAuthTokens as _TokenCls
    except ImportError:
        _TokenCls = None

try:
    from mcp.shared.auth import OAuthClientInformationFull as _ClientInfoCls
except ImportError:
    try:
        from mcp.shared.auth import OAuthClientInformation as _ClientInfoCls
    except ImportError:
        _ClientInfoCls = None

load_dotenv()

SERVER_URL = "https://mcp.upwork.com/mcp"
CALLBACK_PORT = 3000
TOKEN_FILE = Path(".upwork_token.json")
_callback_result = {}


def _obj_to_dict(obj):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "dict"):
        return obj.dict()
    return dict(vars(obj))


def _dict_to_obj(cls, data):
    if data is None or cls is None:
        return None
    if isinstance(data, cls):
        return data
    if hasattr(cls, "model_validate"):
        return cls.model_validate(data)
    return cls(**data)


class FileTokenStorage(TokenStorage):
    """Token + info client disimpan di .upwork_token.json, chmod 600."""

    def _read(self):
        try:
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, data):
        tmp = TOKEN_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, TOKEN_FILE)
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass

    async def get_tokens(self):
        data = self._read().get("tokens")
        return _dict_to_obj(_TokenCls, data) if data else None

    async def set_tokens(self, tokens):
        data = self._read()
        data["tokens"] = _obj_to_dict(tokens)
        self._write(data)

    async def get_client_info(self):
        data = self._read().get("client_info")
        return _dict_to_obj(_ClientInfoCls, data) if data else None

    async def set_client_info(self, client_info):
        data = self._read()
        data["client_info"] = _obj_to_dict(client_info)
        self._write(data)


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        qs = parse_qs(parsed.query)
        if "error" in qs:
            _callback_result["error"] = qs["error"][0]
            _callback_result["error_description"] = qs.get("error_description", [""])[0]
        else:
            _callback_result["code"] = qs.get("code", [""])[0]
            _callback_result["state"] = qs.get("state", [None])[0]
            _callback_result["iss"] = qs.get("iss", [None])[0]
        body = (
            b"<html><body><h1>Login selesai.</h1>"
            b"<p>Balik ke terminal. Jangan tempel URL ini ke chat.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def handle_redirect(auth_url: str) -> None:
    print("\n=== LOGIN UPWORK DIPERLUKAN ===")
    print("Buka URL ini di Brave (salin-tempel dari terminal, jangan kirim ke chat):")
    print(auth_url)
    print("Shields down untuk upwork.com, klik Authorize access SEKALI,")
    print("tunggu halaman 'Login selesai'. JANGAN tempel URL callback ke terminal,")
    print("server di localhost:3000 akan menangkapnya otomatis. Lakukan dalam")
    print("beberapa menit, kode authorize kedaluwarsa cepat.")


async def wait_for_callback():
    def serve_once():
        with HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler) as server:
            server.handle_request()

    await asyncio.wait_for(asyncio.to_thread(serve_once), timeout=300)
    if "error" in _callback_result:
        raise RuntimeError(
            f"OAuth error: {_callback_result['error']} "
            f"{_callback_result.get('error_description', '')}"
        )
    if not _callback_result.get("code"):
        raise RuntimeError("Callback diterima tapi tidak ada code.")
    return AuthorizationCodeResult(
        code=_callback_result["code"],
        state=_callback_result.get("state"),
        iss=_callback_result.get("iss"),
    )


def _result_text(result):
    texts = []
    for part in getattr(result, "content", []) or []:
        t = getattr(part, "text", None)
        if t:
            texts.append(t)
    return "\n".join(texts)


def _result_json(result):
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        return sc
    text = _result_text(result)
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_text": text}
    return {}


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _find_key(item, key)
            if r is not None:
                return r
    return None


def _find_jobs_list(obj):
    if isinstance(obj, dict):
        for k in ("jobs", "results", "data"):
            if isinstance(obj.get(k), list):
                return obj[k]
        for v in obj.values():
            r = _find_jobs_list(v)
            if r:
                return r
    elif isinstance(obj, list):
        return obj
    return []


def _send_telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram dilewati: token/chat id belum ada di .env")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        print(f"Telegram: {r.status_code}")
    except requests.RequestException as exc:
        print(f"Telegram gagal: {exc}")


async def main():
    if _pkg_version is not None:
        try:
            print(f"mcp version: {_pkg_version('mcp')}")
        except Exception:
            pass
    if AuthorizationCodeResult is None or _TokenCls is None or _ClientInfoCls is None:
        print("Import SDK tidak cocok. Kirim hasil 'pip show mcp' yaa.")
        sys.exit(1)
    print("Token file:", TOKEN_FILE, "| ada:" , TOKEN_FILE.exists())
    print("Jangan kirim token / code / URL authorize ke chat.")

    storage = FileTokenStorage()
    oauth = OAuthClientProvider(
        server_url=SERVER_URL,
        client_metadata=OAuthClientMetadata(
            client_name="upwork-job-watcher",
            redirect_uris=[AnyUrl(f"http://localhost:{CALLBACK_PORT}/callback")],
        ),
        storage=storage,
        redirect_handler=handle_redirect,
        callback_handler=wait_for_callback,
    )

    async with httpx2.AsyncClient(
        auth=oauth, timeout=httpx2.Timeout(30.0, read=300.0)
    ) as http_client:
        async with Client(
            streamable_http_client(SERVER_URL, http_client=http_client)
        ) as client:
            tools_result = await client.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            print("Tools:", ", ".join(tool_names))

            def pick(suffix):
                for name in tool_names:
                    if name == suffix or name.endswith("_" + suffix) or name.endswith(suffix):
                        return name
                return None

            list_tool = pick("list_accounts")
            find_tool = pick("find_jobs")
            if not list_tool or not find_tool:
                print("Tool list_accounts / find_jobs tidak ditemukan.")
                return

            accounts = await client.call_tool(list_tool, {})
            accounts_data = _result_json(accounts)
            org_uid = _find_key(accounts_data, "org_uid")
            if not org_uid:
                print("org_uid tidak ditemukan dari list_accounts. Cek hasil di atas.")
                return
            print("org_uid ditemukan (jangan kirim nilainya ke chat).")

            jobs_result = await client.call_tool(
                find_tool,
                {
                    "action": "search",
                    "org_uid": org_uid,
                    "params": {
                        "title": "shopify",
                        "job_type": "fixed",
                        "budget_min": 100,
                        "proposals_max": 5,
                        "verified_payment_only": True,
                        "sort": "recency",
                        "limit": 1,
                        "include_full_details": True,
                    },
                },
            )
            jobs_data = _result_json(jobs_result)
            Path("jobs_real.json").write_text(
                json.dumps(jobs_data, indent=2, default=str), encoding="utf-8"
            )
            print("Hasil mentah tersimpan di jobs_real.json")
            jobs = _find_jobs_list(jobs_data)
            print(f"Jumlah job: {len(jobs)}")
            if jobs:
                first = jobs[0] if isinstance(jobs[0], dict) else {}
                print("Judul:", first.get("title"))
                print("Budget:", first.get("budget") or first.get("amount"))
                print("Negara:", first.get("country") or first.get("client_country"))
                print("Proposals:", first.get("proposals_tier") or first.get("proposals"))
                print("URL:", first.get("url") or first.get("job_url"))
                _send_telegram(
                    f"OAuth Upwork OK, 1 job shopify ditemukan: {first.get('title')}"
                )
            print("Token tersimpan di .upwork_token.json (kalau login baru saja terjadi).")
            print("Jalanin lagi script ini: kalau tidak muncul URL login, token tersimpan bekerja.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDibatalkan. Token lama (kalau ada) tetap tersimpan, jalanin lagi untuk URL baru.")
