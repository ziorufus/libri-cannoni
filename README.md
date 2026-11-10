# Mettete dei libri nei vostri cannoni

Sistema per mettere online i libri da cedere in cambio di un'offerta per
[Emergency](https://www.emergency.it). Il nome si rifà a *Mettete dei fiori nei
vostri cannoni* (I Giganti, 1967).

```
backend/   Python · FastAPI · SQLAlchemy · SQLite (+ processo in background)
frontend/  Vue 3 · Vite · Bootstrap 5
```

## Backend

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # e compilalo
.venv/bin/uvicorn app.main:app --port 8000            # sviluppo
.venv/bin/uvicorn app.main:app --port 8000 --workers 4  # più worker: supportato
```

All'avvio vengono creati il DB, l'amministratore `ADMIN_EMAIL` e le impostazioni
predefinite. Variabili d'ambiente: vedi [backend/.env.example](backend/.env.example).

**Login.** Le credenziali OAuth si creano su Google Cloud Console e su Microsoft
Entra (App registrations, account "any organization + personal"). Gli URI di
redirect sono `$BACKEND_URL/api/auth/callback/google` e
`$BACKEND_URL/api/auth/callback/microsoft`. Un provider senza ID e secret non
compare nella pagina di login.

**Vinted.** La fase 2 usa il pacchetto
[vinted-lister](https://github.com/ziorufus/vinted-lister), installato nel
virtualenv del backend dal suo clone, che deve restare al suo posto
(`config/` e `data/` vengono letti da lì):

```bash
.venv/bin/pip install -e /percorso/vinted-lister
export VINTED_ENV_FILE=/percorso/vinted.env   # es. nel ~/.bashrc
.venv/bin/vinted auth import --file req.txt   # "Copy as cURL" di una richiesta vinted.it
.venv/bin/vinted whoami
```

Lo stesso percorso va in `VINTED_ENV_FILE` del `backend/.env`. Il file deve
contenere `VINTED_DOMAIN=it`; l'utente di uvicorn deve poter scrivere nel file e
nella sua cartella (i token vengono rinnovati in automatico). Se la sessione
scade, rifare `vinted auth import` e premere "Riprova" sui libri falliti: non
serve riavviare il backend.

**Foto.** Il libro 57 ha le sue foto originali in `$BOOKS_DIR/57/01.jpg, 02.jpg, …`
(in ordine di scatto). Le miniature stanno in `$BOOKS_DIR/.thumbs/57/`.

### Fasi e processo in background

| Fase | Nome | Cosa fa il processo |
|---|---|---|
| 0 | Inserito | controlla che le foto ci siano |
| 1 | Analisi Claude | `claude -p` lanciato dentro `$BOOKS_DIR/<id>`, con il prompt (e note) su stdin; l'output va in `books.claude_output` |
| 2 | Prezzi Vinted | cerca su Vinted l'ISBN del JSON di Claude (categoria libri, condizioni ottime/buone, max 10 annunci) e salva prezzi e annunci in `books.vinted_prices`; senza ISBN o senza risultati la fase si considera fatta |
| 3 | Prezzi trovati | nessuna azione (in attesa delle fasi successive) |

In caso di errore: attesa e nuovo tentativo; dopo N tentativi il libro va in
"fallito" (riprovabile dall'interfaccia). Tutti i parametri e il prompt sono
nella sezione **Impostazioni**. Ogni tentativo è registrato in `phase_runs`.

Il processo gira dentro ogni worker uvicorn, ma lavora solo il "leader" (lock su
`data/.worker.lock`); in più ogni libro viene "preso" con un lease nel DB, quindi
più worker non lavorano mai sullo stesso libro.

Per aggiungere una fase: scrivere un handler in [backend/app/phases.py](backend/app/phases.py)
e registrarlo in `PHASES`.

## Frontend

```bash
cd frontend
npm install
cp .env.example .env        # VITE_API_URL=indirizzo del backend
npm run dev                 # sviluppo, http://localhost:5173
npm run build               # produzione → dist/
```

`dist/` è statico: va servito con fallback su `index.html` (routing in history mode),
es. nginx `try_files $uri /index.html;`.

**La fotocamera (`getUserMedia`) funziona solo in HTTPS o su `localhost`.** Per
provare dal telefono in rete locale serve HTTPS (es. un tunnel); in alternativa
c'è il pulsante "Da file".
