/* ==============================================================
   Complaion - ACN Monitor - Dashboard v4
   Aggiunge il modal "Dettaglio variazione" con diff colorato.
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
// THEME
// ============================================================
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("acn-monitor-theme", theme);
  document.getElementById("theme-icon").textContent = theme === "dark" ? "☀️" : "🌙";
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
// FETCH
// ============================================================
async function loadData() {
  try {
    const [docsRes, changesRes] = await Promise.all([
      fetch(DOCS_URL, { cache: "no-store" }),
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
    return d.toLocaleString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}
function fmtRelative(iso) {
  if (!iso) return "—";
  const diffMs = Date.now() - new Date(iso).getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1)   return "ora";
  if (diffMin < 60)  return `${diffMin} min fa`;
  const h = Math.floor(diffMin / 60);
  if (h < 24)        return `${h} h fa`;
  const dd = Math.floor(h / 24);
  if (dd < 30)       return `${dd} g fa`;
  return `${Math.floor(dd / 30)} mesi fa`;
}
function fmtSize(b) {
  if (!b) return "—";
  if (b < 1024) return b + " B";
  if (b < 1024*1024) return (b/1024).toFixed(1) + " KB";
  return (b/1024/1024).toFixed(2) + " MB";
}
function escape(s) {
  return String(s || "").replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}
function statusEmoji(s) {
  if (!s) return "⚫";
  if (s.startsWith("http_") || s === "fetch_error") return "🔴";
  return ({ unchanged: "🟢", changed: "🟡", new: "🔵", stale: "⚪" }[s]) || "⚫";
}
function statusLabel(s) {
  if (!s) return "—";
  if (s.startsWith("http_")) return `HTTP ${s.split("_")[1]}`;
  return ({ unchanged: "Invariata", changed: "Modificata", new: "Nuova", fetch_error: "Errore", stale: "Non trovata" }[s]) || s;
}
function typeIcon(t) { return t === "pdf" ? "📕" : "📄"; }

// ============================================================
// STATS
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
// CHART
// ============================================================
function renderChart() {
  const canvas = document.getElementById("timeline-chart");
  if (!canvas) return;
  const days = 30;
  const labels = [];
  const counts = new Array(days).fill(0);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today); d.setDate(today.getDate() - i);
    labels.push(d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit" }));
  }
  (state.changes.events || []).forEach(ev => {
    const evDate = new Date(ev.timestamp); evDate.setHours(0, 0, 0, 0);
    const delta = Math.floor((today - evDate) / 86400000);
    if (delta >= 0 && delta < days) counts[days - 1 - delta]++;
  });

  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  const primary = isDark ? "#9AAF98" : "#092D0B";
  const accent = isDark ? "rgba(154,175,152,0.18)" : "rgba(9,45,11,0.10)";
  const grid = isDark ? "rgba(154,175,152,0.10)" : "rgba(9,45,11,0.08)";
  const txt = isDark ? "#B0C3AC" : "#4A5550";

  if (state.chartInstance) state.chartInstance.destroy();
  state.chartInstance = new Chart(canvas, {
    type: "line",
    data: { labels, datasets: [{
      label: "Variazioni", data: counts,
      borderColor: primary, backgroundColor: accent, borderWidth: 2.5,
      pointRadius: counts.map(c => c > 0 ? 5 : 0), pointHoverRadius: 7,
      pointBackgroundColor: primary, pointBorderColor: "#FFFFFF", pointBorderWidth: 2,
      tension: 0.35, fill: true,
    }]},
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: primary, titleColor: "#EBFFE5", bodyColor: "#EBFFE5",
          padding: 10, cornerRadius: 8, displayColors: false,
          callbacks: {
            title: items => items[0].label,
            label: item => `${item.parsed.y} variazion${item.parsed.y === 1 ? "e" : "i"}`
          }
        }
      },
      scales: {
        x: { grid: { color: grid }, ticks: { color: txt, font: { size: 11 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } },
        y: { beginAtZero: true, grid: { color: grid }, ticks: { color: txt, font: { size: 11 }, precision: 0 } }
      }
    }
  });
}

// ============================================================
// CHANGES LIST (con click → open diff modal)
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
  container.innerHTML = events.map((ev, idx) => `
    <div class="change-item status-${ev.status}" data-event-idx="${idx}">
      <div class="change-icon">${ev.status === "new" ? "🔵" : "🟡"}</div>
      <div class="change-body">
        <div class="change-title">${escape(ev.name)}</div>
        <div class="change-meta">
          ${ev.status === "new" ? "Nuova risorsa rilevata" : "Contenuto modificato"} ·
          ${typeIcon(ev.type)} ${ev.type === "pdf" ? "PDF" : "Pagina"}
          ${ev.diff ? ` · <strong>${escape(ev.diff.summary || "")}</strong>` : ""}
        </div>
      </div>
      <div class="change-time" title="${fmtDateTime(ev.timestamp)}">${fmtRelative(ev.timestamp)}</div>
      <a class="change-cta" href="${escape(ev.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation();">
        Apri risorsa
      </a>
    </div>
  `).join("");

  container.querySelectorAll(".change-item").forEach(el => {
    el.addEventListener("click", e => {
      if (e.target.closest(".change-cta")) return;
      const idx = parseInt(el.getAttribute("data-event-idx"), 10);
      const event = (state.changes.events || [])[idx];
      if (event) openDiffModal(event);
    });
  });
}

// ============================================================
// DIFF MODAL
// ============================================================
function openDiffModal(event) {
  const modal = document.getElementById("diff-modal");
  if (!modal) return;
  const body = document.getElementById("diff-modal-body");

  const titleIcon = event.status === "new" ? "🔵" : "🟡";
  const titleText = event.status === "new" ? "Nuova risorsa rilevata" : "Variazione rilevata";

  const diff = event.diff;
  let diffHtml = "";
  if (diff && diff.lines && diff.lines.length > 0) {
    diffHtml = `
      <div class="diff-body">
        ${diff.lines.map(line => {
          const cls = line.op === "+" ? "added" : line.op === "-" ? "removed" : "context";
          const prefix = line.op === "+" ? "+" : line.op === "-" ? "-" : " ";
          return `<div class="diff-line ${cls}"><span class="diff-prefix">${prefix}</span><span class="diff-text">${escape(line.text)}</span></div>`;
        }).join("")}
        ${diff.truncated ? '<div class="diff-truncated">… diff troncato. Visita la pagina ACN per il contenuto completo.</div>' : ''}
      </div>
    `;
  } else {
    diffHtml = '<div class="diff-empty">Non è disponibile un diff testuale per questa variazione (probabilmente è un PDF, oppure è la prima volta che la risorsa viene tracciata e non c\'è una versione precedente).</div>';
  }

  body.innerHTML = `
    <div class="diff-header">
      <h2>${titleIcon} ${titleText}</h2>
      <div style="font-weight:600;color:var(--primary);font-size:15px;margin-bottom:6px;">${escape(event.name)}</div>
      <div class="diff-url">${escape(event.url)}</div>
      <div class="diff-meta">
        <span class="badge cat">${escape(event.type === "pdf" ? "PDF" : "Pagina")}</span>
        <span>Rilevato: <strong>${fmtDateTime(event.timestamp)}</strong> (${fmtRelative(event.timestamp)})</span>
        ${diff ? `<span class="diff-summary"><span class="added">+${diff.added}</span> <span class="removed">-${diff.removed}</span></span>` : ''}
      </div>
      <div class="diff-actions">
        <a class="btn-primary" href="${escape(event.url)}" target="_blank" rel="noopener">↗ Apri risorsa ACN</a>
      </div>
    </div>
    ${diffHtml}
  `;

  modal.classList.remove("hidden");
}

function closeDiffModal() {
  const modal = document.getElementById("diff-modal");
  if (modal) modal.classList.add("hidden");
}

// ============================================================
// RESOURCES LIST
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
  if (f.type) items = items.filter(d => d.type === f.type);
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
// EVENTS
// ============================================================
function bindEvents() {
  document.getElementById("search").addEventListener("input", e => { state.filters.search = e.target.value; renderResources(); });
  document.getElementById("filter-status").addEventListener("change", e => { state.filters.status = e.target.value; renderResources(); });
  document.getElementById("filter-type").addEventListener("change", e => { state.filters.type = e.target.value; renderResources(); });
  document.getElementById("filter-category").addEventListener("change", e => { state.filters.category = e.target.value; renderResources(); });
  document.getElementById("sort-by").addEventListener("change", e => { state.filters.sortBy = e.target.value; renderResources(); });
  document.getElementById("reset-filters").addEventListener("click", () => {
    state.filters = { search: "", status: "", type: "", category: "", sortBy: "last_modified" };
    ["search", "filter-status", "filter-type", "filter-category", "sort-by"].forEach(id => document.getElementById(id).value = "");
    document.getElementById("sort-by").value = "last_modified";
    renderResources();
  });

  document.getElementById("theme-toggle").addEventListener("click", toggleTheme);

  // About modal
  const aboutModal = document.getElementById("about-modal");
  const openAbout = () => aboutModal.classList.remove("hidden");
  const closeAbout = () => aboutModal.classList.add("hidden");
  document.getElementById("about-link").addEventListener("click", e => { e.preventDefault(); openAbout(); });
  document.getElementById("footer-about").addEventListener("click", e => { e.preventDefault(); openAbout(); });
  document.getElementById("modal-close").addEventListener("click", closeAbout);
  aboutModal.addEventListener("click", e => { if (e.target === aboutModal) closeAbout(); });

  // Diff modal
  const diffModal = document.getElementById("diff-modal");
  if (diffModal) {
    document.getElementById("diff-modal-close").addEventListener("click", closeDiffModal);
    diffModal.addEventListener("click", e => { if (e.target === diffModal) closeDiffModal(); });
  }

  document.addEventListener("keydown", e => {
    if (e.key === "Escape") { closeAbout(); closeDiffModal(); }
  });
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
