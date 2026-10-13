# User Guide

This guide covers the complete AGENT//TRACE workflow. For the trust model and learning methodology, read [Project Instructions](PROJECT_INSTRUCTIONS.md).

## Start the product

### Docker

```bash
cp .env.example .env
docker compose build --pull
docker compose up -d
```

Open <http://127.0.0.1:47800>.

### Local workstation

macOS / Linux:

```bash
./scripts/bootstrap.sh --install
./scripts/dev.sh
```

Windows PowerShell:

```powershell
./scripts/bootstrap.ps1 -Install
./scripts/dev.ps1
```

Open <http://127.0.0.1:4173>. The API health endpoint is <http://127.0.0.1:8000/api/health>.

## Core navigation

| Workspace | Primary use |
|---|---|
| Home | View the learning map, enter predictions, and collect mastery evidence |
| Lab | Step through the canonical graph and inspect state, events, and reset behavior |
| Scenarios | Browse, clone, validate, and open the seven built-in Scenario Kits |
| Structured Output | Edit a schema and inspect validation, repair, and bounded retry |
| Tool Calling | Inspect registry resolution, typed arguments, permission, timeout, and audit |
| Durable Runtime | Pause, resume, refresh, fork, and reconstruct persisted runs |
| Evidence + Memory | Search local evidence, inspect citations and conflicts, and manage confirmed memory |
| Human Approval | Approve, reject, edit, expire, and resume write-like effects |
| Security | Launch controlled adversarial cases and inspect layered defenses |
| Runs / Evals | Replay traces, compare runs, execute Golden Sets, and export reports |
| Protocols | Compare MCP, A2A, AG-UI, and telemetry at their real system boundaries |
| Builder | Author, validate, preview, and export a portable Scenario Kit |

## First run

1. Confirm the top bar shows `SYSTEM READY`.
2. Open **Scenarios** and choose **Data Analyst** or **Customer Support**.
3. Select **Open in Lab** and load the first case.
4. Inspect the initial graph and state before taking an action.
5. Select **Step** once.
6. Select the active node and read its owner, reads, writes, state patch, and event.
7. Continue stepping until the terminal node, or use **Run** to complete the remaining transitions.
8. Refresh the browser to confirm durable state recovery.
9. Select **Reset** and confirm the new reset cycle does not display state patches from the previous cycle.

`Step` and `Run` are not frontend animations. The interface projects runtime events produced by the backend. Deterministic Mock mode is repeatable, but it still uses the same graph, contract, tool, event, persistence, and evaluation paths as other modes.

## Structured Output lab

Use this lab to separate model generation from validated application data.

1. Run the valid case and inspect the schema version.
2. Remove a required property or introduce an unexpected property.
3. Observe the validation error category and the bounded retry counter.
4. Use manual structured input to repair the payload.
5. Confirm that only the validated value reaches downstream state.

## Tool Calling lab

Use this lab to distinguish a model-authored request from runtime-authorized execution.

1. Select a registered tool and inspect its input and result contracts.
2. Run a valid read-only request.
3. Trigger an invalid argument, timeout, or invented tool name.
4. Inspect the registry, permission, timeout, idempotency, and audit stages.
5. Confirm that a blocked request produces a typed result and zero side effects.

## Durable Runtime lab

1. Create a run and advance it to an intermediate node.
2. Pause the run and refresh the page.
3. Resume from the persisted checkpoint.
4. Fork the run and change one input.
5. Compare the parent and child event streams.

Runs are local, single-instance learning records. The included SQLite configuration is not a shared multi-user database.

## Evidence + Memory lab

1. Run a retrieval scenario.
2. Inspect source authority, version, publication or effective date, and citation locator.
3. Trigger a conflicting or superseded source.
4. Confirm that the answer explains the conflict and cites the selected evidence.
5. Save only an explicitly confirmed memory preference.
6. Delete the preference and verify the audit event.

Retrieved text is data. It cannot grant itself instruction authority or tool permission.

## Human Approval lab

1. Create a run containing a simulated write-like effect.
2. Inspect the exact target, arguments, actor, policy, state version, and idempotency key.
3. Approve, reject, or edit the proposal.
4. If the underlying state changes, observe stale-approval detection.
5. Resume an approved request and confirm exactly one simulated effect and one audit chain.

## Security lab

The Attack Isolation Range uses controlled fictional inputs.

1. Select an attack case.
2. Read the expected stopping boundary.
3. Launch the case.
4. Compare expected and actual stop locations.
5. Inspect provider privacy projection, redactions, side-effect count, and network state.
6. Export only the redacted diagnostic when needed.

Important: do not paste real secrets, customer data, credentials, or private files into the lab.

## Trace and evaluation

Use the evaluation studio to score the path, not only the outcome.

- **Replay** rebuilds a run from its append-only event sequence.
- **Compare** aligns two runs by nodes, events, timings, tool decisions, and assertions.
- **Golden Set** executes deterministic cases across all built-in scenarios.
- **Trajectory evaluation** checks routing, tools, arguments, approvals, evidence, and security.
- **Final evaluation** checks the answer payload and citations.

## Keyboard controls

- `Command/Ctrl + K`: open the workspace command palette.
- `?`: open keyboard help.
- `Up` / `Down`: move through desktop navigation.
- `Left` / `Right`: move through tabs or adjust supported split panes.
- `Escape`: close the active dialog or drawer.
- `Tab`: move through all interactive controls with a visible focus indicator.

Reduced motion and high-contrast modes follow the browser or operating-system preference.

## Local data

- The default provider requires no account or API key.
- Local runs, drafts, traces, and checkpoints are stored under `.local/`.
- `.local/`, SQLite databases, exports, backups, and environment files are ignored by Git.
- Built-in Scenario Kits use fictional data.

To clear the configured local database, stop the product first and run:

```bash
./scripts/reset-local-data.sh
```

This is different from the in-product **Reset** action, which starts a new run cycle without deleting the entire local database.

## Troubleshooting

### `API OFFLINE`

Confirm the launch command is still running and open `/api/health` directly. If you changed ports, pass both values to the same launch command:

```bash
AGENT_TRACE_API_PORT=43100 AGENT_TRACE_WEB_PORT=43173 ./scripts/dev.sh
```

### Invalid `.venv`

The Python interpreter must resolve inside this repository and use Python 3.12 or 3.13. Do not install into a virtual environment copied from another directory. Move the invalid directory aside, then rerun the bootstrap command with a supported interpreter.

### Opening `apps/web/index.html` shows a blank page

That file is a Vite entry point, not a standalone page. Use `./scripts/dev.sh` or Docker. For a backend-free preview, use the hosted [interactive product tour](https://patshin.github.io/animated-compute-machine/).

### Docker page does not load

Check service health:

```bash
docker compose ps
curl --fail http://127.0.0.1:47800/api/ready
```

If the API is not ready, inspect logs with `docker compose logs api` and verify that the persistent volume is writable by the container user.
