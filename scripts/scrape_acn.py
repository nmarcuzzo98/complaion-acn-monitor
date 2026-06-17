#!/usr/bin/env python3
"""
Complaion - ACN Monitor (v4)
Scraper con SNAPSHOTS + DIFF TESTUALE.

Novità v4:
- Salva uno snapshot del testo normalizzato in docs/data/snapshots/<id>.txt
- Quando un hash cambia, calcola il diff testuale (difflib) e lo include in changes.json
- La dashboard mostra il diff cliccando sulla variazione
"""

import difflib
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
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
DOCS_FILE = DATA_DIR / "documents.json"
CHANGES_FILE = DATA_DIR / "changes.json"

USER_AGENT = "Mozilla/5.0 (compatible; ComplaionACNMonitor/1.0; +https://github.com/)"
REQUEST_TIMEOUT = 60
RETRY_COUNT = 3
RETRY_DELAY = 5
SLEEP_BETWEEN = 1.5

CHANGES_RETENTION_DAYS = 180
DIFF_MAX_LINES = 200          # massimo righe di diff salvate nel JSON
SNAPSHOT_MAX_CHARS = 200_000  # ~200 KB di testo per snapshot, abbastanza per pagine HTML


# =============================================================================
# TARGETS
# =============================================================================

TARGETS = [
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
    {
        "id": "acn-nis-normativa",
        "name": "ACN - La normativa",
        "url": "https://www.acn.gov.it/portale/nis/la-normativa",
        "type": "page",
        "category": "NIS2 - Normativa",
    },
    {
        "id": "acn-nis-registrazione",
        "name": "ACN - Registrazione NIS",
        "url": "https://www.acn.gov.it/portale/nis/registrazione",
        "type": "page",
        "category": "NIS2 - Operativo",
    },
    {
        "id": "acn-nis-modalita-specifiche",
        "name": "ACN - Modalita e specifiche di base",
        "url": "https://www.acn.gov.it/portale/nis/modalita-specifiche-base",
        "type": "page",
        "category": "NIS2 - Operativo",
    },
    {
        "id": "acn-nis-categorizzazione",
        "name": "ACN - Categorizzazione",
        "url": "https://www.acn.gov.it/portale/nis/categorizzazione",
        "type": "page",
        "category": "NIS2 - Operativo",
    },
    {
        "id": "acn-nis-ambito",
        "name": "ACN - Ambito NIS",
        "url": "https://www.acn.gov.it/portale/nis/ambito",
        "type": "page",
        "category": "NIS2 - Ambito",
    },
    {
        "id": "acn-nis-obblighi",
        "name": "ACN - Obblighi",
        "url": "https://www.acn.gov.it/portale/nis/obblighi",
        "type": "page",
        "category": "NIS2 - Obblighi",
    },
    {
        "id": "acn-nis-aggiornamento",
        "name": "ACN - Aggiornamento delle informazioni",
        "url": "https://www.acn.gov.it/portale/nis/aggiornamento-informazioni",
        "type": "page",
        "category": "NIS2 - Operativo",
    },
    {
        "id": "acn-nis-notizie-eventi",
        "name": "ACN - Notizie ed eventi NIS",
        "url": "https://www.acn.gov.it/portale/nis/notizie-ed-eventi",
        "type": "page",
        "category": "NIS2 - News",
    },
]

DISCOVER_PDFS = True
PDF_DISCOVERY_KEYWORDS = ["nis", "categorizzazione", "determinazione", "obblighi",
                         "cybersicurezza", "cyber", "tassonomia", "misure", "piattaforma"]


# =============================================================================
# UTILITY
# =============================================================================

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch(url):
    last_err = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT},
                           timeout=REQUEST_TIMEOUT, allow_redirects=True)
            content_type = r.headers.get("Content-Type", "").split(";")[0].strip()
            return r.content, r.status_code, content_type
        except (requests.RequestException, OSError) as e:
            last_err = e
            print(f"  [warn] tentativo {attempt}/{RETRY_COUNT} fallito: {e}", file=sys.stderr)
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
    raise RuntimeError(f"Fetch fallito: {last_err}")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_html(html_bytes: bytes) -> str:
    """Restituisce il testo pulito (stringa) usato sia per hash sia per snapshot."""
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


def safe_load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [warn] errore lettura {path}: {e}", file=sys.stderr)
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def prune_changes(events):
    cutoff = datetime.now(timezone.utc).timestamp() - CHANGES_RETENTION_DAYS * 86400
    out = []
    for c in events:
        try:
            if datetime.fromisoformat(c["timestamp"]).timestamp() >= cutoff:
                out.append(c)
        except Exception:
            out.append(c)
    return out


# =============================================================================
# SNAPSHOT MANAGEMENT
# =============================================================================

def snapshot_path(item_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", item_id)
    return SNAPSHOTS_DIR / f"{safe}.txt"


def load_snapshot(item_id: str) -> str:
    path = snapshot_path(item_id)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def save_snapshot(item_id: str, text: str) -> None:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    truncated = text[:SNAPSHOT_MAX_CHARS]
    snapshot_path(item_id).write_text(truncated, encoding="utf-8")


def compute_diff(old_text: str, new_text: str, max_lines: int = DIFF_MAX_LINES) -> dict:
    """
    Calcola diff tra old_text e new_text. Restituisce:
    {
      "added": int, "removed": int, "summary": str, "lines": [{op: '+'|'-'|' ', text: str}]
    }
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        lineterm="", n=2  # 2 righe di contesto
    ))

    # Skip header lines (--- / +++)
    body = [l for l in diff if not (l.startswith("---") or l.startswith("+++") or l.startswith("@@"))]

    added = removed = 0
    lines = []
    for l in body[:max_lines]:
        if l.startswith("+"):
            lines.append({"op": "+", "text": l[1:]})
            added += 1
        elif l.startswith("-"):
            lines.append({"op": "-", "text": l[1:]})
            removed += 1
        else:
            lines.append({"op": " ", "text": l[1:] if l.startswith(" ") else l})

    truncated = len(body) > max_lines
    summary = f"+{added} aggiunte, -{removed} rimosse"
    if truncated:
        summary += f" (diff troncato a {max_lines} righe)"
    return {"added": added, "removed": removed, "summary": summary,
            "truncated": truncated, "lines": lines}


# =============================================================================
# MAIN SCAN
# =============================================================================

def scan():
    previous_docs = safe_load_json(DOCS_FILE, default={"items": [], "last_scan": None})
    previous_index = {item["id"]: item for item in previous_docs.get("items", [])}

    current_items = []
    new_changes = []
    discovered_pdfs = []

    for target in TARGETS:
        print(f"[scan] {target['name']} ({target['url']})")
        try:
            content, status, ctype = fetch(target["url"])
        except RuntimeError as e:
            print(f"  [error] {e}", file=sys.stderr)
            prev = previous_index.get(target["id"])
            if prev:
                prev = dict(prev); prev["last_status"] = "fetch_error"; prev["last_check"] = utc_now_iso()
                current_items.append(prev)
            continue

        if status >= 400:
            print(f"  [warn] status HTTP {status}")
            prev = previous_index.get(target["id"])
            if prev:
                prev = dict(prev); prev["last_status"] = f"http_{status}"; prev["last_check"] = utc_now_iso()
                current_items.append(prev)
            continue

        if target["type"] == "pdf":
            content_hash = sha256_hex(content)
            normalized_text = ""  # PDF: niente diff testuale per ora
        else:
            normalized_text = normalize_html(content)
            content_hash = sha256_hex(normalized_text.encode("utf-8"))

        prev = previous_index.get(target["id"])
        status_label = "new" if not prev else ("unchanged" if prev.get("hash") == content_hash else "changed")

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
            "last_modified": utc_now_iso() if status_label in ("new", "changed")
                else (prev.get("last_modified") if prev else utc_now_iso()),
            "last_status": status_label,
        }
        current_items.append(item)

        if status_label in ("new", "changed"):
            change_event = {
                "timestamp": utc_now_iso(),
                "id": item["id"],
                "name": item["name"],
                "url": item["url"],
                "type": item["type"],
                "status": status_label,
                "previous_hash": prev.get("hash") if prev else None,
                "new_hash": content_hash,
            }

            if target["type"] == "page" and normalized_text:
                old_text = load_snapshot(target["id"])
                if status_label == "changed" and old_text:
                    diff = compute_diff(old_text, normalized_text)
                    change_event["diff"] = diff
                elif status_label == "new":
                    preview = normalized_text[:3000]
                    change_event["diff"] = {
                        "added": len(preview.splitlines()),
                        "removed": 0,
                        "summary": "Nuova risorsa (anteprima dei primi caratteri)",
                        "truncated": len(normalized_text) > 3000,
                        "lines": [{"op": "+", "text": line} for line in preview.splitlines()[:80]],
                    }
                save_snapshot(target["id"], normalized_text)

            new_changes.append(change_event)
            print(f"  [{status_label.upper()}] hash variato")

        if DISCOVER_PDFS and target["type"] == "page" and ctype.startswith("text/html"):
            for pdf in extract_pdf_links(content, target["url"]):
                discovered_pdfs.append({
                    "id": "pdf-" + sha256_hex(pdf["url"].encode())[:12],
                    "name": pdf["name"] or "Documento PDF",
                    "url": pdf["url"],
                    "type": "pdf",
                    "category": "Documento PDF",
                })
        time.sleep(SLEEP_BETWEEN)

    # PDF scoperti
    tracked_urls = {it["url"] for it in current_items}
    seen_pdf_ids = set()
    pdfs_to_scan = []
    for pdf in discovered_pdfs:
        if pdf["url"] in tracked_urls or pdf["id"] in seen_pdf_ids:
            continue
        seen_pdf_ids.add(pdf["id"])
        pdfs_to_scan.append(pdf)

    print(f"\n[discovery] PDF candidati: {len(pdfs_to_scan)}")
    for pdf in pdfs_to_scan:
        print(f"[scan-pdf] {pdf['name']}")
        try:
            content, status, ctype = fetch(pdf["url"])
        except RuntimeError as e:
            print(f"  [error] {e}", file=sys.stderr); continue
        if status >= 400 or not ctype.lower().startswith("application/pdf"):
            continue
        content_hash = sha256_hex(content)
        prev = previous_index.get(pdf["id"])
        status_label = "new" if not prev else ("unchanged" if prev.get("hash") == content_hash else "changed")
        item = {
            "id": pdf["id"], "name": pdf["name"], "url": pdf["url"],
            "type": "pdf", "category": pdf["category"],
            "hash": content_hash, "size": len(content), "content_type": ctype,
            "first_seen": prev.get("first_seen") if prev else utc_now_iso(),
            "last_check": utc_now_iso(),
            "last_modified": utc_now_iso() if status_label in ("new", "changed")
                else (prev.get("last_modified") if prev else utc_now_iso()),
            "last_status": status_label,
        }
        current_items.append(item)
        if status_label in ("new", "changed"):
            new_changes.append({
                "timestamp": utc_now_iso(), "id": item["id"], "name": item["name"],
                "url": item["url"], "type": "pdf", "status": status_label,
                "previous_hash": prev.get("hash") if prev else None, "new_hash": content_hash,
            })
        time.sleep(SLEEP_BETWEEN)

    # Conserva voci stale
    current_ids = {it["id"] for it in current_items}
    for old_id, old_item in previous_index.items():
        if old_id not in current_ids:
            stale = dict(old_item); stale["last_status"] = "stale"
            current_items.append(stale)

    documents_state = {
        "last_scan": utc_now_iso(),
        "total_tracked": len(current_items),
        "items": sorted(current_items, key=lambda x: (x.get("category", ""), x.get("name", ""))),
    }
    return documents_state, new_changes


def main():
    print(f"=== Complaion - ACN Monitor v4 - scan {utc_now_iso()} ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    documents_state, new_changes = scan()

    changes_log = safe_load_json(CHANGES_FILE, default={"events": []})
    if not isinstance(changes_log, dict):
        changes_log = {"events": []}
    events = list(changes_log.get("events", []))
    events.extend(new_changes)
    events = prune_changes(events)
    events.sort(key=lambda c: c.get("timestamp", ""), reverse=True)
    changes_log = {"last_updated": utc_now_iso(), "total_events": len(events), "events": events}

    save_json(DOCS_FILE, documents_state)
    save_json(CHANGES_FILE, changes_log)

    print(f"\n=== Scan completata ===")
    print(f"Risorse tracciate: {documents_state['total_tracked']}")
    print(f"Variazioni rilevate in questo scan: {len(new_changes)}")
    print(f"Eventi totali nel log: {len(events)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
