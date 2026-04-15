"""
Brand extraction from product names.

Used as a fallback when the supplier leaves the brand field empty.
Covers ~250 brands common in Al-Style / KZ electronics retail.
Ordered longest-first to avoid 'HP' matching inside 'HPE'.
"""
import re

# Canonical brand spelling — what we write into Product.brand.
_RAW_BRANDS = [
    # IT / PC / Components
    "Apple", "ASUS", "Acer", "Dell", "HPE", "HP", "Lenovo", "MSI", "Gigabyte",
    "Intel", "AMD", "NVIDIA", "Western Digital", "WD", "Seagate", "Kingston",
    "ADATA", "A-DATA", "Corsair", "Crucial", "Samsung", "SanDisk", "Transcend",
    "Toshiba", "Hitachi", "Patriot", "GoodRAM", "Goodram", "Silicon Power",
    "Team", "TeamGroup", "G.Skill", "G-Skill", "Micron", "Lexar", "Mushkin",
    "Supermicro", "Fujitsu", "Panasonic", "Huawei", "ZOTAC", "PowerColor",
    "Sapphire", "Palit", "Inno3D", "KFA2", "Colorful", "XFX", "EVGA",
    "Thermaltake", "Cooler Master", "Deepcool", "Noctua", "be quiet!",
    "Seasonic", "Chieftec", "FSP", "AeroCool", "Zalman", "NZXT", "Fractal Design",
    "Lian Li", "BitFenix", "Arctic", "Phanteks",

    # Mobile
    "iPhone", "Xiaomi", "Redmi", "POCO", "Mi", "HONOR", "Honor", "OPPO",
    "realme", "Realme", "Vivo", "OnePlus", "Nokia", "Motorola", "Google",
    "Sony", "Tecno", "Infinix", "itel", "ITEL", "BlackView", "Doogee",
    "Ulefone", "Oukitel",

    # Peripherals / Input
    "Logitech", "Razer", "SteelSeries", "HyperX", "Genius", "A4Tech", "A4-Tech",
    "Defender", "OKLICK", "Oklick", "SVEN", "Sven", "Keychron", "Glorious",
    "Ducky", "Cherry", "Endgame Gear", "Pulsar",

    # Networking
    "TP-Link", "TP Link", "Cisco", "D-Link", "Netgear", "Tenda", "MikroTik",
    "Mikrotik", "Zyxel", "Ubiquiti", "Mercusys", "Keenetic", "Asus", "Totolink",
    "Ruijie", "H3C", "Eltex", "SNR",

    # Audio / Headsets / Speakers
    "JBL", "Bose", "Sennheiser", "Philips", "Beats", "Marshall",
    "Harman Kardon", "Anker", "Soundcore", "Edifier", "Creative", "Tronsmart",
    "Audio-Technica", "AKG", "Shure", "Rode", "Røde", "Yamaha",

    # TVs / Monitors / Projectors
    "LG", "BenQ", "ViewSonic", "AOC", "Iiyama", "Iiyam", "Philips", "Hyundai",
    "Xiaomi", "Haier", "TCL", "Artel", "Skyworth", "Kivi", "Kraft",

    # Home appliances (some show up in Al-Style catalog)
    "Bosch", "Siemens", "Braun", "Tefal", "Moulinex", "Rowenta", "Polaris",
    "Redmond", "REDMOND",

    # Power fittings / Electrical
    "Bticino", "Schneider", "Legrand", "ABB", "Lezard", "Werkel", "Viko",
    "Makel", "Gira", "Jung",

    # Accessories / Cases / Chargers
    "Baseus", "Borofone", "Hoco", "HOCO", "Remax", "Belkin", "Ugreen",
    "UGREEN", "Nillkin", "Spigen", "ESR", "Benks", "AUKEY", "Aukey",
    "RAVPower", "CHOETECH", "Choetech", "Yesido", "Usams", "USAMS",
    "Earldom", "WUW", "Devia", "Mcdodo", "Joyroom",

    # Printers / Cartridges
    "Canon", "Epson", "Brother", "Xerox", "Kyocera", "Ricoh", "Sharp",
    "Pantum", "OKI", "Lexmark", "Konica Minolta", "Katun", "ColorWay",
    "Hi-Black", "NV Print", "CACTUS",

    # Cables / Components wholesale
    "ORICO", "Orico", "Greenconnect", "GreenConnect", "Vention", "Cablexpert",
    "Gembird", "Buro", "Exegate", "ExeGate", "Aceline",

    # Smart home / IoT
    "Yeelight", "Aqara", "Tuya", "Sonoff", "eWeLink",

    # Cameras / Video
    "GoPro", "DJI", "Insta360", "Canon", "Nikon", "Fujifilm", "Olympus",
    "Panasonic", "Leica", "Sigma", "Tamron", "Hikvision", "Dahua", "RVi",
    "TP-Link Tapo", "Tapo", "Ezviz", "Imou",

    # Power / UPS / Stabilizers
    "APC", "Ippon", "Powercom", "CyberPower", "Eaton", "Tripp Lite",
    "SVC", "Sven", "Luxeon",

    # Gaming / Consoles
    "PlayStation", "Xbox", "Nintendo", "Steam Deck", "ASUS ROG", "ROG",
    "Predator",

    # Storage / Bags
    "Targus", "Case Logic", "Rivacase", "RivaCase", "Wenger", "Dell",
    "HP", "Samsonite",

    # Other common
    "Philips", "Xiaomi", "Mi", "Xtreamer", "VOXLink", "EnGenius",
]


def _build_patterns() -> list[tuple[str, re.Pattern]]:
    """
    Build (canonical_brand, compiled_regex) list, sorted so longer names
    win (prevents 'HP' matching 'HPE' or 'Mi' matching 'Microsoft').
    """
    seen_lower: dict[str, str] = {}
    for b in _RAW_BRANDS:
        key = b.lower()
        if key not in seen_lower or len(b) > len(seen_lower[key]):
            seen_lower[key] = b
    # Sort longest first
    uniq = sorted(seen_lower.values(), key=lambda s: (-len(s), s.lower()))
    out: list[tuple[str, re.Pattern]] = []
    for b in uniq:
        # Allow alphanumeric + punctuation inside; word boundary on both sides
        # Escape and make '-' / '.' optional neighbours
        escaped = re.escape(b)
        pat = re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
        out.append((b, pat))
    return out


_PATTERNS = _build_patterns()


def extract_brand(name: str) -> str | None:
    """Find the first known brand mentioned in the product name."""
    if not name:
        return None
    for canonical, pat in _PATTERNS:
        if pat.search(name):
            return canonical
    return None
