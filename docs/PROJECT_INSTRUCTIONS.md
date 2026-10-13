# Project Instructions

AGENT//TRACE is an instructional environment for learning, demonstrating, and reviewing agent systems as typed, observable software. Its instructions are designed to expose authority and evidence, not to hide behavior inside a large prompt.

## The purpose of the project

Use AGENT//TRACE to answer questions such as:

- What state did the runtime read and write?
- Which component selected the next graph edge?
- Did the model merely propose a tool call, or did the runtime authorize it?
- Which evidence supported a claim, and which conflicting source was rejected?
- Why was a write interrupted for approval?
- Which security boundary stopped an adversarial input?
- Did the final answer pass while the trajectory still failed?

The product is deliberately local-first and deterministic by default. This lets a learner repeat the same run, change one condition, and compare the resulting trace without spending tokens or exposing data to an external model.

## The instruction hierarchy

AGENT//TRACE uses a strict authority model:

1. **Application policy** defines non-negotiable permissions, approval requirements, budgets, and safety limits.
2. **Runtime instructions** define the graph, typed state transitions, retries, timeouts, and event contracts.
3. **Scenario instructions** define the learning goal, available tools, evidence corpus, faults, and evaluation assertions for one portable kit.
4. **User input** provides the task and deliberate human decisions.
5. **Retrieved or imported content** is untrusted data. It may become evidence after validation, but it never becomes an instruction merely because it contains imperative text.
6. **Provider output** is a typed proposal. It must pass schema, semantic, policy, and permission checks before the runtime may use it.

This hierarchy is the central lesson of the project: language-model text is not authority by default.

## The learning loop

Every lab uses the same eight-phase loop.

### 1. Observe

Read the mission, graph, initial state, available tools, policy, and expected evidence. Identify which component owns each decision.

### 2. Predict

Choose what should happen next before running it. A prediction turns passive inspection into a falsifiable model of the system.

### 3. Step

Advance exactly one runtime transition. Compare the new state patch, selected edge, event sequence, and timing with your prediction.

### 4. Intervene

Change one controlled input, policy value, approval decision, schema, or tool result. Keep the rest of the scenario stable.

### 5. Break

Inject a known fault such as malformed structured output, timeout, invented tool, retrieval injection, stale approval, or conflicting evidence.

### 6. Repair

Repair the owning boundary instead of hiding the symptom. Examples include tightening a schema, changing a policy, adding an approval, constraining a retry budget, or correcting evidence ranking.

### 7. Explain

Describe the result using trace evidence: the relevant event, state patch, boundary decision, and side-effect count. Avoid explanations that rely only on the final answer.

### 8. Evaluate

Run deterministic assertions and compare the result with the baseline. Score final quality, trajectory, step correctness, security, and latency separately.

## Operating rules

- Start in **Deterministic Mock** mode. Add an optional provider only after the scenario works end to end.
- Treat the browser as a projection of backend-owned state. The UI does not directly call LangGraph or a provider.
- Treat every model decision as untrusted until it passes the typed boundary.
- Use only registered tools. Unknown tool names fail closed.
- Require explicit human approval for write-like effects.
- Preserve idempotency keys and audit evidence when resuming an approved effect.
- Use **Step** to diagnose causality and **Run** only after the next transition is understood.
- Evaluate the trajectory, not only the final sentence.
- Keep real credentials, personal data, and production records out of scenarios and traces.

## Recommended learning path

1. Agent mental model and responsibility boundaries.
2. Structured output and schema repair.
3. Tool calling and execution authority.
4. State graphs and streaming projections.
5. Retrieval, evidence, and citations.
6. Durability, checkpoints, and memory.
7. Human approval and exactly-once effects.
8. Security and adversarial inputs.
9. Tracing and evaluations.
10. MCP, A2A, AG-UI, and telemetry boundaries.
11. Scenario Kit authoring and architecture defense.

## What success looks like

A successful learner can inspect a trace and explain:

- which component owned each decision;
- which contract protected each boundary;
- why a tool or edge was allowed or denied;
- which evidence justified the outcome;
- whether any side effect occurred;
- how the run would be reproduced and evaluated.

That is the purpose of AGENT//TRACE: moving agent design from intuition to inspectable systems engineering.
