#!/usr/bin/env python3
"""
Complaion - ACN Monitor (v5.1)
Scraper con SNAPSHOTS + DIFF + AI SUMMARY + PDF TEXT + ESTRAZIONE SCADENZE.

Novita v5.1 rispetto a v5:
- Seed list di scadenze NIS2 note (30/06/2026, 31/10/2026, finestra 2027, ecc.)
- Filtro automatico delle date passate (sia in estrazione sia in salvataggio)
- Esclusione date in contesti "News -", "Alert -", "Bollettino -", "Articolo -"
- Trigger keywords per scadenze piu stringenti
- Emissione delle variazioni dell'ultimo scan per il notifier Slack
"""

import difflib
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Optional imports gestiti con try/except per non bloccare se non installati
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    print("[warn] pdfplumber non disponibile, salto estrazione testo PDF", file=sys.stderr)

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    print("[warn] google-generativeai non disponibile, salto AI summary", file=sys.stderr)


# =============================================================================
# CONFIG
# =============================================================================

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "docs" / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
DOCS_FILE = DATA_DIR / "documents.json"
CHANGES_FILE = DATA_DIR / "changes.json"
DEADLINES_FILE = DATA_DIR / "scadenze.json"

USER_AGENT = "Mozilla/5.0 (compatible; ComplaionACNMonitor/1.0; +https://github.com/)"
REQUEST_TIMEOUT = 60
RETRY_COUNT = 3
RETRY_DELAY = 5
SLEEP_BETWEEN = 1.5

CHANGES_RETENTION_DAYS = 180
DIFF_MAX_LINES = 200
SNAPSHOT_MAX_CHARS = 200_000

# Gemini config
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_MAX_DIFF_CHARS = 8000  # max diff inviato all'LLM


# =============================================================================
# TARGETS
# =============================================================================

TARGETS = [
    {"id": "acn-portale-nis", "name": "ACN - Portale NIS", "url": "https://www.acn.gov.it/portale/nis", "type": "page", "category": "NIS2"},
    {"id": "acn-portale-nis-faq", "name": "ACN - FAQ NIS", "url": "https://www.acn.gov.it/portale/faq/nis", "type": "page", "category": "NIS2", "expand_children": True},
    {"id": "acn-nis-normativa", "name": "ACN - La normativa", "url": "https://www.acn.gov.it/portale/nis/la-normativa", "type": "page", "category": "NIS2 - Normativa"},
    {"id": "acn-nis-registrazione", "name": "ACN - Registrazione NIS", "url": "https://www.acn.gov.it/portale/nis/registrazione", "type": "page", "category": "NIS2 - Operativo"},
    {"id": "acn-nis-modalita-specifiche", "name": "ACN - Modalita e specifiche di base", "url": "https://www.acn.gov.it/portale/nis/modalita-specifiche-base", "type": "page", "category": "NIS2 - Operativo"},
    {"id": "acn-nis-categorizzazione", "name": "ACN - Categorizzazione", "url": "https://www.acn.gov.it/portale/nis/categorizzazione", "type": "page", "category": "NIS2 - Operativo"},
    {"id": "acn-nis-ambito", "name": "ACN - Ambito NIS", "url": "https://www.acn.gov.it/portale/nis/ambito", "type": "page", "category": "NIS2 - Ambito"},
    {"id": "acn-nis-obblighi", "name": "ACN - Obblighi", "url": "https://www.acn.gov.it/portale/nis/obblighi", "type": "page", "category": "NIS2 - Obblighi"},
    {"id": "acn-nis-aggiornamento", "name": "ACN - Aggiornamento delle informazioni", "url": "https://www.acn.gov.it/portale/nis/aggiornamento-informazioni", "type": "page", "category": "NIS2 - Operativo"},
    {"id": "acn-nis-notizie-eventi", "name": "ACN - Notizie ed eventi NIS", "url": "https://www.acn.gov.it/portale/nis/notizie-ed-eventi", "type": "page", "category": "NIS2 - News"},
]

DISCOVER_PDFS = True
PDF_DISCOVERY_KEYWORDS = ["nis", "categorizzazione", "determinazione", "obblighi", "cybersicurezza", "cyber", "tassonomia", "misure", "piattaforma"]

# Auto-discovery config per hub pages con sotto-sezioni (es. FAQ organizzate per categoria)
CHILD_DISCOVERY = {
    "acn-portale-nis-faq": {
        "child_url_regex": r"^/portale/faq/nis/[^/?#]+/?$",
        "child_category": "NIS2 - FAQ",
        "child_name_prefix": "ACN FAQ",
    },
}


# =============================================================================
# SEED DEADLINES - Scadenze NIS2 note (hardcoded)
# =============================================================================
# Sono sempre presenti finche non scadono. Vengono auto-rimosse dopo la data.

SEED_DEADLINES = [
    {
        "date": "2026-06-30",
        "date_text": "30 giugno 2026",
        "context": "Termine indicativo per la categorizzazione dei soggetti NIS2 registrati nella finestra 2026, sulla base delle determinazioni ACN (cfr. Determinazione 260409/2024 sulla Categorizzazione).",
        "source_id": "seed-cat-2026",
        "source_name": "Scadenza NIS2 - Categorizzazione 2026",
        "source_url": "https://www.acn.gov.it/portale/nis/categorizzazione",
    },
    {
        "date": "2026-10-31",
        "date_text": "31 ottobre 2026",
        "context": "Termine per l'attuazione delle misure di sicurezza base ai sensi della Determinazione ACN n. 164179/2025 per i soggetti NIS2 registrati nel 2026.",
        "source_id": "seed-misure-2026",
        "source_name": "Scadenza NIS2 - Misure di sicurezza base",
        "source_url": "https://www.acn.gov.it/portale/nis/modalita-specifiche-base",
    },
    {
        "date": "2027-01-01",
        "date_text": "1 gennaio 2027",
        "context": "Apertura della finestra annuale di registrazione NIS2 per il 2027 (1 gennaio - 28 febbraio). I soggetti che ricadono nell'ambito devono procedere alla registrazione sul portale ACN.",
        "source_id": "seed-reg-2027-apertura",
        "source_name": "Scadenza NIS2 - Apertura registrazione 2027",
        "source_url": "https://www.acn.gov.it/portale/nis/registrazione",
    },
    {
        "date": "2027-02-28",
        "date_text": "28 febbraio 2027",
        "context": "Chiusura della finestra annuale di registrazione NIS2 per il 2027 (art. 7 D.Lgs. 138/2024). Termine ultimo per la prima registrazione o per l'aggiornamento dei dati anagrafici dei soggetti gia registrati.",
        "source_id": "seed-reg-2027-chiusura",
        "source_name": "Scadenza NIS2 - Chiusura registrazione 2027",
        "source_url": "https://www.acn.gov.it/portale/nis/registrazione",
    },
    {
        "date": "2027-04-15",
        "date_text": "15 aprile 2027",
        "context": "Apertura della finestra annuale di aggiornamento delle informazioni NIS2 (15 aprile - 31 maggio). I soggetti devono verificare e aggiornare i dati comunicati durante la registrazione.",
        "source_id": "seed-agg-2027-apertura",
        "source_name": "Scadenza NIS2 - Apertura aggiornamento informazioni 2027",
        "source_url": "https://www.acn.gov.it/portale/nis/aggiornamento-informazioni",
    },
    {
        "date": "2027-04-17",
        "date_text": "17 aprile 2027",
        "context": "Termine entro il quale ACN aggiorna e pubblica l'elenco dei soggetti essenziali e importanti ai sensi dell'art. 7, comma 4, D.Lgs. 138/2024.",
        "source_id": "seed-elenco-2027",
        "source_name": "Scadenza NIS2 - Aggiornamento elenco ACN 2027",
        "source_url": "https://www.acn.gov.it/portale/nis/la-normativa",
    },
    {
        "date": "2027-05-31",
        "date_text": "31 maggio 2027",
        "context": "Chiusura della finestra annuale di aggiornamento delle informazioni NIS2. Termine ultimo per la verifica/aggiornamento dei dati e per la designazione del sostituto del punto di contatto.",
        "source_id": "seed-agg-2027-chiusura",
        "source_name": "Scadenza NIS2 - Chiusura aggiornamento informazioni 2027",
        "source_url": "https://www.acn.gov.it/portale/nis/aggiornamento-informazioni",
    },
]


# =============================================================================
# UTILITY GENERALI
# =============================================================================

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def fetch(url):
    last_err = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            return r.content, r.status_code, r.headers.get("Content-Type", "").split(";")[0].strip()
        except (requests.RequestException, OSError) as e:
            last_err = e
            print(f"  [warn] tentativo {attempt}/{RETRY_COUNT} fallito: {e}", file=sys.stderr)
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
    raise RuntimeError(f"Fetch fallito: {last_err}")

def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()

def safe_load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =============================================================================
# NORMALIZE HTML / PDF
# =============================================================================

def normalize_html(html_bytes):
    try:
        soup = BeautifulSoup(html_bytes, "html.parser")
    except Exception:
        return html_bytes.decode("utf-8", errors="ignore")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    for meta in soup.find_all("meta"):
        if meta.get("name", "").lower() in ("csrf-token", "csrf-param", "generator", "build-date"):
            meta.decompose()
    from bs4 import Comment
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    text = soup.get_text(separator="\n")
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()

def extract_pdf_text(pdf_bytes):
    """Estrai testo da un PDF usando pdfplumber. Restituisce stringa vuota se errore."""
    if not PDFPLUMBER_AVAILABLE:
        return ""
    try:
        text_parts = []
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
        text = "\n".join(text_parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except Exception as e:
        print(f"  [warn] errore estrazione PDF: {e}", file=sys.stderr)
        return ""

def extract_pdf_links(html_bytes, base_url):
    try:
        soup = BeautifulSoup(html_bytes, "html.parser")
    except Exception:
        return []
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.lower().endswith(".pdf"):
            continue
        absolute = urljoin(base_url, href)
        if "acn.gov.it" not in urlparse(absolute).netloc.lower():
            continue
        text = a.get_text(separator=" ", strip=True) or os.path.basename(urlparse(absolute).path)
        if any(k in (text + " " + href).lower() for k in PDF_DISCOVERY_KEYWORDS):
            found.append({"name": text[:200], "url": absolute})
    seen, out = set(), []
    for f in found:
        if f["url"] not in seen:
            seen.add(f["url"])
            out.append(f)
    return out


# =============================================================================
# AUTO-DISCOVERY DEI SUB-TARGET (hub pages)
# =============================================================================

def _slug_from_url(url):
    """Ritorna un slug id-friendly derivato dall'ultimo segmento del path."""
    try:
        parts = [p for p in urlparse(url).path.split("/") if p]
        return parts[-1] if parts else "root"
    except Exception:
        return "unknown"


def discover_child_targets(hub_target, html_bytes):
    """
    Estrae dai link interni della pagina hub i sub-target da monitorare
    autonomamente (es. sotto-sezioni FAQ). Ritorna una lista di dict target
    compatibili con TARGETS.

    Configurato tramite CHILD_DISCOVERY[hub_id] con:
      - child_url_regex   : pattern sul path della URL child
      - child_category    : categoria da assegnare ai child
      - child_name_prefix : prefisso per il name (poi seguito dal testo del link)
    """
    config = CHILD_DISCOVERY.get(hub_target.get("id"))
    if not config:
        return []
    try:
        soup = BeautifulSoup(html_bytes, "html.parser")
    except Exception:
        return []
    hub_url = hub_target["url"]
    hub_parsed = urlparse(hub_url)
    hub_canonical = hub_url.split("#")[0].split("?")[0].rstrip("/")
    pattern = re.compile(config["child_url_regex"])
    prefix = config.get("child_name_prefix", "FAQ")
    category = config.get("child_category", hub_target.get("category", ""))

    children = []
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        absolute = urljoin(hub_url, href)
        parsed = urlparse(absolute)
        # Solo stesso dominio
        if parsed.netloc.lower() != hub_parsed.netloc.lower():
            continue
        # Deve matchare il pattern del path
        if not pattern.match(parsed.path):
            continue
        # Rimuovi fragment/query per la canonical
        canonical = absolute.split("#")[0].split("?")[0]
        canonical_stripped = canonical.rstrip("/")
        # Evita la hub stessa
        if canonical_stripped == hub_canonical:
            continue
        if canonical_stripped in seen_urls:
            continue
        seen_urls.add(canonical_stripped)

        slug = _slug_from_url(canonical_stripped)
        text = a.get_text(separator=" ", strip=True) or slug.replace("-", " ").title()
        child_id = f"{hub_target['id']}-{re.sub(r'[^a-zA-Z0-9_-]', '-', slug)[:60]}"
        # Nome breve: prefix + testo del link (tagliato)
        child_name = f"{prefix} - {text[:80]}"
        children.append({
            "id": child_id,
            "name": child_name,
            "url": canonical,
            "type": "page",
            "category": category,
            "parent_id": hub_target["id"],
            "auto_discovered": True,
        })
    return children


# =============================================================================
# SNAPSHOTS
# =============================================================================

def snapshot_path(item_id):
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", item_id)
    return SNAPSHOTS_DIR / f"{safe}.txt"

def load_snapshot(item_id):
    p = snapshot_path(item_id)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""

def save_snapshot(item_id, text):
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path(item_id).write_text(text[:SNAPSHOT_MAX_CHARS], encoding="utf-8")


# =============================================================================
# DIFF
# =============================================================================

def compute_diff(old_text, new_text, max_lines=DIFF_MAX_LINES):
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))
    body = [l for l in diff if not (l.startswith("---") or l.startswith("+++") or l.startswith("@@"))]
    added = removed = 0
    lines = []
    for l in body[:max_lines]:
        if l.startswith("+"):
            lines.append({"op": "+", "text": l[1:]}); added += 1
        elif l.startswith("-"):
            lines.append({"op": "-", "text": l[1:]}); removed += 1
        else:
            lines.append({"op": " ", "text": l[1:] if l.startswith(" ") else l})
    truncated = len(body) > max_lines
    summary = f"+{added} aggiunte, -{removed} rimosse"
    if truncated:
        summary += f" (diff troncato a {max_lines} righe)"
    return {"added": added, "removed": removed, "summary": summary, "truncated": truncated, "lines": lines}


# =============================================================================
# AI SUMMARY (GEMINI)
# =============================================================================

def ai_summarize(resource_name, diff_data, resource_type="page"):
    """Chiama Gemini per riassumere il diff. Restituisce None se non disponibile/fallisce."""
    if not GENAI_AVAILABLE or not GEMINI_API_KEY:
        return None
    if not diff_data or not diff_data.get("lines"):
        return None
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)

        diff_text_parts = []
        for line in diff_data["lines"]:
            op = line.get("op", " ")
            if op in ("+", "-"):
                diff_text_parts.append(f"{op}{line.get('text', '')}")
        diff_text = "\n".join(diff_text_parts)[:GEMINI_MAX_DIFF_CHARS]

        type_label = "PDF" if resource_type == "pdf" else "pagina web"
        prompt = f"""Sei un consulente esperto di compliance NIS2 italiana. Analizza questo diff rilevato su una {type_label} ufficiale dell'ACN (Agenzia per la Cybersicurezza Nazionale), risorsa: "{resource_name}".

Statistiche: {diff_data.get('summary', '')}

Diff (righe con + sono state AGGIUNTE, righe con - sono state RIMOSSE):
