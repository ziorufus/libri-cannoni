"""Sessione Vinted: `vinted auth import` / `vinted auth status` dall'interfaccia admin.

Si esegue la CLI di vinted-lister (python -m vinted) invece di copiarne il
parsing: il cURL arriva su stdin (non compare negli argomenti del processo) e il
file dei token è lo stesso usato dal backend (VINTED_ENV_FILE). Le operazioni
successive vedono subito i nuovi token: ogni operazione crea un client nuovo.
"""

import os
import subprocess
import sys

from .config import config
from .vinted_search import VintedOpError

TIMEOUT_SECONDS = 60


def _run(args: list[str], stdin: str = "") -> tuple[bool, str]:
    env = dict(os.environ)
    if config.vinted_env_file:
        env["VINTED_ENV_FILE"] = config.vinted_env_file
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "vinted", *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise VintedOpError(f"vinted {' '.join(args)}: nessuna risposta dopo {TIMEOUT_SECONDS} secondi") from None
    except OSError as exc:
        raise VintedOpError(f"Impossibile avviare vinted-lister: {exc}") from exc
    output = "\n".join(t.strip() for t in (proc.stdout, proc.stderr) if t.strip())
    if "No module named vinted" in output:
        raise VintedOpError("Pacchetto vinted-lister non installato nel virtualenv del backend")
    return proc.returncode == 0, output[:4000]


def auth_import(curl: str) -> tuple[bool, str]:
    """Importa i token da un "Copy as cURL" e verifica la sessione."""
    return _run(["auth", "import"], stdin=curl)


def auth_status() -> tuple[bool, str]:
    return _run(["auth", "status"])
