"""Лаба 2. Подключаем базу данных."""

from __future__ import annotations

import re

from .core import Ctx, Fail, Item, Lab, Star, same, score_three, score_two, tag, yellow

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
        raise Fail(f"GET {path}: в списке нет созданной связанной записи")
    it.ok(f"GET {path}: 200, связанная запись в списке")

    other = ctx.create(ctx.body())
    other_path = ctx.child_path(other[ctx.id_field])
    ctx.expect(ctx.post(other_path, child.get("create_other", child["create"])), 201, f"POST {other_path}")
    listing = ctx.items(ctx.json(ctx.get(path), "GET"), "GET")
    foreign = [x for x in listing if parent_field and str(x.get(parent_field)) != str(pid)]
    if foreign:
        raise Fail(f"GET {path}: в списке связанные записи другого родителя")
    it.ok("записи другого родителя в список не попадают")

    orphan = ctx.post(ctx.child_path(ctx.contract.get("missing_id", "999999")), child["create"])
    if orphan.status_code >= 500 or orphan.status_code in range(200, 300):
        it.warn(f"связанная запись к несуществующему родителю: {orphan.status_code} (лучше 404)")


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
    it.eye("Показано, как схема создаётся из репозитория: скрипт или миграции и команда применения?")


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
    it.eye("Строка подключения читается из переменной окружения (место в коде показано)?")


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
    print(yellow(f"\n  Удалён родитель {pid}, ответ {resp.status_code}; GET его связанных записей, ответ {after.status_code}"
                 f"{ctx.short(after)}"))
    print(yellow("  Вопрос: что стало со связанными записями и почему (CASCADE, RESTRICT, SET NULL)?"))


# ---------- ★ ----------

def migrations_and_seeds(ctx: Ctx, it: Item) -> None:
    tools = [name for path, name in MIGRATION_TOOLS.items() if (ctx.repo / path).exists()]
    if tools:
        it.info("найдено: " + ", ".join(sorted(set(tools))))
    else:
        it.warn("инструмент миграций не найден автоматически")
    it.eye("Миграции идут через инструмент (история файлов и команда применения в README)?")
    it.eye("Сиды запускаются одной командой, и на чистой базе после них список не пуст?")


INDEX_PLAN = re.compile(r"Index Scan|Index Only Scan|Bitmap Index Scan|Bitmap Heap Scan|USING (COVERING )?INDEX|IXSCAN",
                        re.I)


def index_explain(ctx: Ctx, it: Item) -> None:
    readme = ctx.repo / "README.md"
    text = readme.read_text(encoding="utf-8", errors="replace") if readme.exists() else ""
    if "EXPLAIN" not in text.upper():
        raise Fail("в README нет вывода EXPLAIN для частого запроса")
    it.ok("в README есть EXPLAIN")
    if not INDEX_PLAN.search(text):
        raise Fail("в выводе EXPLAIN в README не видно индекса (Index Scan, Bitmap Index Scan, USING INDEX)")
    it.ok("в плане запроса используется индекс")
    it.eye("Индекс создаётся миграцией, и понятно, под какой запрос он сделан?")


def search(ctx: Ctx, it: Item) -> None:
    spec = ctx.need("search")
    param = spec.get("param", "q")
    field = spec.get("field") or ctx.need("text_field")
    word = f"bcsearch{tag(5).lower()}"
    hit = ctx.create(ctx.body(**{field: f"Запись {word.upper()} для поиска"}))
    miss = ctx.create(ctx.body(**{field: f"Запись bcother{tag(5).lower()}"}))
    path = ctx.collection()
    resp = ctx.expect(ctx.get(path, params={param: word, "limit": 100}), 200, f"GET {path}?{param}={word}")
    items = ctx.items(ctx.json(resp, "поиск"), "поиск")
    ids = {str(x.get(ctx.id_field)) for x in items if isinstance(x, dict)}
    if str(hit[ctx.id_field]) not in ids:
        raise Fail(f"GET {path}?{param}={word}: не нашлась запись, где {field} содержит {word.upper()}")
    if str(miss[ctx.id_field]) in ids:
        raise Fail(f"GET {path}?{param}={word}: в выдаче запись, где этого слова нет")
    it.ok(f"?{param}={word}: нашлась запись с {word.upper()} (поиск без учёта регистра), лишних нет")
    evil = "%' OR 1=1 --"
    resp = ctx.get(path, params={param: evil})
    if resp.status_code >= 500:
        raise Fail(f"?{param}={evil}: {resp.status_code}")
    it.ok(f"?{param}={evil!r}: {resp.status_code}, сервер не упал")
    it.eye("Поиск выполняет база (ILIKE, полнотекстовый поиск), а не цикл по всем записям в коде?")


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
    it.eye("Правило в contract.json проверяет ограничение темы, а проверка и запись идут в одной транзакции?")


def stats_endpoint(ctx: Ctx, it: Item) -> None:
    path = ctx.need("stats_path")
    before = ctx.json(ctx.expect(ctx.get(path), 200, f"GET {path}"), f"GET {path}")
    parent = ctx.create(ctx.body())
    if ctx.has("child"):
        ctx.post(ctx.child_path(parent[ctx.id_field]), ctx.need("child", "create"))
    after = ctx.json(ctx.expect(ctx.get(path), 200, f"GET {path}"), f"GET {path}")
    if before == after:
        raise Fail(f"GET {path} не изменился после добавления записей, статистика не пересчитывается")
    it.ok(f"GET {path}: 200, после добавления записей цифры изменились")
    it.eye("Цифры считает база: в коде запрос с GROUP BY и COUNT, SUM или AVG?")


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
        Star("★ миграции, индекс, поиск", score_three, [
            ("Миграции через инструмент и сиды", migrations_and_seeds),
            ("Индекс под частый запрос, EXPLAIN в README", index_explain),
            ("Поиск по тексту ?q=", search),
        ]),
        Star("★★ правило темы и статистика", score_two, [
            ("Правило темы: 409 внутри транзакции", business_rule),
            ("Статистика с агрегатным запросом", stats_endpoint),
        ]),
    ],
    after=cascade_demo,
)
