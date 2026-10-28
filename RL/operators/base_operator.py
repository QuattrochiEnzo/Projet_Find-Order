# RL/operators/base_operator.py
"""
Base abstractions for neighborhood operators used by the RL environment.

An operator is responsible for:
1) Preparing a set of candidate moves for the current environment state.
2) Applying one selected candidate to the environment.

The operator may store internal step-level context (e.g. selected circuits,
destroyed subset, cached candidates) between `prepare()` and `apply()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, Tuple


@dataclass(frozen=True)
class Candidate:
    """
    Immutable description of a candidate move.

    Attributes:
    - kind:    string identifier describing the move type
    - payload: structured parameters required to apply the move
    """
    kind: str
    payload: Dict[str, Any]


class Operator(Protocol):
    """
    Operator interface (structural typing via Protocol).

    Contract:
    - `prepare(env)` must generate at least one candidate move.
    - `apply(env, candidate)` must apply the move in-place on `env`
      and return (ok, details), where:
        * ok      -> indicates feasibility after application
        * details -> optional diagnostic information
    """

    name: str  # Human-readable operator identifier

    def prepare(self, env: Any) -> List[Candidate]:
        """
        Select step-level context and generate candidate moves.

        Returns:
            List[Candidate] with length >= 1.
        """
        ...

    def apply(self, env: Any, candidate: Candidate) -> Tuple[bool, Dict[str, Any]]:
        """
        Apply the selected candidate move to the environment.

        Returns:
            (ok, details)
            - ok:      True if resulting state is valid
            - details: optional metadata about the move execution
        """
        ...