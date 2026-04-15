from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.config import get_settings
from app.exporters import get_cached_feed
from app.exporters.registry import all_feeds, find_feed_by_url_path

settings = get_settings()
router = APIRouter()


@router.get("/")
async def root():
    base = f"https://{settings.feed_domain}"
    feeds = await all_feeds()
    return {
        "service": "PressPlay.kz",
        "feeds": {
            f["id"]: f"{base}{f['url_path']}"
            for f in feeds if f.get("url_path") and f.get("enabled")
        },
    }


def _xml(content: str) -> Response:
    return Response(
        content=content,
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600"},
    )


@router.get("/omarket-feed.xml", response_class=Response)
async def omarket_xml():
    return _xml(await get_cached_feed("omarket"))


@router.get("/omarket-acr-feed.xml", response_class=Response)
async def omarket_acr_xml():
    return _xml(await get_cached_feed("omarket_acr"))


@router.get("/kaspi-feed.xml", response_class=Response)
async def kaspi_xml():
    return _xml(await get_cached_feed("kaspi"))


# Custom feeds: /feed-<slug>.xml. Pattern is narrow enough not to clash
# with any other route.
@router.get("/feed-{slug}.xml", response_class=Response)
async def custom_feed_xml(slug: str):
    meta = await find_feed_by_url_path(f"/feed-{slug}.xml")
    if not meta:
        raise HTTPException(status_code=404)
    return _xml(await get_cached_feed(meta["id"]))
