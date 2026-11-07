# H1: Switch setup (as run)

Tested with Switch 0.31.0: the official compose stack (profiles `collab` + `gateway`) on a colima VM with Rosetta (`--vm-type vz --vz-rosetta`).

Sources:
- docs.switchagents.ai: deploy/self-host, administer/people-and-agents, internals/agent-protocol, internals/identity-and-access, building/payments-room, getting-started/create-a-room;
- the live operation registry (`switch/ops-schemas.json`);
- the Mattermost API v4 reference.

## Why the compose stack, not Switch Console's local server
- **No Gateway in Console's native mode.** Switch Console 0.40's local server runs natively, and that mode has no Gateway web interface. The Gateway's **API Keys** page is the only documented way to create a registration key, and an agent built with its own client needs one.
- **The compose stack has one.** The official compose stack, profiles `collab` + `gateway`, includes the Gateway and a seeded Mattermost.
- **Apple Silicon needs Rosetta.** `mattermost/mattermost-team-edition:11.9.0` is amd64-only and crashes (SIGSEGV) under QEMU. Run Docker with Rosetta: `colima start switch --vm-type vz --vz-rosetta --cpu 4 --memory 8`.

## Steps
| # | Step | How | Who |
|---|---|---|---|
| 1 | Start the server with bundled Mattermost | `make server-init` (pulls compose 0.31.0, generates `server/.env`), then `make server-up` | terminal |
| 2 | Link the FinOps lead's Switch account to Mattermost | the compose `setup` seeder (`Linked finops-lead@switch.local to Mattermost account finops-lead`) | automatic |
| 3 | Create a registration key | Gateway http://localhost:3000 (login in `server/.env`), **API Keys**, **Create Registration Key**; put it in `.env` as `SWITCH_REGISTRATION_KEY` | **FinOps lead (GUI)** |
| 4 | Register `disputedesk-evidence`, `-counsel`, `-gateway`; keys go to `.env` | `make h1` (`POST /agents`) | terminal |
| 5 | Only the FinOps lead may instruct `disputedesk-gateway` | `update_agent_detail {addressing: owner_only}` from a sibling agent. Verified `{owner: true, owner_agents: false, users: []}` | terminal |
| 6 | Create room `#disputes` with instructions, aliases `@evidence`/`@counsel`, exclusive role `submitter`, agents, users `finops-lead` + `riya-support` | one `create_room` operation | terminal |
| 7 | Create Mattermost user `riya-support` (unlinked support user) | Mattermost `POST /api/v4/users`, team membership | terminal |
| 8 | `disputedesk-gateway` takes the exclusive role | `assume_role` at every connect (lease is per connection) | agent |
| 9 | Room document "CE3.0 rulebook" | `create_room_document` when @evidence connects | agent |
| 10 | FinOps lead's stable sender id | read from a live event (`payload.sender`), stored as `FINOPS_LEAD_SENDER_ID` | terminal |

`@submitter` is the exclusive **role**, not an alias: Switch refuses an alias that collides with a role name.
