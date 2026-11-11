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
scade, reimportare i token dalla pagina **Admin → Vinted** (incollando il "Copy as
cURL", equivale a `vinted auth import`) oppure da terminale, poi premere "Riprova"
sui libri falliti: non serve riavviare il backend.

**Foto.** Il libro 57 ha le sue foto originali in `$BOOKS_DIR/57/01.jpg, 02.jpg, …`
(in ordine di scatto). Le miniature stanno in `$BOOKS_DIR/.thumbs/57/`.

### Fasi e processo in background

| Fase | Nome | Cosa fa il processo |
|---|---|---|
| 0 | Inserito | controlla che le foto ci siano |
| 1 | Analisi Claude | `claude -p` lanciato dentro `$BOOKS_DIR/<id>`, con il prompt (e note) su stdin; l'output va in `books.claude_output` |
| 2 | Prezzi Vinted | cerca su Vinted l'ISBN del JSON di Claude (categoria libri, condizioni ottime/buone, max 10 annunci) e salva prezzi e annunci in `books.vinted_prices`; senza ISBN o senza risultati la fase si considera fatta. Se il prezzo è vuoto lo calcola: mediana di Vinted (con ISBN) o prezzo di Claude (senza ISBN), arrotondato all'euro, mai sotto il minimo |
| 3 | Bozza Vinted | crea la bozza su Vinted (equivalente di `vinted draft ... --no-move`) e la salva in `books.vinted_draft`. Richiede il prezzo; **in pausa** per impostazione predefinita |
| 4 | Pubblicazione | chiede a Vinted di pubblicare la bozza (equivalente di `vinted publish ID`). **In pausa** per impostazione predefinita |
| 5 | Approvazione Vinted | ogni N minuti (impostazione, default 2) legge l'annuncio come `vinted item ID`: il 404 vuol dire "ancora in approvazione". Se Vinted lo pubblica si passa alla fase 6; se lo riporta in bozza il libro torna **fermo** alla fase 4 (dopo eventuali correzioni sul sito: "Esegui la fase"). Dopo il numero massimo di controlli (default 20) il libro va in "Fallito": "Riprova" ricomincia i controlli senza ripubblicare |
| 6 | Pubblicato | nessuna azione |

In caso di errore: attesa e nuovo tentativo; dopo N tentativi il libro va in
"fallito" (riprovabile dall'interfaccia). Gli errori nei dati (es. categoria non
riconosciuta) portano subito a "fallito": si corregge e si preme "Riprova".

Prima di eseguire una fase:
- **requisiti** (es. il prezzo per la fase 3): se mancano il libro è "Bloccato" e
  riparte da solo quando lo si modifica; non si può forzare;
- **pausa** (impostazione per ogni fase): i libri restano "In pausa" finché la
  pausa non viene tolta; un singolo libro si forza con "Esegui comunque".

Tutti i parametri e il prompt sono
nella sezione **Impostazioni**. Ogni tentativo è registrato in `phase_runs`.

Il processo gira dentro ogni worker uvicorn, ma lavora solo il "leader" (lock su
`data/.worker.lock`); in più ogni libro viene "preso" con un lease nel DB, quindi
più worker non lavorano mai sullo stesso libro.

Per aggiungere una fase: scrivere un handler in [backend/app/phases.py](backend/app/phases.py)
e registrarlo in `PHASES`.

### Job periodici

Il leader esegue anche dei job periodici ([backend/app/jobs.py](backend/app/jobs.py)), in un
task separato dalle fasi. Ognuno si attiva/disattiva e ha il suo intervallo in
**Impostazioni → Job periodici**; esito e "Esegui ora" nella pagina **Admin → Job**
(lo stato è nella tabella `job_states`, creata all'avvio).

| Job | Default | Cosa fa |
|---|---|---|
| Sessione Vinted | ogni 50 min (5 min se non valida) | `vinted auth status`, che rinnova anche i token. Se la sessione non è valida mette in **pausa automatica** le fasi 2–5; ripartono da sole quando torna valida (anche subito dopo un import da Admin → Vinted) |
| Uso di Claude | ogni 5 min | `claude -p /usage`: percentuali di sessione e settimana, mostrate nella barra in alto. Se una delle due supera la soglia (default 80%) mette in pausa automatica la fase 1; riparte quando entrambe sono sotto i margini (default 70%) |

Le pause automatiche si sommano a quelle manuali: una fase messa in pausa a mano
resta in pausa anche quando la condizione del job rientra.

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
