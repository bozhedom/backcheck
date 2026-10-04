"""Ядро чекера: контекст прогона, HTTP-помощники, пункты чек-листа и вывод."""

from __future__ import annotations

import base64
import json
import os
import random
import shutil
import string
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

PASS, FAIL, MANUAL, HALF = "pass", "fail", "manual", "half"


class Fail(Exception):
    """Пункт не засчитан. Текст исключения печатается в выводе."""


class Unreachable(Exception):
    """Сервис не отвечает вообще."""


class ContractError(Exception):
    """В contract.json не хватает поля или оно неверное."""


# ---------- Цвета ----------

def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        os.system("")  # включает ANSI в консоли Windows
    return True


COLOR = _color_enabled()


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def green(t): return c(t, "32")
def red(t): return c(t, "31")
def yellow(t): return c(t, "33")
def blue(t): return c(t, "36")
def dim(t): return c(t, "2")
def bold(t): return c(t, "1")


# ---------- Пользователи ----------

@dataclass
class User:
    username: str
    password: str
    id: Any = None
    token: str | None = None


# ---------- Пункт чек-листа ----------

@dataclass
class Item:
    """Один пункт базы или одна часть звёздочки. Собирает строки вывода и ответы ручной проверки."""

    ctx: "Ctx"
    key: str
    title: str
    lines: list[tuple[str, str]] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    eye_failed: bool = False
    half: bool = False
    status: str = PASS
    reason: str = ""

    def _print(self, kind: str, text: str) -> None:
        marks = {"ok": green("✓"), "bad": red("✗"), "info": dim("·"), "warn": yellow("⚠"), "eye": blue("?")}
        print(f"      {marks[kind]} {text}")

    def ok(self, text: str) -> None:
        self.lines.append(("ok", text))
        self._print("ok", text)

    def info(self, text: str) -> None:
        self.lines.append(("info", text))
        self._print("info", text)

    def warn(self, text: str) -> None:
        self.ctx.warnings.append(text)
        self.lines.append(("warn", text))
        self._print("warn", text)

    def eye(self, question: str) -> bool | None:
        """Ручная проверка. Без -i вопрос откладывается, и пункт помечается как ручной."""
        if not self.ctx.interactive:
            self.pending.append(question)
            self.lines.append(("eye", question))
            self._print("eye", question + dim("  (проверка вручную)"))
            return None
        answer = self.ctx.ask_yes_no(f"      {blue('?')} {question}")
        self.lines.append(("eye", f"{question}, {'да' if answer else 'нет'}"))
        if not answer:
            self.eye_failed = True
        return answer

    def pause(self, instruction: str) -> bool:
        """Просит сделать действие руками. Возвращает False, если режим не интерактивный."""
        if not self.ctx.interactive:
            return False
        print(f"      {yellow('>>')} {instruction}")
        input(dim("        нажми Enter, когда готово… "))
        return True


# ---------- Контекст прогона ----------

class Ctx:
    def __init__(self, contract: dict, repo: Path, *, base_url: str | None, interactive: bool,
                 verbose: bool, keep: bool, ci: bool):
        self.contract = contract
        self.repo = repo
        self.base_url = (base_url or contract.get("base_url") or "http://localhost:8000").rstrip("/")
        self.interactive = interactive
        self.verbose = verbose
        self.keep = keep
        self.ci = ci
        self.api = "/" + contract.get("api_prefix", "/api").strip("/")
        self.resource = contract.get("resource", "")
        self.id_field = contract.get("id_field", "id")
        self.client = httpx.Client(base_url=self.base_url, timeout=10.0, follow_redirects=False)
        self.warnings: list[str] = []
        self.created: list[tuple[str, User | None]] = []
        self.state: dict[str, Any] = {}
        self._user: User | None = None

    # ----- контракт -----

    def need(self, *keys: str) -> Any:
        """Достаёт поле контракта, например need("auth", "register_path")."""
        value: Any = self.contract
        for key in keys:
            if not isinstance(value, dict) or key not in value or value[key] in (None, ""):
                raise ContractError(f"в contract.json нет поля {'.'.join(keys)}")
            value = value[key]
        return value

    def has(self, *keys: str) -> bool:
        try:
            self.need(*keys)
            return True
        except ContractError:
            return False

    @property
    def auth_enabled(self) -> bool:
        return self.has("auth", "register_path")

    @property
    def scheme(self) -> str:
        return self.contract.get("auth", {}).get("scheme", "basic").lower()

    def collection(self) -> str:
        return f"{self.api}/{self.resource}"

    def item_path(self, item_id: Any) -> str:
        return f"{self.api}/{self.resource}/{item_id}"

    def child_path(self, parent_id: Any) -> str:
        return f"{self.api}/{self.resource}/{parent_id}/{self.need('child', 'resource')}"

    def body(self, **overrides) -> dict:
        """Правильное тело создания из контракта с подменой отдельных полей."""
        return {**self.need("create"), **overrides}

    # ----- HTTP -----

    def request(self, method: str, path: str, *, json_body: Any = ..., content: bytes | None = None,
                headers: dict | None = None, auth: Any = "auto", params: dict | None = None) -> httpx.Response:
        hdrs = dict(headers or {})
        if auth == "auto":
            auth = self.user if self.auth_enabled else None
        if isinstance(auth, User):
            hdrs.setdefault("Authorization", self.auth_header(auth))
        kwargs: dict[str, Any] = {"headers": hdrs, "params": params}
        if content is not None:
            kwargs["content"] = content
        elif json_body is not ...:
            kwargs["json"] = json_body
        try:
            resp = self.client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise Unreachable(f"{method} {path}: сервис не отвечает ({exc.__class__.__name__})") from exc
        if self.verbose:
            sent = content.decode(errors="replace") if content is not None else (
                json.dumps(json_body, ensure_ascii=False) if json_body is not ... else "")
            print(dim(f"        > {method} {resp.request.url} {sent[:200]}"))
            print(dim(f"        < {resp.status_code} {resp.text[:300]}"))
        return resp

    def get(self, path, **kw): return self.request("GET", path, **kw)
    def post(self, path, body=..., **kw): return self.request("POST", path, json_body=body, **kw)
    def delete(self, path, **kw): return self.request("DELETE", path, **kw)

    def update(self, path: str, changes: dict, full: dict, **kw) -> httpx.Response:
        """PATCH, а если его нет (405/501), то PUT с полным телом."""
        resp = self.request("PATCH", path, json_body=changes, **kw)
        if resp.status_code in (405, 501):
            resp = self.request("PUT", path, json_body={**full, **changes}, **kw)
        return resp

    @staticmethod
    def short(resp: httpx.Response) -> str:
        text = resp.text.strip().replace("\n", " ")
        return f" {text[:120]}" if text else ""

    def expect(self, resp: httpx.Response, status: int | tuple | range, what: str) -> httpx.Response:
        ok = resp.status_code in status if isinstance(status, (tuple, range)) else resp.status_code == status
        if not ok:
            want = status if isinstance(status, int) else (
                f"{status.start}-{status.stop - 1}" if isinstance(status, range) else " или ".join(map(str, status)))
            raise Fail(f"{what}: ожидали {want}, получили {resp.status_code}{self.short(resp)}")
        return resp

    def json(self, resp: httpx.Response, what: str) -> Any:
        try:
            return resp.json()
        except ValueError:
            raise Fail(f"{what}: ответ не JSON:{self.short(resp)}")

    def items(self, data: Any, what: str) -> list:
        """Список из ответа: голый массив или обёртка {"items": [...]}."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            key = self.contract.get("list_key")
            for candidate in ([key] if key else []) + ["items", "data", "results"]:
                if isinstance(data.get(candidate), list):
                    return data[candidate]
        raise Fail(f"{what}: ожидали массив или объект со списком (list_key), получили {str(data)[:100]}")

    def find_in_list(self, path: str, item_id: Any) -> bool:
        """Ищет запись в списке. Если у списка есть пагинация, листает страницы по 100."""
        seen: set[str] = set()
        pages = [None] + [{"limit": 100, "offset": offset} for offset in range(0, 2000, 100)]
        for params in pages:
            resp = self.get(path, params=params)
            if params is None:
                self.expect(resp, 200, f"GET {path}")
            elif resp.status_code != 200:
                return False
            page = self.items(self.json(resp, f"GET {path}"), f"GET {path}")
            ids = {str(x.get(self.id_field)) for x in page if isinstance(x, dict)}
            if str(item_id) in ids:
                return True
            if params is not None and not ids - seen:
                return False
            seen |= ids
        return False

    def create(self, body: dict | None = None, *, user: Any = "auto", what: str | None = None) -> dict:
        """Создаёт главную сущность, запоминает для уборки и возвращает объект."""
        path = self.collection()
        resp = self.post(path, body if body is not None else self.body(), auth=user)
        self.expect(resp, range(200, 300), what or f"POST {path}")
        obj = self.json(resp, f"POST {path}")
        if not isinstance(obj, dict) or self.id_field not in obj:
            raise Fail(f"POST {path}: в ответе нет поля {self.id_field}")
        self.track(self.item_path(obj[self.id_field]), user)
        return obj

    def track(self, path: str, user: Any = "auto") -> None:
        self.created.append((path, user))

    def cleanup(self) -> None:
        if self.keep:
            return
        for path, user in reversed(self.created):
            try:
                self.delete(path, auth=user)
            except Exception:
                pass
        self.created.clear()

    def wait_health(self, timeout: float = 30) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.client.get("/health", timeout=2).status_code == 200:
                    return True
            except httpx.TransportError:
                pass
            time.sleep(0.5)
        return False

    # ----- авторизация -----

    @property
    def user(self) -> User:
        if self._user is None:
            self._user = self.register(self.new_user())
        return self._user

    @staticmethod
    def new_user(prefix: str = "bc") -> User:
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return User(f"{prefix}_{suffix}", f"Backcheck-{suffix}-pass!")

    def credentials(self, user: User) -> dict:
        auth = self.need("auth")
        return {auth.get("login_field", "username"): user.username,
                auth.get("password_field", "password"): user.password}

    def register(self, user: User) -> User:
        path = self.need("auth", "register_path")
        body = {**self.credentials(user), **self.contract["auth"].get("register_extra", {})}
        resp = self.post(path, body, auth=None)
        if resp.status_code not in range(200, 300):
            raise Fail(f"не удалось зарегистрировать тестового пользователя: POST {path}: {resp.status_code}{self.short(resp)}")
        return user

    def login(self, user: User) -> str:
        path = self.need("auth", "login_path")
        resp = self.post(path, self.credentials(user), auth=None)
        self.expect(resp, (200, 201), f"POST {path}")
        data = self.json(resp, f"POST {path}")
        token_field = self.contract["auth"].get("token_field", "access_token")
        token = data.get(token_field) if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise Fail(f"POST {path}: в ответе нет токена в поле {token_field}")
        return token

    def auth_header(self, user: User) -> str:
        if self.scheme == "bearer":
            if user.token is None:
                user.token = self.login(user)
            return f"Bearer {user.token}"
        raw = f"{user.username}:{user.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def me(self, user: User) -> dict:
        path = self.need("auth", "me_path")
        resp = self.expect(self.get(path, auth=user), 200, f"GET {path}")
        data = self.json(resp, f"GET {path}")
        if not isinstance(data, dict):
            raise Fail(f"GET {path}: ожидали объект")
        user.id = data.get("id", user.id)
        return data

    # ----- репозиторий и docker -----

    def git(self, *args: str) -> str | None:
        if not shutil.which("git"):
            return None
        proc = subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True)
        return proc.stdout if proc.returncode == 0 else None

    def tracked_files(self) -> list[str] | None:
        out = self.git("ls-files")
        return out.splitlines() if out is not None else None

    def commit(self) -> str:
        return (self.git("rev-parse", "--short", "HEAD") or "").strip() or "нет git"

    def compose_file(self) -> Path | None:
        for name in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"):
            if (self.repo / name).exists():
                return self.repo / name
        return None

    def docker_ok(self) -> bool:
        if not shutil.which("docker") or self.compose_file() is None:
            return False
        return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0

    def compose(self, *args: str, show: bool = True, check: bool = True, timeout: int = 600) -> str:
        if show:
            print(dim(f"      $ docker compose {' '.join(args)}"))
        proc = subprocess.run(["docker", "compose", *args], cwd=self.repo, capture_output=True,
                              text=True, timeout=timeout)
        if check and proc.returncode != 0:
            raise Fail(f"docker compose {' '.join(args)} завершился с ошибкой: {proc.stderr.strip()[-300:]}")
        return proc.stdout + proc.stderr

    # ----- ввод -----

    @staticmethod
    def ask_yes_no(question: str) -> bool:
        # «т» стоит на клавише n в русской раскладке
        while True:
            answer = input(f"{question} [y/n] ").strip().lower()
            if answer in ("y", "yes", "д", "да"):
                return True
            if answer in ("n", "no", "н", "нет", "т"):
                return False


# ---------- Сравнение значений ----------

def _as_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) < 10 or value[4:5] != "-":
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def same(sent: Any, got: Any) -> bool:
    """Значение вернулось тем же, что отправили (с поправкой на формат дат и чисел)."""
    if sent == got:
        return True
    a, b = _as_datetime(sent), _as_datetime(got)
    if a and b:
        return a == b
    if isinstance(sent, (int, float)) and isinstance(got, (int, float)) and not isinstance(sent, bool):
        return float(sent) == float(got)
    if isinstance(sent, (int, float)) and isinstance(got, str):
        try:
            return float(got) == float(sent)
        except ValueError:
            return False
    if isinstance(sent, str) and isinstance(got, str):
        return sent.strip() == got.strip()
    return False


def tag(n: int = 4) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


# ---------- Звёздочки: правила подсчёта ----------

def score_two(parts: list[bool]) -> int:
    """Две части: обе дают 2 балла, одна даёт 1."""
    n = sum(parts)
    return 2 if n == len(parts) else (1 if n else 0)


def score_three(parts: list[bool]) -> int:
    """Три части: все дают 2 балла, две дают 1, меньше дают 0."""
    n = sum(parts)
    return 2 if n == 3 else (1 if n == 2 else 0)


def score_first_required(parts: list[bool]) -> int:
    """Первая часть обязательна: без неё 0; всё вместе 2; иначе 1."""
    if not parts[0]:
        return 0
    return 2 if all(parts) else 1


def score_any(parts: list[bool]) -> int:
    """Одна из частей на выбор (лаба 5 ★★): полностью 2, частично 1."""
    return 2 if any(parts) else 0


# ---------- Описание лабы ----------

@dataclass
class Star:
    title: str
    scorer: Callable[[list[bool]], int]
    parts: list[tuple[str, Callable[[Ctx, Item], None]]]


@dataclass
class Lab:
    number: int
    title: str
    base: list[tuple[str, str, Callable[[Ctx, Item], None]]]
    stars: list[Star]
    before: Callable[[Ctx], None] | None = None
    after: Callable[[Ctx], None] | None = None


@dataclass
class LabResult:
    lab: Lab
    base: list[Item]
    stars: list[tuple[Star, list[Item], int, int]]  # звезда, части, минимум, максимум

    @property
    def base_passed(self) -> int:
        return sum(1 for i in self.base if i.status == PASS)

    @property
    def base_manual(self) -> int:
        return sum(1 for i in self.base if i.status == MANUAL)

    @property
    def base_failed(self) -> int:
        return sum(1 for i in self.base if i.status == FAIL)


def run_item(ctx: Ctx, key: str, title: str, fn: Callable[[Ctx, Item], None], indent: str = "  ") -> Item:
    item = Item(ctx, key, title)
    print(f"\n{indent}{bold(key)}  {bold(title)}")
    try:
        fn(ctx, item)
        if item.eye_failed:
            item.status, item.reason = FAIL, "не подтверждено при ручной проверке"
        elif item.half:
            item.status = HALF
        elif item.pending:
            item.status = MANUAL
    except Fail as exc:
        item.status, item.reason = FAIL, str(exc)
        item.lines.append(("bad", str(exc)))
        item._print("bad", str(exc))
    except ContractError as exc:
        item.status, item.reason = FAIL, str(exc)
        item.lines.append(("bad", str(exc)))
        item._print("bad", f"{exc}. Нужно дописать contract.json")
    except Unreachable as exc:
        item.status, item.reason = FAIL, str(exc)
        item.lines.append(("bad", str(exc)))
        item._print("bad", str(exc))
        ctx.wait_health(10)
    verdict = {
        PASS: green("итог: ✓ засчитано"),
        FAIL: red("итог: ✗ не засчитано"),
        MANUAL: blue("итог: ? автоматическая часть пройдена, остальное проверяется вручную"),
        HALF: yellow("итог: ◐ частично"),
    }[item.status]
    print(f"      {verdict}")
    return item


def run_lab(ctx: Ctx, lab: Lab, *, stars: bool, only: str | None) -> LabResult:
    print("\n" + bold(f"━━ Лаба {lab.number}. {lab.title} ━━"))
    result = LabResult(lab, [], [])
    if lab.before:
        lab.before(ctx)
    for key, title, fn in lab.base:
        if only and key != only:
            continue
        result.base.append(run_item(ctx, key, title, fn))
    if stars and not only:
        for star in lab.stars:
            print("\n" + bold(f"  {star.title}"))
            parts = [run_item(ctx, f"{star.title[:2].strip()}.{n}", t, fn, indent="    ")
                     for n, (t, fn) in enumerate(star.parts, 1)]
            if star.scorer is score_any:
                half = 1 if any(p.status == HALF for p in parts) else 0
                hi = 2 if any(p.status in (PASS, MANUAL) for p in parts) else half
                lo = 2 if any(p.status == PASS for p in parts) else half
            else:
                hi = star.scorer([p.status in (PASS, MANUAL) for p in parts])
                lo = star.scorer([p.status == PASS for p in parts])
            result.stars.append((star, parts, lo, hi))
    if lab.after:
        lab.after(ctx)
    return result
