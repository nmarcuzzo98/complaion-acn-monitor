#!/usr/bin/env python3
"""
Complaion - ACN Monitor - Slack notifier
Legge le variazioni rilevate nell'ultimo scan (file JSON) e invia una notifica
riassuntiva su un canale Slack tramite Incoming Webhook.
"""

import json
import os
import sys
from pathlib import Path

import requests

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
NEW_CHANGES_FILE = os.environ.get("NEW_CHANGES_FILE", "/tmp/last_scan_changes.json")

MAX_EVENTS_INLINE = 20
SUMMARY_MAX_LEN = 400

STATUS_EMOJI = {
    "new": ":new:",
    "changed": ":pencil2:",
    "removed": ":x:",
    "stale": ":ghost:",
}

STATUS_LABEL = {
    "new": "Nuovo",
    "changed": "Modificato",
    "removed": "Rimosso",
    "stale": "Non piu' rilevato",
}


def load_events():
    p = Path(NEW_CHANGES_FILE)
    if not p.exists():
        print(f"[info] File {NEW_CHANGES_FILE} non trovato: nessuna notifica da inviare.")
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[error] Errore lettura {NEW_CHANGES_FILE}: {e}", file=sys.stderr)
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "events" in data:
        return data["events"]
    return []


def truncate(text, n):
    if not text:
        return ""
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0] + "..."


def format_event(ev):
    emoji = STATUS_EMOJI.get(ev.get("status"), ":small_blue_diamond:")
    label = STATUS_LABEL.get(ev.get("status"), (ev.get("status") or "").capitalize() or "Variazione")
    name = ev.get("name", "Documento senza nome")
    url = ev.get("url", "")
    item_type = "PDF" if ev.get("type") == "pdf" else "Pagina web"

    ai = (ev.get("ai_summary") or "").strip()
    diff_summary = (ev.get("diff", {}) or {}).get("summary", "").strip()
    body_summary = truncate(ai or diff_summary, SUMMARY_MAX_LEN)

    parts = [f"{emoji} *{label}* - <{url}|{name}>", f"_{item_type}_"]
    if body_summary:
        parts.append(f"> {body_summary}")
    return "\n".join(parts)


def build_blocks(events):
    total = len(events)

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Aggiornamento ACN NIS2 - {total} novita'"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "_Rilevato dal monitor Complaion ACN - <https://nmarcuzzo98.github.io/complaion-acn-monitor/|apri la dashboard>_"}
            ],
        },
        {"type": "divider"},
    ]

    events_to_show = events[:MAX_EVENTS_INLINE]
    for ev in events_to_show:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": format_event(ev)},
        })

    if total > MAX_EVENTS_INLINE:
        residual = total - MAX_EVENTS_INLINE
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_+ altre {residual} variazioni non mostrate qui - vedi la dashboard per il dettaglio completo._"}
            ],
        })

    return blocks


def main():
    if not SLACK_WEBHOOK_URL:
        print("[error] SLACK_WEBHOOK_URL non impostato: notifica saltata.", file=sys.stderr)
        return 0

    events = load_events()
    if not events:
        print("[info] Nessuna variazione da notificare in questo scan.")
        return 0

    blocks = build_blocks(events)
    payload = {
        "text": f"Aggiornamento ACN NIS2 - {len(events)} novita' rilevata",
        "blocks": blocks,
    }

    try:
        r = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
        print(f"[ok] Notifica Slack inviata: {len(events)} variazioni.")
    except requests.RequestException as e:
        print(f"[error] Invio Slack fallito: {e}", file=sys.stderr)
        if hasattr(e, "response") and e.response is not None:
            print(f"[error] Status: {e.response.status_code} - Body: {e.response.text[:500]}", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
