# PressPlay.kz

Агрегатор товаров от поставщиков для маркетплейсов Казахстана.

Собирает товары от оптовых поставщиков (Al-Style, Marvel и др.), применяет наценку и генерирует XML-фиды для автоматической загрузки на маркетплейсы (OMarket.kz, Kaspi.kz).

## Как это работает

```
Поставщики          PressPlay.kz           Маркетплейсы
┌──────────┐       ┌──────────────┐       ┌──────────┐
│ Al-Style │──API──│  Синхрониза- │       │ OMarket  │
│ Marvel   │──API──│  ция + нацен-│──XML──│ Kaspi    │
│ ...      │──API──│  ка + фиды   │──XML──│ ...      │
└──────────┘       └──────────────┘       └──────────┘
                          │
                    pressplay.kz/admin
                    (панель управления)
```

## Стек

- **Backend**: Python 3.12, FastAPI, SQLAlchemy async
- **БД**: SQLite (aiosqlite)
- **Web**: Caddy (auto SSL)
- **Логи**: Dozzle
- **Деплой**: Docker Compose, GitHub Actions

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

## Конфигурация (.env)

```bash
# Al-Style API
ALSTYLE_API_URL=https://api.al-style.kz/api
ALSTYLE_ACCESS_TOKEN=your-token

# Наценка (1.20 = 20%)
MARKUP_MULTIPLIER=1.20

# OMarket
COMPANY_NAME=Company Name
MERCHANT_ID=000000000000
STORE_IDS='["POS00000001","POS00000002"]'

# Домен
FEED_DOMAIN=pressplay.kz

# Авторизация
ADMIN_PASSWORD=your-password
SECRET_KEY=your-secret-key

# Логи
DOZZLE_USER=admin
DOZZLE_PASSWORD=your-dozzle-password

# Синхронизация
SYNC_INTERVAL_MINUTES=120
```

## Деплой

CI/CD настроен через GitHub Actions. Любой push в `main` автоматически деплоит на VPS:

```
git push → GitHub Actions → SSH → git pull + docker compose up --build
```

Ручной деплой:

```bash
ssh pressplay "cd /opt/al-style-omarket && git pull && docker compose up -d --build"
```

## Инфраструктура

- **VPS**: ps.kz, 1 CPU / 2 GB RAM / 40 GB SSD
- **IP**: 78.40.109.178
- **ОС**: Ubuntu 24
- **SSL**: автоматический (Caddy + Let's Encrypt)
- **Firewall**: UFW (22, 80, 443)
- **Таймзона**: Asia/Almaty

## Roadmap

- [x] Синхронизация товаров Al-Style
- [x] XML-фид (формат Kaspi) для OMarket
- [x] Админ-панель с авторизацией
- [x] Управление наценкой через UI
- [x] Дерево категорий с фильтрацией
- [x] CI/CD (GitHub Actions)
- [x] Мониторинг логов (Dozzle)
- [ ] Мультипоставщик (Marvel и др.)
- [ ] Множественные фиды (OMarket, Kaspi)
- [ ] Наценка по категориям
- [ ] Telegram-уведомления
- [ ] Исключение товаров (чёрный список)
- [ ] Минимальная цена для фидов
- [ ] История цен
- [ ] Экспорт в Excel
- [ ] Мониторинг заказов (webhook)

## Лицензия

Private. All rights reserved.
