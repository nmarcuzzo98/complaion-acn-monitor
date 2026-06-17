/* ============================================================
   Complaion - ACN Monitor — Dashboard logic
   Carica documents.json + changes.json e renderizza la UI.
   ============================================================ */

const DOCS_URL    = "data/documents.json";
const CHANGES_URL = "data/changes.json";

const state = {
  documents: { items: [], last_scan: null },
  changes:   { events: [], last_updated: null },
  filters: {
    search: "",
    status: "",
    type: "",
    category: "",
    sortBy: "last_modified",
  },
};

// ============================================================
// FETCH
// ============================================================

async function loadData() {
  try {
    const [docsRes, changesRes] = await Promise.all([
      fetch(DOCS_URL,    { cache: "no-store" }),
      fetch(CHANGES_URL, { cache: "no-store" }),
    ]);
    if (docsRes.ok)    state.documents = await docsRes.json();
    if (changesRes.ok) state.changes   = await changesRes.json();
  } catch (e) {
    console.error("Errore caricamento dati:", e);
  }
}

// ============================================================
// HELPERS
// ============================================================

function fmtDateTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("it-IT", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

function fmtRelative(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1)   return "ora";
  if (diffMin < 60)  return `${diffMin} min fa`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24)    return `${diffH} h fa`;
  const diffD = Math.floor(diffH / 24);
  if (diffD < 30)    return `${diffD} g fa`;
  const diffMo = Math.floor(diffD / 30);
  return `${diffMo} mese${diffMo !== 1 ? "i" : ""} fa`;
}

function fmtSize(bytes) {
  if (!bytes) return "—";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + " KB";
  return (bytes/1024/1024).toFixed(2) + " MB";
}

function statusEmoji(status) {
  const map = {
    unchanged:    "🟢",
    changed:      "🟡",
    new:          "🔵",
    fetch_error:  "🔴",
    http_4:       "🔴",
    http_5:       "🔴",
    stale:        "⚪",
  };
  // Normalizza es. "http_404" -> "http_4"
  if (status?.startsWith("http_4")) return "🔴";
  if (status?.startsWith("http_5")) return "🔴";
  return map[status] || "⚫";
}

function statusLabel(status) {
  const map = {
    unchanged:    "Invariata",
    changed:      "Modificata",
    new:          "Nuova",
    fetch_error:  "Errore fetch",
    stale:        "Non più trovata",
  };
  if (status?.startsWith("http_")) return `HTTP ${status.split("_")[1]}`;
  return map[status] || status;
}

function typeIcon(type) {
  return type === "pdf" ? "📕" : "📄";
}

// ============================================================
// RENDER STATS
// ============================================================

function renderStats() {
  const docs = state.documents.items || [];
  const totalEl = document.getElementById("stat-total");
  totalEl.textContent = docs.filter(d => d.last_status !== "stale").length;

  // Variazioni 7/30 giorni
  const now = Date.now();
  const events = state.changes.events || [];
  const within = (days) => events.filter(ev => {
    const t = new Date(ev.timestamp).getTime();
    return (now - t) <= days * 86400000;
  }).length;
  document.getElementById("stat-changes-7d").textContent  = within(7);
  document.getElementById("stat-changes-30d").textContent = within(30);
  document.getElementById("stat-pdfs").textContent =
    docs.filter(d => d.type === "pdf" && d.last_status !== "stale").length;

  const lastScan = state.documents.last_scan;
  document.getElementById("last-scan").textContent =
    "Ultima scansione: " + fmtDateTime(lastScan) + " (" + fmtRelative(lastScan) + ")";
}

// ============================================================
// RENDER LATEST CHANGES
// ============================================================

function renderChanges() {
  const container = document.getElementById("changes-list");
  const events = (state.changes.events || []).slice(0, 12);
  if (events.length === 0) {
    container.innerHTML = '<div class="empty">Nessuna variazione rilevata di recente. Tutto è stabile.</div>';
    return;
  }
  container.innerHTML = events.map(ev => `
    <div class="change-item status-${ev.status}">
      <div class="change-icon">${ev.status === "new" ? "🔵" : "🟡"}</div>
      <div class="change-body">
        <div class="change-title"><a href="${ev.url}" target="_blank" rel="noopener">${escape(ev.name)}</a></div>
        <div class="change-meta">${ev.status === "new" ? "Nuova risorsa rilevata" : "Contenuto modificato"} · ${typeIcon(ev.type)} ${ev.type === "pdf" ? "PDF" : "Pagina"}</div>
      </div>
      <div class="change-time" title="${fmtDateTime(ev.timestamp)}">${fmtRelative(ev.timestamp)}</div>
    </div>
  `).join("");
}

// ============================================================
// RENDER RESOURCES
// ============================================================

function populateCategoryFilter() {
  const select = document.getElementById("filter-category");
  const cats = new Set();
  (state.documents.items || []).forEach(d => { if (d.category) cats.add(d.category); });
  const sorted = [...cats].sort();
  const current = select.value;
  // Preserve "Tutte" option
  select.innerHTML = '<option value="">Tutte le categorie</option>' +
    sorted.map(c => `<option value="${escape(c)}"${c === current ? " selected" : ""}>${escape(c)}</option>`).join("");
}

function filteredResources() {
  let items = (state.documents.items || []).slice();
  const f = state.filters;
  if (f.search) {
    const q = f.search.toLowerCase();
    items = items.filter(d =>
      (d.name || "").toLowerCase().includes(q) ||
      (d.url  || "").toLowerCase().includes(q)
    );
  }
  if (f.status)   items = items.filter(d => d.last_status === f.status || (f.status.startsWith("http_") && d.last_status.startsWith("http_")));
  if (f.type)     items = items.filter(d => d.type === f.type);
  if (f.category) items = items.filter(d => d.category === f.category);

  // Sort
  const sortMap = {
    last_modified: (a,b) => (b.last_modified || "").localeCompare(a.last_modified || ""),
    last_check:    (a,b) => (b.last_check || "").localeCompare(a.last_check || ""),
    name:          (a,b) => (a.name || "").localeCompare(b.name || ""),
    status: (a,b) => {
      const order = { changed: 0, new: 1, fetch_error: 2, unchanged: 3, stale: 4 };
      return (order[a.last_status] ?? 9) - (order[b.last_status] ?? 9);
    },
  };
  items.sort(sortMap[f.sortBy] || sortMap.last_modified);
  return items;
}

function renderResources() {
  const items = filteredResources();
  const container = document.getElementById("resources-list");
  const countEl = document.getElementById("resources-count");
  countEl.textContent = `${items.length} risorsa${items.length !== 1 ? "e" : ""} visualizzata${items.length !== 1 ? "e" : ""}`;

  if (items.length === 0) {
    container.innerHTML = '<div class="empty">Nessuna risorsa corrisponde ai filtri selezionati.</div>';
    return;
  }
  container.innerHTML = items.map(d => `
    <div class="resource-item status-${d.last_status}">
      <div class="resource-icon">${typeIcon(d.type)}</div>
      <div class="resource-body">
        <div class="resource-title"><a href="${d.url}" target="_blank" rel="noopener">${escape(d.name)}</a></div>
        <div class="resource-url">${escape(d.url)}</div>
        <div class="resource-meta">
          <span class="badge cat">${escape(d.category || "—")}</span>
          <span class="badge type-${d.type}">${d.type === "pdf" ? "PDF" : "Pagina"}</span>
          · ultimo controllo ${fmtRelative(d.last_check)} · modifica rilevata ${fmtRelative(d.last_modified)} · ${fmtSize(d.size)}
        </div>
      </div>
      <div class="resource-status ${d.last_status}">${statusEmoji(d.last_status)} ${statusLabel(d.last_status)}</div>
    </div>
  `).join("");
}

// ============================================================
// EVENTS
// ============================================================

function bindEvents() {
  document.getElementById("search").addEventListener("input", e => {
    state.filters.search = e.target.value;
    renderResources();
  });
  document.getElementById("filter-status").addEventListener("change", e => {
    state.filters.status = e.target.value; renderResources();
  });
  document.getElementById("filter-type").addEventListener("change", e => {
    state.filters.type = e.target.value; renderResources();
  });
  document.getElementById("filter-category").addEventListener("change", e => {
    state.filters.category = e.target.value; renderResources();
  });
  document.getElementById("sort-by").addEventListener("change", e => {
    state.filters.sortBy = e.target.value; renderResources();
  });
  document.getElementById("reset-filters").addEventListener("click", () => {
    state.filters = { search: "", status: "", type: "", category: "", sortBy: "last_modified" };
    document.getElementById("search").value = "";
    document.getElementById("filter-status").value = "";
    document.getElementById("filter-type").value = "";
    document.getElementById("filter-category").value = "";
    document.getElementById("sort-by").value = "last_modified";
    renderResources();
  });

  // Modal
  const modal = document.getElementById("about-modal");
  const openModal  = () => modal.classList.remove("hidden");
  const closeModal = () => modal.classList.add("hidden");
  document.getElementById("about-link").addEventListener("click", e => { e.preventDefault(); openModal(); });
  document.getElementById("footer-about").addEventListener("click", e => { e.preventDefault(); openModal(); });
  document.getElementById("modal-close").addEventListener("click", closeModal);
  modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });
}

// ============================================================
// UTIL
// ============================================================

function escape(s) {
  return String(s || "").replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}

// ============================================================
// MAIN
// ============================================================

(async function init() {
  await loadData();
  populateCategoryFilter();
  bindEvents();
  renderStats();
  renderChanges();
  renderResources();
})();
