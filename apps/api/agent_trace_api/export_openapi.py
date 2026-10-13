import json
import sys
from pathlib import Path

from agent_trace_api.main import app


def main() -> None:
    destination = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path("packages/contracts/openapi.json")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
