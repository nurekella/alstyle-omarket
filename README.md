# PressPlay.kz

Агрегатор товаров от поставщиков для маркетплейсов Казахстана.

Собирает товары от оптовых поставщиков, применяет наценку (глобальную или по категориям), фильтрует по чёрному списку и минимальной цене, генерирует XML-фиды для автоматической загрузки на маркетплейсы (OMarket.kz, Kaspi.kz) и внешние магазины (skstore.kz).

## Как это работает

```
Поставщики              PressPlay.kz              Каналы дистрибуции
┌──────────────┐       ┌──────────────┐          ┌──────────────┐
│ Al-Style     │──API──│              │          │ OMarket.kz   │
│ Marvel       │──API──│ Синхронизация│          │              │
│ Comportal    │──API──│  + наценка   │──XML────▶│ Kaspi.kz     │
│ ASBIS        │──XML──│  + фильтры   │          │              │
│ Mont Tech    │──XLS──│  + кэш       │──XML────▶│ SK Store     │
│ ... (14)     │──WEB──│              │          │              │
└──────────────┘       └──────────────┘          └──────────────┘
                              │
                        pressplay.kz/admin
                        (панель управления)
```

## Стек

- **Backend**: Python 3.12, FastAPI, SQLAlchemy async
- **БД**: SQLite (aiosqlite, WAL-режим)
- **Web**: Caddy (auto SSL)
- **Логи**: Dozzle
- **Деплой**: Docker Compose, GitHub Actions
- **Защита**: slowapi rate-limit, HMAC-сессии (timing-safe), gzip
- **Надёжность**: tenacity retry для Al-Style API, bulk upsert
- **Экспорт**: Jinja2, openpyxl

## Поставщики

В системе зарегистрировано 13 поставщиков. Al-Style подключён и синхронизируется; остальные помечены `Soon` — добавляются по мере готовности интеграции.

| # | Поставщик | Сайт | Статус | Тип интеграции |
|---|-----------|------|--------|----------------|
| 1 | Al-Style | https://b2b.al-style.kz/ | ✅ Active | REST API |
| 2 | Marvel Kazakhstan | https://www.marvel.kz/ | 🕓 Soon | — |
| 3 | Comportal | https://www.comportal.kz/ | 🕓 Soon | — |
| 4 | ASBIS Distribution | https://www.asbis.kz/ | 🕓 Soon | — |
| 5 | Mont Tech | https://monttech.kz/ | 🕓 Soon | — |
| 6 | Alfa Star Technology | https://alfastar.kz/ | 🕓 Soon | — |
| 7 | Wint | https://wint.kz/ | 🕓 Soon | — |
| 8 | Ak-Cent Microsystems | https://ak-cent.kz/ | 🕓 Soon | — |
| 9 | VS Trade | https://www.vstrade.kz/ | 🕓 Soon | — |
| 10 | FDCom | https://fdcom.kz/ | 🕓 Soon | — |
| 11 | Moon.kz | https://moon.kz/ | 🕓 Soon | — |
| 12 | Q-Max Group | https://qmaxgroup.com.kz/ | 🕓 Soon | — |
| 13 | ABRIS | https://www.abrisdc.com/ | 🕓 Soon | — |

Реестр: [`app/suppliers/registry.py`](app/suppliers/registry.py).

## Маркетплейсы и каналы дистрибуции

Куда уходят сгенерированные XML-фиды:

| Канал | URL | Формат | Статус |
|-------|-----|--------|--------|
| **OMarket** | https://pressplay.kz/omarket-feed.xml | Kaspi XML | ✅ Active |
| **Kaspi** | — | Kaspi XML | 🕓 Soon |
| **SK Store** | https://skstore.kz/ | — | 🕓 Soon |

Реестр: [`app/exporters/registry.py`](app/exporters/registry.py).

## Архитектура: типы интеграций поставщиков

Каждый поставщик отдаёт данные по-своему. Модуль `app/suppliers/` спроектирован так, чтобы принять любой из четырёх типов источников без переписывания ядра:

```
app/suppliers/
  registry.py        # Реестр: id, name, url, enabled
  alstyle.py         # Реализация REST API (пример)
  __init__.py        # Экспортирует run_sync активного поставщика
  # будущие:
  marvel.py          # XML-фид по URL
  comportal.py       # Excel-файл (email / скачивание по URL)
  qmax.py            # Парсинг HTML-витрины
```

### Контракт адаптера поставщика

Каждый адаптер реализует одинаковую функцию верхнего уровня:

```python
async def run_sync() -> None:
    """
    Синхронизировать товары и категории поставщика в локальную БД.
    Записать статус в SyncLog, применить category markup через
    app.pricing.build_category_markup_map().
    """
```

Внутри адаптер сам решает как добыть данные. Общая часть — `upsert_products(list[dict], markup)` и `build_category_markup_map()` — переиспользуется.

### Четыре типа источников

| Тип | Когда использовать | Стек | Пример |
|-----|--------------------|------|--------|
| **1. REST API** | Поставщик даёт API с токеном, JSON-ответами, пагинацией | `httpx.AsyncClient`, `tenacity` retry | [`alstyle.py`](app/suppliers/alstyle.py) |
| **2. XML-фид по URL** | Выгрузка доступна как публичный/авторизованный XML по HTTPS; обычно Kaspi/Satu формат | `httpx` + `xml.etree.ElementTree` | planned `marvel.py` |
| **3. Excel-файл** | Поставщик присылает прайс `.xlsx` по e-mail или выкладывает в облако | `openpyxl` (читать), `aiofiles`, IMAP если с почты | planned `comportal.py` |
| **4. Парсинг сайта** | Нет API, нет фида, только витрина | `httpx` + `selectolax` / `parsel`; опционально Playwright | planned `qmax.py` |

### Общие требования к любому адаптеру

- **Идемпотентность**: повторный запуск не портит данные. Всё через `INSERT ... ON CONFLICT DO UPDATE`.
- **Batching**: bulk upsert чанками (см. `DB_CHUNK=500` в `alstyle.py`). Один `session.execute` со списком строк, не цикл `execute` на каждый товар.
- **Retry на транспортных ошибках**: для сети — `tenacity` с экспоненциальным бэкоффом. 429/502/503/504 считаются retryable.
- **SyncLog**: записывать `started_at`, `finished_at`, `status` (`running|success|error`), `products_fetched`, `products_updated`, `error_message`.
- **Уважать category markup**: строить карту через `app.pricing.build_category_markup_map()` и применять её при расчёте `price_omarket`.
- **Rate limiting**: уважать лимиты источника. В Al-Style — `asyncio.sleep(6)` между страницами.

### Как добавить нового поставщика

1. Добавить запись в [`app/suppliers/registry.py`](app/suppliers/registry.py).
2. Создать модуль `app/suppliers/<id>.py` с `async def run_sync()`.
3. Если хотите в UI: переключить `"enabled": True` в registry (UI снимет бейдж `Soon` автоматически).
4. Подключить в планировщике APScheduler (`app/main.py` lifespan) или дать отдельный интервал sync per supplier.

Для переходного периода (несколько поставщиков в одной БД) в `Product` нужно добавить поле `supplier_id` — сейчас схема на одного поставщика.

## Быстрый старт

### 1. Клонировать

```bash
git clone git@github.com:nurekella/alstyle-omarket.git
cd alstyle-omarket
```

### 2. Настроить

```bash
cp .env.example .env
nano .env
```

### 3. Запустить

```bash
docker compose up -d --build
```

### 4. Проверить

```bash
curl https://pressplay.kz/admin
curl https://pressplay.kz/omarket-feed.xml | head -20
```

## URL

| URL | Описание | Доступ |
|-----|----------|--------|
| `/admin` | Панель управления | Пароль |
| `/admin/login` | Страница входа | Публичный |
| `/omarket-feed.xml` | XML-фид для OMarket | Публичный |
| `/logs` | Docker-логи (Dozzle) | Basic Auth |
| `/api/health` | Статус сервиса | API |
| `/api/suppliers` | Список поставщиков | Auth |
| `/api/feeds` | Список XML-фидов | Auth |
| `/api/export/xlsx` | Экспорт прайса в Excel | Auth |

## Конфигурация (.env)

```bash
# Al-Style API
ALSTYLE_API_URL=https://api.al-style.kz/api
ALSTYLE_ACCESS_TOKEN=your-token

# Наценка по умолчанию (1.20 = 20%). Наценка по категориям — через UI.
MARKUP_MULTIPLIER=1.20

# OMarket
COMPANY_NAME=Company Name
MERCHANT_ID=000000000000
STORE_IDS='["POS00000001","POS00000002"]'

# Домен
FEED_DOMAIN=pressplay.kz

# Авторизация (SECRET_KEY: `openssl rand -hex 32`)
ADMIN_PASSWORD=your-strong-password
SECRET_KEY=your-random-hex-secret

# Логи
DOZZLE_USER=admin
DOZZLE_PASSWORD=your-dozzle-password

# Синхронизация
SYNC_INTERVAL_MINUTES=120
```

## Деплой

CI/CD настроен через GitHub Actions. Любой push в `main` автоматически деплоит на VPS:

```
git push → GitHub Actions → SSH → git reset --hard origin/main → docker compose build + up --force-recreate
```

Ручной деплой:

```bash
ssh -p 2222 pressplay "cd /opt/al-style-omarket && git fetch && git reset --hard origin/main && docker compose build app && docker compose up -d --force-recreate app"
```

## Инфраструктура

- **VPS**: ps.kz, 2 CPU / 2 GB RAM / 40 GB SSD
- **IP**: 78.40.109.178
- **ОС**: Ubuntu 24
- **SSL**: автоматический (Caddy + Let's Encrypt)
- **Firewall**: UFW (2222, 80, 443)
- **Таймзона**: Asia/Almaty

## Roadmap

### Ядро
- [x] Синхронизация товаров Al-Style (REST API)
- [x] XML-фид (формат Kaspi) для OMarket
- [x] Админ-панель с авторизацией (HMAC, timing-safe, rate-limit)
- [x] Управление наценкой через UI
- [x] Дерево категорий с фильтрацией
- [x] Наценка по категориям (с наследованием через nested sets)
- [x] Исключение товаров (чёрный список)
- [x] Минимальная цена для фидов
- [x] Экспорт в Excel
- [x] CI/CD (GitHub Actions)
- [x] Мониторинг логов (Dozzle)

### Мультипоставщик
- [x] Реестр поставщиков и фидов (UI + API)
- [ ] Поле `supplier_id` в Product
- [ ] Изоляция per-supplier sync-расписаний
- [ ] Реализации для XML / Excel / парсинг типов

### Мультифид
- [x] Реестр фидов (OMarket / Kaspi / SK Store)
- [ ] Kaspi.kz фид
- [ ] SK Store фид
- [ ] Per-feed конфигурация магазинов и фильтров

### Прочее
- [ ] Telegram-уведомления об ошибках sync
- [ ] Мониторинг заказов (webhook от поставщиков)

## Лицензия

Private. All rights reserved.
