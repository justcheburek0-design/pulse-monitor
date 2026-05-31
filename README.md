# Pulse — платформа мониторинга и алертинга

## Что это

Pulse — production-ready SaaS-платформа для мониторинга сайтов, API, серверов и DNS. HTTP/HTTPS, TCP, ICMP, DNS проверки с настраиваемыми алертами через Email, Slack, Discord, Telegram, Webhook. Дашборды, инцидент-менеджмент, командная работа.

## Технологии

- **Backend:** FastAPI, SQLAlchemy async, APScheduler
- **Database:** PostgreSQL (production) / SQLite (dev/tests)
- **Auth:** JWT (access + refresh tokens)
- **Notifications:** Email (SMTP), Slack, Discord, Telegram, Webhook
- **CLI:** Typer + Rich
- **Tests:** pytest, pytest-asyncio, httpx
- **Containers:** Docker, docker-compose

## Быстрый старт

```bash
# Установка зависимостей
pip install -e ".[dev]"

# Инициализация БД
pulse-cli init

# Создание админа
pulse-cli create-admin

# Запуск
pulse-cli serve --reload
# или
uvicorn src.main:app --reload
```

API документация: `http://localhost:8000/docs`

## API Endpoints

### Auth
- `POST /api/v1/auth/register` — регистрация
- `POST /api/v1/auth/login` — вход
- `POST /api/v1/auth/refresh` — обновление токена
- `GET /api/v1/auth/me` — профиль

### Monitors
- `GET /api/v1/monitors` — список мониторов
- `POST /api/v1/monitors` — создать монитор
- `GET /api/v1/monitors/{id}` — получить монитор
- `PATCH /api/v1/monitors/{id}` — обновить монитор
- `DELETE /api/v1/monitors/{id}` — удалить монитор
- `POST /api/v1/monitors/{id}/pause` — приостановить
- `POST /api/v1/monitors/{id}/resume` — возобновить
- `GET /api/v1/monitors/{id}/checks` — история проверок
- `GET /api/v1/monitors/{id}/stats` — статистика

### Alerts
- `GET /api/v1/alerts` — список алертов
- `POST /api/v1/alerts/{id}/acknowledge` — подтвердить
- `POST /api/v1/alerts/{id}/resolve` — решить
- `GET /api/v1/alerts/channels` — каналы уведомлений
- `POST /api/v1/alerts/channels` — создать канал
- `GET /api/v1/alerts/rules` — правила алертов
- `POST /api/v1/alerts/rules` — создать правило

### Dashboards
- `GET /api/v1/dashboards` — список дашбордов
- `POST /api/v1/dashboards` — создать дашборд
- `GET /api/v1/dashboards/{id}` — дашборд с виджетами
- `PATCH /api/v1/dashboards/{id}` — обновить
- `DELETE /api/v1/dashboards/{id}` — удалить
- `POST /api/v1/dashboards/{id}/widgets` — добавить виджет

### Incidents
- `GET /api/v1/incidents` — список инцидентов
- `POST /api/v1/incidents` — создать инцидент
- `PATCH /api/v1/incidents/{id}` — обновить
- `POST /api/v1/incidents/{id}/resolve` — решить
- `POST /api/v1/incidents/{id}/comments` — комментарий

### Teams
- `GET /api/v1/teams` — список команд
- `POST /api/v1/teams` — создать команду
- `GET /api/v1/teams/{id}` — детали команды
- `GET /api/v1/teams/{id}/members` — участники
- `POST /api/v1/teams/{id}/invites` — пригласить

## Типы мониторов

| Тип | Описание |
|-----|----------|
| HTTP | HTTP GET/POST/PUT/DELETE проверки |
| HTTPS | HTTPS с валидацией SSL |
| TCP | Проверка порта |
| ICMP | Ping |
| DNS | DNS резолвинг |
| Keyword | Проверка наличия текста в ответе |
| GraphQL | GraphQL запросы |

## Архитектура

```
src/
├── api/
│   ├── middleware/       # Auth, rate limit, CORS, logging, errors
│   └── routes/           # REST endpoints
├── cli/                  # CLI утилита (Typer)
├── config/               # Settings, database
├── models/               # SQLAlchemy модели
├── services/             # Бизнес-логика
├── workers/              # Проверки и планировщик
└── main.py               # FastAPI app factory
tests/
├── conftest.py           # Фикстуры
├── unit/                 # Юнит-тесты
└── integration/          # Интеграционные тесты
```

## Тестирование

```bash
# Все тесты
pytest -v

# С покрытием
pytest -v --cov=src --cov-report=html

# Только юнит-тесты
pytest tests/unit/ -v

# Только интеграционные
pytest tests/integration/ -v
```

## Docker

```bash
docker-compose up -d
```

## Лицензия

MIT
