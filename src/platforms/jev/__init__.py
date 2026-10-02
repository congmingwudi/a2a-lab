"""TypeSafe Jev — a "System One" decision model (WS26, D82, plan/17-jev-system-one.md).

Not an agent and not a platform peer: Jev answers typed questions about a piece
of text with calibrated probabilities, and never writes prose. The lab uses it
inside its seams (the fan-out gate, the calibration study), not behind them.
"""

from platforms.jev.client import Answer, JevError, SystemOneClient, question

__all__ = ["Answer", "JevError", "SystemOneClient", "question"]
