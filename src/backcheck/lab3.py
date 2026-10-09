"""Лаба 3. Пользователи и доступ."""

from __future__ import annotations

import base64
import json
import re
import time

from .core import Ctx, Fail, Item, Lab, Star, User, score_first_required, score_two

HASH_PREFIXES = ("$2a$", "$2b$", "$2y$", "$argon2")


def _users(ctx: Ctx) -> tuple[User, User]:
    """Два свежих пользователя A и B на весь прогон лабы 3."""
    if "users" not in ctx.state:
        a = ctx.register(ctx.new_user("bca"))
        b = ctx.register(ctx.new_user("bcb"))
        ctx.state["users"] = (a, b)
    return ctx.state["users"]


def _bad_login(ctx: Ctx, username: str, password: str):
    """Попытка входа с неверными данными: через /auth/me с Basic или через login_path."""
    if ctx.scheme == "bearer" or not ctx.has("auth", "me_path"):
        return ctx.post(ctx.need("auth", "login_path"), ctx.credentials(User(username, password)), auth=None)
    raw = base64.b64encode(f"{username}:{password}".encode()).decode()
    return ctx.get(ctx.need("auth", "me_path"), auth=None, headers={"Authorization": f"Basic {raw}"})


# ---------- база ----------

def register(ctx: Ctx, it: Item) -> None:
    path = ctx.need("auth", "register_path")
    user = ctx.new_user("bcr")
    body = {**ctx.credentials(user), **ctx.contract["auth"].get("register_extra", {})}
    resp = ctx.expect(ctx.post(path, body, auth=None), range(200, 300), f"POST {path}")
    it.ok(f"POST {path} ({user.username}): {resp.status_code}")
    ctx.expect(ctx.post(path, body, auth=None), 409, f"повторная регистрация {user.username}")
    it.ok("повторная регистрация с тем же логином: 409")
    ctx.state["registered"] = user


def password_hash(ctx: Ctx, it: Item) -> None:
    user = ctx.state.get("registered") or ctx.register(ctx.new_user("bcr"))
    it.info(f"логин для проверки: {user.username}, пароль: {user.password}")
    it.eye(f"В БД у «{user.username}» пароль начинается с $2b$ / $2a$ (bcrypt) или $argon2id$, и пароля в открытом виде нет?")


def needs_auth(ctx: Ctx, it: Item) -> None:
    a, _ = _users(ctx)
    obj = ctx.create(ctx.body(), user=a)
    path = ctx.item_path(obj[ctx.id_field])
    checks = [
        ("POST", ctx.collection(), ctx.body()),
        ("PATCH", path, ctx.need("update")),
        ("PUT", path, ctx.body(**ctx.need("update"))),
        ("DELETE", path, ...),
    ]
    if ctx.has("child"):
        checks.append(("POST", ctx.child_path(obj[ctx.id_field]), ctx.need("child", "create")))
    for method, p, body in checks:
        resp = ctx.request(method, p, json_body=body, auth=None)
        if resp.status_code == 405:
            continue  # такого метода нет, защищать нечего
        if resp.status_code in range(200, 300):
            if method == "POST" and resp.headers.get("content-type", "").startswith("application/json"):
                created = resp.json()
                if isinstance(created, dict) and ctx.id_field in created and p == ctx.collection():
                    ctx.track(ctx.item_path(created[ctx.id_field]), a)
            raise Fail(f"{method} {p} без авторизации: {resp.status_code}: изменение прошло без входа")
        ctx.expect(resp, 401, f"{method} {p} без авторизации")
        it.ok(f"{method} {p} без авторизации: 401")
    resp = ctx.expect(ctx.post(ctx.collection(), ctx.body(), auth=a), range(200, 300), f"POST {ctx.collection()} с авторизацией")
    created = resp.json()
    ctx.track(ctx.item_path(created[ctx.id_field]), a)
    it.ok(f"POST {ctx.collection()} с {ctx.scheme.capitalize()}: {resp.status_code}")
    upd = ctx.expect(ctx.update(path, ctx.need("update"), ctx.body(), auth=a), range(200, 300), f"обновление {path} с авторизацией")
    it.ok(f"{upd.request.method} {path} с авторизацией: {upd.status_code}")
    ctx.expect(ctx.delete(path, auth=a), range(200, 300), f"DELETE {path} с авторизацией")
    it.ok(f"DELETE {path} с авторизацией: 2xx")
    _wrong_password(ctx, it, a)


def _wrong_password(ctx: Ctx, it: Item, a: User) -> None:
    wrong = _bad_login(ctx, a.username, a.password + "x")
    ctx.expect(wrong, 401, "неверный пароль")
    it.ok("неверный пароль: 401")
    ghost = _bad_login(ctx, "no_such_user_bc", "Whatever-123!")
    ctx.expect(ghost, 401, "несуществующий логин")
    it.ok("несуществующий логин: 401")
    if _normalize(wrong.text) != _normalize(ghost.text):
        raise Fail(f"ответы различаются и подсказывают, что неверно: {wrong.text[:60]} / {ghost.text[:60]}")
    it.ok("тела ответов одинаковые: по ним не понять, что неверно, логин или пароль")


def _normalize(text: str) -> str:
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k not in ("timestamp", "time", "path", "request_id", "requestId")}
    return json.dumps(data, sort_keys=True)


def _walk(value, path=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
            yield (f"{path}.{k}" if path else k), v, True
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, value, False


def me(ctx: Ctx, it: Item) -> None:
    a, _ = _users(ctx)
    path = ctx.need("auth", "me_path")
    data = ctx.me(a)
    if a.username not in json.dumps(data, ensure_ascii=False):
        raise Fail(f"GET {path}: нет логина пользователя")
    for key, value, is_key in _walk(data):
        name = key.split(".")[-1].lower()
        if is_key and re.search(r"pass|hash", name):
            raise Fail(f"GET {path}: в ответе есть поле {key}")
        if isinstance(value, str) and value.startswith(HASH_PREFIXES):
            raise Fail(f"GET {path}: в ответе хэш пароля ({key})")
    it.ok(f"GET {path}: 200, логин есть, пароля и хэша нет")
    ctx.expect(ctx.get(path, auth=None), 401, f"GET {path} без авторизации")
    it.ok(f"GET {path} без авторизации: 401")


def owner_from_server(ctx: Ctx, it: Item) -> None:
    a, _ = _users(ctx)
    owner_field = ctx.contract["auth"].get("owner_field", "owner_id")
    me_data = ctx.me(a)
    obj = ctx.create(ctx.body(**{owner_field: 999999}), user=a)
    if owner_field not in obj:
        raise Fail(f"в ответе POST нет поля {owner_field}")
    if str(obj[owner_field]) == "999999":
        raise Fail(f"{owner_field} взят из тела запроса: так можно создать запись от чужого имени")
    if str(obj[owner_field]) != str(me_data.get("id")):
        raise Fail(f"{owner_field}={obj[owner_field]}, а id пользователя в /auth/me = {me_data.get('id')}")
    it.ok(f'прислали "{owner_field}": 999999, сервер поставил {obj[owner_field]} (id из авторизации)')


# ---------- ★ JWT ----------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64json(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def _jwt_user(ctx: Ctx) -> tuple[User, str]:
    if "jwt" not in ctx.state:
        user = ctx.register(ctx.new_user("bcj"))
        ctx.state["jwt"] = (user, ctx.login(user))
    return ctx.state["jwt"]


def jwt_login(ctx: Ctx, it: Item) -> None:
    user, token = _jwt_user(ctx)
    if token.count(".") != 2:
        raise Fail("токен не похож на JWT (нужно три части через точку)")
    payload = _b64json(token.split(".")[1])
    it.ok(f"POST {ctx.need('auth', 'login_path')}: токен, payload: {json.dumps(payload, ensure_ascii=False)[:80]}")
    resp = ctx.post(ctx.collection(), ctx.body(), auth=None, headers={"Authorization": f"Bearer {token}"})
    ctx.expect(resp, range(200, 300), f"POST {ctx.collection()} с Bearer")
    ctx.track(ctx.item_path(resp.json()[ctx.id_field]), user)
    it.ok(f"POST {ctx.collection()} с заголовком Bearer: {resp.status_code}")


def jwt_forgery(ctx: Ctx, it: Item) -> None:
    _, token = _jwt_user(ctx)
    header, payload, signature = token.split(".")
    data = _b64json(payload)
    forged_payload = dict(data)
    for key in ("sub", "user_id", "id", "uid"):
        if key in forged_payload:
            forged_payload[key] = "999999" if isinstance(forged_payload[key], str) else 999999
    forged_payload["role"] = "admin"
    variants = {
        "изменена подпись": f"{header}.{payload}.{signature[:-2] + ('AA' if signature[-2:] != 'AA' else 'BB')}",
        "подменён payload": f"{header}.{_b64url(json.dumps(forged_payload).encode())}.{signature}",
        'alg: "none"': f"{_b64url(json.dumps({'alg': 'none', 'typ': 'JWT'}).encode())}.{payload}.",
    }
    for label, bad in variants.items():
        resp = ctx.post(ctx.collection(), ctx.body(), auth=None, headers={"Authorization": f"Bearer {bad}"})
        if resp.status_code in range(200, 300):
            ctx.track(ctx.item_path(resp.json().get(ctx.id_field)), "auto")
        ctx.expect(resp, 401, f"токен: {label}")
        it.ok(f"{label}: 401")


def jwt_expiry(ctx: Ctx, it: Item) -> None:
    _, token = _jwt_user(ctx)
    data = _b64json(token.split(".")[1])
    if "exp" not in data:
        raise Fail("в токене нет exp, поэтому он никогда не истекает")
    ttl = data["exp"] - data.get("iat", time.time())
    it.ok(f"exp есть, срок жизни ≈ {int(ttl)} с")
    left = data["exp"] - time.time()
    if left <= 12:
        it.info(f"срок жизни короткий: ждём {max(0, int(left)) + 2} с и проверяем просроченный токен")
        time.sleep(max(0, left) + 2)
        resp = ctx.post(ctx.collection(), ctx.body(), auth=None, headers={"Authorization": f"Bearer {token}"})
        ctx.expect(resp, 401, "просроченный токен")
        it.ok("просроченный токен: 401")
        return
    it.eye(f"Срок жизни ({int(ttl)} с) задаётся переменной окружения? "
           "(или запусти сервис с TTL до 10 с, тогда чекер проверит просрочку сам)")


def jwt_secret_env(ctx: Ctx, it: Item) -> None:
    example = ctx.repo / ".env.example"
    text = example.read_text(encoding="utf-8", errors="ignore") if example.exists() else ""
    if re.search(r"(JWT|SECRET|TOKEN)\w*\s*=", text, re.I):
        it.ok(".env.example содержит переменную с секретом JWT")
    else:
        raise Fail("в .env.example нет переменной для секрета JWT")
    it.eye("Секрет и срок жизни читаются из окружения, строки-секрета в коде нет?")


# ---------- ★★ роли и лимит ----------

def foreign_403(ctx: Ctx, it: Item) -> None:
    a, b = _users(ctx)
    obj = ctx.create(ctx.body(), user=a)
    path = ctx.item_path(obj[ctx.id_field])
    resp = ctx.update(path, ctx.need("update"), ctx.body(), auth=b)
    ctx.expect(resp, 403, f"{resp.request.method} чужой записи")
    it.ok(f"пользователь B меняет запись A: 403")
    ctx.expect(ctx.delete(path, auth=b), 403, "DELETE чужой записи")
    it.ok("пользователь B удаляет запись A: 403")
    ctx.expect(ctx.update(path, ctx.need("update"), ctx.body(), auth=a), range(200, 300), "владелец меняет свою запись")
    it.ok("владелец A меняет свою запись: 2xx")
    ctx.expect(ctx.get(path, auth=b), 200, "чтение чужой записи")
    it.ok("читать чужую запись можно: 200")


def admin_role(ctx: Ctx, it: Item) -> None:
    a, _ = _users(ctx)
    obj = ctx.create(ctx.body(), user=a)
    path = ctx.item_path(obj[ctx.id_field])
    admin_data = ctx.need("auth", "admin")
    admin = User(admin_data["username"], admin_data["password"])
    ctx.expect(ctx.update(path, ctx.need("update"), ctx.body(), auth=admin), range(200, 300), "админ меняет чужую запись")
    it.ok(f"админ «{admin.username}» меняет запись A: 2xx")
    ctx.expect(ctx.delete(path, auth=admin), range(200, 300), "админ удаляет чужую запись")
    it.ok("админ удаляет запись A: 2xx")
    path_reg = ctx.need("auth", "register_path")
    sneaky = ctx.new_user("bcadm")
    body = {**ctx.credentials(sneaky), **ctx.contract["auth"].get("register_extra", {}), "role": "admin"}
    resp = ctx.post(path_reg, body, auth=None)
    if resp.status_code in range(200, 300):
        data = ctx.me(sneaky)
        if str(data.get("role", "")).lower() == "admin":
            raise Fail('регистрация с "role": "admin" в теле дала роль admin')
        it.ok('регистрация с "role": "admin" в теле: роль осталась обычной')


def rate_limit(ctx: Ctx, it: Item) -> None:
    victim = ctx.register(ctx.new_user("bcl"))
    statuses = []
    for _ in range(8):
        resp = _bad_login(ctx, victim.username, "Wrong-password-1")
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    it.info("неверные попытки: " + " ".join(map(str, statuses)))
    if 429 not in statuses:
        raise Fail("после 5+ неверных паролей подряд нет 429")
    first_429 = statuses.index(429) + 1
    if statuses[0] != 401:
        raise Fail(f"первая неверная попытка: {statuses[0]}, ожидали 401")
    if first_429 > 6:
        raise Fail(f"429 только на попытке №{first_429}, ожидали не позже 6-й")
    it.ok(f"попытка №{first_429}: 429")


LAB = Lab(
    number=3,
    title="Пользователи и доступ",
    base=[
        ("1", "Регистрация, повтор: 409", register),
        ("2", "Пароль хранится хэшем", password_hash),
        ("3", "Без входа 401, неверный пароль 401 без подсказок", needs_auth),
        ("4", "GET /auth/me без пароля и хэша", me),
        ("5", "Автор записи ставится сервером", owner_from_server),
        ("6", "Чужую запись менять нельзя: 403", foreign_403),
    ],
    stars=[
        Star("★ JWT", score_first_required, [
            ("Вход и запросы с Bearer", jwt_login),
            ("Подделанный токен: 401", jwt_forgery),
            ("Срок жизни токена", jwt_expiry),
            ("Секрет и срок из окружения", jwt_secret_env),
        ]),
        Star("★★ админ и лимит попыток", score_two, [
            ("Роль admin может всё", admin_role),
            ("5 неудачных входов: 429", rate_limit),
        ]),
    ],
)
