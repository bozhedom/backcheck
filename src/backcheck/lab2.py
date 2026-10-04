"""Лаба 2. Подключаем базу данных."""

from __future__ import annotations

import re

from .core import Ctx, Fail, Item, Lab, Star, same, score_three, score_two, yellow

MIGRATION_HINTS = ("alembic", "migrations", "prisma/migrations", "db/migrations", "Migrations",
                   "src/main/resources/db/migration", "database/migrations", "sql")
MIGRATION_TOOLS = {
    "alembic.ini": "Alembic", "prisma/schema.prisma": "Prisma", "flyway.conf": "Flyway",
    "src/main/resources/db/migration": "Flyway", "Migrations": "EF Migrations", "goose": "goose",
    "knexfile.js": "Knex", ".sequelizerc": "Sequelize", "migrations": "папка migrations",
}
SECRET_URL = re.compile(r"(postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?)(\+\w+)?://([^:/\s\"']+):([^@\s\"']+)@")
SKIP_SUFFIXES = (".md", ".example", ".lock", ".sum", ".svg", ".png", ".jpg")


# ---------- база ----------

def survives_restart(ctx: Ctx, it: Item) -> None:
    obj = ctx.create(ctx.body())
    path = ctx.item_path(obj[ctx.id_field])
    it.ok(f"создана запись {path}")
    if ctx.interactive:
        it.pause("Останови сервис (Ctrl+C) и запусти заново.")
    elif ctx.docker_ok() and ctx.contract.get("compose_service"):
        ctx.compose("restart", ctx.contract["compose_service"])
    else:
        it.eye("Перезапуск: запусти backcheck с -i, чтобы проверить переживание перезапуска")
        return
    if not ctx.wait_health(60):
        raise Fail("после перезапуска /health не отвечает 60 секунд")
    got = ctx.json(ctx.expect(ctx.get(path), 200, f"GET {path} после перезапуска"), "GET")
    lost = [k for k, v in ctx.need("create").items() if not same(v, got.get(k))]
    if lost:
        raise Fail(f"после перезапуска изменились поля: {', '.join(lost)}")
    it.ok(f"GET {path} после перезапуска: 200, запись на месте")


def child_entity(ctx: Ctx, it: Item) -> None:
    child = ctx.need("child")
    parent_field = child.get("parent_field")
    parent = ctx.create(ctx.body())
    pid = parent[ctx.id_field]
    path = ctx.child_path(pid)
    resp = ctx.expect(ctx.post(path, child["create"]), 201, f"POST {path}")
    kid = ctx.json(resp, f"POST {path}")
    if not isinstance(kid, dict) or ctx.id_field not in kid:
        raise Fail(f"POST {path}: в ответе нет {ctx.id_field}")
    if parent_field:
        if parent_field not in kid:
            raise Fail(f"POST {path}: в ответе нет поля связи {parent_field}")
        if str(kid[parent_field]) != str(pid):
            raise Fail(f"POST {path}: {parent_field}={kid[parent_field]}, ожидали {pid}")
    it.ok(f"POST {path}: 201, {parent_field or 'связь'}={pid}")
    ctx.state["child"] = (pid, kid)

    listing = ctx.items(ctx.json(ctx.expect(ctx.get(path), 200, f"GET {path}"), f"GET {path}"), f"GET {path}")
    if str(kid[ctx.id_field]) not in {str(x.get(ctx.id_field)) for x in listing}:
        raise Fail(f"GET {path}: в списке нет созданного ребёнка")
    it.ok(f"GET {path}: 200, ребёнок в списке")

    other = ctx.create(ctx.body())
    other_path = ctx.child_path(other[ctx.id_field])
    ctx.expect(ctx.post(other_path, child.get("create_other", child["create"])), 201, f"POST {other_path}")
    listing = ctx.items(ctx.json(ctx.get(path), "GET"), "GET")
    foreign = [x for x in listing if parent_field and str(x.get(parent_field)) != str(pid)]
    if foreign:
        raise Fail(f"GET {path}: в списке дети другого родителя")
    it.ok("дети другого родителя в список не попадают")

    orphan = ctx.post(ctx.child_path(ctx.contract.get("missing_id", "999999")), child["create"])
    if orphan.status_code >= 500 or orphan.status_code in range(200, 300):
        it.warn(f"ребёнок к несуществующему родителю: {orphan.status_code} (лучше 404)")


def schema_in_repo(ctx: Ctx, it: Item) -> None:
    found = []
    for hint in MIGRATION_HINTS:
        p = ctx.repo / hint
        if p.exists():
            found.append(hint + "/")
    sql_files = [str(p.relative_to(ctx.repo)) for p in ctx.repo.rglob("*.sql")
                 if "node_modules" not in p.parts and ".venv" not in p.parts][:5]
    found += sql_files
    if found:
        it.info("в репо найдено: " + ", ".join(found[:6]))
    else:
        it.warn("не нашли ни миграций, ни .sql: на приёме нужно показать, откуда берётся схема")
    it.eye("Студент показал, как схема создаётся из репо (скрипт/миграции, команда применения)?")


def env_config(ctx: Ctx, it: Item) -> None:
    if not (ctx.repo / ".env.example").exists():
        raise Fail("нет .env.example в корне репозитория")
    it.ok(".env.example есть")
    files = ctx.tracked_files()
    if files is None:
        it.warn("это не git-репозиторий, поэтому не проверить, что .env не закоммичен")
        files = [str(p.relative_to(ctx.repo)) for p in ctx.repo.rglob("*") if p.is_file()
                 and not {".git", "node_modules", ".venv", "venv"} & set(p.parts)]
    elif ".env" in files:
        raise Fail(".env отслеживается git: убери его (git rm --cached .env) и добавь в .gitignore")
    else:
        it.ok(".env не в git")
    leaks = []
    for name in files:
        if name.endswith(SKIP_SUFFIXES) or name.startswith(".env"):
            continue
        path = ctx.repo / name
        try:
            if path.stat().st_size > 300_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in SECRET_URL.finditer(text):
            password = m.group(4)
            if not password.startswith(("${", "<", "$", "%")):
                leaks.append(f"{name}: {m.group(0)[:50]}")
    if leaks:
        raise Fail("пароль от БД в файлах репозитория: " + "; ".join(leaks[:3]))
    it.ok("строк подключения с паролем в коде нет")
    it.eye("Строка подключения читается из переменной окружения (покажи место в коде)?")


def er_diagram(ctx: Ctx, it: Item) -> None:
    readme = ctx.repo / "README.md"
    text = readme.read_text(encoding="utf-8", errors="replace") if readme.exists() else ""
    if "erDiagram" in text:
        entities = sorted(set(re.findall(r"^\s*(\w+)\s*\{", text, re.M)))
        it.ok("в README есть Mermaid erDiagram" + (f": {', '.join(entities)}" if entities else ""))
    elif re.search(r"!\[[^\]]*\]\([^)]+\)", text):
        it.info("в README есть картинка, возможно это ER-диаграмма")
    else:
        raise Fail("в README нет ER-диаграммы (```mermaid erDiagram``` или картинки)")
    it.eye("На диаграмме обе сущности, ключи и связь 1:N?")


def sql_injection(ctx: Ctx, it: Item) -> None:
    field = ctx.need("text_field")
    table = ctx.contract.get("table", ctx.resource)
    payload = f"'); DROP TABLE {table};--"
    obj = ctx.create(ctx.body(**{field: payload}), what=f"POST с {field} = {payload}")
    path = ctx.item_path(obj[ctx.id_field])
    got = ctx.json(ctx.expect(ctx.get(path), 200, f"GET {path}"), "GET")
    if got.get(field) != payload:
        raise Fail(f"строка вернулась искажённой: {got.get(field)!r}")
    it.ok(f"{field} = {payload!r} сохранилась как обычный текст")
    ctx.expect(ctx.get(ctx.collection()), 200, f"GET {ctx.collection()} после инъекции")
    it.ok("таблица цела, список отдаётся")
    flt = ctx.contract.get("filter")
    if flt:
        evil = "' OR 1=1 --"
        resp = ctx.get(ctx.collection(), params={flt["param"]: evil})
        if resp.status_code >= 500:
            raise Fail(f"?{flt['param']}={evil}: {resp.status_code}")
        if resp.status_code == 200:
            items = ctx.items(ctx.json(resp, "фильтр"), "фильтр")
            if any(x.get(flt["param"]) != evil for x in items):
                raise Fail(f"?{flt['param']}={evil} вернул записи с другими значениями: инъекция в фильтре")
        it.ok(f"?{flt['param']}={evil!r}: {resp.status_code}, лишнего не вернул")


def cascade_demo(ctx: Ctx) -> None:
    """Сценарий демо: удалить родителя и объяснить, что стало с детьми. Не балл, а тема для вопроса."""
    if "child" not in ctx.state:
        return
    pid, kid = ctx.state.pop("child")
    resp = ctx.delete(ctx.item_path(pid))
    after = ctx.get(ctx.child_path(pid))
    print(yellow(f"\n  Демо: удалили родителя {pid}, ответ {resp.status_code}; GET его детей, ответ {after.status_code}"
                 f"{ctx.short(after)}"))
    print(yellow("  Спроси: что стало с дочерними записями и почему (CASCADE, RESTRICT, SET NULL)?"))


# ---------- ★ ----------

def migrations_tool(ctx: Ctx, it: Item) -> None:
    tools = [name for path, name in MIGRATION_TOOLS.items() if (ctx.repo / path).exists()]
    if tools:
        it.info("найдено: " + ", ".join(sorted(set(tools))))
    else:
        it.warn("инструмент миграций не найден автоматически")
    it.eye("Миграции через инструмент (история миграций + команда upgrade в README)?")


def seeds(ctx: Ctx, it: Item) -> None:
    it.eye("Студент запустил сиды на чистой БД, и список не пуст?")


def sql_filter_sort(ctx: Ctx, it: Item) -> None:
    sort = ctx.need("sort")
    path = ctx.collection()
    for _ in range(3):
        ctx.create(ctx.body())
    for direction in ("", "-"):
        value = direction + sort["value"].lstrip("-")
        resp = ctx.expect(ctx.get(path, params={sort["param"]: value, "limit": 100}), 200, f"GET {path}?{sort['param']}={value}")
        items = ctx.items(ctx.json(resp, "sort"), "sort")
        keys = [x.get(sort["field"]) for x in items if x.get(sort["field"]) is not None]
        if not is_sorted_any(keys, reverse=bool(direction)):
            raise Fail(f"?{sort['param']}={value}: порядок {sort['field']} неверный")
        it.ok(f"?{sort['param']}={value}: отсортировано ({len(items)})")
    if ctx.has("filter"):
        flt = ctx.need("filter")
        items = ctx.items(ctx.json(ctx.get(path, params={flt["param"]: flt["value"]}), "filter"), "filter")
        if any(not same(flt["value"], x.get(flt["param"])) for x in items):
            raise Fail("фильтр возвращает лишние записи")
        it.ok(f"?{flt['param']}={flt['value']}: фильтр работает")
    it.eye("В коде слоя БД видно WHERE / ORDER BY (или их ORM-аналог), а не сортировку списка в памяти?")


def is_sorted_any(keys: list, reverse: bool) -> bool:
    """Отсортировано ли хоть по одному из правил сравнения строк.

    Разные БД и локали сортируют строки по-разному (C-локаль: заглавные раньше строчных,
    ICU/glibc: регистр и знаки препинания почти не важны), поэтому принимаем любой из вариантов.
    """
    def as_date(v):
        from .core import _as_datetime
        return _as_datetime(v)

    rules = [
        lambda v: v,
        lambda v: v.casefold() if isinstance(v, str) else v,
        lambda v: "".join(ch for ch in v.casefold() if ch.isalnum()) if isinstance(v, str) else v,
        lambda v: as_date(v) or v,
    ]
    for rule in rules:
        try:
            converted = [rule(k) for k in keys]
            if converted == sorted(converted, reverse=reverse):
                return True
        except TypeError:
            continue
    return False


# ---------- ★★ ----------

def _rule_request(ctx: Ctx, spec: dict, pid) -> object:
    path = spec["path"].replace("{id}", str(pid))
    return ctx.request(spec.get("method", "POST").upper(), path, json_body=spec.get("body", ...))


def business_rule(ctx: Ctx, it: Item) -> None:
    rule = ctx.need("rule")
    parent = ctx.create(ctx.body(**rule.get("parent", {})))
    pid = parent[ctx.id_field]
    it.info(f"фишка: {rule.get('description', '(описание не указано)')}")
    setups = rule.get("setup", [])
    for spec in setups if isinstance(setups, list) else [setups]:
        resp = _rule_request(ctx, spec, pid)
        ctx.expect(resp, range(200, 300), f"подготовка {spec.get('method', 'POST')} {spec['path']}")
        it.ok(f"подготовка {spec.get('method', 'POST')} {spec['path'].replace('{id}', str(pid))}: {resp.status_code}")
    spec = ctx.need("rule", "violate")
    expected = rule.get("expect_status", 409)
    resp = _rule_request(ctx, spec, pid)
    ctx.expect(resp, expected, f"нарушение правила {spec.get('method', 'POST')} {spec['path']}")
    it.ok(f"нарушение правила: {resp.status_code}{ctx.short(resp)}")
    it.eye("Правило в contract.json действительно проверяет фишку темы, а не что-то тривиальное?")


def stats_endpoint(ctx: Ctx, it: Item) -> None:
    path = ctx.need("stats_path")
    before = ctx.json(ctx.expect(ctx.get(path), 200, f"GET {path}"), f"GET {path}")
    parent = ctx.create(ctx.body())
    if ctx.has("child"):
        ctx.post(ctx.child_path(parent[ctx.id_field]), ctx.need("child", "create"))
    after = ctx.json(ctx.expect(ctx.get(path), 200, f"GET {path}"), f"GET {path}")
    if before == after:
        raise Fail(f"GET {path} не изменился после добавления записей. Статистика не считается из БД?")
    it.ok(f"GET {path}: 200, после добавления записей цифры изменились")
    it.eye("В коде SQL с GROUP BY / COUNT / AVG (агрегат считает БД)?")


LAB = Lab(
    number=2,
    title="Подключаем базу данных",
    base=[
        ("1", "Данные переживают перезапуск", survives_restart),
        ("2", "Вторая сущность 1:N, вложенный маршрут", child_entity),
        ("3", "Схема создаётся из репозитория", schema_in_repo),
        ("4", "Подключение из переменных окружения", env_config),
        ("5", "ER-диаграмма в README", er_diagram),
        ("6", "SQL-инъекция сохраняется как текст", sql_injection),
    ],
    stars=[
        Star("★ миграции, сиды, SQL-фильтры", score_three, [
            ("Миграции через инструмент", migrations_tool),
            ("Сиды с тестовыми данными", seeds),
            ("Фильтрация и сортировка в SQL", sql_filter_sort),
        ]),
        Star("★★ фишка темы и статистика", score_two, [
            ("Фишка с правильным кодом ошибки", business_rule),
            ("Статистика с агрегатным запросом", stats_endpoint),
        ]),
    ],
    after=cascade_demo,
)
