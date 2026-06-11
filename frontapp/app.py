import sqlite3
import os
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

GAS_DB    = os.path.join(os.path.dirname(__file__), "../gas.db")
LEDGER_DB = os.path.join(os.path.dirname(__file__), "../ledger.db")
MEW_DB    = os.path.join(os.path.dirname(__file__), "../mew.db")

def get_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

def build_where(clauses, params):
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""

@app.route("/")
def index():
    return render_template("index.html")

# ── GAS ──────────────────────────────────────────────────────────────────────

@app.route("/api/transactions")
def transactions():
    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    wallet   = request.args.get("wallet", "")
    tx_type  = request.args.get("tx_type", "")
    sort_by  = request.args.get("sort_by", "block")
    sort_dir = request.args.get("sort_dir", "desc")
    search   = request.args.get("search", "")

    allowed = {
        "block", "from_addr", "wallet", "max_fee_gwei", "max_priority_gwei",
        "base_fee_gwei", "fee_factor", "fee_factor_parent",
        "gas_limit", "estimated_gas", "gas_limit_factor", "tx_type"
    }
    if sort_by not in allowed: sort_by = "block"
    if sort_dir not in ("asc", "desc"): sort_dir = "desc"

    clauses, params = [], []
    if wallet:
        clauses.append("wallet = ?"); params.append(wallet)
    if tx_type != "":
        clauses.append("tx_type = ?"); params.append(int(tx_type))
    if search:
        clauses.append("(hash LIKE ? OR from_addr LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = build_where(clauses, params)
    offset = (page - 1) * per_page
    conn = get_db(GAS_DB)
    total = conn.execute(f"SELECT COUNT(*) FROM transactions {where}", params).fetchone()[0]
    rows  = conn.execute(
        f"""SELECT hash, block, from_addr, wallet, max_fee_gwei, max_priority_gwei,
               base_fee_gwei, fee_factor, fee_factor_parent,
               gas_limit, estimated_gas, gas_limit_factor, tx_type
            FROM transactions {where}
            ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?""",
        params + [per_page, offset]
    ).fetchall()
    conn.close()
    return jsonify({
        "total": total, "page": page, "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "rows": [dict(r) for r in rows]
    })

@app.route("/api/stats")
def stats():
    wallet = request.args.get("wallet", "")
    conn = get_db(GAS_DB)
    total   = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    wallets = conn.execute(
        "SELECT wallet, COUNT(*) as cnt FROM transactions GROUP BY wallet ORDER BY cnt DESC"
    ).fetchall()
    if wallet:
        tx_types = conn.execute(
            "SELECT tx_type, COUNT(*) as cnt FROM transactions WHERE wallet = ? GROUP BY tx_type ORDER BY tx_type",
            [wallet]
        ).fetchall()
    else:
        tx_types = conn.execute(
            "SELECT tx_type, COUNT(*) as cnt FROM transactions GROUP BY tx_type ORDER BY tx_type"
        ).fetchall()
    conn.close()
    return jsonify({
        "total": total,
        "wallets": [dict(r) for r in wallets],
        "tx_types": [dict(r) for r in tx_types]
    })

# ── LEDGER ───────────────────────────────────────────────────────────────────

@app.route("/api/ledger/transactions")
def ledger_transactions():
    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    tier     = request.args.get("tier", "")
    tx_type  = request.args.get("tx_type", "")
    sort_by  = request.args.get("sort_by", "block")
    sort_dir = request.args.get("sort_dir", "desc")
    search   = request.args.get("search", "")

    allowed = {
        "block", "from_addr", "tier", "max_fee_gwei", "max_priority_gwei",
        "base_fee_gwei", "fee_factor", "ledger_slow", "ledger_medium", "ledger_fast",
        "gas_limit", "estimated_gas", "gas_limit_factor", "tx_type"
    }
    if sort_by not in allowed: sort_by = "block"
    if sort_dir not in ("asc", "desc"): sort_dir = "desc"

    clauses, params = [], []
    if tier:
        clauses.append("tier = ?"); params.append(tier)
    if tx_type != "":
        clauses.append("tx_type = ?"); params.append(int(tx_type))
    if search:
        clauses.append("(hash LIKE ? OR from_addr LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = build_where(clauses, params)
    offset = (page - 1) * per_page
    conn = get_db(LEDGER_DB)
    total = conn.execute(f"SELECT COUNT(*) FROM transactions {where}", params).fetchone()[0]
    rows  = conn.execute(
        f"""SELECT hash, block, from_addr, tier, max_fee_gwei, max_priority_gwei,
               base_fee_gwei, fee_factor, ledger_slow, ledger_medium, ledger_fast,
               gas_limit, estimated_gas, gas_limit_factor, tx_type
            FROM transactions {where}
            ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?""",
        params + [per_page, offset]
    ).fetchall()
    conn.close()
    return jsonify({
        "total": total, "page": page, "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "rows": [dict(r) for r in rows]
    })

@app.route("/api/ledger/stats")
def ledger_stats():
    tier = request.args.get("tier", "")
    conn = get_db(LEDGER_DB)
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    tiers = conn.execute(
        "SELECT tier, COUNT(*) as cnt FROM transactions GROUP BY tier ORDER BY cnt DESC"
    ).fetchall()
    if tier:
        tx_types = conn.execute(
            "SELECT tx_type, COUNT(*) as cnt FROM transactions WHERE tier = ? GROUP BY tx_type ORDER BY tx_type",
            [tier]
        ).fetchall()
    else:
        tx_types = conn.execute(
            "SELECT tx_type, COUNT(*) as cnt FROM transactions GROUP BY tx_type ORDER BY tx_type"
        ).fetchall()
    conn.close()
    return jsonify({
        "total": total,
        "tiers": [dict(r) for r in tiers],
        "tx_types": [dict(r) for r in tx_types]
    })

# ── MEW ──────────────────────────────────────────────────────────────────────

@app.route("/api/mew/transactions")
def mew_transactions():
    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    tier     = request.args.get("tier", "")
    tx_type  = request.args.get("tx_type", "")
    sort_by  = request.args.get("sort_by", "block")
    sort_dir = request.args.get("sort_dir", "desc")
    search   = request.args.get("search", "")

    allowed = {
        "block", "from_addr", "tier", "max_fee_gwei", "max_priority_gwei",
        "base_fee_gwei", "fee_factor", "raw_priority_gwei",
        "gas_limit", "estimated_gas", "gas_limit_factor", "tx_type"
    }
    if sort_by not in allowed: sort_by = "block"
    if sort_dir not in ("asc", "desc"): sort_dir = "desc"

    clauses, params = [], []
    if tier:
        clauses.append("tier = ?"); params.append(tier)
    if tx_type != "":
        clauses.append("tx_type = ?"); params.append(int(tx_type))
    if search:
        clauses.append("(hash LIKE ? OR from_addr LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = build_where(clauses, params)
    offset = (page - 1) * per_page
    conn = get_db(MEW_DB)
    total = conn.execute(f"SELECT COUNT(*) FROM transactions {where}", params).fetchone()[0]
    rows  = conn.execute(
        f"""SELECT hash, block, from_addr, tier, max_fee_gwei, max_priority_gwei,
               base_fee_gwei, fee_factor, raw_priority_gwei,
               gas_limit, estimated_gas, gas_limit_factor, tx_type
            FROM transactions {where}
            ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?""",
        params + [per_page, offset]
    ).fetchall()
    conn.close()
    return jsonify({
        "total": total, "page": page, "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "rows": [dict(r) for r in rows]
    })

@app.route("/api/mew/stats")
def mew_stats():
    tier = request.args.get("tier", "")
    conn = get_db(MEW_DB)
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    tiers = conn.execute(
        "SELECT tier, COUNT(*) as cnt FROM transactions GROUP BY tier ORDER BY cnt DESC"
    ).fetchall()
    if tier:
        tx_types = conn.execute(
            "SELECT tx_type, COUNT(*) as cnt FROM transactions WHERE tier = ? GROUP BY tx_type ORDER BY tx_type",
            [tier]
        ).fetchall()
    else:
        tx_types = conn.execute(
            "SELECT tx_type, COUNT(*) as cnt FROM transactions GROUP BY tx_type ORDER BY tx_type"
        ).fetchall()
    conn.close()
    return jsonify({
        "total": total,
        "tiers": [dict(r) for r in tiers],
        "tx_types": [dict(r) for r in tx_types]
    })

if __name__ == "__main__":
    app.run(debug=True, port=5050)
