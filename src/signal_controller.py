"""
Signal controller protocol and implementations.

Defines the interface for traffic signal control strategies.
The default FixedTimingController wraps the existing TrafficLight class.
Future implementations can provide adaptive, actuated, or learning-based control.
"""

from __future__ import annotations
from typing import Protocol, Tuple, Dict, Any, runtime_checkable


@runtime_checkable
class SignalController(Protocol):
    """Protocol for traffic signal controllers.

    Any signal controller must implement these methods to be usable
    by the TrafficLightManager.
    """

    def step(self, dt: float) -> None:
        """Advance the controller by dt seconds."""
        ...

    def is_green(self, edge_id: Tuple) -> bool:
        """Return True if the given incoming edge currently has green."""
        ...

    def get_state(self) -> Dict[str, Any]:
        """Return serializable state for visualization/logging."""
        ...

    @property
    def state(self) -> str:
        """Current state: 'green', 'yellow', or 'red'."""
        ...

    @property
    def current_phase(self) -> int:
        """Index of the currently active phase."""
        ...
