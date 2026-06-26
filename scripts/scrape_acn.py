#!/usr/bin/env python3
"""
Complaion - ACN Monitor (v5)
Scraper con SNAPSHOTS + DIFF + AI SUMMARY + PDF TEXT + ESTRAZIONE SCADENZE.

Novita v5:
- AI summary del diff via Google Gemini (gratis, free tier)
- Estrazione testo dai PDF e diff testuale anche sui PDF
- Estrazione automatica delle scadenze NIS2 via regex pattern matching
- Salva snapshot anche per i PDF
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
    {"id": "acn-home", "name": "ACN - Home", "url": "https://www.acn.gov.it/", "type": "page", "category": "Home"},
    {"id": "acn-portale-nis", "name": "ACN - Portale NIS", "url": "https://www.acn.gov.it/portale/nis", "type": "page", "category": "NIS2"},
    {"id": "acn-portale-nis-faq", "name": "ACN - FAQ NIS", "url": "https://www.acn.gov.it/portale/nis/faq", "type": "page", "category": "NIS2"},
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


# =============================================================================
# UTILITY GENERALI
# =============================================================================

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

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
        # Normalizza whitespace
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

        # Prepara il diff testuale (solo righe + e -)
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
```
{diff_text}
```

Produci un riassunto in italiano molto sintetico (3-5 righe massimo) di cosa e cambiato.
Concentrati sugli aspetti operativi rilevanti per i soggetti NIS2 italiani.
NON usare markdown. NON usare emoji.
Inizia direttamente con il contenuto, senza preamboli tipo "Il documento e stato modificato...".

Se il diff non sembra contenere informazioni utili (es. solo modifiche minori al layout, refresh tecnici, modifiche di formattazione), rispondi solo: "Modifiche tecniche/grafiche non rilevanti."
"""
        response = model.generate_content(prompt)
        summary = (response.text or "").strip()
        if not summary or len(summary) < 10:
            return None
        return summary
    except Exception as e:
        print(f"  [warn] Gemini API fallita: {e}", file=sys.stderr)
        return None


# =============================================================================
# ESTRAZIONE SCADENZE
# =============================================================================

MONTHS_IT = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

# Trigger keywords che indicano una scadenza (in lowercase, cerchiamo nei 50 chars prima)
DEADLINE_TRIGGERS = [
    "entro", "termine", "scadenza", "scade", "deadline", "scadono", "scadr",
    "entro la data", "dovranno", "obbligo entro", "non oltre"
]

# Pattern regex per date in italiano
DATE_PATTERNS = [
    # "28 febbraio 2026" o "28 Febbraio 2026"
    (re.compile(r'\b(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s+(\d{4})\b', re.IGNORECASE), "verbose"),
    # "28/02/2026"
    (re.compile(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b'), "slash"),
    # "28.02.2026"
    (re.compile(r'\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b'), "dot"),
    # ISO "2026-02-28"
    (re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b'), "iso"),
]

def parse_date(match, pattern_type):
    """Restituisce (date_iso, date_text) o None se non valida/passata."""
    try:
        if pattern_type == "verbose":
            day = int(match.group(1))
            month = MONTHS_IT[match.group(2).lower()]
            year = int(match.group(3))
        elif pattern_type in ("slash", "dot"):
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
        elif pattern_type == "iso":
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
        else:
            return None
        dt = datetime(year, month, day)
        # Filtra date troppo vecchie (>2 anni nel passato) o troppo future (>5 anni)
        now = datetime.now()
        if dt.year < now.year - 2 or dt.year > now.year + 5:
            return None
        return dt.strftime("%Y-%m-%d"), match.group(0)
    except (ValueError, KeyError):
        return None

def extract_deadlines(text, source_id, source_name, source_url):
    """
    Cerca pattern di scadenze nel testo. Restituisce lista di dict.
    Strategia: trova una data e verifica che nei 80 chars prima ci sia un trigger keyword.
    """
    if not text:
        return []
    text_lower = text.lower()
    found = []
    for pattern, ptype in DATE_PATTERNS:
        for m in pattern.finditer(text):
            start, end = m.span()
            # Controlla che nei 80 chars prima ci sia un trigger keyword
            window_before = text_lower[max(0, start - 80):start]
            has_trigger = any(t in window_before for t in DEADLINE_TRIGGERS)
            if not has_trigger:
                continue
            parsed = parse_date(m, ptype)
            if not parsed:
                continue
            date_iso, date_text = parsed
            # Contesto: 100 chars prima e 50 dopo
            ctx_start = max(0, start - 100)
            ctx_end = min(len(text), end + 50)
            context = text[ctx_start:ctx_end].replace("\n", " ").strip()
            context = re.sub(r"\s+", " ", context)
            found.append({
                "date": date_iso,
                "date_text": date_text,
                "context": context[:300],
                "source_id": source_id,
                "source_name": source_name,
                "source_url": source_url,
            })
    return found

def merge_deadlines(existing_deadlines, new_deadlines):
    """Merge: dedup per (date, source_id, context-prefix)."""
    seen_keys = {}
    for d in existing_deadlines:
        key = (d["date"], d["source_id"], d.get("context", "")[:60])
        seen_keys[key] = d
    for d in new_deadlines:
        key = (d["date"], d["source_id"], d.get("context", "")[:60])
        if key in seen_keys:
            seen_keys[key]["last_seen"] = utc_now_iso()
        else:
            d["first_seen"] = utc_now_iso()
            d["last_seen"] = utc_now_iso()
            seen_keys[key] = d
    return sorted(seen_keys.values(), key=lambda x: x["date"])


# =============================================================================
# CHANGES PRUNE
# =============================================================================

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
# MAIN SCAN
# =============================================================================

def scan():
    previous_docs = safe_load_json(DOCS_FILE, default={"items": [], "last_scan": None})
    previous_index = {item["id"]: item for item in previous_docs.get("items", [])}

    current_items = []
    new_changes = []
    discovered_pdfs = []
    all_deadlines_from_scan = []  # ogni risorsa contribuisce con le sue scadenze

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

        # Estrai testo normalizzato + hash
        normalized_text = normalize_html(content)
        content_hash = sha256_hex(normalized_text.encode("utf-8"))

        prev = previous_index.get(target["id"])
        status_label = "new" if not prev else ("unchanged" if prev.get("hash") == content_hash else "changed")

        item = {
            "id": target["id"], "name": target["name"], "url": target["url"], "type": target["type"],
            "category": target.get("category", ""), "hash": content_hash, "size": len(content),
            "content_type": ctype, "first_seen": prev.get("first_seen") if prev else utc_now_iso(),
            "last_check": utc_now_iso(),
            "last_modified": utc_now_iso() if status_label in ("new", "changed") else (prev.get("last_modified") if prev else utc_now_iso()),
            "last_status": status_label,
        }
        current_items.append(item)

        # Diff + snapshot + AI summary
        if status_label in ("new", "changed"):
            change_event = {
                "timestamp": utc_now_iso(), "id": item["id"], "name": item["name"],
                "url": item["url"], "type": item["type"], "status": status_label,
                "previous_hash": prev.get("hash") if prev else None, "new_hash": content_hash,
            }
            if normalized_text:
                old_text = load_snapshot(target["id"])
                if status_label == "changed" and old_text:
                    diff = compute_diff(old_text, normalized_text)
                    change_event["diff"] = diff
                    # AI summary del diff
                    summary = ai_summarize(item["name"], diff, "page")
                    if summary:
                        change_event["ai_summary"] = summary
                        print(f"  [ai] summary generato ({len(summary)} chars)")
                elif status_label == "new":
                    preview = normalized_text[:3000]
                    change_event["diff"] = {
                        "added": len(preview.splitlines()), "removed": 0,
                        "summary": "Nuova risorsa (anteprima dei primi caratteri)",
                        "truncated": len(normalized_text) > 3000,
                        "lines": [{"op": "+", "text": line} for line in preview.splitlines()[:80]],
                    }
                save_snapshot(target["id"], normalized_text)
            new_changes.append(change_event)
            print(f"  [{status_label.upper()}] hash variato")

        # Estrazione scadenze dalla pagina (sempre, anche se non c'è variazione)
        deadlines = extract_deadlines(normalized_text, item["id"], item["name"], item["url"])
        if deadlines:
            print(f"  [deadlines] trovate {len(deadlines)} potenziali scadenze")
            all_deadlines_from_scan.extend(deadlines)

        # Scoperta PDF
        if DISCOVER_PDFS and ctype.startswith("text/html"):
            for pdf in extract_pdf_links(content, target["url"]):
                discovered_pdfs.append({
                    "id": "pdf-" + sha256_hex(pdf["url"].encode())[:12],
                    "name": pdf["name"] or "Documento PDF",
                    "url": pdf["url"], "type": "pdf", "category": "Documento PDF",
                })
        time.sleep(SLEEP_BETWEEN)

    # SCAN PDF (con estrazione testo + diff + AI summary)
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

        # Estrazione testo PDF + hash sul testo
        pdf_text = extract_pdf_text(content)
        if pdf_text:
            content_hash = sha256_hex(pdf_text.encode("utf-8"))
        else:
            # Fallback hash binario
            content_hash = sha256_hex(content)

        prev = previous_index.get(pdf["id"])
        status_label = "new" if not prev else ("unchanged" if prev.get("hash") == content_hash else "changed")

        item = {
            "id": pdf["id"], "name": pdf["name"], "url": pdf["url"], "type": "pdf",
            "category": pdf["category"], "hash": content_hash, "size": len(content),
            "content_type": ctype, "first_seen": prev.get("first_seen") if prev else utc_now_iso(),
            "last_check": utc_now_iso(),
            "last_modified": utc_now_iso() if status_label in ("new", "changed") else (prev.get("last_modified") if prev else utc_now_iso()),
            "last_status": status_label,
        }
        current_items.append(item)

        if status_label in ("new", "changed"):
            change_event = {
                "timestamp": utc_now_iso(), "id": item["id"], "name": item["name"],
                "url": item["url"], "type": "pdf", "status": status_label,
                "previous_hash": prev.get("hash") if prev else None, "new_hash": content_hash,
            }
            if pdf_text:
                old_text = load_snapshot(pdf["id"])
                if status_label == "changed" and old_text:
                    diff = compute_diff(old_text, pdf_text)
                    change_event["diff"] = diff
                    summary = ai_summarize(item["name"], diff, "pdf")
                    if summary:
                        change_event["ai_summary"] = summary
                        print(f"  [ai] summary PDF generato")
                elif status_label == "new":
                    preview = pdf_text[:3000]
                    change_event["diff"] = {
                        "added": len(preview.splitlines()), "removed": 0,
                        "summary": "Nuovo PDF (anteprima del contenuto)",
                        "truncated": len(pdf_text) > 3000,
                        "lines": [{"op": "+", "text": line} for line in preview.splitlines()[:80]],
                    }
                save_snapshot(pdf["id"], pdf_text)
            new_changes.append(change_event)

        # Scadenze nei PDF
        if pdf_text:
            deadlines = extract_deadlines(pdf_text, pdf["id"], pdf["name"], pdf["url"])
            if deadlines:
                print(f"  [deadlines] trovate {len(deadlines)} in PDF")
                all_deadlines_from_scan.extend(deadlines)

        time.sleep(SLEEP_BETWEEN)

    # Conserva voci stale
    current_ids = {it["id"] for it in current_items}
    for old_id, old_item in previous_index.items():
        if old_id not in current_ids:
            stale = dict(old_item); stale["last_status"] = "stale"
            current_items.append(stale)

    documents_state = {
        "last_scan": utc_now_iso(), "total_tracked": len(current_items),
        "items": sorted(current_items, key=lambda x: (x.get("category", ""), x.get("name", ""))),
    }
    return documents_state, new_changes, all_deadlines_from_scan


def main():
    print(f"=== Complaion - ACN Monitor v5 - scan {utc_now_iso()} ===")
    print(f"  pdfplumber: {'OK' if PDFPLUMBER_AVAILABLE else 'NO'}")
    print(f"  google-generativeai: {'OK' if GENAI_AVAILABLE else 'NO'}")
    print(f"  GEMINI_API_KEY: {'SET' if GEMINI_API_KEY else 'NOT SET'}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    documents_state, new_changes, deadlines = scan()

    # Update changes
    changes_log = safe_load_json(CHANGES_FILE, default={"events": []})
    if not isinstance(changes_log, dict):
        changes_log = {"events": []}
    events = list(changes_log.get("events", []))
    events.extend(new_changes)
    events = prune_changes(events)
    events.sort(key=lambda c: c.get("timestamp", ""), reverse=True)
    changes_log = {"last_updated": utc_now_iso(), "total_events": len(events), "events": events}

    # Update deadlines
    existing_deadlines = safe_load_json(DEADLINES_FILE, default={"deadlines": []})
    if not isinstance(existing_deadlines, dict):
        existing_deadlines = {"deadlines": []}
    merged_deadlines = merge_deadlines(existing_deadlines.get("deadlines", []), deadlines)
    deadlines_state = {
        "last_updated": utc_now_iso(),
        "total_deadlines": len(merged_deadlines),
        "deadlines": merged_deadlines,
    }

    save_json(DOCS_FILE, documents_state)
    save_json(CHANGES_FILE, changes_log)
    save_json(DEADLINES_FILE, deadlines_state)

    print(f"\n=== Scan completata ===")
    print(f"Risorse tracciate: {documents_state['total_tracked']}")
    print(f"Variazioni rilevate in questo scan: {len(new_changes)}")
    print(f"Eventi totali nel log: {len(events)}")
    print(f"Scadenze totali trovate: {len(merged_deadlines)} (nuove in questo scan: {len(deadlines)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
