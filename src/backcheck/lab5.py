"""Лаба 5. Docker и соседние сервисы."""

from __future__ import annotations

import json
import re
import time

import httpx

from .core import Ctx, Fail, Item, Lab, Star, score_two, tag
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
            raise Fail(f"{rel}: .env копируется в образ, и секреты окажутся внутри")
        deps_at = next((i for i, ln in enumerate(copies) if any(d in ln for d in DEPS_FILES)), None)
        all_at = next((i for i, ln in enumerate(copies) if re.match(r"(COPY|ADD)\s+(\S+\s+)?\.\s+\S+", ln, re.I)), None)
        if deps_at is not None and (all_at is None or deps_at < all_at):
            it.ok(f"{rel}: зависимости копируются до кода (кэш слоёв работает)")
        elif all_at is not None and any(d.exists() for name in DEPS_FILES for d in f.parent.glob(f"*{name}*")):
            it.warn(f"{rel}: весь код копируется до установки зависимостей, и каждая правка пересобирает зависимости")
        else:
            it.ok(f"{rel}: найден")
    if not (ctx.repo / ".dockerignore").exists():
        it.warn("нет .dockerignore: в образ может попасть .env, .git, node_modules")
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
    it.ok("бэкенд в контейнере отвечает: GET /health: 200")
    front = ctx.contract.get("frontend_url")
    if front:
        try:
            resp = httpx.get(front, timeout=10)
        except httpx.TransportError:
            raise Fail(f"фронт не отвечает: {front}")
        if resp.status_code != 200:
            raise Fail(f"GET {front}: {resp.status_code}")
        it.ok(f"фронт отвечает: {front}: 200")
    if ctx.ci:
        it.ok("CI: проект поднялся на чистой машине GitHub только по .env.example")
        return
    workflow = ctx.repo / ".github" / "workflows" / "backcheck.yml"
    if not workflow.exists():
        raise Fail("нет .github/workflows/backcheck.yml: проверка на чистой машине не подключена")
    it.ok("workflow backcheck.yml подключён")
    it.eye("На последнем коммите PR у workflow backcheck зелёная галочка?")


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
    it.ok(f"GET {path} после down и up: 200, данные в volume")


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
            it.warn(".env когда-то был закоммичен: секреты из него остались в истории git, их стоит сменить")
    compose = ctx.compose_file()
    if compose:
        leaks = []
        for n, line in enumerate(compose.read_text(encoding="utf-8").splitlines(), 1):
            m = SECRET_LINE.match(line)
            if m and not m.group(3).strip().strip("\"'").startswith("$") and m.group(3).strip() not in ("", "''", '""'):
                leaks.append(f"строка {n}: {m.group(1)}")
        if leaks:
            raise Fail(f"{compose.name}: секреты прямо в файле: " + "; ".join(leaks))
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


# ---------- база: второй сервис ----------

def _trigger(ctx: Ctx, spec: dict, label: str | None = None):
    parent = ctx.create(ctx.body())
    path = spec["path"].replace("{id}", str(parent[ctx.id_field]))
    body = json.loads(json.dumps(spec.get("body", {})).replace("{tag}", label or tag()))
    return ctx.request(spec.get("method", "POST").upper(), path, json_body=body)


def second_service(ctx: Ctx, it: Item) -> None:
    spec = ctx.need("second_service")
    _need_docker(ctx)
    name = spec["name"]
    if name not in _config(ctx).get("services", {}):
        raise Fail(f"в compose нет сервиса {name}")
    it.ok(f"сервис «{name}» работает в своём контейнере")
    trigger = spec["trigger"]
    ctx.expect(_trigger(ctx, trigger), range(200, 300), "действие, которое вызывает второй сервис")
    it.ok(f"{trigger.get('method', 'POST')} {trigger['path']}: 2xx, основной сервис сходил во второй")
    ctx.compose("stop", name)
    try:
        resp = _trigger(ctx, trigger)
    finally:
        ctx.compose("start", name)
    expected = spec.get("expect_when_down", [502, 503, 504])
    if resp.status_code not in expected:
        raise Fail(f"при остановленном «{name}» ответ {resp.status_code}, ожидали {'/'.join(map(str, expected))}")
    it.ok(f"«{name}» остановлен: {resp.status_code}{ctx.short(resp)}")
    time.sleep(1)


# ---------- ★ ----------

def _own_workflows(ctx: Ctx) -> list:
    folder = ctx.repo / ".github" / "workflows"
    files = list(folder.glob("*.yml")) + list(folder.glob("*.yaml")) if folder.exists() else []
    return [f for f in files if "backcheck" not in f.read_text(encoding="utf-8", errors="ignore")]


TEST_PATTERNS = {"*.py": r"^\s*(async\s+)?def test_", "*.go": r"^func Test", "*.js": r"\b(it|test)\(",
                 "*.ts": r"\b(it|test)\(", "*.java": r"@Test", "*.kt": r"@Test", "*.cs": r"\[(Fact|Test)\]",
                 "*.php": r"function test"}


def ci_tests(ctx: Ctx, it: Item) -> None:
    own = _own_workflows(ctx)
    if not own:
        raise Fail("нет своего workflow в .github/workflows (backcheck.yml не считается, это общая проверка курса)")
    for f in own:
        text = f.read_text(encoding="utf-8", errors="ignore")
        runs_tests = re.search(r"pytest|npm (run )?test|go test|jest|vitest|mvn .*test|gradle.*test|dotnet test|phpunit", text)
        it.ok(f"{f.name}: " + ("запускает тесты" if runs_tests else "найден"))
    count = 0
    for glob, rx in TEST_PATTERNS.items():
        for f in ctx.repo.rglob(glob):
            if {"node_modules", ".venv", "venv", ".git"} & set(f.parts):
                continue
            if "test" not in f.name.lower() and "test" not in str(f.parent).lower():
                continue
            count += len(re.findall(rx, f.read_text(encoding="utf-8", errors="ignore"), re.M))
    if count < 3:
        raise Fail(f"нашли тестов: {count}, нужно минимум 3 (создание, 404, 401)")
    it.ok(f"найдено тестов: {count}")
    it.eye("Тесты обращаются к API (создание, 404, 401), а на последнем коммите PR свой workflow зелёный?")


PROXIES = ("nginx", "caddy", "traefik", "haproxy", "envoy")


def reverse_proxy(ctx: Ctx, it: Item) -> None:
    url = ctx.need("proxy_url").rstrip("/")
    _need_docker(ctx)
    services = _config(ctx).get("services", {})
    found = []
    for name, svc in services.items():
        image = str(svc.get("image", "")).lower()
        dockerfile = None
        build = svc.get("build")
        if isinstance(build, dict):
            dockerfile = ctx.repo / build.get("context", ".") / build.get("dockerfile", "Dockerfile")
        text = dockerfile.read_text(encoding="utf-8", errors="ignore").lower() if dockerfile and dockerfile.exists() else ""
        if any(p in image or re.search(rf"^from\s+\S*{p}", text, re.M) for p in PROXIES):
            found.append(name)
    if not found:
        raise Fail("в compose нет прокси (nginx, caddy, traefik, haproxy)")
    it.ok("прокси в compose: " + ", ".join(found))
    client = httpx.Client(base_url=url, timeout=10)
    try:
        page = client.get("/")
        health = client.get("/health")
        listing = client.get(ctx.collection())
    except httpx.TransportError:
        raise Fail(f"{url} не отвечает")
    if page.status_code != 200 or "html" not in page.headers.get("content-type", ""):
        raise Fail(f"GET {url}/: {page.status_code}, ожидали страницу фронтенда")
    it.ok(f"GET {url}/: 200, страница")
    if health.status_code != 200 or "json" not in health.headers.get("content-type", ""):
        raise Fail(f"GET {url}/health: {health.status_code}, ожидали ответ бэкенда")
    if listing.status_code != 200:
        raise Fail(f"GET {url}{ctx.collection()}: {listing.status_code}")
    it.ok(f"GET {url}/health и {ctx.collection()}: 200, запросы к API проходят через прокси")


# ---------- ★★ ----------

def _image_in_compose(ctx: Ctx, names: tuple[str, ...]) -> list[str]:
    return [f"{svc_name} ({svc.get('image')})" for svc_name, svc in _config(ctx).get("services", {}).items()
            if any(n in str(svc.get("image", "")).lower() for n in names)]


def cache(ctx: Ctx, it: Item) -> None:
    spec = ctx.need("cache")
    path = spec.get("path") or ctx.need("stats_path")
    header = spec.get("header", "X-Cache")
    _need_docker(ctx)
    stores = _image_in_compose(ctx, ("redis", "valkey", "memcached", "keydb", "dragonfly"))
    if not stores:
        raise Fail("в compose нет Redis, Valkey или Memcached")
    it.ok("хранилище кэша: " + ", ".join(stores))
    ctx.get(path)
    second = ctx.expect(ctx.get(path), 200, f"GET {path}")
    if second.headers.get(header, "").upper() != "HIT":
        raise Fail(f"повторный GET {path}: {header} = {second.headers.get(header)!r}, ожидали HIT")
    it.ok(f"повторный GET {path}: {header}: HIT")
    parent = ctx.create(ctx.body())
    if ctx.has("child"):
        ctx.post(ctx.child_path(parent[ctx.id_field]), ctx.need("child", "create"))
    fresh = ctx.expect(ctx.get(path), 200, f"GET {path} после изменения данных")
    if fresh.headers.get(header, "").upper() == "HIT" and fresh.text == second.text:
        raise Fail(f"после создания записи GET {path} отдал старые данные из кэша: кэш не сбрасывается")
    it.ok(f"после создания записи: {header}: {fresh.headers.get(header)}, данные свежие")


def broker(ctx: Ctx, it: Item) -> None:
    spec = ctx.need("broker")
    _need_docker(ctx)
    brokers = _image_in_compose(ctx, ("rabbitmq", "kafka", "redpanda", "nats", "redis", "valkey"))
    if not brokers:
        raise Fail("в compose нет брокера (RabbitMQ, Kafka, NATS или Redis)")
    it.ok("брокер: " + ", ".join(brokers))
    consumer = spec["consumer"]
    if consumer not in _config(ctx).get("services", {}):
        raise Fail(f"в compose нет сервиса-получателя {consumer}")

    def wait_in_logs(label: str, seconds: int) -> str | None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            out = ctx.compose("logs", "--no-color", "--tail", "300", consumer, show=False, check=False)
            line = next((ln for ln in out.splitlines() if label in ln), None)
            if line:
                return line
            time.sleep(1)
        return None

    label = f"bc{tag(6).lower()}"
    ctx.expect(_trigger(ctx, spec["trigger"], label), range(200, 300), "действие, которое отправляет событие")
    line = wait_in_logs(label, 15)
    if line is None:
        raise Fail(f"событие с меткой {label} не появилось в логах «{consumer}» за 15 секунд")
    it.ok(f"«{consumer}» получил событие: {line.strip()[-100:]}")

    late = f"bc{tag(6).lower()}"
    ctx.compose("stop", consumer)
    try:
        resp = _trigger(ctx, spec["trigger"], late)
    finally:
        ctx.compose("start", consumer)
    ctx.expect(resp, range(200, 300), f"действие при остановленном «{consumer}»")
    it.ok(f"«{consumer}» остановлен, а основной сервис ответил {resp.status_code}: он не ждёт получателя")
    line = wait_in_logs(late, 30)
    if line is None:
        raise Fail(f"после запуска «{consumer}» событие {late} не дошло: сообщения теряются, пока получатель лежит")
    it.ok(f"после запуска «{consumer}» событие дошло из очереди")


def public_deploy(ctx: Ctx, it: Item) -> None:
    url = ctx.need("public_url")
    if not url.startswith("https://"):
        raise Fail(f"{url}: нужен адрес с https://")
    try:
        resp = httpx.get(url.rstrip("/") + "/health", timeout=15)
    except httpx.TransportError:
        raise Fail(f"{url} не открывается")
    if resp.status_code != 200:
        raise Fail(f"{url}/health: {resp.status_code}")
    it.ok(f"{url}/health: 200 по HTTPS")
    it.info(f"API целиком: backcheck --lab 3 --all --url {url}")
    it.eye("Ссылка открывается с телефона через мобильный интернет, а не только из локальной сети?")


def score_two_of_three(parts: list[bool]) -> int:
    """Любые две части из трёх дают 2 балла, одна даёт 1."""
    n = sum(parts)
    return 2 if n >= 2 else n


LAB = Lab(
    number=5,
    title="Docker и соседние сервисы",
    base=[
        ("1", "Dockerfile бэкенда", dockerfile),
        ("2", "docker compose up поднимает всё", compose_up),
        ("3", "Данные в volume переживают down/up", volume),
        ("4", "Конфигурация через .env, секретов в git нет", env_config),
        ("5", "Второй сервис по HTTP, 503 при его падении", second_service),
        ("6", "Лог каждого запроса в docker compose logs", logs),
    ],
    stars=[
        Star("★ CI и прокси", score_two, [
            ("Свой CI с автотестами API", ci_tests),
            ("Nginx или другой прокси как единая точка входа", reverse_proxy),
        ]),
        Star("★★ кэш, брокер, деплой: два из трёх", score_two_of_three, [
            ("Кэш в Redis", cache),
            ("Событие через брокер сообщений", broker),
            ("Публичный деплой с HTTPS", public_deploy),
        ]),
    ],
)
