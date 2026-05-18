#!/usr/bin/env python3
"""
MetaMask Gas API — Local Simulation Server

Reproduces the MetaMask suggestedGasFees endpoint using eth_feeHistory:
  - last 5 blocks, percentile 5  → maxPriorityFeePerGas for "low"
  - last 5 blocks, percentile 90 → maxPriorityFeePerGas for "medium" and "high"
  - baseFee = baseFeePerGas of next predicted block (last element of feeHistory)
  - maxFeePerGas = baseFee * factor + maxPriorityFeePerGas
      factor = 1.00 for low
      factor = 1.43 for medium and high

Usage:
  python gas_api_server.py --rpc <ETH_RPC_URL>
  python gas_api_server.py --rpc <ETH_RPC_URL> --port 8080

Endpoints:
  GET /                        → usage info
  GET /suggestedGasFees        → MetaMask-compatible JSON response
  GET /health                  → {"status": "ok"}
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from web3 import Web3

N_BLOCKS = 5
PERCENTILE_LOW = 5
PERCENTILE_MED_HIGH = 90
FACTOR_LOW = 1.00
FACTOR_MED_HIGH = 1.43

FIXED_PRIORITY_LOW      = 0.0001  # Gwei
FIXED_PRIORITY_MED_HIGH = 2.0     # Gwei


def _get_base_fee(w3: Web3) -> float:
    """Return baseFeePerGas of the next predicted block in Gwei."""
    block = w3.eth.get_block("latest")
    # EIP-1559: next baseFee ≈ current baseFee (close enough for fixed mode)
    return block["baseFeePerGas"] / 1e9


def compute_gas_fees(w3: Web3, mode: str) -> dict:
    if mode == "fixed":
        base_fee_gwei     = _get_base_fee(w3)
        priority_low      = FIXED_PRIORITY_LOW
        priority_med_high = FIXED_PRIORITY_MED_HIGH
        meta = {
            "mode":                  "fixed",
            "fixed_priority_low":    FIXED_PRIORITY_LOW,
            "fixed_priority_med_high": FIXED_PRIORITY_MED_HIGH,
            "factor_low":            FACTOR_LOW,
            "factor_med_high":       FACTOR_MED_HIGH,
        }
    else:
        result = w3.eth.fee_history(N_BLOCKS, "latest", [PERCENTILE_LOW, PERCENTILE_MED_HIGH])
        base_fee_gwei = result["baseFeePerGas"][-1] / 1e9
        p5_tips  = [blk[0] / 1e9 for blk in result["reward"]]
        p90_tips = [blk[1] / 1e9 for blk in result["reward"]]
        priority_low      = sum(p5_tips)  / len(p5_tips)
        priority_med_high = sum(p90_tips) / len(p90_tips)
        meta = {
            "mode":                  "predict",
            "n_blocks":              N_BLOCKS,
            "percentile_low":        PERCENTILE_LOW,
            "percentile_med_high":   PERCENTILE_MED_HIGH,
            "factor_low":            FACTOR_LOW,
            "factor_med_high":       FACTOR_MED_HIGH,
            "p5_tips_gwei":          [round(x, 6) for x in p5_tips],
            "p90_tips_gwei":         [round(x, 6) for x in p90_tips],
        }

    max_fee_low      = base_fee_gwei * FACTOR_LOW      + priority_low
    max_fee_med_high = base_fee_gwei * FACTOR_MED_HIGH + priority_med_high

    return {
        "low": {
            "suggestedMaxPriorityFeePerGas": f"{priority_low:.6f}",
            "suggestedMaxFeePerGas":         f"{max_fee_low:.6f}",
        },
        "medium": {
            "suggestedMaxPriorityFeePerGas": f"{priority_med_high:.6f}",
            "suggestedMaxFeePerGas":         f"{max_fee_med_high:.6f}",
        },
        "high": {
            "suggestedMaxPriorityFeePerGas": f"{priority_med_high:.6f}",
            "suggestedMaxFeePerGas":         f"{max_fee_med_high:.6f}",
        },
        "estimatedBaseFee": f"{base_fee_gwei:.6f}",
        "_meta": meta,
    }


def make_handler(w3: Web3, mode: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path

            if path in ("/", ""):
                self._send_json(200, {
                    "description": "MetaMask Gas API simulation",
                    "mode": mode,
                    "endpoints": {
                        "GET /suggestedGasFees": "Computed gas fees (low/medium/high)",
                        "GET /health":           "Health check",
                    },
                })

            elif path == "/suggestedGasFees":
                try:
                    fees = compute_gas_fees(w3, mode)
                    self._send_json(200, fees)
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})

            elif path == "/health":
                connected = w3.is_connected()
                self._send_json(200 if connected else 503, {
                    "status": "ok" if connected else "rpc_unreachable",
                    "latest_block": w3.eth.block_number if connected else None,
                })

            else:
                self._send_json(404, {"error": "not found"})

        def _send_json(self, code: int, body: dict):
            payload = json.dumps(body, indent=2).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):
            print(f"[{self.log_date_time_string()}] {fmt % args}")

    return Handler


def main():
    p = argparse.ArgumentParser(
        description="Local MetaMask gas fee API server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--rpc",  default=os.environ.get("ETH_RPC_URL"), required=not os.environ.get("ETH_RPC_URL"), help="Ethereum JSON-RPC endpoint URL")
    p.add_argument("--port", type=int, default=3020, help="HTTP port to listen on (default 3020)")
    p.add_argument("--host", default="127.0.0.1", help="Host to bind to (default 127.0.0.1)")
    p.add_argument(
        "--mode",
        choices=["predict", "fixed"],
        default="predict",
        help=(
            "predict: compute maxPriorityFeePerGas from eth_feeHistory (percentile 5/90, last 5 blocks); "
            f"fixed: use constants ({FIXED_PRIORITY_LOW} Gwei low, {FIXED_PRIORITY_MED_HIGH} Gwei med/high)"
        ),
    )
    args = p.parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        sys.exit("ERROR: Cannot connect to Ethereum node. Check --rpc.")
    print(f"Connected to RPC. Latest block: {w3.eth.block_number}")
    print(f"Mode: {args.mode}")

    server = HTTPServer((args.host, args.port), make_handler(w3, args.mode))
    print(f"Server running on http://{args.host}:{args.port}")
    print(f"  GET http://{args.host}:{args.port}/suggestedGasFees")
    print("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()