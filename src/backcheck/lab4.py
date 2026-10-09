"""Лаба 4. Веб-страница и безопасность в браузере.

Часть пунктов ручные: чекер ведёт по сценарию и проверяет то, что видно по HTTP.
"""

from __future__ import annotations

import re
import threading
import time
from urllib.parse import urlsplit

import httpx

from .core import Ctx, Fail, Item, Lab, Star, User, score_any, score_three, tag

XSS = "<img src=x onerror=alert('backcheck')>"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{parts.hostname}:{port}"


def _text_field(ctx: Ctx) -> str:
    return ctx.contract.get("text_field") or ctx.need("required_field")


def _marker(ctx: Ctx) -> dict:
    """Запись-метка, созданная через API: должна появиться на странице после обновления."""
    if "marker" not in ctx.state:
        label = f"backcheck-{tag()}"
        obj = ctx.create(ctx.body(**{_text_field(ctx): label}))
        ctx.state["marker"] = (label, obj)
    return ctx.state["marker"][1]


def _front(ctx: Ctx) -> httpx.Response:
    url = ctx.need("frontend_url")
    try:
        return httpx.get(url, timeout=10, follow_redirects=True)
    except httpx.TransportError:
        raise Fail(f"страница не отвечает: {url}")


# ---------- база ----------

def page_loads_list(ctx: Ctx, it: Item) -> None:
    url = ctx.need("frontend_url")
    resp = _front(ctx)
    if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
        raise Fail(f"GET {url}: {resp.status_code} {resp.headers.get('content-type', '')}, ожидали HTML-страницу")
    it.ok(f"GET {url}: 200 HTML")
    _marker(ctx)
    label = ctx.state["marker"][0]
    it.info(f"через API создана запись «{label}»")
    it.eye(f"На {url} с открытой вкладкой Network после обновления виден запрос к {ctx.collection()} и запись «{label}»?")


def login_form(ctx: Ctx, it: Item) -> None:
    it.eye("В режиме инкогнито без входа кнопок создания и удаления нет или они дают понятную ошибку, а после входа работают?")


def create_delete(ctx: Ctx, it: Item) -> None:
    it.eye("Запись, созданная через форму, появилась в списке без F5, в Network виден POST с ответом 201?")
    obj = _marker(ctx)
    label = ctx.state["marker"][0]
    if not it.pause(f"Удали «{label}» кнопкой в интерфейсе."):
        it.eye(f"Удаление через интерфейс (с флагом -i чекер проверит, что «{label}» удалена на сервере)")
        return
    path = ctx.item_path(obj[ctx.id_field])
    resp = ctx.get(path)
    if resp.status_code != 404:
        raise Fail(f"GET {path}: {resp.status_code}, со страницы пропало, а на сервере осталось")
    it.ok(f"GET {path}: 404, запись удалена на сервере")


def human_errors(ctx: Ctx, it: Item) -> None:
    it.eye("Пустое поле (400), неверный пароль (401) и уже удалённая запись (404) дают на странице понятный текст, "
           "а не тишину, ошибку в консоли или сырой JSON?")


def xss(ctx: Ctx, it: Item) -> None:
    field = _text_field(ctx)
    obj = ctx.create(ctx.body(**{field: XSS}))
    got = ctx.json(ctx.expect(ctx.get(ctx.item_path(obj[ctx.id_field])), 200, "GET записи с XSS"), "GET")
    if got.get(field) != XSS:
        it.info(f"API вернул {field} = {got.get(field)!r}")
    else:
        it.ok(f"через API создана запись, где {field} = {XSS}")
    it.eye(f"После обновления страницы строка {XSS} видна как текст, а окно alert не появилось?")


def cors(ctx: Ctx, it: Item) -> None:
    front = ctx.need("frontend_url")
    if _origin(front) == _origin(ctx.base_url):
        it.ok(f"фронт и API на одном origin ({_origin(front)}), поэтому CORS не нужен")
        it.eye("Объяснено, почему здесь нет ошибки CORS?")
        return
    origin = f"{urlsplit(front).scheme}://{urlsplit(front).netloc}"
    path = ctx.collection()
    resp = ctx.request("OPTIONS", path, auth=None, headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, content-type",
    })
    if resp.status_code not in (200, 204):
        raise Fail(f"preflight OPTIONS {path} с Origin {origin}: {resp.status_code}{ctx.short(resp)}")
    allow_origin = resp.headers.get("access-control-allow-origin", "")
    if allow_origin not in (origin, "*"):
        raise Fail(f"preflight: Access-Control-Allow-Origin = {allow_origin!r}, ожидали {origin!r}")
    methods = resp.headers.get("access-control-allow-methods", "").upper()
    if methods and "POST" not in methods and "*" not in methods:
        raise Fail(f"preflight: POST не разрешён (Access-Control-Allow-Methods: {methods})")
    headers = resp.headers.get("access-control-allow-headers", "").lower()
    if "authorization" not in headers and "*" not in headers:
        raise Fail("preflight: заголовок Authorization не разрешён, и запросы с токеном браузер не пустит")
    it.ok(f"preflight OPTIONS {path}: {resp.status_code}, Allow-Origin: {allow_origin}, Authorization разрешён")
    simple = ctx.get(path, headers={"Origin": origin})
    if simple.headers.get("access-control-allow-origin") not in (origin, "*"):
        raise Fail(f"GET {path} с Origin {origin}: нет Access-Control-Allow-Origin в ответе")
    it.ok(f"GET {path} с Origin {origin}: Access-Control-Allow-Origin есть")
    it.eye("Объяснено, что такое CORS и кто его проверяет?")


# ---------- ★ ----------

def edit(ctx: Ctx, it: Item) -> None:
    it.eye("Изменение записи через интерфейс сохраняется и видно после F5?")


def related_page(ctx: Ctx, it: Item) -> None:
    it.eye("Есть страница записи со списком связанных записей и формой добавления?")


def security_headers(ctx: Ctx, it: Item) -> None:
    resp = ctx.get(ctx.collection())
    nosniff = resp.headers.get("x-content-type-options", "").lower()
    if nosniff != "nosniff":
        raise Fail(f"GET {ctx.collection()}: нет заголовка X-Content-Type-Options: nosniff")
    it.ok("API: X-Content-Type-Options: nosniff")
    page = _front(ctx)
    csp = page.headers.get("content-security-policy", "")
    if not csp:
        m = re.search(r'<meta[^>]+http-equiv=["\']Content-Security-Policy["\'][^>]*content=(["\'])(.*?)\1', page.text,
                      re.I | re.S)
        csp = " ".join(m.group(2).split()) if m else ""
    if not csp:
        raise Fail("у страницы нет Content-Security-Policy (ни заголовка, ни meta)")
    it.ok(f"страница: Content-Security-Policy: {csp[:80]}")
    directives = {d.strip().split(" ")[0]: d.strip() for d in csp.split(";") if d.strip()}
    scripts = directives.get("script-src") or directives.get("default-src", "")
    if "'unsafe-inline'" in scripts:
        it.warn("в script-src разрешён 'unsafe-inline', такая политика почти не защищает от XSS")
    frame = page.headers.get("x-frame-options", "") or ("frame-ancestors" in csp and "frame-ancestors")
    if not frame:
        it.warn("нет X-Frame-Options или frame-ancestors: страницу можно встроить в чужой сайт")


# ---------- ★★ ----------

def cookie_csrf(ctx: Ctx, it: Item) -> None:
    spec = ctx.need("cookie")
    path = spec.get("login_path", "/auth/session")
    user = ctx.register(ctx.new_user("bck"))
    resp = ctx.post(path, ctx.credentials(user), auth=None)
    ctx.expect(resp, range(200, 300), f"POST {path}")
    cookies = resp.headers.get_list("set-cookie")
    session = next((c for c in cookies if "httponly" in c.lower()), None)
    if session is None:
        raise Fail(f"POST {path}: нет cookie с флагом HttpOnly")
    if "samesite" not in session.lower():
        raise Fail(f"cookie без SameSite: {session.split(';')[0]}")
    it.ok(f"cookie: {session.split(';')[0].split('=')[0]}; HttpOnly; " +
          next((p.strip() for p in session.split(";") if p.strip().lower().startswith("samesite")), ""))
    name, value = session.split(";")[0].split("=", 1)
    me_path = ctx.contract.get("auth", {}).get("me_path")
    if me_path:
        ctx.expect(ctx.get(me_path, auth=None, headers={"Cookie": f"{name}={value}"}), 200, f"GET {me_path} с cookie")
        it.ok(f"GET {me_path} только с cookie: 200, сессия работает")
    bad = ctx.request("POST", ctx.collection(), json_body=ctx.body(), auth=None,
                      headers={"Cookie": f"{name}={value}", "Origin": "http://evil.example"})
    if bad.status_code in range(200, 300):
        ctx.track(ctx.item_path(bad.json().get(ctx.id_field)), user)
        raise Fail("POST только с cookie, с чужого Origin и без CSRF-токена прошёл: CSRF возможен")
    it.ok(f"POST только с cookie, с чужого Origin и без CSRF-токена: {bad.status_code}")
    it.eye("Страница входит через cookie, токена в localStorage нет, а создание записи со страницы работает?")


def realtime(ctx: Ctx, it: Item) -> None:
    spec = ctx.need("realtime")
    kind = spec.get("type", "sse").lower()
    path = spec.get("path", "/api/events")
    if kind != "sse":
        it.eye(f"Во втором окне браузера новая запись появляется сама, без F5 ({kind} {path})?")
        return
    label = f"backcheck-rt-{tag()}"
    got: list[str] = []
    ready = threading.Event()

    def listen() -> None:
        try:
            with httpx.Client(base_url=ctx.base_url, timeout=httpx.Timeout(10, read=8)) as client:
                with client.stream("GET", path, headers={"Accept": "text/event-stream"}) as resp:
                    got.append(f"status {resp.status_code} {resp.headers.get('content-type', '')}")
                    ready.set()
                    for line in resp.iter_lines():
                        got.append(line)
                        if label in line:
                            return
        except httpx.HTTPError as exc:
            got.append(f"ошибка {exc.__class__.__name__}")
            ready.set()

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    if not ready.wait(5):
        raise Fail(f"GET {path}: поток событий не открылся за 5 секунд")
    if "text/event-stream" not in got[0]:
        raise Fail(f"GET {path}: {got[0]}, ожидали text/event-stream")
    it.ok(f"GET {path}: поток событий открыт")
    time.sleep(0.3)
    ctx.create(ctx.body(**{_text_field(ctx): label}))
    thread.join(6)
    if not any(label in line for line in got):
        raise Fail(f"после создания записи событие с «{label}» не пришло за 6 секунд")
    it.ok("после создания записи событие пришло в поток")
    it.eye("Во втором окне браузера новая запись появляется сама, без F5?")


def _cleanup_marker(ctx: Ctx) -> None:
    ctx.state.pop("marker", None)


LAB = Lab(
    number=4,
    title="Веб-страница и безопасность в браузере",
    base=[
        ("1", "Страница загружает список с API", page_loads_list),
        ("2", "Вход, без входа изменять нельзя", login_form),
        ("3", "Создание и удаление через интерфейс", create_delete),
        ("4", "Понятные ошибки", human_errors),
        ("5", "XSS: опасная строка показывается текстом", xss),
        ("6", "CORS", cors),
    ],
    stars=[
        Star("★ редактирование, связанная сущность, заголовки", score_three, [
            ("Редактирование", edit),
            ("Страница связанной сущности", related_page),
            ("Заголовки безопасности", security_headers),
        ]),
        Star("★★ cookie и CSRF или обновления в реальном времени", score_any, [
            ("(а) Вход через cookie, защита от CSRF", cookie_csrf),
            ("(б) Обновления в реальном времени", realtime),
        ]),
    ],
    after=_cleanup_marker,
)
