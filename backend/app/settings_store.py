"""Impostazioni modificabili dagli amministratori, salvate nella tabella `settings`."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from .models import Setting
from .phases import ACTIVE_PHASES, PHASES, min_gap_key, pause_key

DEFAULT_DESCRIPTION_TEMPLATE = """\
{descrizione}

Titolo: {titolo}
Autore: {autore}
Editore: {editore}
Collana: {collana}
Anno: {anno}
Lingua: {lingua}
Condizioni: {condizioni}"""

DEFAULT_CLAUDE_PROMPT = """\
Nella cartella corrente ci sono le fotografie di un libro usato che verrà ceduto \
in cambio di un'offerta per Emergency. Le fotografie, nell'ordine in cui sono state \
scattate, sono: {foto}

Esamina tutte le fotografie (copertina, retro, costa, frontespizio, colophon, \
eventuali difetti) e ricava tutte le informazioni possibili sul libro.

Note aggiuntive fornite da chi ha fotografato il libro:
{note}

Rispondi SOLO con un oggetto JSON, senza altro testo, con queste chiavi:
- "titolo": titolo (e sottotitolo)
- "autore": autore/i
- "curatore", "traduttore"
- "editore", "collana", "anno", "edizione"
- "isbn": se visibile
- "lingua": lingua del testo, una parola in minuscolo (es. "italiano", "inglese")
- "formato", "legatura", "pagine"
- "categoria": esattamente una di queste: "narrativa", "saggistica", "ragazzi", \
"bambini", "neonati e bimbi piccoli", "fumetti", "libri di testo", \
"libri da colorare, rompicapo e attività", "riviste"
- "stato": stato di conservazione, con i difetti visibili
- "condizioni": esattamente una di queste parole: "ottime", "buone", "discrete"
- "condizioni_vinted": esattamente uno di questi valori: "nuovo con cartellino", \
"nuovo senza cartellino", "ottime", "buone", "discrete"
- "prezzo": prezzo di vendita dell'usato in euro, numero intero
- "descrizione": breve descrizione del contenuto, adatta a un annuncio

Se un'informazione non è ricavabile usa null invece di inventarla. \
Scrivi i testi in italiano."""


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    type: str  # "int" | "str" | "text" | "bool"
    default: Any
    help: str = ""
    min: int | None = None
    max: int | None = None
    group: str = "Generale"


DEFINITIONS: list[SettingDef] = [
    SettingDef(
        "photo_max_side",
        "Lato massimo delle foto (pixel)",
        "int",
        2048,
        "Le foto con il lato lungo più grande vengono ridimensionate (prima dell'invio dal "
        "telefono e comunque dal server). Vale per i libri inseriti da qui in avanti.",
        min=640,
        max=12000,
        group="Fotografie",
    ),
    SettingDef(
        "worker_interval_seconds",
        "Intervallo di controllo (secondi)",
        "int",
        30,
        "Ogni quanti secondi il processo in background cerca libri da far avanzare.",
        min=5,
        max=3600,
        group="Processo in background",
    ),
    SettingDef(
        "phase_max_attempts",
        "Numero massimo di tentativi",
        "int",
        3,
        "Dopo questo numero di tentativi falliti il libro si ferma nello stato \"fallito\".",
        min=1,
        max=100,
        group="Processo in background",
    ),
    SettingDef(
        "phase_retry_delay_minutes",
        "Attesa prima di ritentare (minuti)",
        "int",
        10,
        "Dopo un tentativo fallito, quanto aspettare prima di riprovare.",
        min=0,
        max=24 * 60,
        group="Processo in background",
    ),
    SettingDef(
        "claude_timeout_seconds",
        "Timeout chiamata a Claude (secondi)",
        "int",
        600,
        "Oltre questo tempo la chiamata a `claude -p` viene interrotta e conta come fallita.",
        min=30,
        max=3600,
        group="Fase 1 · Claude",
    ),
    SettingDef(
        "claude_extra_args",
        "Argomenti aggiuntivi per `claude -p`",
        "str",
        "--allowedTools Read",
        "Passati dopo `claude -p`. `--allowedTools Read` permette a Claude di leggere le foto.",
        group="Fase 1 · Claude",
    ),
    SettingDef(
        "claude_prompt",
        "Prompt per Claude",
        "text",
        DEFAULT_CLAUDE_PROMPT,
        "Segnaposto disponibili: {foto} (elenco dei file in ordine) e {note} (note del libro).",
        group="Fase 1 · Claude",
    ),
    SettingDef(
        "json_title_key",
        "Etichetta JSON del titolo",
        "str",
        "titolo",
        "Chiave del JSON di Claude che contiene il titolo. Ammessi percorsi (es. libro.titolo).",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_author_key",
        "Etichetta JSON dell'autore",
        "str",
        "autore",
        "Chiave del JSON di Claude che contiene l'autore (se è un elenco, gli autori vengono "
        "separati da virgole). Il nome del libro diventa \"Titolo - Autore\".",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_condition_key",
        "Etichetta JSON delle condizioni",
        "str",
        "condizioni",
        "Chiave del JSON di Claude con le condizioni del libro. Viene usata solo se all'inserimento "
        "è stato scelto \"Usa valore di Claude\", e solo se il valore è esattamente "
        "\"ottime\", \"buone\" o \"discrete\" (maiuscole/minuscole non contano).",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_isbn_key",
        "Etichetta JSON dell'ISBN",
        "str",
        "isbn",
        "Chiave del JSON di Claude con l'ISBN, usato per cercare il libro su Vinted. Trattini e "
        "spazi vengono tolti; se manca o non è un ISBN valido la ricerca viene saltata.",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_price_key",
        "Etichetta JSON del prezzo",
        "str",
        "prezzo",
        "Prezzo proposto da Claude: usato solo per i libri senza ISBN (vedi Fase 2).",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_category_key",
        "Etichetta JSON della categoria",
        "str",
        "categoria",
        "Categoria Vinted della bozza: un nome come narrativa, saggistica, fumetti… (o un id numerico). Se non è riconosciuta la fase 3 fallisce.",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_vinted_condition_key",
        "Etichetta JSON delle condizioni per Vinted",
        "str",
        "condizioni_vinted",
        "Usata per la bozza quando le condizioni non sono state scelte a mano (es. ottime, nuovo senza cartellino). Se non è riconosciuta la fase 3 fallisce.",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_condition_text_key",
        "Etichetta JSON del testo delle condizioni",
        "str",
        "condizioni",
        "Segnaposto {condizioni} della descrizione dell'annuncio: testo libero sullo stato del "
        "libro, letto dal JSON di Claude al momento della bozza. Se manca si usano le condizioni "
        "del libro (ottime, buone, discrete).",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_language_key",
        "Etichetta JSON della lingua",
        "str",
        "lingua",
        "Lingua del libro per Vinted (es. italiano, inglese). Se manca o non è riconosciuta la fase 3 fallisce.",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_publisher_key",
        "Etichetta JSON dell'editore",
        "str",
        "editore",
        "Segnaposto {editore}.",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_year_key",
        "Etichetta JSON dell'anno",
        "str",
        "anno",
        "Segnaposto {anno}.",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_series_key",
        "Etichetta JSON della collana",
        "str",
        "collana",
        "Segnaposto {collana}.",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "json_description_key",
        "Etichetta JSON della descrizione",
        "str",
        "descrizione",
        "Segnaposto {descrizione}.",
        group="Dati dal JSON di Claude",
    ),
    SettingDef(
        "vinted_search_categories",
        "Categorie della ricerca",
        "str",
        "libri",
        "Categorie Vinted in cui cercare, separate da virgole (nomi o id numerici, "
        "come per `vinted search --category`).",
        group="Fase 2 · Prezzi Vinted",
    ),
    SettingDef(
        "vinted_search_conditions",
        "Condizioni degli annunci",
        "str",
        "ottime, buone",
        "Condizioni degli annunci da considerare, separate da virgole (come per "
        "`vinted search --condition`). Vuoto = tutte.",
        group="Fase 2 · Prezzi Vinted",
    ),
    SettingDef(
        "vinted_search_limit",
        "Numero massimo di annunci",
        "int",
        10,
        "Quanti annunci salvare al massimo per ogni libro.",
        min=1,
        max=96,
        group="Fase 2 · Prezzi Vinted",
    ),
    SettingDef(
        "price_min_euro",
        "Prezzo minimo (euro)",
        "int",
        3,
        "Al termine della fase 2, se il prezzo è vuoto, viene calcolato: mediana di Vinted se c'è "
        "l'ISBN, altrimenti prezzo di Claude; arrotondato all'euro e mai sotto questo minimo. "
        "Un prezzo inserito a mano non viene mai modificato.",
        min=0,
        max=10000,
        group="Fase 2 · Prezzi Vinted",
    ),
    SettingDef(
        "vinted_description_template",
        "Descrizione dell'annuncio",
        "text",
        DEFAULT_DESCRIPTION_TEMPLATE,
        "Segnaposto: {titolo} {autore} {editore} {anno} {collana} {lingua} {categoria} "
        "{descrizione} {condizioni} (testo delle condizioni, vedi sopra). Le righe in cui tutti i "
        "segnaposto sono vuoti vengono tolte.",
        group="Fase 3 · Bozza Vinted",
    ),
]

# Attesa minima tra due esecuzioni consecutive della stessa fase (una voce per
# ogni fase automatica, generata dal registro delle fasi).
DEFINITIONS += [
    SettingDef(
        min_gap_key(n),
        f"Fase {n} · {PHASES[n].label}: attesa minima (secondi)",
        "int",
        PHASES[n].min_gap_default,
        "Tempo minimo tra la fine di un'esecuzione di questa fase (riuscita o fallita) e "
        "l'inizio della successiva. Nel frattempo possono girare le altre fasi. 0 = nessuna attesa.",
        min=0,
        max=24 * 3600,
        group="Attese tra esecuzioni",
    )
    for n in ACTIVE_PHASES
]

# Pausa di una fase automatica: i libri che la raggiungono restano "In pausa"
# finché non la si toglie (o finché non si forza il singolo libro).
DEFINITIONS += [
    SettingDef(
        pause_key(n),
        f"Fase {n} · {PHASES[n].label}: in pausa",
        "bool",
        PHASES[n].paused_default,
        "I libri che arrivano a questa fase si fermano. Togliendo la pausa ripartono tutti "
        "(uno alla volta, rispettando l'attesa minima); un singolo libro si può forzare dalla "
        "sua scheda.",
        group="Fasi in pausa",
    )
    for n in ACTIVE_PHASES
]

BY_KEY = {d.key: d for d in DEFINITIONS}

_TRUE = {"true", "1", "on", "yes", "si", "sì"}
_FALSE = {"false", "0", "off", "no", ""}


def _parse(d: SettingDef, raw: Any) -> Any:
    if d.type == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f"{d.label}: valore non valido")
    if d.type == "int":
        value = int(raw)
        if d.min is not None and value < d.min:
            raise ValueError(f"{d.label}: il valore minimo è {d.min}")
        if d.max is not None and value > d.max:
            raise ValueError(f"{d.label}: il valore massimo è {d.max}")
        return value
    return str(raw)


def _store(value: Any) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def paused_phases(settings: dict[str, Any]) -> list[int]:
    return [n for n in ACTIVE_PHASES if settings.get(pause_key(n))]


def seed_defaults(db: Session) -> None:
    existing = {s.key for s in db.query(Setting.key)}
    for d in DEFINITIONS:
        if d.key not in existing:
            db.add(Setting(key=d.key, value=_store(d.default)))
    db.commit()


def get_setting(db: Session, key: str) -> Any:
    d = BY_KEY[key]
    row = db.get(Setting, key)
    if row is None:
        return d.default
    try:
        return _parse(d, row.value)
    except ValueError:
        return d.default


def all_settings(db: Session) -> list[dict]:
    return [
        {
            "key": d.key,
            "label": d.label,
            "type": d.type,
            "help": d.help,
            "min": d.min,
            "max": d.max,
            "group": d.group,
            "default": d.default,
            "value": get_setting(db, d.key),
        }
        for d in DEFINITIONS
    ]


def update_settings(db: Session, values: dict[str, Any]) -> None:
    parsed = {}
    for key, raw in values.items():
        if key not in BY_KEY:
            raise ValueError(f"Impostazione sconosciuta: {key}")
        try:
            parsed[key] = _parse(BY_KEY[key], raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc) or f"Valore non valido per {key}") from exc
    for key, value in parsed.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=_store(value)))
        else:
            row.value = _store(value)
    db.commit()
