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

**Foto.** Il libro 57 ha le sue foto originali in `$BOOKS_DIR/57/01.jpg, 02.jpg, …`
(in ordine di scatto). Le miniature stanno in `$BOOKS_DIR/.thumbs/57/`.

### Fasi e processo in background

| Fase | Nome | Cosa fa il processo |
|---|---|---|
| 0 | Inserito | controlla che le foto ci siano |
| 1 | Analisi Claude | `claude -p` lanciato dentro `$BOOKS_DIR/<id>`, con il prompt (e note) su stdin; l'output va in `books.claude_output` |
| 2 | Descritto | nessuna azione (in attesa delle fasi successive) |

In caso di errore: attesa e nuovo tentativo; dopo N tentativi il libro va in
"fallito" (riprovabile dall'interfaccia). Tutti i parametri e il prompt sono
nella sezione **Impostazioni**. Ogni tentativo è registrato in `phase_runs`.

Il processo gira dentro ogni worker uvicorn; ogni libro viene "preso" con un lock
nel DB (lease con scadenza), quindi più worker non lavorano mai sullo stesso libro
e un worker morto viene sostituito alla scadenza del lease.

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
