"""ACP ``Client`` implementation that funnels Hermes' session updates
into an in-memory queue the SSE driver consumes.

This is the *bridge* side of the ACP transport: Hermes acts as the
``Agent`` and we act as the ``Client``. We must respond to permission
requests and consume ``session_update`` notifications.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


# Permission policy:
#   For the AX engine we run in a constrained stub-skill mode. By default we
#   *deny* anything that would let Hermes touch our infrastructure (terminal,
#   file write, network) and *allow* read-only / observational actions. Until
#   the skill is fully exercised we keep the policy strict.
_AUTO_ALLOW_TOOL_KINDS = frozenset({"think", "search", "read"})


class HermesACPClient:
    """Client end of the ACP connection used by the bridge.

    Instances are single-use (one Hermes session). The bridge driver
    awaits :meth:`updates` to yield session_update payloads as they
    arrive, and closes the queue (via :meth:`close`) when the session
    completes.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._closed = False

    # ---- ACP Client protocol surface ------------------------------------

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        """Hermes pushes a session update; queue it for the SSE driver."""

        # ``update`` can be one of many ACP variant types — they all share
        # ``model_dump`` because they're Pydantic models. Convert to a plain
        # dict so the consumer doesn't depend on the ACP schema package.
        try:
            payload = update.model_dump(mode="json") if hasattr(update, "model_dump") else dict(update)
        except Exception:
            payload = {"raw_repr": repr(update)}
        await self._queue.put({"session_id": session_id, "update": payload})

    async def request_permission(
        self,
        options: list[Any],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> Any:
        """Auto-decide tool-call permissions per the bridge policy.

        Returns the ACP-defined :class:`RequestPermissionResponse`. We
        construct it lazily so the module stays importable even if the
        ACP package isn't available (e.g. unit tests on the translator).
        """

        from acp.schema import RequestPermissionResponse  # local import — see docstring

        kind = getattr(tool_call, "kind", None) or (
            tool_call.get("kind") if isinstance(tool_call, dict) else None
        )
        decision = "allow_once" if kind in _AUTO_ALLOW_TOOL_KINDS else "reject_once"
        chosen = next(
            (opt for opt in options if getattr(opt, "kind", None) == decision or
             (isinstance(opt, dict) and opt.get("kind") == decision)),
            options[0] if options else None,
        )
        logger.info(
            "[bridge] permission request session=%s tool_kind=%s -> %s",
            session_id,
            kind,
            decision,
        )
        # ACP expects a typed response; fall through to a plain dict if we
        # can't construct the model (very old or very new ACP versions).
        try:
            return RequestPermissionResponse(outcome={"selected": chosen})
        except Exception:
            return {"outcome": {"selected": chosen}}

    # ---- Bridge consumer surface ----------------------------------------

    async def updates(self):
        """Yield queued session updates until the session is closed."""

        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)


__all__ = ["HermesACPClient"]
