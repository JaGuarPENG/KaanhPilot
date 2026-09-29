import json
import re
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from .actions import ACTION_TYPES
from .schemas import AgentDecision, StrictModel


class Scene(StrictModel):
    name: str
    description: str
    agent_role: str
    rules: list[str]
    available_actions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def known_actions(self):
        if not set(self.available_actions) <= ACTION_TYPES.keys():
            raise ValueError("Scene references an unregistered action")
        return self


def load_scene(name: str) -> Scene:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
        raise ValueError("Invalid scene name")
    path = Path(__file__).parent / "scenes" / f"{name}.yaml"
    return Scene.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def build_prompt(scene: Scene) -> str:
    # Resolve against the package so callers can launch from any working directory.
    # Read every turn so edits to the text file take effect on the next request.
    prompt_path = Path(__file__).resolve().parent / "prompts" / "system.txt"
    instructions = prompt_path.read_text(encoding="utf-8-sig").strip()
    if not instructions:
        raise ValueError(f"System prompt file is empty: {prompt_path}")
    schemas = {name: ACTION_TYPES[name].model_json_schema() for name in scene.available_actions}
    return (
        instructions + "\n\n"
        + "SCENE=" + scene.model_dump_json() + "\n"
        + "ACTION_PARAMETER_SCHEMAS=" + json.dumps(schemas, ensure_ascii=False) + "\n"
        + "DECISION_SCHEMA=" + json.dumps(AgentDecision.model_json_schema(), ensure_ascii=False)
    )
