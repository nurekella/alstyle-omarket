from fastapi import APIRouter
from fastapi.responses import Response

from app.config import get_settings
from app.exporters import get_cached_feed

settings = get_settings()
router = APIRouter()


@router.get("/")
async def root():
    return {
        "service": "PressPlay.kz",
        "feeds": {
            "omarket": f"https://{settings.feed_domain}/omarket-feed.xml",
            "kaspi": f"https://{settings.feed_domain}/kaspi-feed.xml",
        },
    }


@router.get("/omarket-feed.xml", response_class=Response)
async def omarket_xml():
    return Response(
        content=await get_cached_feed("omarket"),
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600"},
    )


@router.get("/kaspi-feed.xml", response_class=Response)
async def kaspi_xml():
    return Response(
        content=await get_cached_feed("kaspi"),
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600"},
    )
