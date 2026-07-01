"""Checkpoint persistence for resumable sync operations.

Saves and loads connector state to/from PostgreSQL so that
interrupted full syncs can resume from the last successful batch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ..database import checkpoints_table

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages checkpoint records in PostgreSQL for sync resume capability."""

    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def save_checkpoint(self, connector: str, state: dict) -> None:
        """Save checkpoint state for a connector (upsert)."""
        try:
            stmt = pg_insert(checkpoints_table).values(
                connector=connector,
                state=state,
                updated_at=datetime.now(timezone.utc),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["connector"],
                set_={"state": state, "updated_at": datetime.now(timezone.utc)},
            )
            async with self.engine.begin() as conn:
                await conn.execute(stmt)
            logger.debug(f"Checkpoint saved for {connector}")
        except Exception as e:
            logger.error(f"Failed to save checkpoint for {connector}: {e}")

    async def load_checkpoint(self, connector: str) -> dict | None:
        """Load checkpoint state for a connector. Returns None if not found."""
        try:
            async with self.engine.begin() as conn:
                result = await conn.execute(
                    select(checkpoints_table.c.state).where(
                        checkpoints_table.c.connector == connector
                    )
                )
                row = result.scalar_one_or_none()
            if row is not None:
                logger.info(f"Loaded checkpoint for {connector}")
                return row
            return None
        except Exception as e:
            logger.error(f"Failed to load checkpoint for {connector}: {e}")
            return None

    async def clear_checkpoint(self, connector: str) -> None:
        """Remove checkpoint for a connector."""
        async with self.engine.begin() as conn:
            await conn.execute(
                delete(checkpoints_table).where(
                    checkpoints_table.c.connector == connector
                )
            )
        logger.debug(f"Checkpoint cleared for {connector}")

    async def has_checkpoint(self, connector: str) -> bool:
        """Check if a checkpoint exists for a connector."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(checkpoints_table.c.connector).where(
                    checkpoints_table.c.connector == connector
                )
            )
            return result.first() is not None

    async def clear_all(self) -> None:
        """Remove all checkpoint records."""
        async with self.engine.begin() as conn:
            await conn.execute(delete(checkpoints_table))
        logger.info("All checkpoints cleared")
