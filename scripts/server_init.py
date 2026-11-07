"""Generate server/.env for the official Switch compose stack (run once; never prints secrets).

Source: /switch-rooms/deploy/self-host.mdx (required values, profiles collab + gateway, ports,
ID_SERVER_NAME) and the variables declared in ghcr.io/sandbox-quantum/standalone-compose:0.31.0.
Also copies the Mattermost logins the stack seeds into the project .env for `make h1`.
"""

import re
import secrets
import sys

from disputedesk.settings import ROOT

SERVER_ENV = ROOT / "server" / ".env"
PROJECT_ENV = ROOT / ".env"


def _password() -> str:
    return "Dd1!" + secrets.token_urlsafe(18)  # meets Mattermost's complexity rules


def _server_values() -> dict[str, str]:
    return {
        "SWITCH_VERSION": "0.31.0",
        "COMPOSE_PROFILES": "collab,gateway",
        "AGENT_REGISTRATION_TOKEN": secrets.token_urlsafe(32),
        "SECRET_KEYS": "k1:" + secrets.token_hex(32),
        "JWT_SECRET_KEY": secrets.token_hex(32),
        "CONTROLLER_TOKEN_SECRET": secrets.token_hex(32),
        "DB_NAME": "switch", "DB_OWNER_USER": "switch_owner", "DB_OWNER_PASSWORD": secrets.token_hex(16),
        "DB_USER": "switch_app", "DB_PASSWORD": secrets.token_hex(16),
        "POSTGRES_HOST_PORT": "55432",  # avoid clashing with a host PostgreSQL on 5432
        "FRONTEND_BASE_URL": "http://localhost:3000",
        "GATEWAY_PUBLIC_URL": "http://localhost:8000",
        "GATEWAY_ADMIN_EMAIL": "finops-lead@switch.local", "GATEWAY_ADMIN_PASSWORD": _password(),
        "MATTERMOST_ADMIN_USER": "mmadmin", "MATTERMOST_ADMIN_PASSWORD": _password(),
        "MATTERMOST_TEAM_NAME": "disputedesk",
        "MATTERMOST_USER": "finops-lead", "MATTERMOST_USER_PASSWORD": _password(),
    }


def _put(text: str, key: str, value: str) -> str:
    if re.search(rf"(?m)^{key}=", text):
        return re.sub(rf"(?m)^{key}=.*$", lambda _: f"{key}={value}", text)
    return text + f"{key}={value}\n"


def main() -> int:
    if not SERVER_ENV.exists():
        SERVER_ENV.parent.mkdir(exist_ok=True)
        SERVER_ENV.write_text("# Switch server secrets (generated, gitignored). Gateway login at http://localhost:3000:\n"
                              "# GATEWAY_ADMIN_EMAIL / GATEWAY_ADMIN_PASSWORD below.\n"
                              + "".join(f"{k}={v}\n" for k, v in _server_values().items()))
        SERVER_ENV.chmod(0o600)
        print("server/.env generated")
    values = dict(line.split("=", 1) for line in SERVER_ENV.read_text().splitlines()
                  if "=" in line and not line.startswith("#"))
    text = PROJECT_ENV.read_text() if PROJECT_ENV.exists() else (ROOT / ".env.example").read_text()
    for key, value in {
        "SWITCH_BASE_URL": "http://localhost:8000", "MATTERMOST_URL": "http://localhost:8065",
        "MM_ADMIN_LOGIN": values["MATTERMOST_ADMIN_USER"], "MM_ADMIN_PASSWORD": values["MATTERMOST_ADMIN_PASSWORD"],
        "MM_LEAD_LOGIN": values["MATTERMOST_USER"], "MM_LEAD_PASSWORD": values["MATTERMOST_USER_PASSWORD"],
        "FINOPS_LEAD": values["MATTERMOST_USER"],
    }.items():
        text = _put(text, key, value)
    PROJECT_ENV.write_text(text)
    print(".env updated with server addresses and Mattermost logins (values not shown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
