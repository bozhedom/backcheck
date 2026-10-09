"""backcheck ask --lab N: случайный вопрос по лабе, без повторов в течение дня."""

from __future__ import annotations

import argparse
import json
import random
from datetime import date
from pathlib import Path

from .core import bold, dim

QUESTIONS = {
    1: [
        "Что такое клиент и сервер?",
        "Что такое порт? На каком порту работает сервис?",
        "Что такое localhost?",
        "Из чего состоит HTTP-запрос?",
        "Чем GET отличается от POST?",
        "Чем PUT отличается от PATCH?",
        "Что значат коды 201 и 204?",
        "Когда сервис отвечает 400, а когда 404?",
        "Что такое JSON?",
        "Что такое REST?",
    ],
    2: [
        "Зачем бэкенду база данных?",
        "Что такое первичный ключ?",
        "Что такое внешний ключ? Где он в проекте?",
        "Что такое связь один ко многим? Пример из своей темы.",
        "Что такое SQL-инъекция?",
        "Как защититься от SQL-инъекции?",
        "Что такое миграция?",
        "Зачем файл .env и почему его нет в git?",
        "Что такое индекс?",
        "Что такое транзакция?",
    ],
    3: [
        "Чем аутентификация отличается от авторизации?",
        "Когда сервис отвечает 401, а когда 403?",
        "Почему пароль хранят хэшем?",
        "Что такое хэш?",
        "Что передаётся при Basic Auth?",
        "Почему owner_id ставит сервер?",
        "Что такое JWT?",
        "Из каких частей состоит JWT?",
        "Что такое роль пользователя? Какие роли в проекте?",
        "Почему повторная регистрация даёт 409?",
    ],
    4: [
        "Чем фронтенд отличается от бэкенда?",
        "Как страница получает данные с сервера?",
        "Что видно во вкладке Network?",
        "Что такое origin?",
        "Что такое CORS?",
        "Что такое XSS?",
        "Почему textContent безопаснее innerHTML?",
        "Что такое cookie?",
        "Что такое CSRF?",
        "Зачем проверять данные на сервере, если форма уже проверяет?",
    ],
    5: [
        "Что такое Docker?",
        "Чем образ отличается от контейнера?",
        "Что такое Dockerfile?",
        "Что делает docker compose up?",
        "Что такое volume?",
        "Как контейнеры обращаются друг к другу в compose?",
        "Что такое переменная окружения?",
        "Почему при падении второго сервиса ответ 503, а не 500?",
        "Что такое микросервис?",
        "Что такое кэш?",
    ],
}

STATE = Path.home() / ".cache" / "backcheck" / "asked.json"


def _load() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if data.get("date") == date.today().isoformat() else {"date": date.today().isoformat()}


def ask_command(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="backcheck ask", description="Случайный вопрос к лабе")
    p.add_argument("--lab", type=int, choices=range(1, 6), required=True, metavar="N")
    p.add_argument("--reset", action="store_true", help="забыть, какие вопросы уже выпадали сегодня")
    args = p.parse_args(argv)

    state = {"date": date.today().isoformat()} if args.reset else _load()
    used = state.setdefault(str(args.lab), [])
    pool = QUESTIONS[args.lab]
    free = [i for i in range(len(pool)) if i not in used] or list(range(len(pool)))
    if len(free) == len(pool):
        used.clear()
    n = random.choice(free)
    used.append(n)
    print(bold(f"Лаба {args.lab} · вопрос №{n + 1}: ") + pool[n])
    print(dim("  2: ответ сам · 1: с подсказкой · 0: нет ответа"))
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return 0
