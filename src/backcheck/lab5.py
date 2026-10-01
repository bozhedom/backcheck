"""Лаба 5. Docker и запуск одной командой."""

from __future__ import annotations

import json
import re

import httpx

from .core import Ctx, Fail, Item, Lab, Star, score_any, score_two, tag
from .lab1 import check_log_line

DEPS_FILES = ("requirements", "package.json", "package-lock.json", "pnpm-lock", "yarn.lock", "go.mod", "go.sum",
              "pom.xml", "build.gradle", ".csproj", "composer.json", "Gemfile", "pyproject.toml", "poetry.lock",
              "Cargo.toml", "uv.lock")
SECRET_LINE = re.compile(r"^\s*-?\s*\"?(\w*(PASSWORD|SECRET|TOKEN|API_KEY)\w*)\"?\s*[:=]\s*(.+)$", re.I)


def _need_docker(ctx: Ctx) -> None:
    if ctx.compose_file() is None:
        raise Fail("в корне репозитория нет docker-compose.yml / compose.yaml")
    if not ctx.docker_ok():
        raise Fail("docker compose недоступен на этой машине")


def _config(ctx: Ctx) -> dict:
    if "compose_config" not in ctx.state:
        out = ctx.compose("config", "--format", "json", show=False)
        ctx.state["compose_config"] = json.loads(out[out.index("{"):])
    return ctx.state["compose_config"]


# ---------- база ----------

def dockerfile(ctx: Ctx, it: Item) -> None:
    files = [p for p in ctx.repo.rglob("Dockerfile*")
             if not {"node_modules", ".venv", "venv", ".git"} & set(p.parts) and p.is_file()]
    if not files:
        raise Fail("в репозитории нет Dockerfile")
    for f in files:
        rel = f.relative_to(ctx.repo)
        lines = [ln.strip() for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines()]
        copies = [ln for ln in lines if re.match(r"(COPY|ADD)\s", ln, re.I)]
        if any(re.search(r"(^|\s|/)\.env(\s|$)", ln) for ln in copies):
            raise Fail(f"{rel}: .env копируется в образ — секреты окажутся внутри")
        deps_at = next((i for i, ln in enumerate(copies) if any(d in ln for d in DEPS_FILES)), None)
        all_at = next((i for i, ln in enumerate(copies) if re.match(r"(COPY|ADD)\s+(\S+\s+)?\.\s+\S+", ln, re.I)), None)
        if deps_at is not None and (all_at is None or deps_at < all_at):
            it.ok(f"{rel}: зависимости копируются до кода (кэш слоёв работает)")
        elif all_at is not None and any(d.exists() for name in DEPS_FILES for d in f.parent.glob(f"*{name}*")):
            it.warn(f"{rel}: весь код копируется до установки зависимостей — каждая правка пересобирает зависимости")
        else:
            it.ok(f"{rel}: найден")
    if not (ctx.repo / ".dockerignore").exists():
        it.warn("нет .dockerignore — в образ может попасть .env, .git, node_modules")
    if ctx.docker_ok():
        built = [name for name, svc in _config(ctx).get("services", {}).items() if "build" in svc]
        if not built:
            raise Fail("в compose ни один сервис не собирается из Dockerfile (нет build:)")
        it.ok("в compose собираются из Dockerfile: " + ", ".join(built))


def compose_up(ctx: Ctx, it: Item) -> None:
    _need_docker(ctx)
    services = list(_config(ctx).get("services", {}))
    out = ctx.compose("ps", "--format", "json", show=False, check=False)
    running = set()
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith(("{", "[")):
            continue
        data = json.loads(line)
        for svc in data if isinstance(data, list) else [data]:
            if svc.get("State") == "running":
                running.add(svc.get("Service"))
    missing = [s for s in services if s not in running]
    if missing:
        raise Fail(f"не запущены сервисы: {', '.join(missing)} (запусти docker compose up -d)")
    it.ok(f"docker compose: запущены {', '.join(services)}")
    ctx.expect(ctx.get("/health", auth=None), 200, "GET /health")
    it.ok("бэкенд в контейнере отвечает: GET /health → 200")
    front = ctx.contract.get("frontend_url")
    if front:
        try:
            resp = httpx.get(front, timeout=10)
        except httpx.TransportError:
            raise Fail(f"фронт не отвечает: {front}")
        if resp.status_code != 200:
            raise Fail(f"GET {front} → {resp.status_code}")
        it.ok(f"фронт отвечает: {front} → 200")


def foreign_machine(ctx: Ctx, it: Item) -> None:
    if ctx.ci:
        it.ok("CI: проект поднялся на чистой машине GitHub только по .env.example")
    it.eye("Сосед или преподаватель поднял проект строго по README за ≤ 5 минут (комментарий в PR)?")


def volume(ctx: Ctx, it: Item) -> None:
    _need_docker(ctx)
    volumes = list(_config(ctx).get("volumes", {}) or {})
    if volumes:
        it.info("volumes в compose: " + ", ".join(volumes))
    obj = ctx.create(ctx.body())
    path = ctx.item_path(obj[ctx.id_field])
    it.ok(f"создана запись {path}")
    ctx.compose("down", timeout=300)
    ctx.compose("up", "-d", "--wait", timeout=600)
    if not ctx.wait_health(90):
        raise Fail("после docker compose up сервис не ответил за 90 секунд")
    ctx.expect(ctx.get(path), 200, f"GET {path} после down/up")
    it.ok(f"GET {path} после down и up → 200: данные в volume")


def env_config(ctx: Ctx, it: Item) -> None:
    if not (ctx.repo / ".env.example").exists():
        raise Fail("нет .env.example")
    it.ok(".env.example есть")
    files = ctx.tracked_files()
    if files is not None:
        if ".env" in files:
            raise Fail(".env отслеживается git")
        it.ok(".env не в git")
        history = ctx.git("log", "--all", "--format=", "--name-only") or ""
        if ".env" in history.split():
            it.warn(".env когда-то был закоммичен — секреты из него остались в истории git, их стоит сменить")
    compose = ctx.compose_file()
    if compose:
        leaks = []
        for n, line in enumerate(compose.read_text(encoding="utf-8").splitlines(), 1):
            m = SECRET_LINE.match(line)
            if m and not m.group(3).strip().strip("\"'").startswith("$") and m.group(3).strip() not in ("", "''", '""'):
                leaks.append(f"строка {n}: {m.group(1)}")
        if leaks:
            raise Fail(f"{compose.name}: секреты прямо в файле — " + "; ".join(leaks))
        it.ok(f"{compose.name}: секреты берутся из переменных (${{...}} / env_file)")


def logs(ctx: Ctx, it: Item) -> None:
    _need_docker(ctx)
    service = ctx.need("compose_service")
    code = f"backcheck-log-{tag()}"
    path = ctx.item_path(code)
    resp = ctx.get(path)
    out = ctx.compose("logs", "--no-color", "--tail", "500", service)
    line = next((ln for ln in out.splitlines() if code in ln), None)
    if line is None:
        raise Fail(f"в docker compose logs {service} нет строки с запросом {path}")
    check_log_line(line, resp.status_code)
    it.ok(f"в логе: {line.strip()[-110:]}")


# ---------- ★ ----------

def _own_workflows(ctx: Ctx) -> list:
    folder = ctx.repo / ".github" / "workflows"
    files = list(folder.glob("*.yml")) + list(folder.glob("*.yaml")) if folder.exists() else []
    return [f for f in files if "backcheck" not in f.read_text(encoding="utf-8", errors="ignore")]


def pipeline(ctx: Ctx, it: Item) -> None:
    own = _own_workflows(ctx)
    if not own:
        raise Fail("нет своего workflow в .github/workflows (backcheck.yml преподавателя не считается)")
    for f in own:
        text = f.read_text(encoding="utf-8", errors="ignore")
        runs_tests = re.search(r"pytest|npm (run )?test|go test|jest|vitest|mvn .*test|gradle.*test|dotnet test|phpunit", text)
        it.ok(f"{f.name}: " + ("запускает тесты" if runs_tests else "найден"))
    it.eye("На последнем коммите PR зелёная галочка своего workflow (вкладка Actions)?")


def api_tests(ctx: Ctx, it: Item) -> None:
    patterns = {"*.py": r"^\s*(async\s+)?def test_", "*.go": r"^func Test", "*.js": r"\b(it|test)\(",
                "*.ts": r"\b(it|test)\(", "*.java": r"@Test", "*.cs": r"\[(Fact|Test)\]", "*.php": r"function test"}
    count = 0
    for glob, rx in patterns.items():
        for f in ctx.repo.rglob(glob):
            if {"node_modules", ".venv", "venv", ".git"} & set(f.parts):
                continue
            if "test" not in f.name.lower() and "test" not in str(f.parent).lower():
                continue
            count += len(re.findall(rx, f.read_text(encoding="utf-8", errors="ignore"), re.M))
    if count < 3:
        raise Fail(f"нашли тестов: {count}, нужно минимум 3 (создание, 404, 401)")
    it.ok(f"найдено тестов: {count}")
    it.eye("Тесты ходят в API (создание, 404, 401), а в логе CI видно, что они прошли?")


# ---------- ★★ ----------

def public_deploy(ctx: Ctx, it: Item) -> None:
    url = ctx.contract.get("public_url")
    if not url:
        raise Fail("не выбран (нет public_url в contract.json)")
    try:
        resp = httpx.get(url.rstrip("/") + "/health", timeout=15)
    except httpx.TransportError:
        raise Fail(f"{url} не открывается")
    if resp.status_code != 200:
        raise Fail(f"{url}/health → {resp.status_code}")
    it.ok(f"{url}/health → 200")
    it.info(f"проверь API целиком: backcheck --lab 3 --all --url {url}")
    it.eye("Ссылка открылась с телефона через мобильный интернет (не локальная сеть, не туннель с ноутбука)?")


def _trigger(ctx: Ctx, spec: dict):
    parent = ctx.create(ctx.body())
    path = spec["path"].replace("{id}", str(parent[ctx.id_field]))
    body = json.loads(json.dumps(spec.get("body", {})).replace("{tag}", tag()))
    return ctx.request(spec.get("method", "POST").upper(), path, json_body=body)


def second_service(ctx: Ctx, it: Item) -> None:
    spec = ctx.contract.get("second_service")
    if not spec:
        raise Fail("не выбран (нет second_service в contract.json)")
    _need_docker(ctx)
    name = spec["name"]
    if name not in _config(ctx).get("services", {}):
        raise Fail(f"в compose нет сервиса {name}")
    it.ok(f"сервис «{name}» в своём контейнере")
    trigger = spec["trigger"]
    ctx.expect(_trigger(ctx, trigger), range(200, 300), "действие, которое вызывает второй сервис")
    it.ok(f"{trigger.get('method', 'POST')} {trigger['path']} → 2xx (основной сервис сходил во второй)")
    ctx.compose("stop", name)
    try:
        resp = _trigger(ctx, trigger)
    finally:
        ctx.compose("start", name)
    expected = spec.get("expect_when_down", [502, 503, 504])
    if resp.status_code not in expected:
        it.half = True
        it.warn(f"при остановленном «{name}» ответ {resp.status_code}, ожидали {'/'.join(map(str, expected))} — "
                "основной сервис не обрабатывает недоступность зависимости")
        return
    it.ok(f"«{name}» остановлен → {resp.status_code}{ctx.short(resp)}")
    it.eye(f"В docker compose logs видно, что основной сервис ходит в «{name}» по имени сервиса?")


LAB = Lab(
    number=5,
    title="Docker и запуск одной командой",
    base=[
        ("1", "Dockerfile бэкенда", dockerfile),
        ("2", "docker compose up поднимает всё", compose_up),
        ("3", "Запуск на чужой машине по README", foreign_machine),
        ("4", "Данные в volume переживают down/up", volume),
        ("5", "Конфигурация через .env, секретов в git нет", env_config),
        ("6", "Лог каждого запроса в docker compose logs", logs),
    ],
    stars=[
        Star("★ CI с автотестами", score_two, [
            ("Свой пайплайн GitHub Actions", pipeline),
            ("Минимум 3 автотеста API", api_tests),
        ]),
        Star("★★ деплой или второй сервис", score_any, [
            ("(а) Публичный деплой", public_deploy),
            ("(б) Второй сервис на другом языке", second_service),
        ]),
    ],
)
