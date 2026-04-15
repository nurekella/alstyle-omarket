FEEDS = [
    {
        "id": "omarket",
        "name": "OMarket",
        "format": "Kaspi XML",
        "url_path": "/omarket-feed.xml",
        "enabled": True,
        "target": "skstore.kz / OMarket.kz",
    },
    {
        "id": "kaspi",
        "name": "Kaspi",
        "format": "Kaspi XML",
        "url_path": None,
        "enabled": False,
        "target": "skstore.kz / Kaspi.kz",
    },
]

FEEDS_BY_ID = {f["id"]: f for f in FEEDS}
