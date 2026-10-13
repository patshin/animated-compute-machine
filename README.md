# AGENT//TRACE

**A visual systems studio for understanding how production-grade AI agents actually work.**

[![Interactive Tour](https://img.shields.io/badge/interactive_tour-live-F2C84B?style=for-the-badge&labelColor=17191D)](https://patshin.github.io/animated-compute-machine/)
[![License](https://img.shields.io/badge/license-Apache--2.0-40B8AC?style=for-the-badge&labelColor=17191D)](LICENSE)
[![Runtime](https://img.shields.io/badge/runtime-local--first-8B78E6?style=for-the-badge&labelColor=17191D)](#run-the-full-studio)
[![Mode](https://img.shields.io/badge/default-deterministic_mock-F28C3A?style=for-the-badge&labelColor=17191D)](#why-it-is-different)

AGENT//TRACE turns agent execution into an inspectable workbench. Instead of showing only a final chat response, it exposes the graph, typed state, node transitions, tool requests, evidence, approvals, security decisions, event trace, persistence, and evaluations behind every run.

**[Open the interactive product tour](https://patshin.github.io/animated-compute-machine/)** — no installation or API key required.

![AGENT//TRACE runtime graph, event stream, and state inspector](assets/screenshots/validated-runtime-preview-1440x1000.png)

## Why it exists

Agent systems usually fail in the invisible space between a prompt and an answer. A model proposes a decision, a runtime selects an edge, a tool crosses a permission boundary, retrieved text competes for authority, or a write waits for human approval. Ordinary chat interfaces hide those moments.

AGENT//TRACE makes that middle visible and testable. It is built for:

- AI product leaders who need to review behavior without reading framework internals;
- engineers learning typed agent architecture, tools, durability, and observability;
- security reviewers testing prompt injection, authorization, and unsafe side effects;
- educators teaching agents as systems rather than prompt tricks;
- teams designing Scenario Kits, evaluation sets, and human-in-the-loop workflows.

This is a learning, demonstration, and architecture-review studio. It is not a multi-tenant SaaS, a general-purpose code executor, or an unattended operator for real external systems.

## What you can inspect

| Workbench | What it makes visible |
|---|---|
| Runtime Graph | Nodes, edges, reducers, loops, branches, state patches, and reset cycles |
| Structured Output | JSON Schema validation, failure classification, repair, and bounded retry |
| Tool Calling | Registry lookup, typed arguments, permission checks, timeout, idempotency, and audit |
| Durable Runtime | LangGraph checkpoints, pause/resume, refresh recovery, forks, and thread isolation |
| Evidence + Memory | Local retrieval, citations, authority, freshness, conflicts, confirmed memory, and deletion |
| Human Approval | Scoped approval, rejection, edit, expiry, stale-state detection, and exactly-once effects |
| Attack Isolation Range | Prompt injection, invented tools, malicious files, unsafe imports, redaction, and fail-closed boundaries |
| Trace Evaluation Studio | Replay, run comparison, Golden Sets, trajectory scoring, safety gates, and latency analysis |
| Protocol Deck | Practical boundaries between MCP, A2A, AG-UI, OpenTelemetry, and application state |
| Scenario Builder | Eight-part portable kits with contracts, validation, preview, export, and capstone evidence |

![AGENT//TRACE controlled attack isolation range](assets/screenshots/attack-range-1440x1000.png)

## Why it is different

### The model proposes. The runtime owns authority.

Every model-like decision crosses a typed schema boundary. The provider cannot directly own graph state, execute tools, grant permission, or rewrite policy.

```text
Browser workbench
       │ REST + SSE, typed projections only
       ▼
Application boundary
       ├── Runtime + state machine
       ├── Tool registry + validation + permission + timeout + audit
       ├── Evidence, approval, security, and evaluation services
       └── Provider adapter
                │ typed proposal only
                ▼
       Deterministic Mock / optional provider
```

### Instructions are explicit system boundaries

In AGENT//TRACE, instructions are not hidden prompt decoration. They define who may decide, what data may influence a decision, which tools exist, when approval is mandatory, how failures are classified, and which evidence proves the result. Retrieved content is treated as data, never as a new source of authority.

The studio teaches this operating loop:

`OBSERVE → PREDICT → STEP → INTERVENE → BREAK → REPAIR → EXPLAIN → EVALUATE`

See [Project Instructions](docs/PROJECT_INSTRUCTIONS.md) for the exact purpose of each phase and the trust hierarchy used throughout the labs.

### Deterministic before expensive

The default provider is a seeded deterministic mock. Once dependencies are installed, the core learning path runs without an API key or network model call. Mock, replay, manual structured input, and optional providers all pass through the same contracts, runtime, tools, events, persistence, and evaluation paths.

## Run the full studio

### Fastest path: Docker

Requirements: Docker with Compose support.

```bash
git clone https://github.com/patshin/animated-compute-machine.git
cd agent-trace
cp .env.example .env
docker compose build --pull
docker compose up -d
```

Open <http://127.0.0.1:47800>. The header should show `PUBLIC PREVIEW / SAFE LAB MODE`.

Stop the stack with:

```bash
docker compose down
```

### Local workstation

Requirements:

- macOS or Linux; Windows uses PowerShell 7+
- Node.js 24.15+ or 26.x
- Python 3.12 or 3.13

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

Then open <http://127.0.0.1:4173>. The API health endpoint is <http://127.0.0.1:8000/api/health>.

The bootstrap process writes only repository-local environments, caches, dependencies, and build output. It does not enable global Corepack or modify global Node, Python, or shell configuration.

## Your first five minutes

1. Open **Home** and confirm `SYSTEM READY`.
2. Open **Scenarios** and choose one of the seven built-in Scenario Kits.
3. Open **Lab**, load a case, and select **Step**.
4. Select a graph node and compare the State Inspector, State Diff, and Event Timeline.
5. Use **Run** to complete the same runtime path, then refresh to observe checkpoint recovery.
6. Open **Security** to launch a controlled attack and inspect the exact boundary that blocks it.
7. Open **Runs / Evals** to replay and score the trajectory, not only the final answer.

For the complete workflow, shortcuts, data model, and troubleshooting guide, read the [User Guide](docs/USER_GUIDE.md).

## GitHub Pages versus the full product

The GitHub Pages site is a static, interactive product tour. It is intentionally safe, requires no backend, writes no data, and demonstrates the visual and interaction model.

The full studio includes FastAPI, SQLite, streaming events, durable state, security labs, approvals, and evaluations. GitHub Pages cannot host those server components, so use Docker or the local workstation instructions above for the complete experience. See [Deployment](docs/DEPLOYMENT.md) for a hardened HTTPS deployment.

## Public repository contents

This public history is intentionally curated. It contains only:

- the React workbench and FastAPI product source;
- typed JSON Schema and OpenAPI contracts;
- deterministic Scenario Kits and evaluation data required by the runtime;
- container and local launch files;
- the interactive Pages tour and selected product screenshots;
- end-user, project-instruction, deployment, security, and license documentation.

Internal planning files, task ledgers, review prompts, private checkpoints, raw browser traces, local paths, credentials, and unrelated development artifacts are not part of this repository or its Git history.

## Safety and privacy

- Built-in scenarios contain fictional data.
- Local runtime data is stored under `.local/` and ignored by Git.
- Write-like simulations require explicit approval, idempotency checks, and audit records.
- Public-preview mode disables unsafe capabilities and applies request, rate, process, filesystem, and network boundaries.
- Do not place credentials, customer data, secrets, or private files into prompts, fixtures, traces, screenshots, or exported Scenario Kits.
- Never expose the development server, raw API port, SQLite files, or optional provider subprocess directly to the public internet.

Read [Security](SECURITY.md) before any internet-facing deployment.

## Documentation

- [User Guide](docs/USER_GUIDE.md) — operate every major workbench.
- [Project Instructions](docs/PROJECT_INSTRUCTIONS.md) — understand the learning loop, trust model, and instruction hierarchy.
- [Deployment](docs/DEPLOYMENT.md) — run locally, with Docker, or behind HTTPS.
- [Scenario Kits](scenarios/README.md) — inspect the portable scenario format.
- [Security Policy](SECURITY.md) — supported security boundary and reporting guidance.

## License

Product code and documentation are licensed under [Apache License 2.0](LICENSE). Fictional sample Scenario Kits use CC0-1.0. Third-party packages retain their original licenses; see [License Scope](LICENSE_SCOPE.md) and [Third-Party Notices](THIRD_PARTY_NOTICES.md).

---

**AGENT//TRACE does not ask you to trust an agent. It gives you the instruments to inspect one.**
