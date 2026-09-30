from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    ACT = "act"          # apply the label / mark the duplicate
    SUGGEST = "suggest"  # leave a comment for the maintainer
    SILENT = "silent"    # do nothing


@dataclass(frozen=True)
class Thresholds:
    act: float = 0.90
    suggest: float = 0.60

    def __post_init__(self) -> None:
        if not 0.0 <= self.suggest <= self.act <= 1.0:
            raise ValueError("need 0 <= suggest <= act <= 1")


DEFAULT_THRESHOLDS = Thresholds()


def decide_action(confidence: float, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> Action:
    if confidence >= thresholds.act:
        return Action.ACT
    if confidence >= thresholds.suggest:
        return Action.SUGGEST
    return Action.SILENT
