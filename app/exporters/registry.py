"""
Registry of XML feeds exposed by PressPlay.

Built-in feeds are declared here as constants. Users can also add custom
feeds via the admin UI — those are stored in the CustomFeed table and
merged on read through `all_feeds()`.
"""
import re

from sqlalchemy import select

from app.models import CustomFeed, async_session


BUILTIN_FEEDS = [
    {
        "id": "omarket",
        "name": "OMarket Top1",
        "format": "Kaspi XML",
        "url_path": "/omarket-feed.xml",
        "enabled": True,
        "target": "OMarket.kz (аккаунт Top1)",
        "site": None,
        "strict_xsd": False,
        "custom": False,
    },
    {
        "id": "omarket_acr",
        "name": "OMarket АЦР",
        "format": "Kaspi XML",
        "url_path": "/omarket-acr-feed.xml",
        "enabled": True,
        "target": "OMarket.kz (аккаунт АЦР)",
        "site": None,
        "strict_xsd": False,
        "custom": False,
    },
    {
        "id": "kaspi",
        "name": "Kaspi",
        "format": "Kaspi XML (strict)",
        "url_path": "/kaspi-feed.xml",
        "enabled": True,
        "target": "Kaspi.kz",
        "site": "https://kaspi.kz/",
        "strict_xsd": True,
        "custom": False,
    },
    {
        "id": "skstore",
        "name": "SK Store",
        "format": "—",
        "url_path": None,
        "enabled": False,
        "target": "skstore.kz",
        "site": "https://skstore.kz/",
        "strict_xsd": False,
        "custom": False,
    },
]

BUILTIN_IDS = {f["id"] for f in BUILTIN_FEEDS}
BUILTIN_URL_PATHS = {f["url_path"] for f in BUILTIN_FEEDS if f["url_path"]}

# Reserved top-level URL segments we must never let a custom slug collide with.
_RESERVED_PATH_PREFIXES = (
    "admin", "api", "logs", "static", "docs", "openapi.json",
    "omarket-feed.xml", "omarket-acr-feed.xml", "kaspi-feed.xml",
)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def slug_path(slug: str) -> str:
    """Compute URL path for a custom feed from its slug."""
    return f"/feed-{slug}.xml"


def is_valid_slug(slug: str) -> bool:
    if not _SLUG_RE.match(slug):
        return False
    # Reject slugs that collide with built-in ids/url-paths
    if slug in BUILTIN_IDS:
        return False
    if slug_path(slug) in BUILTIN_URL_PATHS:
        return False
    return True


def _custom_to_dict(row: CustomFeed) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "format": "Kaspi XML (strict)" if row.strict_xsd else "Kaspi XML",
        "url_path": slug_path(row.id),
        "enabled": True,
        "target": row.target or "",
        "site": row.site,
        "strict_xsd": bool(row.strict_xsd),
        "custom": True,
    }


async def all_feeds() -> list[dict]:
    """Built-in + user-added feeds."""
    async with async_session() as session:
        rows = (await session.execute(
            select(CustomFeed).order_by(CustomFeed.created_at)
        )).scalars().all()
    return list(BUILTIN_FEEDS) + [_custom_to_dict(r) for r in rows]


async def get_feed_meta(feed_id: str) -> dict | None:
    for f in BUILTIN_FEEDS:
        if f["id"] == feed_id:
            return f
    async with async_session() as session:
        row = (await session.execute(
            select(CustomFeed).where(CustomFeed.id == feed_id)
        )).scalar_one_or_none()
    return _custom_to_dict(row) if row else None


async def find_feed_by_url_path(path: str) -> dict | None:
    for f in BUILTIN_FEEDS:
        if f.get("url_path") == path:
            return f
    # Custom slug match — path comes in as "/feed-xxx.xml"
    if path.startswith("/feed-") and path.endswith(".xml"):
        slug = path[len("/feed-"):-len(".xml")]
        return await get_feed_meta(slug)
    return None


# Kept for backwards compatibility where direct constant access is expected.
FEEDS = BUILTIN_FEEDS
FEEDS_BY_ID = {f["id"]: f for f in BUILTIN_FEEDS}
