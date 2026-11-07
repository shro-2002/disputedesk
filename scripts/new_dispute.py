"""Simulate the payment processor's chargeback webhook: `make demo CASE=C` -> POST to @evidence."""

import sys

import httpx

WEBHOOK = "http://127.0.0.1:8098/webhook/disputes"


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isalnum():
        print("usage: python -m scripts.new_dispute <CASE>   e.g. A, B, C, D, E")
        return 2
    dispute_id = f"DSP-{sys.argv[1].upper()}"
    try:
        resp = httpx.post(WEBHOOK, json={"dispute_id": dispute_id}, timeout=10.0)
    except httpx.ConnectError:
        print("Agents aren't running. Start them with `make agents` (and `make gateway`).")
        return 1
    print(f"webhook {dispute_id}: HTTP {resp.status_code}")
    return 0 if resp.status_code == 202 else 1


if __name__ == "__main__":
    sys.exit(main())
