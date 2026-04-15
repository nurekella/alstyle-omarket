from fastapi import APIRouter
from fastapi.responses import Response

from app.config import get_settings
from app.exporters import get_cached_feed

settings = get_settings()
router = APIRouter()


@router.get("/")
async def root():
    return {"service": "PressPlay.kz", "feed": f"https://{settings.feed_domain}/omarket-feed.xml"}


@router.get("/omarket-feed.xml", response_class=Response)
async def xml_feed():
    return Response(
        content=await get_cached_feed(),
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600"},
    )
