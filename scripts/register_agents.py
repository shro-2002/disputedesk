"""Register the three DisputeDesk agents with the Switch server (run once, at H1).

Docs: /switch-rooms/internals/agent-protocol.mdx "Register the agent" and "Choose who the agent answers".
- POST /agents with the Gateway registration key as Bearer -> {id, api_key} (key returned once).
- disputedesk-gateway gets addressing "owner_only": only its owner (the FinOps lead who created the
  registration key) may instruct it. Switch refuses everyone else, visibly, in the room.
Writes ids/keys into .env (gitignored). Never prints a key.
"""

import re
import sys

import httpx

from disputedesk.settings import ROOT, load_env, require

AGENTS = {
    "EVIDENCE": ("disputedesk-evidence", "Evidence", "Runs the Visa CE3.0 check as SQL and posts the evidence table."),
    "COUNSEL": ("disputedesk-counsel", "Counsel", "Drafts the chargeback rebuttal text and a recommendation."),
    "GATEWAY": ("disputedesk-gateway", "Submitter", "Holds the exclusive submitter role; the only agent that calls the payment gateway."),
}
PROFILE = {
    "connection_model": "always_on",
    "message_exchange": True,
    "pre_invocation_mediation": [],
    "post_invocation_mediation": [],
    "event_reporting": [],
}
TIMEOUT = httpx.Timeout(90.0, connect=10.0)  # registration creates a Mattermost bot account


def _set_env(lines: list[str], key: str, value: str) -> list[str]:
    pattern = re.compile(rf"^{key}=.*$")
    for i, line in enumerate(lines):
        if pattern.match(line):
            return lines[:i] + [f"{key}={value}"] + lines[i + 1:]
    return lines + [f"{key}={value}"]


def main() -> int:
    load_env()
    base = require("SWITCH_BASE_URL").rstrip("/")
    reg_key = require("SWITCH_REGISTRATION_KEY")
    overwrite = "--overwrite" in sys.argv
    env_path = ROOT / ".env"
    lines = env_path.read_text().splitlines()
    with httpx.Client(timeout=TIMEOUT) as http:
        if http.get(f"{base}/health").status_code != 200:
            print(f"{base}/health is not healthy: is SWITCH_BASE_URL the server address (not the Gateway)?")
            return 1
        for prefix, (name, display, description) in AGENTS.items():
            have_key = any(line.startswith(f"{prefix}_API_KEY=") and len(line) > len(prefix) + 9 for line in lines)
            body = {"name": name, "display_name": display, "description": description,
                    "connector_type": "disputedesk", "integration_profile": PROFILE, "overwrite": overwrite}
            resp = http.post(f"{base}/agents", headers={"Authorization": f"Bearer {reg_key}"}, json=body)
            if resp.status_code == 409 and have_key and not overwrite:
                print(f"{name}: already registered, key already in .env")
                continue
            if resp.status_code == 409:
                # Registered earlier but we never stored its key (e.g. a timed-out response):
                # documented overwrite issues a fresh key for the same name we own.
                resp = http.post(f"{base}/agents", headers={"Authorization": f"Bearer {reg_key}"},
                                 json={**body, "overwrite": True})
            resp.raise_for_status()
            created = resp.json()
            lines = _set_env(lines, f"{prefix}_AGENT_ID", created["id"])
            lines = _set_env(lines, f"{prefix}_API_KEY", created["api_key"])
            env_path.write_text("\n".join(lines) + "\n")  # persist each key immediately
            print(f"{name}: registered (id {created['id']}), key saved to .env")
            if prefix == "GATEWAY":
                ops = http.post(f"{base}/agents/{created['id']}/ops/update_agent_detail",
                                headers={"Authorization": f"Bearer {created['api_key']}"},
                                json={"agent_id": created["id"], "addressing": "owner_only"})
                print(f"{name}: addressing owner_only -> HTTP {ops.status_code}")
    env_path.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
