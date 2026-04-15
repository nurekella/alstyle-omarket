from sqlalchemy import select

from app.models import Category, async_session
from app.settings_store import get_markup


async def build_category_markup_map(session) -> dict[int, float]:
    """
    Returns {category_id: effective_multiplier}.
    Walks nested sets: each category inherits the nearest ancestor with a
    non-null markup_multiplier, or the global markup as fallback.
    """
    global_markup = await get_markup()

    result = await session.execute(
        select(
            Category.id, Category.parent_id, Category.markup_multiplier,
        )
    )
    rows = result.fetchall()
    own: dict[int, float | None] = {r[0]: r[2] for r in rows}
    parent: dict[int, int | None] = {r[0]: r[1] for r in rows}

    resolved: dict[int, float] = {}

    def resolve(cat_id: int) -> float:
        if cat_id in resolved:
            return resolved[cat_id]
        chain = []
        current = cat_id
        while current is not None and current not in resolved:
            if own.get(current) is not None:
                val = own[current]
                resolved[current] = val
                break
            chain.append(current)
            current = parent.get(current)
        if current is None:
            base = global_markup
        else:
            base = resolved[current]
        for cid in chain:
            resolved[cid] = base
        return resolved[cat_id]

    for cat_id in own:
        resolve(cat_id)

    return resolved


async def get_markup_for_category(category_id: int | None) -> float:
    if category_id is None:
        return await get_markup()
    async with async_session() as session:
        m = await build_category_markup_map(session)
    return m.get(category_id, await get_markup())
