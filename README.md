# Al-Style → OMarket Sync

Синхронизация товаров Al-Style → наценка 20% → XML-фид (формат Kaspi) → OMarket.kz

## Архитектура

- **VPS**: ps.kz, 1 CPU / 2 GB RAM / 40 GB SSD
- **IP**: 78.40.109.178
- **Домен**: omarket-feed.pressplay.kz → A-запись на VPS
- **Стек**: FastAPI + SQLite + Caddy (auto SSL)
- **RAM**: ~150-200 MB (SQLite вместо PostgreSQL)

## Деплой

### 1. DNS — добавить A-запись

В панели ps.kz (или Cloudflare):
```
omarket-feed.pressplay.kz  A  78.40.109.178
```

### 2. Подготовить VPS

```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Проект
git clone <repo> ~/al-style-omarket
cd ~/al-style-omarket
cp .env.example .env
nano .env
```

### 3. Заполнить .env

- `ALSTYLE_ACCESS_TOKEN` — токен Al-Style API
- `COMPANY_NAME` — название компании в OMarket
- `MERCHANT_ID` — ID из кабинета поставщика OMarket
- `STORE_IDS` — ID пунктов выдачи, например `["store1","store2"]`

### 4. Запустить

```bash
docker compose up -d --build
```

### 5. Проверить

```bash
curl http://localhost:8000/health
curl https://omarket-feed.pressplay.kz/feed.xml | head -30
```

### 6. Настроить OMarket

В кабинете → Товары → Автоматическая загрузка прайсов (XML):
- URL: `https://omarket-feed.pressplay.kz/feed.xml`
- Логин/пароль: оставить пустыми
- Нажать «Проверить» → «Сохранить»

## Команды

```bash
docker compose logs -f app          # логи
docker compose restart app          # перезапуск
curl -X POST localhost:8000/sync/trigger  # ручная синхронизация
curl localhost:8000/sync/logs       # история синхронизаций
curl localhost:8000/products?limit=5      # товары в БД
```

## Структура

```
├── docker-compose.yml    # app + Caddy
├── Dockerfile
├── Caddyfile             # auto SSL для omarket-feed.pressplay.kz
├── .env
└── app/
    ├── config.py         # настройки
    ├── models.py         # SQLite модели
    ├── fetcher.py        # загрузка из Al-Style API
    ├── xml_generator.py  # Kaspi XML для OMarket
    └── main.py           # FastAPI + cron
```
