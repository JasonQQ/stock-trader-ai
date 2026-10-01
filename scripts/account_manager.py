#!/usr/bin/env python3
"""
多账户管理（无「运行账户」概念）
- 账户文件: data/accounts/<id>.json
- 注册表: data/accounts_registry.json
- 当前操作上下文: 环境变量 STOCK_ACCOUNT_ID 或显式传入 account_id
- Cron / 盯盘应对全部账户逐个执行
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
ACCOUNTS_DIR = DATA_DIR / "accounts"
REGISTRY_FILE = DATA_DIR / "accounts_registry.json"
LEGACY_FILE = DATA_DIR / "trend_account.json"
ENV_ACCOUNT = "STOCK_ACCOUNT_ID"

ID_RE = re.compile(r"^[a-zA-Z0-9_]{1,32}$")


def _now_iso() -> str:
    return datetime.now(CST).isoformat()


def account_path(account_id: str) -> Path:
    return ACCOUNTS_DIR / f"{account_id}.json"


def _default_registry() -> dict:
    return {"accounts": {}}


def load_registry() -> dict:
    ensure_migrated()
    if not REGISTRY_FILE.exists():
        return _default_registry()
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _default_registry()
    data.setdefault("accounts", {})
    # 清理历史 active_id 字段（不再使用）
    data.pop("active_id", None)
    return data


def save_registry(reg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    reg = dict(reg)
    reg.pop("active_id", None)
    REGISTRY_FILE.write_text(
        json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def ensure_migrated() -> None:
    """首次将旧 trend_account.json 迁入多账户体系。"""
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    if REGISTRY_FILE.exists():
        # 若注册表仍带 active_id，下次 save 时清掉
        return

    reg = _default_registry()
    if LEGACY_FILE.exists():
        dest = account_path("default")
        if not dest.exists():
            shutil.copy2(LEGACY_FILE, dest)
        try:
            acc = json.loads(LEGACY_FILE.read_text(encoding="utf-8"))
        except Exception:
            acc = {}
        reg["accounts"]["default"] = {
            "id": "default",
            "name": "默认账户",
            "created_at": acc.get("created_at") or _now_iso(),
            "initial_cash": acc.get("initial_cash", 100000),
        }
    save_registry(reg)


def validate_id(account_id: str) -> str:
    account_id = (account_id or "").strip()
    if not ID_RE.match(account_id):
        raise ValueError("账户 ID 仅允许字母/数字/下划线，长度 1–32")
    return account_id


def all_account_ids() -> list[str]:
    reg = load_registry()
    ids = sorted(reg.get("accounts", {}).keys())
    # 仅返回文件存在的
    return [i for i in ids if account_path(i).exists()]


def resolve_account_id(account_id: str | None = None) -> str:
    """
    解析当前账户 ID：
    1) 显式参数 2) 环境变量 STOCK_ACCOUNT_ID 3) 仅有一个账户时用它
    """
    ensure_migrated()
    if account_id:
        return validate_id(account_id)
    env = os.environ.get(ENV_ACCOUNT, "").strip()
    if env:
        return validate_id(env)
    ids = all_account_ids()
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise ValueError("尚无账户，请先创建：simulate_trend.py account create <id>")
    raise ValueError(
        f"存在多个账户 {ids}，请指定 --account <id> 或设置环境变量 {ENV_ACCOUNT}"
    )


def get_account_file(account_id: str | None = None) -> Path:
    return account_path(resolve_account_id(account_id))


def load_account_data(account_id: str | None = None) -> dict | None:
    try:
        path = get_account_file(account_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def set_account_context(account_id: str) -> str:
    """设置当前进程账户上下文（供子逻辑/同进程调用）。"""
    aid = validate_id(account_id)
    if aid not in load_registry().get("accounts", {}):
        raise ValueError(f"账户不存在: {aid}")
    os.environ[ENV_ACCOUNT] = aid
    return aid


def clear_account_context() -> None:
    os.environ.pop(ENV_ACCOUNT, None)


def list_accounts() -> list[dict]:
    reg = load_registry()
    rows = []
    for aid, meta in sorted(reg.get("accounts", {}).items()):
        path = account_path(aid)
        summary = {
            "id": aid,
            "name": meta.get("name") or aid,
            "created_at": meta.get("created_at"),
            "initial_cash": meta.get("initial_cash"),
            "exists": path.exists(),
        }
        if path.exists():
            try:
                acc = json.loads(path.read_text(encoding="utf-8"))
                summary["total_assets"] = acc.get("total_assets")
                summary["current_cash"] = acc.get("current_cash")
                summary["positions_count"] = len(acc.get("positions") or {})
                summary["regime"] = (acc.get("market_regime") or {}).get("current")
                summary["leader_style"] = (acc.get("market_regime") or {}).get("leader_style_cn")
                peak = float(acc.get("peak_total_assets") or acc.get("initial_cash") or 0)
                total = float(acc.get("total_assets") or 0)
                summary["drawdown_pct"] = round((peak - total) / peak * 100, 2) if peak else 0
                init = float(acc.get("initial_cash") or 0)
                summary["return_pct"] = (
                    round((total - init) / init * 100, 2) if init else 0
                )
            except Exception:
                pass
        rows.append(summary)
    return rows


def create_account(
    account_id: str,
    name: str | None = None,
    cash: float = 100000.0,
    *,
    overwrite: bool = False,
) -> dict:
    account_id = validate_id(account_id)
    reg = load_registry()
    path = account_path(account_id)
    if path.exists() and not overwrite:
        raise ValueError(f"账户已存在: {account_id}")

    acc = {
        "version": 3,
        "account_id": account_id,
        "account_name": name or account_id,
        "created_at": _now_iso(),
        "initial_cash": float(cash),
        "current_cash": float(cash),
        "total_assets": float(cash),
        "peak_total_assets": float(cash),
        "frozen_until": None,
        "positions": {},
        "trade_history": [],
        "daily_snapshots": [],
        "market_regime": {
            "current": "range",
            "confidence": 0,
            "params_active": {
                "stop_loss": -0.07,
                "tp1": 0.15, "tp2": 0.25, "tp3": 0.35,
                "max_position": 0.85,
                "max_holdings": 3,
                "min_change": 1, "max_change": 9.5,
                "atr_multiplier": 1.5,
            },
        },
        "stats": {
            "total_trades": 0, "total_buys": 0, "total_sells": 0,
            "stop_loss_count": 0, "take_profit_count": 0,
            "trailing_stop_count": 0,
            "drawdown_circuit_breaks": 0,
            "total_fees": 0.0, "total_profit": 0.0,
            "win_trades": 0, "lose_trades": 0,
        },
    }

    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(acc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    reg["accounts"][account_id] = {
        "id": account_id,
        "name": name or account_id,
        "created_at": acc["created_at"],
        "initial_cash": float(cash),
    }
    save_registry(reg)
    return {"status": "ok", "id": account_id, "name": name or account_id, "cash": cash}


def delete_account(account_id: str) -> dict:
    account_id = validate_id(account_id)
    reg = load_registry()
    if account_id not in reg.get("accounts", {}):
        raise ValueError(f"账户不存在: {account_id}")
    if len(reg.get("accounts", {})) <= 1:
        raise ValueError("至少保留一个账户")

    path = account_path(account_id)
    if path.exists():
        bak = ACCOUNTS_DIR / f"{account_id}_deleted_{datetime.now(CST).strftime('%Y%m%d%H%M%S')}.json"
        shutil.move(str(path), str(bak))
    del reg["accounts"][account_id]
    save_registry(reg)
    if os.environ.get(ENV_ACCOUNT) == account_id:
        clear_account_context()
    return {"status": "ok", "deleted": account_id}


def rename_account(account_id: str, name: str) -> dict:
    account_id = validate_id(account_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("名称不能为空")
    reg = load_registry()
    if account_id not in reg.get("accounts", {}):
        raise ValueError(f"账户不存在: {account_id}")
    reg["accounts"][account_id]["name"] = name
    save_registry(reg)
    acc = load_account_data(account_id)
    if acc:
        acc["account_name"] = name
        account_path(account_id).write_text(
            json.dumps(acc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return {"status": "ok", "id": account_id, "name": name}


def status_payload() -> dict:
    return {
        "accounts": list_accounts(),
        "context": os.environ.get(ENV_ACCOUNT),
        "registry_file": str(REGISTRY_FILE),
        "accounts_dir": str(ACCOUNTS_DIR),
        "note": "Cron 会监控全部账户；单账户操作请用 --account 或 STOCK_ACCOUNT_ID",
    }


# 兼容旧调用名
def get_active_id() -> str | None:
    """已废弃：若环境已设置则返回，否则多账户时返回 None。"""
    try:
        return resolve_account_id(None)
    except ValueError:
        env = os.environ.get(ENV_ACCOUNT, "").strip()
        return env or None


def sync_legacy(account_id: str | None = None) -> None:
    """兼容保留：不再维护单一 trend_account.json 作为运行指针。"""
    return


def switch_account(account_id: str) -> dict:
    """兼容保留：仅设置当前进程环境变量，不影响 Cron。"""
    aid = set_account_context(account_id)
    meta = load_registry().get("accounts", {}).get(aid, {})
    return {"status": "ok", "account_id": aid, "name": meta.get("name", aid),
            "note": "仅设置进程上下文，Cron 始终监控全部账户"}


def main(argv: list[str] | None = None):
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(json.dumps(status_payload(), ensure_ascii=False, indent=2))
        return

    cmd = argv[0]
    try:
        if cmd in ("list", "status"):
            print(json.dumps(status_payload(), ensure_ascii=False, indent=2))
        elif cmd == "create":
            if len(argv) < 2:
                raise SystemExit("用法: account_manager.py create <id> [--name 名称] [--cash 金额]")
            aid = argv[1]
            name, cash = None, 100000.0
            i = 2
            while i < len(argv):
                if argv[i] == "--name" and i + 1 < len(argv):
                    name = argv[i + 1]; i += 2
                elif argv[i] == "--cash" and i + 1 < len(argv):
                    cash = float(argv[i + 1]); i += 2
                else:
                    i += 1
            print(json.dumps(create_account(aid, name=name, cash=cash),
                             ensure_ascii=False, indent=2))
        elif cmd == "delete":
            if len(argv) < 2:
                raise SystemExit("用法: account_manager.py delete <id>")
            print(json.dumps(delete_account(argv[1]), ensure_ascii=False, indent=2))
        elif cmd == "rename":
            if len(argv) < 3:
                raise SystemExit("用法: account_manager.py rename <id> <name>")
            print(json.dumps(rename_account(argv[1], argv[2]), ensure_ascii=False, indent=2))
        else:
            raise SystemExit("用法: account_manager.py [list|create|delete|rename|status]")
    except ValueError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
