"""Token OAuth dell'abbonamento Claude, letto e scritto in un file JSON.

Il file (CLAUDE_JSON_FILE) ha lo stesso formato delle credenziali di Claude Code:
{"claudeAiOauth": {"accessToken": ..., "refreshToken": ..., "expiresAt": <ms>, ...}}.
Il refresh riscrive il file (le altre chiavi restano), quindi l'utente di uvicorn
deve poter scrivere nel file e nella sua cartella. Serve per leggere l'uso
dell'abbonamento: l'analisi (claude -p) usa ancora CLAUDE_CODE_OAUTH_TOKEN.
"""

import asyncio
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .config import config

TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # client OAuth di Claude Code
BETA_HEADER = "oauth-2025-04-20"
TIMEOUT = 30
EXPIRY_MARGIN_MS = 5 * 60 * 1000  # rinnova se scade entro 5 minuti

_lock = asyncio.Lock()  # un solo refresh alla volta (il refresh token cambia)


class ClaudeOAuthError(Exception):
    pass


def _path() -> Path:
    if not config.claude_json_file:
        raise ClaudeOAuthError("CLAUDE_JSON_FILE non impostato nel backend")
    return Path(config.claude_json_file).expanduser()


def _load() -> tuple[Path, dict[str, Any]]:
    path = _path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise ClaudeOAuthError(f"File del token Claude non trovato: {path}") from None
    except (OSError, ValueError) as exc:
        raise ClaudeOAuthError(f"File del token Claude illeggibile ({path}): {exc}") from exc
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        raise ClaudeOAuthError(f'{path}: manca "claudeAiOauth.accessToken"')
    return path, data


def _save(path: Path, data: dict[str, Any]) -> None:
    """Scrittura atomica (file temporaneo + rename) con permessi 600."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _fmt_ms(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).astimezone().strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


async def refresh() -> str:
    """Rinnova access e refresh token e li salva; restituisce un messaggio di esito."""
    async with _lock:
        path, data = await asyncio.to_thread(_load)
        oauth = data["claudeAiOauth"]
        if not oauth.get("refreshToken"):
            raise ClaudeOAuthError(f'{path}: manca "claudeAiOauth.refreshToken"')
        body = {"grant_type": "refresh_token", "refresh_token": oauth["refreshToken"], "client_id": CLIENT_ID}
        if oauth.get("scopes"):
            body["scope"] = " ".join(oauth["scopes"])
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(TOKEN_URL, json=body)
        except httpx.HTTPError as exc:
            raise ClaudeOAuthError(f"Refresh del token Claude: errore di rete ({exc!r})") from exc
        if resp.status_code != 200:
            raise ClaudeOAuthError(f"Refresh del token Claude: HTTP {resp.status_code}\n{resp.text[:1000]}")
        try:
            tok = resp.json()
            access = tok["access_token"]
        except (ValueError, KeyError, TypeError):
            raise ClaudeOAuthError(f"Refresh del token Claude: risposta inattesa\n{resp.text[:1000]}") from None

        oauth["accessToken"] = access
        if tok.get("refresh_token"):
            oauth["refreshToken"] = tok["refresh_token"]
        if tok.get("expires_in"):
            oauth["expiresAt"] = int(time.time() * 1000) + int(tok["expires_in"]) * 1000
        if tok.get("refresh_token_expires_in"):
            oauth["refreshTokenExpiresAt"] = int(time.time() * 1000) + int(tok["refresh_token_expires_in"]) * 1000
        if isinstance(tok.get("scope"), str) and tok["scope"]:
            oauth["scopes"] = tok["scope"].split()
        try:
            await asyncio.to_thread(_save, path, data)
        except OSError as exc:
            raise ClaudeOAuthError(f"Token rinnovato ma non salvato in {path}: {exc}") from exc

    msg = f"Token rinnovato, scade il {_fmt_ms(oauth.get('expiresAt'))}"
    if oauth.get("refreshTokenExpiresAt"):
        msg += f" (refresh token valido fino al {_fmt_ms(oauth['refreshTokenExpiresAt'])})"
    return msg


async def _access_token() -> str:
    """Access token valido, rinnovato prima se è scaduto o sta per scadere."""
    _, data = await asyncio.to_thread(_load)
    oauth = data["claudeAiOauth"]
    expires = oauth.get("expiresAt")
    if isinstance(expires, (int, float)) and expires - EXPIRY_MARGIN_MS < time.time() * 1000:
        await refresh()
        _, data = await asyncio.to_thread(_load)
    return data["claudeAiOauth"]["accessToken"]


async def fetch_usage() -> dict[str, Any]:
    """Risposta di /api/oauth/usage; con un 401 rinnova il token e riprova una volta."""
    token = await _access_token()
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {token}", "anthropic-beta": BETA_HEADER}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(USAGE_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise ClaudeOAuthError(f"Uso di Claude: errore di rete ({exc!r})") from exc
        if resp.status_code == 401 and attempt == 0:
            await refresh()
            token = await _access_token()
            continue
        if resp.status_code != 200:
            raise ClaudeOAuthError(f"Uso di Claude: HTTP {resp.status_code}\n{resp.text[:1000]}")
        try:
            data = resp.json()
        except ValueError:
            raise ClaudeOAuthError(f"Uso di Claude: risposta non JSON\n{resp.text[:1000]}") from None
        if not isinstance(data, dict):
            raise ClaudeOAuthError(f"Uso di Claude: risposta inattesa\n{resp.text[:1000]}")
        return data
    raise AssertionError("unreachable")
