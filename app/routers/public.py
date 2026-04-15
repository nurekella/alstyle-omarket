from fastapi import APIRouter
from fastapi.responses import Response

from app.config import get_settings
from app.exporters import get_cached_feed

settings = get_settings()
router = APIRouter()


@router.get("/")
async def root():
    base = f"https://{settings.feed_domain}"
    return {
        "service": "PressPlay.kz",
        "feeds": {
            "omarket_top1": f"{base}/omarket-feed.xml",
            "omarket_acr": f"{base}/omarket-acr-feed.xml",
            "kaspi": f"{base}/kaspi-feed.xml",
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
