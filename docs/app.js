/* ==============================================================
   Complaion — ACN Monitor — Dashboard v3
   Logica: dati, rendering, filtri, chart, theme toggle, interazioni
   ============================================================== */

const DOCS_URL    = "data/documents.json";
const CHANGES_URL = "data/changes.json";

const state = {
  documents: { items: [], last_scan: null },
  changes:   { events: [], last_updated: null },
  filters: { search: "", status: "", type: "", category: "", sortBy: "last_modified" },
  chartInstance: null,
};

// ============================================================
// THEME (light/dark)
// ============================================================

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("acn-monitor-theme", theme);
  document.getElementById("theme-icon").textContent = theme === "dark" ? "☀️" : "🌙";
  // Aggiorna anche il grafico se esiste
  if (state.chartInstance) renderChart();
}

function initTheme() {
  const saved = localStorage.getItem("acn-monitor-theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved || (prefersDark ? "dark" : "light"));
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "light";
  applyTheme(current === "light" ? "dark" : "light");
}

// ============================================================
// FETCH DATA
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
// FORMATTERS
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
  const h = Math.floor(diffMin / 60);
  if (h < 24)        return `${h} h fa`;
  const dd = Math.floor(h / 24);
  if (dd < 30)       return `${dd} g fa`;
  return `${Math.floor(dd / 30)} mesi fa`;
}
function fmtSize(bytes) {
  if (!bytes) return "—";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + " KB";
  return (bytes/1024/1024).toFixed(2) + " MB";
}
function escape(s) {
  return String(s || "").replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}

function statusEmoji(status) {
  if (!status) return "⚫";
  if (status.startsWith("http_4") || status.startsWith("http_5") || status === "fetch_error") return "🔴";
  return ({ unchanged: "🟢", changed: "🟡", new: "🔵", stale: "⚪" }[status]) || "⚫";
}
function statusLabel(status) {
  if (!status) return "—";
  if (status.startsWith("http_")) return `HTTP ${status.split("_")[1]}`;
  return ({ unchanged: "Invariata", changed: "Modificata", new: "Nuova", fetch_error: "Errore", stale: "Non trovata" }[status]) || status;
}
function typeIcon(type) { return type === "pdf" ? "📕" : "📄"; }

// ============================================================
// RENDER STATS
// ============================================================

function renderStats() {
  const docs = state.documents.items || [];
  const active = docs.filter(d => d.last_status !== "stale");
  document.getElementById("stat-total").textContent = active.length;

  const now = Date.now();
  const events = state.changes.events || [];
  const within = days => events.filter(ev => (now - new Date(ev.timestamp).getTime()) <= days * 86400000).length;
  document.getElementById("stat-changes-7d").textContent  = within(7);
  document.getElementById("stat-changes-30d").textContent = within(30);
  document.getElementById("stat-pdfs").textContent       = active.filter(d => d.type === "pdf").length;

  document.getElementById("last-scan").textContent =
    "Ultima scansione: " + fmtDateTime(state.documents.last_scan) + " (" + fmtRelative(state.documents.last_scan) + ")";
}

// ============================================================
// RENDER CHART — Timeline variazioni ultimi 30gg
// ============================================================

function renderChart() {
  const canvas = document.getElementById("timeline-chart");
  if (!canvas) return;

  const days = 30;
  const labels = [];
  const counts = new Array(days).fill(0);
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    labels.push(d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit" }));
  }

  (state.changes.events || []).forEach(ev => {
    const evDate = new Date(ev.timestamp);
    evDate.setHours(0, 0, 0, 0);
    const delta = Math.floor((today - evDate) / 86400000);
    if (delta >= 0 && delta < days) counts[days - 1 - delta]++;
  });

  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  const primaryColor = isDark ? "#9AAF98" : "#092D0B";
  const accentColor = isDark ? "rgba(154,175,152,0.18)" : "rgba(9,45,11,0.10)";
  const gridColor = isDark ? "rgba(154,175,152,0.10)" : "rgba(9,45,11,0.08)";
  const textColor = isDark ? "#B0C3AC" : "#4A5550";

  // Destroy precedente se esiste
  if (state.chartInstance) state.chartInstance.destroy();

  state.chartInstance = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Variazioni rilevate",
        data: counts,
        borderColor: primaryColor,
        backgroundColor: accentColor,
        borderWidth: 2.5,
        pointRadius: counts.map(c => c > 0 ? 5 : 0),
        pointHoverRadius: 7,
        pointBackgroundColor: primaryColor,
        pointBorderColor: "#FFFFFF",
        pointBorderWidth: 2,
        tension: 0.35,
        fill: true,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: primaryColor,
          titleColor: "#EBFFE5",
          bodyColor: "#EBFFE5",
          padding: 10,
          cornerRadius: 8,
          displayColors: false,
          callbacks: {
            title: (items) => items[0].label,
            label: (item) => `${item.parsed.y} variazion${item.parsed.y === 1 ? "e" : "i"}`
          }
        }
      },
      scales: {
        x: {
          grid: { color: gridColor, drawBorder: false },
          ticks: { color: textColor, font: { size: 11 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }
        },
        y: {
          beginAtZero: true,
          grid: { color: gridColor, drawBorder: false },
          ticks: { color: textColor, font: { size: 11 }, precision: 0 }
        }
      }
    }
  });
}

// ============================================================
// RENDER CHANGES
// ============================================================

function renderChanges() {
  const container = document.getElementById("changes-list");
  const meta = document.getElementById("changes-meta");
  const events = (state.changes.events || []).slice(0, 12);

  meta.textContent = `${state.changes.events?.length || 0} eventi totali`;

  if (events.length === 0) {
    container.innerHTML = '<div class="empty">Nessuna variazione rilevata di recente. Tutto è stabile. 🟢</div>';
    return;
  }

  container.innerHTML = events.map(ev => `
    <div class="change-item status-${ev.status}" data-url="${escape(ev.url)}">
      <div class="change-icon">${ev.status === "new" ? "🔵" : "🟡"}</div>
      <div class="change-body">
        <div class="change-title">${escape(ev.name)}</div>
        <div class="change-meta">
          ${ev.status === "new" ? "Nuova risorsa rilevata" : "Contenuto modificato"} ·
          ${typeIcon(ev.type)} ${ev.type === "pdf" ? "PDF" : "Pagina"} ·
          hash: <code style="font-family:monospace;font-size:11px">${ev.new_hash?.substring(0, 8) || "—"}</code>
        </div>
      </div>
      <div class="change-time" title="${fmtDateTime(ev.timestamp)}">${fmtRelative(ev.timestamp)}</div>
      <a class="change-cta" href="${escape(ev.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation();">
        Apri risorsa
      </a>
    </div>
  `).join("");

  // Click sull'intera card → apre la risorsa
  container.querySelectorAll(".change-item").forEach(el => {
    el.addEventListener("click", () => {
      const url = el.getAttribute("data-url");
      if (url) window.open(url, "_blank", "noopener");
    });
  });
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
  select.innerHTML = '<option value="">Tutte le categorie</option>' +
    sorted.map(c => `<option value="${escape(c)}"${c === current ? " selected" : ""}>${escape(c)}</option>`).join("");
}

function filteredResources() {
  let items = (state.documents.items || []).slice();
  const f = state.filters;
  if (f.search) {
    const q = f.search.toLowerCase();
    items = items.filter(d => (d.name || "").toLowerCase().includes(q) || (d.url || "").toLowerCase().includes(q));
  }
  if (f.status) items = items.filter(d =>
    d.last_status === f.status || (f.status === "fetch_error" && (d.last_status || "").startsWith("http_")));
  if (f.type)     items = items.filter(d => d.type === f.type);
  if (f.category) items = items.filter(d => d.category === f.category);

  const sortMap = {
    last_modified: (a, b) => (b.last_modified || "").localeCompare(a.last_modified || ""),
    last_check:    (a, b) => (b.last_check || "").localeCompare(a.last_check || ""),
    name:          (a, b) => (a.name || "").localeCompare(b.name || ""),
    status: (a, b) => {
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
  countEl.textContent = `${items.length} risors${items.length !== 1 ? "e" : "a"} visualizzat${items.length !== 1 ? "e" : "a"}`;

  if (items.length === 0) {
    container.innerHTML = '<div class="empty">Nessuna risorsa corrisponde ai filtri selezionati.</div>';
    return;
  }
  container.innerHTML = items.map(d => `
    <div class="resource-item status-${d.last_status}">
      <div class="resource-icon">${typeIcon(d.type)}</div>
      <div class="resource-body">
        <div class="resource-title"><a href="${escape(d.url)}" target="_blank" rel="noopener">${escape(d.name)}</a></div>
        <div class="resource-url">${escape(d.url)}</div>
        <div class="resource-meta">
          <span class="badge cat">${escape(d.category || "—")}</span>
          <span class="badge type-${d.type}">${d.type === "pdf" ? "PDF" : "Pagina"}</span>
          <span>· ${fmtRelative(d.last_check)} · ${fmtSize(d.size)}</span>
        </div>
      </div>
      <div class="resource-actions">
        <span class="resource-status ${(d.last_status || "").startsWith("http_") ? "fetch_error" : d.last_status}">${statusEmoji(d.last_status)} ${statusLabel(d.last_status)}</span>
        <a class="btn-open" href="${escape(d.url)}" target="_blank" rel="noopener">Apri</a>
      </div>
    </div>
  `).join("");
}

// ============================================================
// EVENTS BINDING
// ============================================================

function bindEvents() {
  document.getElementById("search").addEventListener("input", e => { state.filters.search = e.target.value; renderResources(); });
  document.getElementById("filter-status").addEventListener("change", e => { state.filters.status = e.target.value; renderResources(); });
  document.getElementById("filter-type").addEventListener("change", e => { state.filters.type = e.target.value; renderResources(); });
  document.getElementById("filter-category").addEventListener("change", e => { state.filters.category = e.target.value; renderResources(); });
  document.getElementById("sort-by").addEventListener("change", e => { state.filters.sortBy = e.target.value; renderResources(); });
  document.getElementById("reset-filters").addEventListener("click", () => {
    state.filters = { search: "", status: "", type: "", category: "", sortBy: "last_modified" };
    document.getElementById("search").value = "";
    document.getElementById("filter-status").value = "";
    document.getElementById("filter-type").value = "";
    document.getElementById("filter-category").value = "";
    document.getElementById("sort-by").value = "last_modified";
    renderResources();
  });

  // Theme toggle
  document.getElementById("theme-toggle").addEventListener("click", toggleTheme);

  // Modal
  const modal = document.getElementById("about-modal");
  const openModal = () => modal.classList.remove("hidden");
  const closeModal = () => modal.classList.add("hidden");
  document.getElementById("about-link").addEventListener("click", e => { e.preventDefault(); openModal(); });
  document.getElementById("footer-about").addEventListener("click", e => { e.preventDefault(); openModal(); });
  document.getElementById("modal-close").addEventListener("click", closeModal);
  modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });

  // Tasto ESC chiude la modal
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });
}

// ============================================================
// MAIN
// ============================================================

(async function init() {
  initTheme();
  await loadData();
  populateCategoryFilter();
  bindEvents();
  renderStats();
  renderChart();
  renderChanges();
  renderResources();
})();
