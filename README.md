# backcheck

Чекер лабораторных работ курса «Backend-разработка». Шлёт запросы к твоему сервису и по каждому пункту чек-листа лабы печатает ✓ или ✗. Язык и фреймворк бэкенда не важны — проверяется только поведение по HTTP.

## Установка

Нужен Python 3.10+.

```bash
pipx install git+https://github.com/bozhedom/backcheck@v1
# или без pipx:
pip install --user git+https://github.com/bozhedom/backcheck@v1

backcheck --version
```

Обновиться до свежей версии: `pipx reinstall backcheck` (или `pip install --user --force-reinstall git+https://github.com/bozhedom/backcheck@v1`).

## Запуск

Из корня своего репозитория, где лежит `contract.json`, при запущенном сервисе:

```bash
backcheck --lab 1                 # база лабы 1: шесть пунктов
backcheck --lab 1 --stars         # плюс ★ и ★★
backcheck --lab 2 --only 6        # только пункт 6
backcheck --lab 3 --all           # регрессия: базы лаб 1–3 подряд
backcheck --lab 4 -i              # интерактивно: на пунктах «глазами» спрашивает [y/n]
backcheck --lab 1 -v              # печатать каждый запрос и ответ — для отладки
backcheck --lab 3 --stars --report  # записать backcheck-report.md для описания PR
```

| Флаг | Что делает |
| --- | --- |
| `--lab N` | Номер лабы 1–5 |
| `--stars` | Проверить ★ и ★★ |
| `--all` | Базы всех лаб от 1 до N |
| `--only K` | Только пункт K базы |
| `-i` | Интерактивный режим: пункты «Глаз» превращаются в вопросы `[y/n]`, пункты «Действие» ждут Enter |
| `--url URL` | Адрес сервиса вместо `base_url` из `contract.json` |
| `--contract PATH` | Путь к `contract.json` |
| `--report` | Записать `backcheck-report.md` (хэш коммита, время, версия, результаты) |
| `--keep` | Не удалять записи, созданные чекером |
| `--ci` | Режим CI для лабы 5: сам делает `cp .env.example .env` и `docker compose up` |
| `-v` | Подробный вывод запросов и ответов |

Код выхода: `0` — в базе нет ✗, `1` — есть ✗, `2` — сервис не отвечает или `contract.json` неверный.

Чекер создаёт свои записи и пользователей с префиксами `bc_` / `backcheck-` и в конце удаляет созданные записи.

### Ещё две команды

```bash
backcheck jwt <токен>             # разобрать JWT: header, payload, срок жизни
backcheck ask --lab 3 --edit      # для преподавателя: случайный вопрос из банка и живая правка
```

## Три типа пунктов

| Значок | Тип | Кто решает |
| --- | --- | --- |
| ✓ / ✗ | **Авто** — чекер сам шлёт запросы | Чекер |
| ⏸ | **Действие** — чекер просит перезапустить сервис или удалить запись в интерфейсе, потом проверяет результат | Чекер |
| 👁 | **Глаз** — то, чего не видно по HTTP: README, хэш в БД, интерфейс | Преподаватель (`-i` → `[y/n]`) |

Без `-i` пункты «Глаз» помечаются «проверит преподаватель» и не считаются ни ✓, ни ✗. ⚠ — предупреждения: на баллы не влияют, но про это могут спросить.

## contract.json

Темы у всех разные, а чекер один: в `contract.json` ты описываешь свою тему. Файл растёт от лабы к лабе.

```json
{
  "base_url": "http://localhost:8000",
  "resource": "habits",
  "id_field": "id",
  "missing_id": "999999",
  "list_key": null,
  "create": { "title": "Пить воду", "goal_per_day": 8, "status": "active" },
  "update": { "title": "Пить много воды" },
  "required_field": "title",
  "errors": [
    { "body": { "title": "" }, "field": "title" },
    { "body": { "goal_per_day": -1 }, "field": "goal_per_day" },
    { "body": { "status": "unknown" }, "field": "status" }
  ],
  "filter": { "param": "status", "value": "active", "other": "archived" },

  "text_field": "title",
  "sort": { "param": "sort", "value": "title", "field": "title" },
  "child": {
    "resource": "checks",
    "create": { "date": "2026-10-01" },
    "parent_field": "habit_id"
  },
  "rule": {
    "description": "Нельзя отметить привычку дважды за один день",
    "setup": [{ "method": "POST", "path": "/api/habits/{id}/checks", "body": { "date": "2026-10-01" } }],
    "violate": { "method": "POST", "path": "/api/habits/{id}/checks", "body": { "date": "2026-10-01" } },
    "expect_status": 409
  },
  "stats_path": "/api/habits/stats",

  "auth": {
    "scheme": "basic",
    "register_path": "/auth/register",
    "login_path": "/auth/login",
    "me_path": "/auth/me",
    "login_field": "username",
    "password_field": "password",
    "token_field": "access_token",
    "owner_field": "owner_id",
    "admin": { "username": "admin", "password": "admin-test-pass" }
  },

  "frontend_url": "http://localhost:5173",

  "compose_service": "backend",
  "public_url": null,
  "second_service": null
}
```

| Поле | Лаба | Зачем |
| --- | --- | --- |
| `base_url` | 1 | Адрес сервиса |
| `api_prefix` | 1 | Префикс путей, по умолчанию `/api` |
| `resource` | 1 | Имя ресурса во множественном числе: `/api/<resource>` |
| `id_field` | 1 | Как называется id в ответе (по умолчанию `id`) |
| `missing_id` | 1 | Заведомо несуществующий id (для 404). Для UUID — любой несуществующий UUID |
| `list_key` | 1 | `null`, если список — массив; `"items"`, если ответ `{"items": [...]}` |
| `create` | 1 | Правильное тело создания. Все поля должны вернуться в ответе теми же значениями |
| `update` | 1 | Какие поля поменять при обновлении |
| `required_field` | 1 | Обязательное поле: чекер сделает его пустым и выкинет, ожидая 400 |
| `errors` | 1 ★ | Случаи ошибок: поля из `body` подставляются поверх `create`, ожидается 400 и `{"error": ..., "field": <field>}`. Нужен случай на каждое поле из `create` |
| `filter` | 1 ★ | Параметр фильтра, подходящее и неподходящее значение |
| `openapi_path` | 1 ★★ | Путь к OpenAPI JSON, если не стандартный (`/openapi.json`, `/swagger.json`, `/v3/api-docs` ищутся сами) |
| `text_field` | 2 | Строковое поле для проверки SQL-инъекции |
| `table` | 2 | Имя таблицы, если отличается от `resource` |
| `sort` | 2 ★ | Параметр сортировки и поле, по которому сортируется (`-` в начале значения — по убыванию) |
| `child` | 2 | Вторая сущность: `resource` (путь `/api/<resource>/{id}/<child.resource>`), тело `create`, поле связи `parent_field` |
| `rule` | 2 ★★ | Фишка темы: подготовительные запросы `setup`, запрос-нарушение `violate`, ожидаемый код. `{id}` в пути — id родителя, которого создаст чекер |
| `stats_path` | 2 ★★ | Эндпоинт статистики |
| `auth` | 3 | Пути и имена полей авторизации. `scheme`: `basic` или `bearer`. `register_extra` — доп. поля регистрации (например, email). `admin` — тестовый админ из сидов (для ★★) |
| `frontend_url` | 4 | Адрес фронта (для CORS и проверки страницы) |
| `compose_service` | 5 | Имя сервиса бэкенда в compose (для логов и перезапуска) |
| `public_url` | 5 ★★а | Публичный адрес деплоя |
| `second_service` | 5 ★★б | Второй сервис: `name` в compose, `trigger` — запрос к основному сервису, который ходит во второй (`{id}` — новый родитель, `{tag}` — случайная строка), `expect_when_down` — какие коды ждать, когда второй остановлен |

## Лаба 5 в GitHub Actions

Workflow преподавателя подключается в репозитории одной строкой — файл `.github/workflows/backcheck.yml`:

```yaml
name: backcheck
on:
  pull_request:
  workflow_dispatch:
jobs:
  backcheck:
    uses: bozhedom/backcheck/.github/workflows/lab5.yml@v1
```

На чистой машине GitHub он копирует `.env.example` в `.env`, делает `docker compose up --build --wait` и прогоняет `backcheck --lab 5 --all --ci`. Зелёная галочка в PR — проект поднимается на чужой машине и проходит авто-пункты всех лаб.

## Для преподавателя

- `backcheck ask --lab N [--edit]` — вопрос из банка и живая правка, без повторов в течение дня (`--reset` — начать заново).
- `scripts/deadline-snapshot.sh` — снимок коммитов всех открытых PR на момент дедлайна.
- Перед каждой встречей прогоните чекер на эталонном проекте: там всё должно быть зелёным.
