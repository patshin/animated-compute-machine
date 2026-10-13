import { useCallback, useEffect, useMemo, useState } from "react";

import {
  completeRuntimeRun,
  loadRuntimeSnapshot,
  parseAgentEvent,
  resetRuntimeRun,
  startRuntimeRun,
  stepRuntimeRun,
  type RunSnapshot
} from "./api";
import { projectRuntimeEvent } from "./runtime-projection";

type RuntimeAction = "load" | "run" | "step" | "reset";

export function useLabRuntime() {
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const [pending, setPending] = useState<RuntimeAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const runId = snapshot?.run.run_id ?? null;
  const terminal = snapshot?.run.status === "completed" || snapshot?.run.status === "error";

  useEffect(() => {
    const savedRunId = new URLSearchParams(window.location.search).get("run");
    if (!savedRunId) return;
    const controller = new AbortController();
    setPending("load");
    void loadRuntimeSnapshot(savedRunId, controller.signal)
      .then((next) => setSnapshot(next))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Saved run could not be restored");
        }
      })
      .finally(() => { if (!controller.signal.aborted) setPending(null); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!runId || terminal) return;
    const after = snapshot?.run.last_sequence ?? 0;
    const source = new EventSource(
      `/api/runs/${encodeURIComponent(runId)}/events?after_sequence=${after}&follow=true`
    );
    const receive = (raw: MessageEvent<string>) => {
      try {
        const event = parseAgentEvent(JSON.parse(raw.data));
        setSnapshot((current) => current ? projectRuntimeEvent(current, event) : current);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Event projection failed");
        source.close();
      }
    };
    source.addEventListener("agent-event", receive as EventListener);
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) setError("Live event stream closed unexpectedly");
    };
    return () => source.close();
  }, [runId, terminal]);

  const perform = useCallback(async (action: RuntimeAction, task: () => Promise<RunSnapshot>) => {
    setPending(action);
    setError(null);
    try {
      const next = await task();
      setSnapshot(next);
      if (action === "load") {
        const url = new URL(window.location.href);
        url.searchParams.set("run", next.run.run_id);
        window.history.replaceState({}, "", url);
      }
      return next;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Runtime request failed");
      return null;
    } finally {
      setPending(null);
    }
  }, []);

  const load = useCallback(
    (input: string) => perform("load", () => startRuntimeRun(input)),
    [perform]
  );
  const step = useCallback(
    (pace: "fast" | "observe") => runId
      ? perform("step", () => stepRuntimeRun(runId, pace))
      : Promise.resolve(null),
    [perform, runId]
  );
  const run = useCallback(
    (pace: "fast" | "observe") => runId
      ? perform("run", () => completeRuntimeRun(runId, pace))
      : Promise.resolve(null),
    [perform, runId]
  );
  const reset = useCallback(
    () => runId ? perform("reset", () => resetRuntimeRun(runId)) : Promise.resolve(null),
    [perform, runId]
  );

  return useMemo(() => ({ snapshot, pending, error, load, step, run, reset }), [
    snapshot,
    pending,
    error,
    load,
    step,
    run,
    reset
  ]);
}
