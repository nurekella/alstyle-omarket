FEEDS = [
    {
        "id": "omarket",
        "name": "OMarket Top1",
        "format": "Kaspi XML",
        "url_path": "/omarket-feed.xml",
        "enabled": True,
        "target": "OMarket.kz (аккаунт Top1)",
        "site": None,
    },
    {
        "id": "omarket_acr",
        "name": "OMarket АЦР",
        "format": "Kaspi XML",
        "url_path": "/omarket-acr-feed.xml",
        "enabled": True,
        "target": "OMarket.kz (аккаунт АЦР)",
        "site": None,
    },
    {
        "id": "kaspi",
        "name": "Kaspi",
        "format": "Kaspi XML",
        "url_path": "/kaspi-feed.xml",
        "enabled": True,
        "target": "Kaspi.kz",
        "site": "https://kaspi.kz/",
    },
    {
        "id": "skstore",
        "name": "SK Store",
        "format": "—",
        "url_path": None,
        "enabled": False,
        "target": "skstore.kz",
        "site": "https://skstore.kz/",
    },
]

FEEDS_BY_ID = {f["id"]: f for f in FEEDS}
