"""ACENT FastAPI ↔ Hermes ACP bridge.

Bridges acent-flow's HTTP/SSE surface to Hermes' stdio JSON-RPC ACP
protocol. Owns the per-request Hermes process lifecycle, translates
session updates to ``AXStreamEvent`` SSE frames, and pins the wire
contract via :mod:`ax_bridge.contract`.
"""

__version__ = "0.1.0"
