PYTHON ?= python3
PY := .venv/bin/python
CASE ?= C

-include .env
export

.PHONY: setup test seed gateway demo agents key register reset h1 server-init server-up server-down audit tamper-demo

.venv: requirements.txt
	@$(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "DisputeDesk needs Python >= 3.10. Run: make setup PYTHON=python3.12"; exit 1; }
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -q -r requirements.txt -c constraints.txt
	touch .venv

setup: .venv key seed

# Generate AUDIT_HMAC_KEY into .env once (never printed).
key:
	@test -f .env || cp .env.example .env
	@grep -q '^AUDIT_HMAC_KEY=.\+' .env || { $(PY) -c "import secrets,re,pathlib;p=pathlib.Path('.env');s=p.read_text();k=secrets.token_hex(32);s=re.sub(r'(?m)^AUDIT_HMAC_KEY=.*$$','AUDIT_HMAC_KEY='+k,s) if re.search(r'(?m)^AUDIT_HMAC_KEY=',s) else s+'\nAUDIT_HMAC_KEY='+k+'\n';p.write_text(s)"; echo "AUDIT_HMAC_KEY generated in .env"; }

test: .venv
	$(PY) -m pytest -q

seed: .venv
	$(PY) -m db.seed

gateway: .venv
	$(PY) -m uvicorn gateway.serve:app --port 8099

agents: .venv
	$(PY) -m agents.run_all

demo: .venv
	$(PY) -m scripts.new_dispute $(CASE)

register: .venv
	$(PY) -m scripts.register_agents

# Wipe demo state (disputes, audit log, receipts) and reseed cases A-E.
reset: .venv
	rm -f data/disputedesk.db data/receipts.db
	$(PY) -m db.seed

# Automate the non-GUI H1 steps. Optional: make h1 PHASE="riya"
h1: .venv
	$(PY) -m scripts.h1_setup $(PHASE)

COMPOSE := docker compose -p disputedesk-switch -f server/standalone-docker-compose.yml --env-file server/.env
SWITCH_VERSION_PIN := 0.31.0

# Official Switch compose stack (core + Mattermost + Gateway), pinned. Needs Docker + oras.
server-init: .venv
	@test -f server/standalone-docker-compose.yml || (cd server && oras pull ghcr.io/sandbox-quantum/standalone-compose:$(SWITCH_VERSION_PIN))
	$(PY) -m scripts.server_init

server-up:
	$(COMPOSE) up -d

server-down:
	$(COMPOSE) down

audit: .venv
	$(PY) -m scripts.verify_audit

tamper-demo: .venv
	$(PY) -m scripts.verify_audit --tamper-copy
