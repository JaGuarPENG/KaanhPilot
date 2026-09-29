"""Export the model decision and public response contracts from their Python definitions."""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from voice_agent.schemas import AgentDecision, AgentResponse  # noqa: E402


def main():
    output = PROJECT_ROOT / "schemas"
    output.mkdir(exist_ok=True)
    for filename, model in [("agent-decision.schema.json", AgentDecision),
                            ("agent-response.schema.json", AgentResponse)]:
        path = output / filename
        path.write_text(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
