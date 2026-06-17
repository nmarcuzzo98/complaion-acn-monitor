#!/usr/bin/env python3
"""
Complaion - ACN Monitor
Scraper che monitora il sito dell'Agenzia per la Cybersicurezza Nazionale (ACN)
per rilevare aggiornamenti su NIS2 (pagine HTML + documenti PDF).

Architettura:
- TARGETS: lista delle risorse da monitorare (pagine + PDF)
- Per ogni target: fetch HTTP -> hash SHA256 -> confronto con stato precedente
- Output: data/documents.json (stato corrente) + data/changes.json (storico variazioni)

Eseguito da GitHub Actions, idempotente.
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# =============================================================================
# CONFIG
# =============================================================================

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "docs" / "data"
DOCS_FILE = DATA_DIR / "documents.json"
CHANGES_FILE = DATA_DIR / "changes.json"

USER_AGENT = "Mozilla/5.0 (compatible; ComplaionACNMonitor/1.0; +https://github.com/)"
REQUEST_TIMEOUT = 60
RETRY_COUNT = 3
RETRY_DELAY = 5  # secondi tra retry
SLEEP_BETWEEN = 1.5  # politico verso ACN - 1.5s tra una richiesta e l'altra

# Mantieni storico cambiamenti negli ultimi N giorni (per non far esplodere il JSON)
CHANGES_RETENTION_DAYS = 180

# =============================================================================
# TARGETS — URL da monitorare
# =============================================================================
# NOTA: questi URL sono indicativi e vanno verificati e adattati alla struttura
# attuale del sito ACN. Aggiungi/rimuovi target secondo necessità.

TARGETS = [
    # Pagine verificate funzionanti (status 200)
    {
        "id": "acn-home",
        "name": "ACN - Home",
        "url": "https://www.acn.gov.it/",
        "type": "page",
        "category": "Home",
    },
    {
        "id": "acn-portale-nis",
        "name": "ACN - Portale NIS",
        "url": "https://www.acn.gov.it/portale/nis",
        "type": "page",
        "category": "NIS2",
    },
    {
        "id": "acn-portale-nis-faq",
        "name": "ACN - FAQ NIS",
        "url": "https://www.acn.gov.it/portale/nis/faq",
        "type": "page",
        "category": "NIS2",
    },
    # Da TESTARE: se danno 404, rimuovili dopo il primo scan
    {
        "id": "acn-portale-nis-normativa",
        "name": "ACN - Normativa NIS",
        "url": "https://www.acn.gov.it/portale/nis/normativa",
        "type": "page",
        "category": "NIS2",
    },
    {
        "id": "acn-portale-news",
        "name": "ACN - News",
        "url": "https://www.acn.gov.it/portale/news",
        "type": "page",
        "category": "News",
    },
]

# Scoperta dinamica di nuovi PDF nelle pagine monitorate.
# Quando True, lo scraper estrae link a documenti PDF e li aggiunge ai target.
DISCOVER_PDFS = True
PDF_DISCOVERY_KEYWORDS = ["nis", "categorizzazione", "determinazione", "obblighi", "cybersicurezza", "cyber", "tassonomia", "misure", "piattaforma"]

# =============================================================================
# UTILITY
# =============================================================================

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def fetch(url: str) -> tuple[bytes, int, str]:
    """Fetch URL con retry. Restituisce (content, status_code, content_type)."""
    last_err = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            content_type = r.headers.get("Content-Type", "").split(";")[0].strip()
            return r.content, r.status_code, content_type
        except (requests.RequestException, OSError) as e:
            last_err = e
            print(f"  [warn] tentativo {attempt}/{RETRY_COUNT} fallito per {url}: {e}", file=sys.stderr)
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
    raise RuntimeError(f"Fetch fallito dopo {RETRY_COUNT} tentativi per {url}: {last_err}")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_html(html_bytes: bytes) -> bytes:
    """
    Normalizza l'HTML per stabilizzare l'hash:
    - rimuove script/style dinamici
    - rimuove meta tag con date/timestamp
    - normalizza whitespace
    Riduce i 'falsi positivi' tipici dei siti con elementi dinamici (es. analytics, csrf token).
    """
    try:
        soup = BeautifulSoup(html_bytes, "html.parser")
    except Exception:
        return html_bytes

    # Rimuovi elementi tipicamente dinamici
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    # Rimuovi meta tag con valori volatili
    for meta in soup.find_all("meta"):
        if meta.get("name", "").lower() in ("csrf-token", "csrf-param", "generator", "build-date"):
            meta.decompose()

    # Rimuovi commenti
    from bs4 import Comment
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()

    text = soup.get_text(separator="\n")
    # Normalizza whitespace
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip().encode("utf-8")


def extract_pdf_links(html_bytes: bytes, base_url: str) -> list[dict]:
    """Estrae link a PDF dalla pagina, filtrati per keyword di interesse."""
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
        # Filtra solo PDF del dominio ACN
        host = urlparse(absolute).netloc.lower()
        if "acn.gov.it" not in host:
            continue
        text = a.get_text(separator=" ", strip=True) or os.path.basename(urlparse(absolute).path)
        lower = (text + " " + href).lower()
        if any(k in lower for k in PDF_DISCOVERY_KEYWORDS):
            found.append({"name": text[:200], "url": absolute})
    # Dedupe by URL
    seen = set()
    out = []
    for f in found:
        if f["url"] not in seen:
            seen.add(f["url"])
            out.append(f)
    return out


def safe_load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [warn] errore lettura {path}: {e}", file=sys.stderr)
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def prune_changes(changes: list[dict]) -> list[dict]:
    cutoff = datetime.now(timezone.utc).timestamp() - CHANGES_RETENTION_DAYS * 86400
    out = []
    for c in changes:
        try:
            ts = datetime.fromisoformat(c["timestamp"]).timestamp()
            if ts >= cutoff:
                out.append(c)
        except Exception:
            out.append(c)
    return out


# =============================================================================
# MAIN SCAN
# =============================================================================

def scan() -> tuple[dict, list[dict]]:
    """Esegue una scansione completa. Restituisce (documents_state, new_changes)."""
    previous_docs = safe_load_json(DOCS_FILE, default={"items": [], "last_scan": None})
    previous_index = {item["id"]: item for item in previous_docs.get("items", [])}

    current_items = []
    new_changes = []
    discovered_pdfs = []

    # 1) Scansiona i target pre-configurati
    for target in TARGETS:
        print(f"[scan] {target['name']} ({target['url']})")
        try:
            content, status, ctype = fetch(target["url"])
        except RuntimeError as e:
            print(f"  [error] {e}", file=sys.stderr)
            # Conserva il record precedente con stato di errore
            prev = previous_index.get(target["id"])
            if prev:
                prev = dict(prev)
                prev["last_status"] = "fetch_error"
                prev["last_check"] = utc_now_iso()
                current_items.append(prev)
            continue

        if status >= 400:
            print(f"  [warn] status HTTP {status} per {target['url']}")
            prev = previous_index.get(target["id"])
            if prev:
                prev = dict(prev)
                prev["last_status"] = f"http_{status}"
                prev["last_check"] = utc_now_iso()
                current_items.append(prev)
            continue

        # Hash: per HTML usiamo la versione normalizzata, per PDF il bytes raw
        if target["type"] == "pdf":
            content_hash = sha256_hex(content)
        else:
            content_hash = sha256_hex(normalize_html(content))

        prev = previous_index.get(target["id"])
        status_label = "new"
        if prev:
            if prev.get("hash") == content_hash:
                status_label = "unchanged"
            else:
                status_label = "changed"

        item = {
            "id": target["id"],
            "name": target["name"],
            "url": target["url"],
            "type": target["type"],
            "category": target.get("category", ""),
            "hash": content_hash,
            "size": len(content),
            "content_type": ctype,
            "first_seen": prev.get("first_seen") if prev else utc_now_iso(),
            "last_check": utc_now_iso(),
            "last_modified": (
                utc_now_iso() if status_label in ("new", "changed")
                else prev.get("last_modified") if prev else utc_now_iso()
            ),
            "last_status": status_label,
        }
        current_items.append(item)

        if status_label in ("new", "changed"):
            new_changes.append({
                "timestamp": utc_now_iso(),
                "id": item["id"],
                "name": item["name"],
                "url": item["url"],
                "type": item["type"],
                "status": status_label,
                "previous_hash": prev.get("hash") if prev else None,
                "new_hash": content_hash,
            })
            print(f"  [{status_label.upper()}] hash variato")

        # Scoperta dinamica di PDF
        if DISCOVER_PDFS and target["type"] == "page" and ctype.startswith("text/html"):
            pdfs = extract_pdf_links(content, target["url"])
            for pdf in pdfs:
                discovered_pdfs.append({
                    "id": "pdf-" + sha256_hex(pdf["url"].encode())[:12],
                    "name": pdf["name"] or "Documento PDF",
                    "url": pdf["url"],
                    "type": "pdf",
                    "category": "Documento PDF",
                })

        time.sleep(SLEEP_BETWEEN)

    # 2) Scansiona PDF scoperti che non sono ancora nel tracking
    tracked_urls = {it["url"] for it in current_items}
    pdfs_to_scan = []
    seen_pdf_ids = set()
    for pdf in discovered_pdfs:
        if pdf["url"] in tracked_urls:
            continue
        if pdf["id"] in seen_pdf_ids:
            continue
        seen_pdf_ids.add(pdf["id"])
        pdfs_to_scan.append(pdf)

    print(f"\n[discovery] PDF candidati scoperti: {len(pdfs_to_scan)}")
    for pdf in pdfs_to_scan:
        print(f"[scan-pdf] {pdf['name']} ({pdf['url']})")
        try:
            content, status, ctype = fetch(pdf["url"])
        except RuntimeError as e:
            print(f"  [error] {e}", file=sys.stderr)
            continue
        if status >= 400:
            print(f"  [warn] status HTTP {status}")
            continue
        if not ctype.lower().startswith("application/pdf"):
            print(f"  [skip] non è un PDF (content-type: {ctype})")
            continue

        content_hash = sha256_hex(content)
        prev = previous_index.get(pdf["id"])
        status_label = "new"
        if prev:
            status_label = "unchanged" if prev.get("hash") == content_hash else "changed"

        item = {
            "id": pdf["id"],
            "name": pdf["name"],
            "url": pdf["url"],
            "type": "pdf",
            "category": pdf["category"],
            "hash": content_hash,
            "size": len(content),
            "content_type": ctype,
            "first_seen": prev.get("first_seen") if prev else utc_now_iso(),
            "last_check": utc_now_iso(),
            "last_modified": (
                utc_now_iso() if status_label in ("new", "changed")
                else prev.get("last_modified") if prev else utc_now_iso()
            ),
            "last_status": status_label,
        }
        current_items.append(item)

        if status_label in ("new", "changed"):
            new_changes.append({
                "timestamp": utc_now_iso(),
                "id": item["id"],
                "name": item["name"],
                "url": item["url"],
                "type": "pdf",
                "status": status_label,
                "previous_hash": prev.get("hash") if prev else None,
                "new_hash": content_hash,
            })
            print(f"  [{status_label.upper()}] hash variato")

        time.sleep(SLEEP_BETWEEN)

    # 3) Conserva voci pre-esistenti che non sono state ri-trovate (per non perdere lo storico)
    current_ids = {it["id"] for it in current_items}
    for old_id, old_item in previous_index.items():
        if old_id not in current_ids:
            # Marca come 'stale' ma conserva
            stale = dict(old_item)
            stale["last_status"] = "stale"
            current_items.append(stale)

    documents_state = {
        "last_scan": utc_now_iso(),
        "total_tracked": len(current_items),
        "items": sorted(current_items, key=lambda x: (x.get("category", ""), x.get("name", ""))),
    }
    return documents_state, new_changes


def main() -> int:
    print(f"=== Complaion - ACN Monitor — scan {utc_now_iso()} ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    documents_state, new_changes = scan()

    # Update changes log
    changes_log = safe_load_json(CHANGES_FILE, default={"events": []})
    if not isinstance(changes_log, dict):
        changes_log = {"events": []}
    events = list(changes_log.get("events", []))
    events.extend(new_changes)
    events = prune_changes(events)
    # Sort descending by timestamp
    events.sort(key=lambda c: c.get("timestamp", ""), reverse=True)
    changes_log = {
        "last_updated": utc_now_iso(),
        "total_events": len(events),
        "events": events,
    }

    save_json(DOCS_FILE, documents_state)
    save_json(CHANGES_FILE, changes_log)

    print(f"\n=== Scan completata ===")
    print(f"Risorse tracciate: {documents_state['total_tracked']}")
    print(f"Variazioni rilevate in questo scan: {len(new_changes)}")
    print(f"Eventi totali nel log: {len(events)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
