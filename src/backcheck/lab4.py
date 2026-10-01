"""Лаба 4. Веб-клиент. Большая часть — глазами; чекер ведёт преподавателя по сценарию."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from .core import Ctx, Fail, Item, Lab, Star, score_three, score_two, tag


def _origin(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{parts.hostname}:{port}"


def _marker(ctx: Ctx) -> dict:
    """Запись-метка, созданная через API: должна появиться на странице после обновления."""
    if "marker" not in ctx.state:
        field = ctx.contract.get("text_field") or ctx.need("required_field")
        label = f"backcheck-{tag()}"
        obj = ctx.create(ctx.body(**{field: label}))
        ctx.state["marker"] = (label, obj)
    return ctx.state["marker"][1]


def page_loads_list(ctx: Ctx, it: Item) -> None:
    url = ctx.need("frontend_url")
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
    except httpx.TransportError:
        raise Fail(f"фронт не отвечает: {url}")
    if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
        raise Fail(f"GET {url} → {resp.status_code} {resp.headers.get('content-type', '')}, ожидали HTML-страницу")
    it.ok(f"GET {url} → 200 HTML")
    _marker(ctx)
    label = ctx.state["marker"][0]
    it.info(f"через API создана запись «{label}»")
    it.eye(f"Открой {url}, DevTools → Network, обнови страницу: есть запрос к {ctx.collection()} и видна «{label}»?")


def login_form(ctx: Ctx, it: Item) -> None:
    it.eye("В режиме инкогнито без входа: кнопки создания/удаления скрыты или показывают понятную ошибку; после входа — работают?")


def create_via_form(ctx: Ctx, it: Item) -> None:
    it.eye("Создай запись через форму: она появилась в списке без F5, в Network виден POST с 201?")


def delete_via_ui(ctx: Ctx, it: Item) -> None:
    obj = _marker(ctx)
    label = ctx.state["marker"][0]
    if not it.pause(f"Удали «{label}» кнопкой в интерфейсе."):
        it.eye(f"Удаление через интерфейс (запусти с -i — чекер проверит, что «{label}» удалена на сервере)")
        return
    path = ctx.item_path(obj[ctx.id_field])
    resp = ctx.get(path)
    if resp.status_code != 404:
        raise Fail(f"GET {path} → {resp.status_code}: со страницы пропало, а на сервере осталось")
    it.ok(f"GET {path} → 404: запись действительно удалена на сервере")


def human_errors(ctx: Ctx, it: Item) -> None:
    it.eye("Три ошибки — пустое поле (400), неверный пароль (401), несуществующая/уже удалённая запись (404): "
           "на странице понятный текст, а не тишина, консоль или сырой JSON?")


def cors(ctx: Ctx, it: Item) -> None:
    front = ctx.need("frontend_url")
    if _origin(front) == _origin(ctx.base_url):
        it.ok(f"фронт и API на одном origin ({_origin(front)}) — CORS не нужен")
        it.eye("Студент объяснил, почему у него нет ошибки CORS?")
        return
    origin = f"{urlsplit(front).scheme}://{urlsplit(front).netloc}"
    path = ctx.collection()
    resp = ctx.request("OPTIONS", path, auth=None, headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, content-type",
    })
    if resp.status_code not in (200, 204):
        raise Fail(f"preflight OPTIONS {path} с Origin {origin} → {resp.status_code}{ctx.short(resp)}")
    allow_origin = resp.headers.get("access-control-allow-origin", "")
    if allow_origin not in (origin, "*"):
        raise Fail(f"preflight: Access-Control-Allow-Origin = {allow_origin!r}, ожидали {origin!r}")
    methods = resp.headers.get("access-control-allow-methods", "").upper()
    if methods and "POST" not in methods and "*" not in methods:
        raise Fail(f"preflight: POST не разрешён (Access-Control-Allow-Methods: {methods})")
    headers = resp.headers.get("access-control-allow-headers", "").lower()
    if "authorization" not in headers and "*" not in headers:
        raise Fail("preflight: заголовок Authorization не разрешён — запросы с токеном браузер не пустит")
    it.ok(f"preflight OPTIONS {path} → {resp.status_code}, Allow-Origin: {allow_origin}, Authorization разрешён")
    simple = ctx.get(path, headers={"Origin": origin})
    if simple.headers.get("access-control-allow-origin") not in (origin, "*"):
        raise Fail(f"GET {path} с Origin {origin}: нет Access-Control-Allow-Origin в ответе")
    it.ok(f"GET {path} с Origin {origin} → Access-Control-Allow-Origin есть")
    it.eye("Студент объяснил, что такое CORS и кто его проверяет?")


# ---------- ★ ----------

def edit(ctx: Ctx, it: Item) -> None:
    it.eye("Изменение записи через интерфейс сохраняется (видно после F5)?")


def related_page(ctx: Ctx, it: Item) -> None:
    it.eye("Есть страница родителя со списком детей, и ребёнка можно добавить?")


def mobile(ctx: Ctx, it: Item) -> None:
    it.eye("DevTools → режим устройства, ширина 375 px: нет горизонтальной прокрутки, кнопки нажимаются?")


# ---------- ★★ ----------

def spa(ctx: Ctx, it: Item) -> None:
    it.eye("SPA на фреймворке: при переходах URL меняется без загрузки документа (Network → Doc пусто), F5 на глубокой ссылке работает?")


def visualization(ctx: Ctx, it: Item) -> None:
    it.eye("График / карта / прогресс-бар строится по данным API и меняется после создания записи?")


def _cleanup_marker(ctx: Ctx) -> None:
    ctx.state.pop("marker", None)


LAB = Lab(
    number=4,
    title="Веб-клиент",
    base=[
        ("1", "Страница загружает список с API", page_loads_list),
        ("2", "Форма входа", login_form),
        ("3", "Создание через форму", create_via_form),
        ("4", "Удаление через интерфейс", delete_via_ui),
        ("5", "Понятные ошибки", human_errors),
        ("6", "CORS", cors),
    ],
    stars=[
        Star("★ редактирование, связанная сущность, телефон", score_three, [
            ("Редактирование", edit),
            ("Страница связанной сущности", related_page),
            ("Вид на телефоне", mobile),
        ]),
        Star("★★ SPA и визуализация", score_two, [
            ("SPA с роутингом", spa),
            ("Визуализация фишки или статистики", visualization),
        ]),
    ],
    after=_cleanup_marker,
)
