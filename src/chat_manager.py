from datetime import datetime

from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from src.database import chat_threads_table


class ChatManager:
    """Manages user chat threads in PostgreSQL."""

    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def get_user_chats(self, user_id: str) -> list[dict]:
        """Get all chat threads for a given user, ordered by latest updated."""
        async with self.engine.begin() as conn:
            stmt = (
                select(chat_threads_table)
                .where(chat_threads_table.c.user_id == user_id)
                .order_by(desc(chat_threads_table.c.updated_at))
            )

            result = await conn.execute(stmt)
            rows = result.fetchall()

            chats = []
            for row in rows:
                row_dict = dict(row._mapping)
                row_dict["created_at"] = (
                    row_dict["created_at"].isoformat() if row_dict["created_at"] else None
                )
                row_dict["updated_at"] = (
                    row_dict["updated_at"].isoformat() if row_dict["updated_at"] else None
                )
                chats.append(row_dict)

            return chats

    async def save_chat(
        self,
        user_id: str,
        chat_id: str,
        title: str,
        messages: list[dict],
        created_at: str | None = None,
    ) -> None:
        """Upsert a chat thread (update if exists, insert if new)."""
        async with self.engine.begin() as conn:
            values = {
                "id": chat_id,
                "user_id": user_id,
                "title": title,
                "messages": messages,
            }
            if created_at:
                try:
                    parsed_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    values["created_at"] = parsed_dt
                except ValueError:
                    pass

            stmt = insert(chat_threads_table).values(**values)
            set_dict = {
                "title": stmt.excluded.title,
                "messages": stmt.excluded.messages,
                "updated_at": stmt.excluded.updated_at,
            }

            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_=set_dict,
                where=chat_threads_table.c.user_id == user_id,
            )

            await conn.execute(stmt)

    async def delete_chat(self, user_id: str, chat_id: str) -> bool:
        """Delete a chat thread."""
        async with self.engine.begin() as conn:
            stmt = delete(chat_threads_table).where(
                chat_threads_table.c.id == chat_id,
                chat_threads_table.c.user_id == user_id,
            )
            res = await conn.execute(stmt)
            return res.rowcount > 0
