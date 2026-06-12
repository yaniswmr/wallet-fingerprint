const state = {
  mode: "tx",          // "tx" | "fees"
  db: "gas",           // "gas" | "ledger" | "mew"
  page: 1,
  per_page: 50,
  wallet: "",          // gas only
  tier: "",            // ledger only
  tx_type: "",
  sort_by: "block",
  sort_dir: "desc",
  search: "",
  debounce: null,
};

// Server gas-fee explorer state (independent from the tx explorer)
const feesState = {
  wallet: "metamask",  // "metamask" | "okx" | "ambire" | "rabby"
  page: 1,
  per_page: 50,
  sort_by: "ts",
  sort_dir: "desc",
  search: "",
  columns: [],
};

const ETHERSCAN = "https://etherscan.io/tx/0x";

const WALLET_BADGE = {
  "MetaMask":    "badge-metamask",
  "OKX Wallet":  "badge-okx",
  "Trust Wallet":"badge-trust",
};

const TIER_BADGE = {
  "slow":    "badge-slow",
  "medium":  "badge-medium",
  "fast":    "badge-fast",
  "economy": "badge-slow",
  "regular": "badge-medium",
};

// tier-based DBs (share the Tier filter + tier badge styling)
const TIER_DBS = ["ledger", "mew"];
const isTierDb = db => TIER_DBS.includes(db);
const COL_COUNT = { gas: 13, ledger: 15, mew: 14 };
const tableIdFor = db => `${db}-table`;
const bodyIdFor  = db => `${db}-body`;

function walletBadge(w) {
  const cls = WALLET_BADGE[w] || "badge-default";
  return `<span class="badge ${cls}">${w || "—"}</span>`;
}

function tierBadge(t) {
  const cls = TIER_BADGE[t] || "badge-default";
  return `<span class="badge ${cls}">${t || "—"}</span>`;
}

function txTypePill(t) {
  if (t === null || t === undefined) return `<span class="null-val">—</span>`;
  const label = t === 0 ? "Legacy" : t === 1 ? "2930" : t === 2 ? "1559" : t;
  return `<span class="txtype txtype-${t}">EIP-${label}</span>`;
}

function fmt(val, decimals = 4) {
  if (val === null || val === undefined || val === "") return `<span class="null-val">—</span>`;
  const n = parseFloat(val);
  if (isNaN(n)) return `<span class="null-val">—</span>`;
  return n.toFixed(decimals);
}

function fmtInt(val) {
  if (val === null || val === undefined || val === "") return `<span class="null-val">—</span>`;
  return parseInt(val).toLocaleString();
}

function colorFactor(val) {
  if (val === null || val === undefined) return `<span class="null-val">—</span>`;
  const n = parseFloat(val);
  if (isNaN(n)) return `<span class="null-val">—</span>`;
  const cls = n > 1.5 ? "num-hi" : n > 1.1 ? "num-mid" : "num-lo";
  return `<span class="${cls}">${n.toFixed(4)}</span>`;
}

function shortHash(h) {
  if (!h) return "—";
  return h.slice(0, 8) + "…" + h.slice(-6);
}

function shortAddr(a) {
  if (!a) return "—";
  return a.slice(0, 6) + "…" + a.slice(-4);
}

// ── Stats / filters ──────────────────────────────────────────────────────────

async function loadStats() {
  if (state.db === "gas") {
    const res  = await fetch("/api/stats");
    const data = await res.json();

    renderStatsBar(data.total, data.wallets.map(w => `${w.wallet}: <b>${w.cnt.toLocaleString()}</b>`).join(" &nbsp;·&nbsp; "));

    const walletSel = document.getElementById("filter-wallet");
    walletSel.innerHTML = `<option value="">All</option>`;
    data.wallets.forEach(w => {
      const opt = document.createElement("option");
      opt.value = w.wallet;
      opt.textContent = `${w.wallet} (${w.cnt.toLocaleString()})`;
      walletSel.appendChild(opt);
    });
  } else {
    const res  = await fetch(`/api/${state.db}/stats`);
    const data = await res.json();

    renderStatsBar(data.total, data.tiers.map(t => `${t.tier}: <b>${t.cnt.toLocaleString()}</b>`).join(" &nbsp;·&nbsp; "));

    const tierSel = document.getElementById("filter-tier");
    tierSel.innerHTML = `<option value="">All</option>`;
    data.tiers.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t.tier;
      opt.textContent = `${t.tier} (${t.cnt.toLocaleString()})`;
      tierSel.appendChild(opt);
    });
  }

  await refreshTxTypeOptions();
}

function renderStatsBar(total, breakdown) {
  document.getElementById("stats-bar").innerHTML = `
    <div class="stat-item">
      <span class="stat-label">Total Txs</span>
      <span class="stat-value">${total.toLocaleString()}</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">${state.db === "gas" ? "Wallets" : "Tiers"}</span>
      <span class="stat-value" style="font-size:11px;color:var(--text-dim)">${breakdown}</span>
    </div>
  `;
}

async function refreshTxTypeOptions() {
  let url;
  if (state.db === "gas") {
    url = "/api/stats" + (state.wallet ? `?wallet=${encodeURIComponent(state.wallet)}` : "");
  } else {
    url = `/api/${state.db}/stats` + (state.tier ? `?tier=${encodeURIComponent(state.tier)}` : "");
  }

  const res  = await fetch(url);
  const data = await res.json();

  const txSel  = document.getElementById("filter-txtype");
  const current = txSel.value;
  txSel.innerHTML = `<option value="">All</option>`;
  data.tx_types.forEach(t => {
    const opt = document.createElement("option");
    opt.value = t.tx_type;
    opt.textContent = `Type ${t.tx_type} (${t.cnt.toLocaleString()})`;
    txSel.appendChild(opt);
  });
  const exists = data.tx_types.some(t => String(t.tx_type) === current);
  txSel.value = exists ? current : "";
  if (!exists) state.tx_type = "";
}

// ── Load table data ───────────────────────────────────────────────────────────

async function loadPage() {
  const params = new URLSearchParams({
    page: state.page,
    per_page: state.per_page,
    sort_by: state.sort_by,
    sort_dir: state.sort_dir,
  });
  if (state.tx_type !== "") params.set("tx_type", state.tx_type);
  if (state.search)         params.set("search",   state.search);

  const colCount = COL_COUNT[state.db];
  const tbody    = document.getElementById(bodyIdFor(state.db));
  tbody.innerHTML = `<tr><td colspan="${colCount}" class="loading">Loading…</td></tr>`;

  let endpoint;
  if (state.db === "gas") {
    if (state.wallet) params.set("wallet", state.wallet);
    endpoint = "/api/transactions";
  } else {
    if (state.tier) params.set("tier", state.tier);
    endpoint = `/api/${state.db}/transactions`;
  }

  const res  = await fetch(`${endpoint}?${params}`);
  const data = await res.json();

  const from = ((state.page - 1) * state.per_page + 1).toLocaleString();
  const to   = Math.min(state.page * state.per_page, data.total).toLocaleString();
  document.getElementById("result-count").innerHTML =
    `Showing <span>${from}–${to}</span> of <span>${data.total.toLocaleString()}</span> transactions`;

  const offset = (state.page - 1) * state.per_page;

  if (state.db === "gas") {
    tbody.innerHTML = data.rows.map((r, i) => `
      <tr>
        <td class="row-num">${offset + i + 1}</td>
        <td>${hashCell(r.hash)}</td>
        <td>${r.block ? r.block.toLocaleString() : "—"}</td>
        <td>${walletBadge(r.wallet)}</td>
        <td>${txTypePill(r.tx_type)}</td>
        <td><span class="addr" title="${r.from_addr || ""}">${shortAddr(r.from_addr)}</span></td>
        <td class="num">${fmt(r.max_fee_gwei)}</td>
        <td class="num">${fmt(r.max_priority_gwei)}</td>
        <td class="num">${fmt(r.base_fee_gwei)}</td>
        <td class="num">${colorFactor(r.fee_factor)}</td>
        <td class="num">${colorFactor(r.fee_factor_parent)}</td>
        <td class="num">${fmtInt(r.gas_limit)}</td>
        <td class="num">${fmtInt(r.estimated_gas)}</td>
        <td class="num">${colorFactor(r.gas_limit_factor)}</td>
      </tr>
    `).join("");
  } else if (state.db === "ledger") {
    tbody.innerHTML = data.rows.map((r, i) => `
      <tr>
        <td class="row-num">${offset + i + 1}</td>
        <td>${hashCell(r.hash)}</td>
        <td>${r.block ? r.block.toLocaleString() : "—"}</td>
        <td>${tierBadge(r.tier)}</td>
        <td>${txTypePill(r.tx_type)}</td>
        <td><span class="addr" title="${r.from_addr || ""}">${shortAddr(r.from_addr)}</span></td>
        <td class="num">${fmt(r.max_fee_gwei)}</td>
        <td class="num">${fmt(r.max_priority_gwei)}</td>
        <td class="num">${fmt(r.base_fee_gwei)}</td>
        <td class="num">${colorFactor(r.fee_factor)}</td>
        <td class="num">${fmt(r.ledger_slow)}</td>
        <td class="num">${fmt(r.ledger_medium)}</td>
        <td class="num">${fmt(r.ledger_fast)}</td>
        <td class="num">${fmtInt(r.gas_limit)}</td>
        <td class="num">${fmtInt(r.estimated_gas)}</td>
        <td class="num">${colorFactor(r.gas_limit_factor)}</td>
      </tr>
    `).join("");
  } else {
    tbody.innerHTML = data.rows.map((r, i) => `
      <tr>
        <td class="row-num">${offset + i + 1}</td>
        <td>${hashCell(r.hash)}</td>
        <td>${r.block ? r.block.toLocaleString() : "—"}</td>
        <td>${tierBadge(r.tier)}</td>
        <td>${txTypePill(r.tx_type)}</td>
        <td><span class="addr" title="${r.from_addr || ""}">${shortAddr(r.from_addr)}</span></td>
        <td class="num">${fmt(r.max_fee_gwei)}</td>
        <td class="num">${fmt(r.max_priority_gwei)}</td>
        <td class="num">${fmt(r.base_fee_gwei)}</td>
        <td class="num">${colorFactor(r.fee_factor)}</td>
        <td class="num">${fmt(r.raw_priority_gwei)}</td>
        <td class="num">${fmtInt(r.gas_limit)}</td>
        <td class="num">${fmtInt(r.estimated_gas)}</td>
        <td class="num">${colorFactor(r.gas_limit_factor)}</td>
      </tr>
    `).join("");
  }

  renderPagination(data.page, data.pages);
}

function hashCell(hash) {
  return `
    <a class="tx-hash" href="${ETHERSCAN}${hash}" target="_blank" rel="noopener" title="0x${hash}">
      0x${shortHash(hash)}
    </a>
    <button class="copy-btn" onclick="copyHash('${hash}')" title="Copy full hash">⧉</button>
  `;
}

// ── Server gas fees (suggestions collected from wallet APIs) ───────────────────

function fmtFeeCell(value, type) {
  if (value === null || value === undefined || value === "")
    return `<span class="null-val">—</span>`;
  switch (type) {
    case "datetime": {
      const d = new Date(parseInt(value) * 1000);
      return `<span title="${value}">${d.toLocaleString()}</span>`;
    }
    case "int":
      return parseInt(value).toLocaleString();
    case "gwei": {                                  // already gwei
      const n = parseFloat(value);
      return isNaN(n) ? `<span class="null-val">—</span>` : n.toFixed(4);
    }
    case "wei_gwei": {                              // wei → gwei
      const n = parseFloat(value) / 1e9;
      return isNaN(n) ? `<span class="null-val">—</span>` : n.toFixed(4);
    }
    case "mult":
      return colorFactor(value);
    case "ratio": {
      const n = parseFloat(value);
      return isNaN(n) ? `<span class="null-val">—</span>` : n.toFixed(3);
    }
    case "flag":
      return parseInt(value)
        ? `<span class="badge badge-fast">yes</span>`
        : `<span class="badge badge-slow">no</span>`;
    case "text": {
      const t = String(value);
      const cls = t === "up" ? "num-hi" : t === "down" ? "num-lo" : "";
      return `<span class="${cls}">${t}</span>`;
    }
    default:
      return value;
  }
}

async function loadFeesStats() {
  const res  = await fetch(`/api/fees/${feesState.wallet}/stats`);
  const data = await res.json();
  const range = (data.block_min && data.block_max)
    ? `${data.block_min.toLocaleString()} → ${data.block_max.toLocaleString()}`
    : "—";
  document.getElementById("stats-bar").innerHTML = `
    <div class="stat-item">
      <span class="stat-label">Samples</span>
      <span class="stat-value">${data.total.toLocaleString()}</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Block range</span>
      <span class="stat-value" style="font-size:11px;color:var(--text-dim)">${range}</span>
    </div>
  `;
}

async function loadFees() {
  const params = new URLSearchParams({
    page: feesState.page,
    per_page: feesState.per_page,
    sort_by: feesState.sort_by,
    sort_dir: feesState.sort_dir,
  });
  if (feesState.search) params.set("search", feesState.search);

  const head = document.getElementById("fees-head");
  const body = document.getElementById("fees-body");
  body.innerHTML = `<tr><td colspan="${(feesState.columns.length || 12) + 1}" class="loading">Loading…</td></tr>`;

  const res  = await fetch(`/api/fees/${feesState.wallet}?${params}`);
  const data = await res.json();
  feesState.columns = data.columns;

  head.innerHTML = `<th class="col-num">#</th>` + data.columns.map(c =>
    `<th class="sortable fees-sort ${feesState.sort_by === c.key ? "active " + feesState.sort_dir : ""}" data-col="${c.key}">${c.label}</th>`
  ).join("");

  const offset = (data.page - 1) * feesState.per_page;
  body.innerHTML = data.rows.length
    ? data.rows.map((r, i) => `
        <tr>
          <td class="row-num">${offset + i + 1}</td>
          ${data.columns.map(c => `<td class="num">${fmtFeeCell(r[c.key], c.type)}</td>`).join("")}
        </tr>`).join("")
    : `<tr><td colspan="${data.columns.length + 1}" class="loading">No data</td></tr>`;

  const from = (offset + 1).toLocaleString();
  const to   = Math.min(data.page * feesState.per_page, data.total).toLocaleString();
  document.getElementById("result-count").innerHTML =
    `Showing <span>${from}–${to}</span> of <span>${data.total.toLocaleString()}</span> samples`;

  renderPagination(data.page, data.pages);
}

// ── Mode switch (Transactions ⇄ Server Gas Fees) ───────────────────────────────

function switchMode(mode) {
  state.mode = mode;
  const feesMode = mode === "fees";

  document.querySelectorAll(".mode-tab").forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
  document.getElementById("tx-tabs").classList.toggle("hidden", feesMode);
  document.getElementById("fees-tabs").classList.toggle("hidden", !feesMode);

  // tx-only filters
  document.getElementById("group-wallet").classList.toggle("hidden", feesMode || state.db !== "gas");
  document.getElementById("group-tier").classList.toggle("hidden", feesMode || !isTierDb(state.db));
  document.getElementById("group-txtype").classList.toggle("hidden", feesMode);

  // tables
  ["gas", "ledger", "mew"].forEach(d =>
    document.getElementById(tableIdFor(d)).classList.toggle("hidden", feesMode || d !== state.db));
  document.getElementById("fees-table").classList.toggle("hidden", !feesMode);

  // shared controls
  const searchEl = document.getElementById("search");
  searchEl.value = "";
  searchEl.placeholder = feesMode ? "Search block number…" : "Search hash or address…";
  document.getElementById("per-page").value = "50";

  if (feesMode) {
    feesState.page = 1; feesState.per_page = 50; feesState.search = "";
    feesState.sort_by = "ts"; feesState.sort_dir = "desc";
    document.querySelectorAll(".fees-tab").forEach(b => b.classList.toggle("active", b.dataset.fees === feesState.wallet));
    loadFeesStats();
    loadFees();
  } else {
    switchDb(state.db);   // fully restore the tx view
  }
}

// ── Pagination ────────────────────────────────────────────────────────────────

function renderPagination(current, total) {
  const el = document.getElementById("pagination");
  if (total <= 1) { el.innerHTML = ""; return; }

  const range = [];
  const delta = 2;
  for (let i = Math.max(2, current - delta); i <= Math.min(total - 1, current + delta); i++) range.push(i);
  if (range[0] - 2 > 1)  range.unshift("...");
  if (range[0] !== 2)     range.unshift(2);
  range.unshift(1);
  if (total - range[range.length - 1] > 2) range.push("...");
  if (range[range.length - 1] !== total)   range.push(total);

  const btn = (label, page, disabled = false, active = false) =>
    `<button ${disabled ? "disabled" : ""} ${active ? 'class="active"' : ""} ${page ? `data-page="${page}"` : ""}>${label}</button>`;

  el.innerHTML =
    btn("‹ Prev", current - 1, current === 1) +
    range.map(p => p === "..." ? `<span class="ellipsis">…</span>` : btn(p, p, false, p === current)).join("") +
    btn("Next ›", current + 1, current === total);

  el.querySelectorAll("button[data-page]").forEach(b => {
    b.addEventListener("click", () => {
      const p = parseInt(b.dataset.page);
      if (state.mode === "fees") { feesState.page = p; loadFees(); }
      else                       { state.page = p;    loadPage(); }
    });
  });
}

function copyHash(hash) {
  navigator.clipboard.writeText("0x" + hash);
}

// ── DB tab switch ─────────────────────────────────────────────────────────────

function switchDb(db) {
  state.db      = db;
  state.page    = 1;
  state.wallet  = "";
  state.tier    = "";
  state.tx_type = "";
  state.search  = "";
  state.sort_by  = "block";
  state.sort_dir = "desc";

  document.getElementById("search").value = "";
  document.getElementById("filter-wallet").value = "";
  document.getElementById("filter-tier").value   = "";

  // swap visible filter
  document.getElementById("group-wallet").classList.toggle("hidden", db !== "gas");
  document.getElementById("group-tier").classList.toggle("hidden",   !isTierDb(db));

  // swap visible table — hide all, show current
  ["gas", "ledger", "mew"].forEach(d => {
    document.getElementById(tableIdFor(d)).classList.toggle("hidden", d !== db);
  });
  document.getElementById("fees-table").classList.add("hidden");

  // reset sort headers on active table
  const activeTable = tableIdFor(db);
  document.querySelectorAll(`#${activeTable} th`).forEach(t => t.classList.remove("active", "asc", "desc"));
  document.querySelector(`#${activeTable} th[data-col='block']`).classList.add("active", "desc");

  // update tab styles
  document.querySelectorAll(".db-tab").forEach(b => b.classList.toggle("active", b.dataset.db === db));

  loadStats();
  loadPage();
}

// ── Events ────────────────────────────────────────────────────────────────────

document.querySelectorAll(".db-tab:not(.fees-tab)").forEach(btn => {
  btn.addEventListener("click", () => switchDb(btn.dataset.db));
});

// Mode toggle
document.querySelectorAll(".mode-tab").forEach(btn => {
  btn.addEventListener("click", () => switchMode(btn.dataset.mode));
});

// Fees wallet tabs
document.querySelectorAll(".fees-tab").forEach(btn => {
  btn.addEventListener("click", () => {
    feesState.wallet   = btn.dataset.fees;
    feesState.page     = 1;
    feesState.search   = "";
    feesState.sort_by  = "ts";
    feesState.sort_dir = "desc";
    document.getElementById("search").value = "";
    document.querySelectorAll(".fees-tab").forEach(b => b.classList.toggle("active", b === btn));
    loadFeesStats();
    loadFees();
  });
});

// Fees sort — delegated (headers are rebuilt on every load)
document.getElementById("fees-head").addEventListener("click", e => {
  const th = e.target.closest("th.fees-sort");
  if (!th) return;
  const col = th.dataset.col;
  if (feesState.sort_by === col) {
    feesState.sort_dir = feesState.sort_dir === "desc" ? "asc" : "desc";
  } else {
    feesState.sort_by  = col;
    feesState.sort_dir = "desc";
  }
  feesState.page = 1;
  loadFees();
});

// Sort — delegate on both tables
document.querySelectorAll("th.sortable").forEach(th => {
  th.addEventListener("click", () => {
    const col = th.dataset.col;
    if (state.sort_by === col) {
      state.sort_dir = state.sort_dir === "desc" ? "asc" : "desc";
    } else {
      state.sort_by  = col;
      state.sort_dir = "desc";
    }
    const tableId = tableIdFor(state.db);
    document.querySelectorAll(`#${tableId} th`).forEach(t => t.classList.remove("active", "asc", "desc"));
    th.classList.add("active", state.sort_dir);
    state.page = 1;
    loadPage();
  });
});

document.getElementById("filter-wallet").addEventListener("change", async e => {
  state.wallet  = e.target.value;
  state.tx_type = "";
  state.page    = 1;
  await refreshTxTypeOptions();
  loadPage();
});

document.getElementById("filter-tier").addEventListener("change", async e => {
  state.tier    = e.target.value;
  state.tx_type = "";
  state.page    = 1;
  await refreshTxTypeOptions();
  loadPage();
});

document.getElementById("filter-txtype").addEventListener("change", e => {
  state.tx_type = e.target.value;
  state.page    = 1;
  loadPage();
});

document.getElementById("per-page").addEventListener("change", e => {
  const v = parseInt(e.target.value);
  if (state.mode === "fees") {
    feesState.per_page = v; feesState.page = 1; loadFees();
  } else {
    state.per_page = v; state.page = 1; loadPage();
  }
});

document.getElementById("search").addEventListener("input", e => {
  clearTimeout(state.debounce);
  state.debounce = setTimeout(() => {
    const val = e.target.value.trim();
    if (state.mode === "fees") {
      feesState.search = val; feesState.page = 1; loadFees();
    } else {
      state.search = val; state.page = 1; loadPage();
    }
  }, 350);
});

document.getElementById("btn-reset").addEventListener("click", async () => {
  if (state.mode === "fees") {
    feesState.page     = 1;
    feesState.search   = "";
    feesState.sort_by  = "ts";
    feesState.sort_dir = "desc";
    feesState.per_page = 50;
    document.getElementById("search").value   = "";
    document.getElementById("per-page").value = "50";
    loadFees();
    return;
  }
  state.page    = 1;
  state.wallet  = "";
  state.tier    = "";
  state.tx_type = "";
  state.search  = "";
  state.sort_by  = "block";
  state.sort_dir = "desc";
  document.getElementById("filter-wallet").value = "";
  document.getElementById("filter-tier").value   = "";
  document.getElementById("search").value        = "";
  const tableId = tableIdFor(state.db);
  document.querySelectorAll(`#${tableId} th`).forEach(t => t.classList.remove("active", "asc", "desc"));
  document.querySelector(`#${tableId} th[data-col='block']`).classList.add("active", "desc");
  await refreshTxTypeOptions();
  loadPage();
});

// ── Init ──────────────────────────────────────────────────────────────────────
loadStats();
loadPage();
