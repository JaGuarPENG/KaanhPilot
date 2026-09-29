from typing import Literal

from pydantic import Field

from ..schemas import Action, StrictModel


class LightParameters(StrictModel):
    room: Literal["living_room", "bedroom", "kitchen"]
    state: Literal["on", "off"]


class MoveParameters(StrictModel):
    location: Literal["living_room", "bedroom", "kitchen", "user", "workspace"]


class PickParameters(StrictModel):
    object: Literal["water_bottle", "cup"]


class LookParameters(StrictModel):
    target: str = Field(min_length=1, max_length=80)


class StopParameters(StrictModel):
    pass


class OrderParameters(StrictModel):
    item_id: Literal["water", "cola", "oolong_tea"]


ACTION_TYPES = {
    "submit_order": OrderParameters,
    "set_light": LightParameters,
    "move_to": MoveParameters,
    "pick_object": PickParameters,
    "look_at": LookParameters,
    "stop": StopParameters,
}


def validate_actions(actions: list[Action], allowed: list[str]) -> None:
    # Validate the entire batch BEFORE executing even the first action.
    if any(action.type == "submit_order" for action in actions) and len(actions) != 1:
        raise ValueError("Submit exactly one order per request")
    for action in actions:
        if action.type not in allowed or action.type not in ACTION_TYPES:
            raise ValueError("Unsupported action for the active scene")
        ACTION_TYPES[action.type].model_validate(action.parameters)

