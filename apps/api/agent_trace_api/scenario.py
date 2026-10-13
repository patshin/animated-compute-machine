import json
from functools import lru_cache
from pathlib import Path

from agent_trace_api.models import ScenarioDefinition


@lru_cache(maxsize=1)
def load_data_analyst_scenario() -> ScenarioDefinition:
    scenario_path = Path(__file__).resolve().parents[3] / "scenarios/data-analyst/case-1.json"
    payload = json.loads(scenario_path.read_text(encoding="utf-8"))
    return ScenarioDefinition.model_validate(payload)
