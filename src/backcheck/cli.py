"""Точка входа: backcheck --lab N, backcheck jwt <токен>, backcheck ask --lab N."""

from __future__ import annotations

import argparse
import importlib
import base64
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .core import (FAIL, MANUAL, PASS, ContractError, Ctx, LabResult, Unreachable, blue, bold, dim, green,
                   red, run_lab, yellow)


def load_lab(n: int):
    return importlib.import_module(f"backcheck.lab{n}").LAB


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "jwt":
        sys.exit(jwt_command(argv[1:]))
    if argv and argv[0] == "ask":
        from .questions import ask_command
        sys.exit(ask_command(argv[1:]))
    sys.exit(check_command(argv))


# ---------- backcheck --lab N ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backcheck",
        description="Проверяет лабы курса «Backend-разработка» по HTTP. "
                    "Ещё команды: «backcheck jwt <токен>» разбирает JWT, «backcheck ask --lab N» выдаёт вопрос по лабе.",
    )
    p.add_argument("--lab", type=int, choices=range(1, 6), required=True, metavar="N", help="номер лабы 1-5")
    p.add_argument("--stars", action="store_true", help="проверить ещё ★ и ★★")
    p.add_argument("--all", action="store_true", help="регрессия: базы всех лаб от 1 до N")
    p.add_argument("--only", metavar="K", help="только пункт K базы (1-6)")
    p.add_argument("--url", help="адрес сервиса вместо base_url из contract.json")
    p.add_argument("--ci", action="store_true", help="режим CI для лабы 5: сам поднимает docker compose")
    p.add_argument("-v", "--verbose", action="store_true", help="печатать каждый запрос и ответ")
    p.add_argument("--version", action="version", version=f"backcheck {__version__}")
    return p


def check_command(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    contract_path = Path("contract.json").resolve()
    if not contract_path.exists():
        print(red(f"Не найден {contract_path}. Чекер запускается из корня репозитория, где лежит contract.json."))
        return 2
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(red(f"contract.json, невалидный JSON: {exc}"))
        return 2

    ctx = Ctx(contract, contract_path.parent, base_url=args.url, verbose=args.verbose, ci=args.ci)
    numbers = list(range(1, args.lab + 1)) if args.all else [args.lab]

    print(bold(f"backcheck {__version__}") + dim(f" · лаба {args.lab} · {ctx.base_url} · ресурс: {ctx.resource}"
                                                 f" · коммит {ctx.commit()}"))
    if args.ci:
        if not ctx.docker_ok():
            print(red("--ci: нет docker compose или compose-файла в репозитории"))
            return 2
        if not (ctx.repo / ".env").exists() and (ctx.repo / ".env.example").exists():
            shutil.copy(ctx.repo / ".env.example", ctx.repo / ".env")
            print(dim("      $ cp .env.example .env"))
        try:
            ctx.compose("up", "-d", "--build", "--wait", timeout=900)
        except Exception as exc:
            print(red(f"docker compose up не поднялся: {exc}"))
            print(ctx.compose("logs", "--no-color", "--tail", "80", show=False, check=False))
            return 1

    if not ctx.wait_health(60 if args.ci else 5):
        print(red(f"\nСервис не отвечает: GET {ctx.base_url}/health. Запусти его или проверь base_url в contract.json или --url."))
        return 2

    results: list[LabResult] = []
    try:
        for n in numbers:
            last = n == args.lab
            results.append(run_lab(ctx, load_lab(n), stars=args.stars and last, only=args.only if last else None))
    except KeyboardInterrupt:
        print(yellow("\nПрервано."))
        ctx.cleanup()
        return 1
    except (ContractError, Unreachable) as exc:
        print(red(f"\n{exc}"))
        ctx.cleanup()
        return 2
    ctx.cleanup()

    print_summary(results)
    if ctx.warnings:
        print(yellow("\nПредупреждения (на баллы не влияют, но про это могут спросить):"))
        for w in ctx.warnings:
            print(yellow(f"  ⚠ {w}"))
    return 1 if any(r.base_failed for r in results) else 0


def verdict(r: LabResult) -> str:
    total = len(r.base)
    if total < 6:
        return f"{r.base_passed}/{total} из выбранных пунктов"
    if r.base_passed >= 4:
        return green("порог «принята» (≥ 4) пройден")
    if r.base_passed + r.base_manual >= 4:
        return blue("порог пройдёт, если на сдаче подтвердятся остальные пункты")
    return red("порог «принята» (≥ 4) не пройден")


def stars_text(r: LabResult) -> str:
    parts = []
    for star, _, lo, hi in r.stars:
        name = star.title.split()[0]
        parts.append(f"{name} {lo}/2" if lo == hi else f"{name} {lo}-{hi}/2 (часть проверяется на сдаче)")
    return " · ".join(parts)


def print_summary(results: list[LabResult]) -> None:
    print("\n" + bold("━━ Итог ━━"))
    for r in results:
        marks = "".join({PASS: green("✓"), FAIL: red("✗"), MANUAL: blue("?")}.get(i.status, "?") for i in r.base)
        manual = f" + {r.base_manual} проверяется на сдаче" if r.base_manual else ""
        print(f"Лаба {r.lab.number}: база {r.base_passed}/{len(r.base)}{manual}  {marks}  {verdict(r)}")
        if r.stars:
            print(f"        {stars_text(r)}")
            print(dim("        звёздочки засчитываются, только если база принята и PR открыт вовремя"))


# ---------- backcheck jwt <токен> ----------

def _b64(part: str) -> dict:
    padded = part + "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def jwt_command(argv: list[str]) -> int:
    if not argv:
        print("Использование: backcheck jwt <токен>")
        return 2
    token = argv[0].removeprefix("Bearer ").strip()
    try:
        header_raw, payload_raw, signature = token.split(".")
        header, payload = _b64(header_raw), _b64(payload_raw)
    except ValueError:
        print(red("Это не JWT: нужно три части через точку, header и payload в base64url."))
        return 2
    print(bold("Header:  ") + json.dumps(header, ensure_ascii=False))
    print(bold("Payload: ") + json.dumps(payload, ensure_ascii=False))
    print(bold("Подпись: ") + (signature[:20] + "…" if signature else red("пустая!")))
    fmt = lambda ts: datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    if "iat" in payload:
        print(f"Выдан:    {fmt(payload['iat'])}")
    if "exp" in payload:
        left = payload["exp"] - datetime.now(timezone.utc).timestamp()
        print(f"Истекает: {fmt(payload['exp'])} " + (green(f"(осталось {int(left)} с)") if left > 0 else red("(истёк)")))
        if "iat" in payload:
            print(f"Срок жизни: {payload['exp'] - payload['iat']} с")
    else:
        print(yellow("В токене нет exp, значит он вечный."))
    print(dim("Header и payload читаются без секрета: это base64, а не шифрование. Секрет нужен только для подписи."))
    return 0
