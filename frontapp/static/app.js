const state = {
  db: "gas",           // "gas" | "ledger"
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

const ETHERSCAN = "https://etherscan.io/tx/0x";

const WALLET_BADGE = {
  "MetaMask":    "badge-metamask",
  "OKX Wallet":  "badge-okx",
  "Trust Wallet":"badge-trust",
};

const TIER_BADGE = {
  "slow":   "badge-slow",
  "medium": "badge-medium",
  "fast":   "badge-fast",
};

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
    const res  = await fetch("/api/ledger/stats");
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
    url = "/api/ledger/stats" + (state.tier ? `?tier=${encodeURIComponent(state.tier)}` : "");
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

  const colCount = state.db === "gas" ? 13 : 15;
  const bodyId   = state.db === "gas" ? "gas-body" : "ledger-body";
  const tbody    = document.getElementById(bodyId);
  tbody.innerHTML = `<tr><td colspan="${colCount}" class="loading">Loading…</td></tr>`;

  let endpoint;
  if (state.db === "gas") {
    if (state.wallet) params.set("wallet", state.wallet);
    endpoint = "/api/transactions";
  } else {
    if (state.tier) params.set("tier", state.tier);
    endpoint = "/api/ledger/transactions";
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
        <td class="num">${fmt(r.ledger_slow)}</td>
        <td class="num">${fmt(r.ledger_medium)}</td>
        <td class="num">${fmt(r.ledger_fast)}</td>
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
      state.page = parseInt(b.dataset.page);
      loadPage();
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
  document.getElementById("group-tier").classList.toggle("hidden",   db !== "ledger");

  // swap visible table
  document.getElementById("gas-table").classList.toggle("hidden",    db !== "gas");
  document.getElementById("ledger-table").classList.toggle("hidden", db !== "ledger");

  // reset sort headers on active table
  const activeTable = db === "gas" ? "gas-table" : "ledger-table";
  document.querySelectorAll(`#${activeTable} th`).forEach(t => t.classList.remove("active", "asc", "desc"));
  document.querySelector(`#${activeTable} th[data-col='block']`).classList.add("active", "desc");

  // update tab styles
  document.querySelectorAll(".db-tab").forEach(b => b.classList.toggle("active", b.dataset.db === db));

  loadStats();
  loadPage();
}

// ── Events ────────────────────────────────────────────────────────────────────

document.querySelectorAll(".db-tab").forEach(btn => {
  btn.addEventListener("click", () => switchDb(btn.dataset.db));
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
    const tableId = state.db === "gas" ? "gas-table" : "ledger-table";
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
  state.per_page = parseInt(e.target.value);
  state.page     = 1;
  loadPage();
});

document.getElementById("search").addEventListener("input", e => {
  clearTimeout(state.debounce);
  state.debounce = setTimeout(() => {
    state.search = e.target.value.trim();
    state.page   = 1;
    loadPage();
  }, 350);
});

document.getElementById("btn-reset").addEventListener("click", async () => {
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
  const tableId = state.db === "gas" ? "gas-table" : "ledger-table";
  document.querySelectorAll(`#${tableId} th`).forEach(t => t.classList.remove("active", "asc", "desc"));
  document.querySelector(`#${tableId} th[data-col='block']`).classList.add("active", "desc");
  await refreshTxTypeOptions();
  loadPage();
});

// ── Init ──────────────────────────────────────────────────────────────────────
loadStats();
loadPage();
