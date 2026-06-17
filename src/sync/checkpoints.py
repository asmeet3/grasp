"""Checkpoint persistence for resumable sync operations.

Saves and loads connector state to/from JSON files so that
interrupted full syncs can resume from the last successful batch.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages checkpoint files for sync resume capability."""

    def __init__(self, checkpoints_dir: Path):
        self.checkpoints_dir = checkpoints_dir
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, connector: str) -> Path:
        return self.checkpoints_dir / f"{connector}.json"

    def save_checkpoint(self, connector: str, state: dict) -> None:
        """Save checkpoint state for a connector."""
        path = self._path_for(connector)
        try:
            path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            logger.debug(f"Checkpoint saved for {connector}")
        except Exception as e:
            logger.error(f"Failed to save checkpoint for {connector}: {e}")

    def load_checkpoint(self, connector: str) -> dict | None:
        """Load checkpoint state for a connector. Returns None if not found."""
        path = self._path_for(connector)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            logger.info(f"Loaded checkpoint for {connector}")
            return data
        except Exception as e:
            logger.error(f"Failed to load checkpoint for {connector}: {e}")
            return None

    def clear_checkpoint(self, connector: str) -> None:
        """Remove checkpoint file for a connector."""
        path = self._path_for(connector)
        if path.exists():
            path.unlink()
            logger.debug(f"Checkpoint cleared for {connector}")

    def has_checkpoint(self, connector: str) -> bool:
        """Check if a checkpoint exists for a connector."""
        return self._path_for(connector).exists()

    def clear_all(self) -> None:
        """Remove all checkpoint files."""
        for path in self.checkpoints_dir.glob("*.json"):
            path.unlink()
        logger.info("All checkpoints cleared")
