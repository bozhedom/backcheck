"""Лаба 1. Первый сервис: HTTP CRUD в памяти."""

from __future__ import annotations

import re

from .core import Ctx, Fail, Item, Lab, Star, same, score_two, tag

HTTP_FILES = ("requests.http",)
DEPS_FILES = ("requirements.txt", "pyproject.toml", "Pipfile", "package.json", "go.mod", "pom.xml",
              "build.gradle", "build.gradle.kts", "composer.json", "Gemfile", "Cargo.toml")


def _find_requests_file(ctx: Ctx):
    for name in HTTP_FILES:
        if (ctx.repo / name).exists():
            return ctx.repo / name
    for pattern in ("*.http", "**/*.bru", "**/*postman_collection*.json"):
        found = [p for p in ctx.repo.glob(pattern) if "node_modules" not in p.parts]
        if found:
            return found[0]
    return None


def readme_and_requests(ctx: Ctx, it: Item) -> None:
    readme = ctx.repo / "README.md"
    if not readme.exists():
        raise Fail("нет README.md в корне репозитория")
    text = readme.read_text(encoding="utf-8", errors="replace")
    table_rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    if not any(ctx.resource in row for row in table_rows):
        raise Fail(f"в README нет таблицы эндпоинтов с путём /{ctx.resource}")
    it.ok(f"README.md: таблица эндпоинтов есть ({len(table_rows)} строк таблиц)")

    req = _find_requests_file(ctx)
    if req is None:
        raise Fail("нет requests.http (или коллекции Bruno/Postman)")
    body = req.read_text(encoding="utf-8", errors="replace")
    if "/health" not in body or f"/{ctx.resource}" not in body:
        raise Fail(f"{req.name}: нет запросов к /health и /{ctx.resource}")
    count = len(re.findall(r"^(GET|POST|PUT|PATCH|DELETE)\s", body, re.M)) or body.count('"method"')
    it.ok(f"{req.name}: {count} запросов")

    deps = _find_deps_file(ctx)
    if deps is None:
        raise Fail("нет файла зависимостей (requirements.txt, package.json, go.mod, pom.xml и т. п.)")
    it.ok(f"зависимости записаны в {deps}")
    it.eye("В README есть тема проекта и понятный раздел «как запустить» с установкой зависимостей?")


def _find_deps_file(ctx: Ctx) -> str | None:
    for name in DEPS_FILES:
        if (ctx.repo / name).exists():
            return name
    for p in ctx.repo.glob("*/*"):
        if p.name in DEPS_FILES or p.suffix == ".csproj":
            if not {"node_modules", ".venv", "venv", ".git"} & set(p.parts):
                return str(p.relative_to(ctx.repo))
    found = next(ctx.repo.glob("*.csproj"), None)
    return found.name if found else None


def health(ctx: Ctx, it: Item) -> None:
    resp = ctx.expect(ctx.get("/health", auth=None), 200, "GET /health")
    ctype = resp.headers.get("content-type", "")
    if "json" not in ctype:
        raise Fail(f"GET /health: Content-Type {ctype or 'не указан'}, нужен application/json")
    data = ctx.json(resp, "GET /health")
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise Fail(f'GET /health: ожидали {{"status": "ok"}}, получили {resp.text[:80]}')
    it.ok(f"GET /health: 200 {resp.text.strip()[:60]}")


def create(ctx: Ctx, it: Item) -> None:
    body = ctx.body()
    path = ctx.collection()
    resp = ctx.expect(ctx.post(path, body), 201, f"POST {path}")
    obj = ctx.json(resp, f"POST {path}")
    if not isinstance(obj, dict) or ctx.id_field not in obj:
        raise Fail(f"POST {path}: в ответе нет поля {ctx.id_field}")
    ctx.track(ctx.item_path(obj[ctx.id_field]))
    lost = [k for k, v in body.items() if not same(v, obj.get(k))]
    if lost:
        raise Fail(f"POST {path}: поля вернулись не такими, как отправили: {', '.join(lost)}")
    ctx.state["item"] = obj
    it.ok(f"POST {path}: 201, {ctx.id_field}={obj[ctx.id_field]}, поля совпадают с отправленными")


def _ensure_item(ctx: Ctx) -> dict:
    if "item" not in ctx.state:
        ctx.state["item"] = ctx.create()
    return ctx.state["item"]


def read(ctx: Ctx, it: Item) -> None:
    obj = _ensure_item(ctx)
    item_id = obj[ctx.id_field]
    if not ctx.find_in_list(ctx.collection(), item_id):
        raise Fail(f"GET {ctx.collection()}: в списке нет только что созданной записи {item_id}")
    it.ok(f"GET {ctx.collection()}: 200, созданная запись в списке")
    path = ctx.item_path(item_id)
    got = ctx.json(ctx.expect(ctx.get(path), 200, f"GET {path}"), f"GET {path}")
    if not isinstance(got, dict) or str(got.get(ctx.id_field)) != str(item_id):
        raise Fail(f"GET {path}: вернулась другая запись")
    it.ok(f"GET {path}: 200, та же запись")
    missing = ctx.item_path(ctx.contract.get("missing_id", "999999"))
    ctx.expect(ctx.get(missing), 404, f"GET {missing}")
    it.ok(f"GET {missing}: 404")


def update_delete(ctx: Ctx, it: Item) -> None:
    obj = ctx.create()
    path = ctx.item_path(obj[ctx.id_field])
    changes = ctx.need("update")
    resp = ctx.expect(ctx.update(path, changes, ctx.body()), range(200, 300), f"{'PATCH/PUT'} {path}")
    it.ok(f"{resp.request.method} {path}: {resp.status_code}")
    got = ctx.json(ctx.expect(ctx.get(path), 200, f"GET {path}"), f"GET {path}")
    stale = [k for k, v in changes.items() if not same(v, got.get(k))]
    if stale:
        raise Fail(f"GET {path} после обновления: не изменились поля: {', '.join(stale)}")
    it.ok(f"GET {path}: изменения сохранились")
    ctx.expect(ctx.delete(path), 204, f"DELETE {path}")
    it.ok(f"DELETE {path}: 204")
    ctx.expect(ctx.get(path), 404, f"GET {path} после удаления")
    it.ok(f"GET {path} после удаления: 404")


def bad_requests(ctx: Ctx, it: Item, strict: bool = False) -> None:
    path = ctx.collection()
    required = ctx.need("required_field")
    allowed = (400,) if strict else (400, 422)
    want = "400" if strict else "400/422"
    cases = [
        ("битый JSON", dict(content=f'{{"{required}": '.encode(), headers={"Content-Type": "application/json"})),
        (f"пустое {required}", dict(json_body=ctx.body(**{required: ""}))),
        (f"без {required}", dict(json_body={k: v for k, v in ctx.body().items() if k != required})),
    ]
    for label, kwargs in cases:
        resp = ctx.request("POST", path, **kwargs)
        if resp.status_code in range(200, 300):
            obj = resp.json() if "json" in resp.headers.get("content-type", "") else {}
            if isinstance(obj, dict) and ctx.id_field in obj:
                ctx.track(ctx.item_path(obj[ctx.id_field]))
            raise Fail(f"{label}: {resp.status_code}: сервер принял неправильное тело")
        if resp.status_code not in allowed:
            raise Fail(f"{label}: ожидали {want}, получили {resp.status_code}{ctx.short(resp)}")
        it.ok(f"{label}: {resp.status_code}")
    ctx.expect(ctx.get("/health", auth=None), 200, "GET /health после плохих запросов")
    it.ok("сервер жив после плохих запросов")
    weird = ctx.get(ctx.item_path("abc"))
    if weird.status_code >= 500:
        it.warn(f"GET {ctx.item_path('abc')}: {weird.status_code} (лучше 404 или 400)")


# ---------- ★ ----------

def unified_errors(ctx: Ctx, it: Item) -> None:
    cases = ctx.need("errors")
    path = ctx.collection()
    covered = set()
    for case in cases:
        body = ctx.body(**case["body"])
        resp = ctx.post(path, body)
        if resp.status_code in range(200, 300):
            obj = resp.json()
            if isinstance(obj, dict) and ctx.id_field in obj:
                ctx.track(ctx.item_path(obj[ctx.id_field]))
        ctx.expect(resp, 400, f"ошибка в поле {case['field']}")
        data = ctx.json(resp, f"ошибка в поле {case['field']}")
        if not (isinstance(data, dict) and isinstance(data.get("error"), str) and data["error"]):
            raise Fail(f'ошибка в поле {case["field"]}: нет "error" со строкой: {resp.text[:100]}')
        if data.get("field") != case["field"]:
            raise Fail(f'ошибка в поле {case["field"]}: "field": {data.get("field")!r}, ожидали {case["field"]!r}')
        covered.add(case["field"])
        it.ok(f'{case["field"]}: 400 {{"error": "{data["error"][:50]}", "field": "{data["field"]}"}}')
    not_covered = [k for k in ctx.need("create") if k not in covered]
    if not_covered:
        raise Fail(f"в contract.json в errors нет случаев для полей: {', '.join(not_covered)}")
    it.ok("ошибки описаны для всех полей сущности")


def filter_and_pages(ctx: Ctx, it: Item) -> None:
    flt = ctx.need("filter")
    param, value, other = flt["param"], flt["value"], flt["other"]
    mine = [ctx.create(ctx.body(**{param: value}))[ctx.id_field] for _ in range(3)]
    stranger = ctx.create(ctx.body(**{param: other}))[ctx.id_field]
    path = ctx.collection()
    resp = ctx.expect(ctx.get(path, params={param: value, "limit": 100}), 200, f"GET {path}?{param}={value}")
    items = ctx.items(ctx.json(resp, "фильтр"), "фильтр")
    if not items:
        raise Fail(f"GET {path}?{param}={value}: пустой список")
    wrong = [x.get(ctx.id_field) for x in items if not same(value, x.get(param))]
    if wrong or str(stranger) in {str(x.get(ctx.id_field)) for x in items}:
        raise Fail(f"GET {path}?{param}={value}: в выдаче есть записи с другим {param}")
    it.ok(f"GET {path}?{param}={value}: только подходящие записи ({len(items)})")

    first = ctx.items(ctx.json(ctx.expect(ctx.get(path, params={"limit": 2, "offset": 0}), 200, "limit=2"), "limit"), "limit")
    second = ctx.items(ctx.json(ctx.expect(ctx.get(path, params={"limit": 2, "offset": 2}), 200, "offset=2"), "offset"), "offset")
    if len(first) != 2:
        raise Fail(f"GET {path}?limit=2&offset=0: {len(first)} записей, ожидали 2")
    if not second:
        raise Fail(f"GET {path}?limit=2&offset=2: пусто, а записей больше двух")
    ids1 = {str(x.get(ctx.id_field)) for x in first}
    ids2 = {str(x.get(ctx.id_field)) for x in second}
    if ids1 & ids2:
        raise Fail("страницы offset=0 и offset=2 пересекаются")
    it.ok(f"limit=2&offset=0: 2 записи, limit=2&offset=2: {len(second)}, без пересечений")
    del mine


# ---------- ★★ ----------

OPENAPI_PATHS = ("/openapi.json", "/docs/openapi.json", "/swagger.json", "/docs/swagger.json",
                 "/v3/api-docs", "/swagger/doc.json", "/api-docs")


def docs(ctx: Ctx, it: Item) -> None:
    resp = ctx.expect(ctx.get("/docs", auth=None), (200, 301, 302, 307, 308), "GET /docs")
    if resp.status_code != 200:
        resp = ctx.client.get("/docs", follow_redirects=True)
    if "html" not in resp.headers.get("content-type", ""):
        raise Fail("GET /docs: не HTML-страница")
    it.ok("GET /docs: 200 HTML")
    candidates = ([ctx.contract["openapi_path"]] if ctx.contract.get("openapi_path") else []) + list(OPENAPI_PATHS)
    for path in candidates:
        r = ctx.get(path, auth=None)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            spec = r.json()
            if ("openapi" in spec or "swagger" in spec) and any(ctx.resource in p for p in spec.get("paths", {})):
                it.ok(f"{path}: OpenAPI {spec.get('openapi') or spec.get('swagger')}, путей: {len(spec['paths'])}")
                it.eye("В Swagger UI видны все эндпоинты и «Try it out» работает?")
                return
    raise Fail("не нашли OpenAPI-спецификацию с путями ресурса (укажи openapi_path в contract.json)")


def request_log(ctx: Ctx, it: Item) -> None:
    code = f"backcheck-log-{tag()}"
    path = ctx.item_path(code)
    resp = ctx.get(path)
    it.info(f"отправлен GET {path}: {resp.status_code}")
    if ctx.docker_ok() and ctx.contract.get("compose_service") and _compose_running(ctx):
        logs = ctx.compose("logs", "--no-color", "--tail", "300", ctx.contract["compose_service"], show=False)
        line = next((ln for ln in logs.splitlines() if code in ln), None)
        if line is None:
            raise Fail("в docker compose logs нет строки с этим запросом")
        check_log_line(line, resp.status_code)
        it.ok(f"в логе: {line.strip()[-120:]}")
        return
    it.eye(f"В логе сервиса есть строка с «{code}», статусом {resp.status_code} и временем в мс?")


def check_log_line(line: str, status: int) -> None:
    if str(status) not in line:
        raise Fail(f"в строке лога нет статуса {status}: {line.strip()[-120:]}")
    if not re.search(r"\d+(\.\d+)?\s*(ms|мс)|\d+\.\d+", line):
        raise Fail(f"в строке лога нет времени ответа: {line.strip()[-120:]}")


def _compose_running(ctx: Ctx) -> bool:
    out = ctx.compose("ps", "--services", "--filter", "status=running", show=False, check=False)
    return ctx.contract.get("compose_service", "") in out.split()


LAB = Lab(
    number=1,
    title="HTTP CRUD в памяти",
    base=[
        ("1", "README, requests.http, файл зависимостей", readme_and_requests),
        ("2", "GET /health", health),
        ("3", "Создание: 201 и id", create),
        ("4", "Получение: список, по id, 404", read),
        ("5", "Обновление и удаление", update_delete),
        ("6", "Плохие запросы: 400, сервер жив", bad_requests),
    ],
    stars=[
        Star("★ валидация и пагинация", score_two, [
            ("Единый формат ошибок {error, field}", unified_errors),
            ("Фильтр и пагинация", filter_and_pages),
        ]),
        Star("★★ документация и логи", score_two, [
            ("OpenAPI по /docs", docs),
            ("Лог каждого запроса", request_log),
        ]),
    ],
)
