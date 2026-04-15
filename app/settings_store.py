from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import get_settings
from app.models import async_session, Setting

settings = get_settings()


async def get_setting(key: str, default: str = "") -> str:
    async with async_session() as session:
        r = await session.execute(select(Setting).where(Setting.key == key))
        row = r.scalar_one_or_none()
        return row.value if row else default


async def set_setting(key: str, value: str) -> None:
    async with async_session() as session:
        await session.execute(
            sqlite_insert(Setting)
            .values(key=key, value=value)
            .on_conflict_do_update(index_elements=["key"], set_={"value": value})
        )
        await session.commit()


async def get_markup() -> float:
    return float(await get_setting("markup_multiplier", str(settings.markup_multiplier)))
