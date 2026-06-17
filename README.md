# Complaion — ACN Monitor

> Monitoraggio automatico degli aggiornamenti NIS2 pubblicati dall'**Agenzia per la Cybersicurezza Nazionale (ACN)**. Tutto gratuito, su infrastruttura GitHub (Pages + Actions).

---

## Cosa fa

- 🔍 **Scansiona** automaticamente le pagine ufficiali ACN e i PDF correlati alla NIS2.
- 🔐 **Calcola un hash SHA256** di ciascuna risorsa per rilevare variazioni anche minime.
- 📊 **Mostra in dashboard pubblica** lo stato di ogni risorsa (invariata 🟢, modificata 🟡, nuova 🔵, errori 🔴).
- 📜 **Mantiene uno storico** degli ultimi 180 giorni di variazioni.
- ⏱ **Scansione ogni ora** tramite GitHub Actions schedulato.
- 🤖 **Scoperta automatica di nuovi PDF** linkati nelle pagine monitorate (filtrati per keyword NIS2).

---

## Architettura

```
complaion-acn-monitor/
├── .github/workflows/
│   └── monitor.yml             # GitHub Actions: cron job ogni ora
├── scripts/
│   ├── scrape_acn.py            # Scraper Python (cuore del progetto)
│   └── requirements.txt         # Dipendenze Python
├── data/
│   ├── documents.json           # Stato corrente delle risorse tracciate
│   └── changes.json             # Storico delle variazioni (ultimi 180 gg)
├── docs/                        # GitHub Pages (la dashboard pubblica)
│   ├── index.html
│   ├── style.css
│   └── app.js
├── .gitignore
└── README.md
```

**Flusso operativo:**

1. GitHub Actions esegue `scrape_acn.py` ogni ora.
2. Lo scraper fetcha le URL ACN configurate, calcola hash, confronta con stato precedente.
3. Aggiorna `data/documents.json` (stato corrente) e `data/changes.json` (storico variazioni).
4. Commit & push automatico delle modifiche.
5. GitHub Pages serve la dashboard `docs/index.html` che legge i JSON e mostra tutto.

---

## Setup iniziale (15 minuti)

### Step 1 — Crea il repository GitHub

1. Vai su [github.com](https://github.com) → **New repository**
2. Nome: `complaion-acn-monitor` (o quello che preferisci)
3. Visibilità: **Public** (necessario per il free tier di GitHub Actions e Pages)
4. **Non** aggiungere README/license/gitignore (sono già nel pacchetto)
5. Crea il repository

### Step 2 — Carica il contenuto del pacchetto

Da terminale, nella cartella del progetto:

```bash
cd complaion-acn-monitor
git init
git branch -M main
git add .
git commit -m "Initial commit — Complaion ACN Monitor scaffold"
git remote add origin https://github.com/<TUO_USERNAME>/complaion-acn-monitor.git
git push -u origin main
```

In alternativa via GitHub Desktop o caricamento manuale via web UI.

### Step 3 — Abilita GitHub Pages

1. Vai su **Settings** → **Pages** del repository
2. Source: `Deploy from a branch`
3. Branch: `main` → Folder: `/docs`
4. Salva
5. Dopo 1-2 minuti la dashboard sarà disponibile su:
   `https://<TUO_USERNAME>.github.io/complaion-acn-monitor/`

### Step 4 — Abilita GitHub Actions

1. Vai su **Settings** → **Actions** → **General**
2. **Workflow permissions**: seleziona "Read and write permissions" (consente allo workflow di committare i dati aggiornati)
3. Salva

### Step 5 — Lancia la prima scansione manuale

1. Vai sul tab **Actions** del repository
2. Clicca sul workflow "ACN NIS2 Monitor"
3. Clicca **Run workflow** → **Run workflow**
4. Aspetta ~30-60 secondi che parta e completi
5. Verifica che `data/documents.json` sia stato popolato
6. Visita la dashboard: dovresti vedere i dati

Da quel momento, lo scan partirà automaticamente ogni ora.

---

## Personalizzazione

### Aggiungere/rimuovere URL da monitorare

Apri `scripts/scrape_acn.py` e modifica la lista `TARGETS`:

```python
TARGETS = [
    {
        "id": "acn-portale-nis",
        "name": "ACN — Portale NIS",
        "url": "https://www.acn.gov.it/portale/nis",
        "type": "page",
        "category": "NIS2",
    },
    # Aggiungi altri target qui...
]
```

I campi:
- `id` — identificatore univoco (usato per tracciare la risorsa nel tempo)
- `name` — nome leggibile mostrato in dashboard
- `url` — URL completa da monitorare
- `type` — `page` o `pdf`
- `category` — etichetta per il filtro categoria

### Modificare la frequenza di scan

In `.github/workflows/monitor.yml`, modifica la riga `cron`:

```yaml
on:
  schedule:
    - cron: '7 * * * *'    # Ogni ora al minuto 7 (default)
    # - cron: '*/30 * * * *' # Ogni 30 minuti
    # - cron: '0 */6 * * *'  # Ogni 6 ore
    # - cron: '0 9 * * *'    # Una volta al giorno alle 9 UTC
```

### Modificare la retention dello storico variazioni

In `scripts/scrape_acn.py`:

```python
CHANGES_RETENTION_DAYS = 180   # Mantieni eventi negli ultimi 180 giorni
```

### Modificare le keyword per la scoperta automatica di PDF

In `scripts/scrape_acn.py`:

```python
PDF_DISCOVERY_KEYWORDS = ["nis", "categorizzazione", "determinazione", ...]
```

---

## Esecuzione locale (per testare prima del deploy)

```bash
cd complaion-acn-monitor
python -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows
pip install -r scripts/requirements.txt
python scripts/scrape_acn.py
```

Genera `data/documents.json` e `data/changes.json`. Apri `docs/index.html` con un server locale (es. `python -m http.server 8000 --directory docs` e poi http://localhost:8000) per vedere la dashboard.

---

## Costi (free tier — repo pubblico)

| Risorsa | Limite free tier | Nostro utilizzo previsto |
|---|---|---|
| **GitHub Actions** | 2.000 min/mese per repo pubblici | ~360-720 min/mese (24 scan/giorno × 30 gg) |
| **GitHub Pages** | 100 GB banda/mese, 10 build/h | Trascurabile |
| **Storage repo** | 1 GB | Pochi MB di JSON |

**Tutto gratuito** purché il repo sia pubblico. Per repo privato Actions costa ($0.008/min su Linux runner).

---

## Disclaimer

Questo strumento è **non ufficiale**. Sebbene progettato per monitorare costantemente le pagine pubbliche dell'ACN, non è possibile garantire l'accuratezza, la completezza o la tempestività delle informazioni. Per ogni adempimento normativo o tecnico fare sempre riferimento ai canali ufficiali dell'**Agenzia per la Cybersicurezza Nazionale** ([www.acn.gov.it](https://www.acn.gov.it)).

---

## Troubleshooting

**La GitHub Action fallisce con "Permission denied" al push**
→ Vai su Settings → Actions → General → Workflow permissions → seleziona "Read and write permissions".

**La dashboard mostra "Caricamento…" e non si aggiorna**
→ Controlla con DevTools (F12 → tab Network) che i file `documents.json` e `changes.json` vengano caricati. Se errore 404, verifica che siano presenti nella cartella `data/` del repository.

**Voglio aggiungere notifiche Telegram più avanti**
→ Crea un bot Telegram con BotFather, salva il token come secret del repository (Settings → Secrets → Actions), aggiungi uno step al workflow che chiama l'API Telegram con `curl` quando ci sono variazioni. Posso aiutare con questo step se serve.

**Voglio integrare AI summary delle modifiche**
→ Aggiungi una API key (Claude/OpenAI/Gemini) come secret, e modifica lo scraper per fare un diff testuale tra versione vecchia e nuova quando rileva un cambio, passando il diff all'LLM per generare un riassunto. Si può aggiungere come miglioria futura.

---

## Roadmap (futura)

- [ ] Notifiche Telegram via bot
- [ ] AI summary dei diff (Claude/Gemini)
- [ ] Estrazione testo PDF + diff testuale (oltre all'hash)
- [ ] RSS/Atom feed degli aggiornamenti
- [ ] Esportazione storico in CSV per analisi periodica
- [ ] Multi-source: aggiungere monitoraggio anche di EU NIS Cooperation Group, ENISA, Garante Privacy

---

**Sviluppato da Complaion** · Per dubbi o suggerimenti: apri una issue su GitHub.
