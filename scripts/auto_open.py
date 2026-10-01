#!/usr/bin/env python3
"""
开盘自动建仓：按账户当前市场环境的 max_position / max_holdings
对评分候选均分买入，并遵守板块/涨停/冻结等约束。

用法:
  python3 scripts/auto_open.py --account default --candidates-file data/open_candidates_xxx.json
  python3 scripts/auto_open.py --account default   # 从 stdin 读 scored list / {candidates:[...]}
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from account_manager import (  # noqa: E402
    load_account_data,
    set_account_context,
    resolve_account_id,
)
from simulate_trend import (  # noqa: E402
    ALLOWED_BOARDS,
    LOT_SIZES,
    calc_total_assets,
    detect_board,
    is_frozen,
)

# monitor 告警类型：出现则本轮禁止新开仓
BUY_BLOCK_ALERTS = {
    "frozen",
    "index_circuit_breaker",
    "stampede",
    "drawdown_circuit_breaker",
}


def _python() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else (sys.executable or "python3")


def market_code(code: str) -> str:
    pure = str(code).lstrip("shszSHSZ")
    return f"sh{pure}" if pure.startswith("6") else f"sz{pure}"


def fetch_quote(code: str) -> dict | None:
    mc = market_code(code)
    try:
        r = subprocess.run(
            [_python(), str(SCRIPT_DIR / "get_price.py"), mc],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
        )
        if not (r.stdout or "").strip():
            return None
        data = json.loads(r.stdout)
        if isinstance(data, dict) and not data.get("error"):
            return data
    except Exception:
        return None
    return None


def regime_params(acc: dict) -> dict:
    mr = acc.get("market_regime") or {}
    params = dict(mr.get("params_active") or {})
    params.setdefault("max_position", 0.85)
    params.setdefault("max_holdings", 3)
    params.setdefault("max_change", 9.5)
    params.setdefault("min_change", 1)
    return params


def buy_block_reason(monitor_detail: dict | None, acc: dict) -> str | None:
    """返回禁止开仓原因；None 表示可买。"""
    if is_frozen(acc):
        return f"交易冻结至 {acc.get('frozen_until')}"
    if not monitor_detail or not isinstance(monitor_detail, dict):
        return None
    for alert in monitor_detail.get("alerts") or []:
        at = alert.get("type")
        # 回撤熔断只在冻结期内禁止开仓；空仓后净值仍低于历史峰值不得永久锁死
        if at == "drawdown_circuit_breaker":
            if is_frozen(acc):
                return f"风控阻断: {at}"
            continue
        if at in BUY_BLOCK_ALERTS:
            return f"风控阻断: {at}"
    return None


def slots_needed(acc: dict) -> int:
    params = regime_params(acc)
    max_h = max(0, int(params.get("max_holdings") or 0))
    held = len(acc.get("positions") or {})
    return max(0, max_h - held)


def calc_shares(budget: float, price: float, board: str) -> int:
    if price <= 0 or budget <= 0:
        return 0
    lot = LOT_SIZES.get(board, 100)
    shares = int(budget / price / lot) * lot
    return shares if shares >= lot else 0


def execute_buy(
    account_id: str,
    code: str,
    name: str,
    shares: int,
    price: float,
    reason: str,
) -> tuple[bool, dict]:
    cmd = [
        _python(),
        str(SCRIPT_DIR / "simulate_trend.py"),
        "--account",
        account_id,
        "buy",
        code,
        name,
        str(shares),
        f"{price:.3f}",
        "--reason",
        reason,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        raw = (r.stdout or "").strip()
        data = json.loads(raw) if raw else {"error": "无输出", "stderr": (r.stderr or "")[-200:]}
    except Exception as e:
        return False, {"error": str(e)}
    if r.returncode != 0 or data.get("error"):
        return False, data if isinstance(data, dict) else {"error": str(data)}
    return True, data


def pick_and_buy(
    account_id: str,
    candidates: list,
    *,
    monitor_detail: dict | None = None,
    dry_run: bool = False,
) -> dict:
    """
    按 regime 自适应仓位对候选自动买入。
    返回结构化结果（供 cron / CLI）。
    """
    set_account_context(account_id)
    acc = load_account_data(account_id)
    if not acc:
        return {"status": "error", "message": "账户未初始化", "buys": [], "skipped": []}

    params = regime_params(acc)
    regime = (acc.get("market_regime") or {}).get("current", "range")
    max_pos = float(params["max_position"])
    max_h = int(params["max_holdings"])
    max_chg = float(params.get("max_change") or 9.5)

    block = buy_block_reason(monitor_detail, acc)
    if block:
        return {
            "status": "skip",
            "message": block,
            "regime": regime,
            "max_position": max_pos,
            "max_holdings": max_h,
            "buys": [],
            "skipped": [],
        }

    need = slots_needed(acc)
    if need <= 0:
        return {
            "status": "skip",
            "message": f"已满仓（{len(acc.get('positions') or {})}/{max_h}）",
            "regime": regime,
            "max_position": max_pos,
            "max_holdings": max_h,
            "buys": [],
            "skipped": [],
        }

    if not candidates:
        return {
            "status": "skip",
            "message": "无候选股票",
            "regime": regime,
            "max_position": max_pos,
            "max_holdings": max_h,
            "buys": [],
            "skipped": [],
        }

    held = set((acc.get("positions") or {}).keys())
    buys: list[dict] = []
    skipped: list[dict] = []

    # 均分：总资产 × max_position / max_holdings（与回测一致）
    for cand in candidates:
        if len(buys) >= need:
            break
        # 实盘每轮重载账户；dry-run 在内存中累积
        if not dry_run:
            acc = load_account_data(account_id) or acc
        if slots_needed(acc) <= 0:
            break

        code = str(cand.get("code") or "").lstrip("shszSHSZ")
        name = cand.get("name") or code
        if not code:
            continue
        if code in held:
            skipped.append({"code": code, "name": name, "reason": "已持有"})
            continue

        board = detect_board(code)
        if board not in ALLOWED_BOARDS:
            skipped.append({"code": code, "name": name, "reason": f"不可交易板块 {board}"})
            continue

        quote = fetch_quote(code)
        if not quote:
            skipped.append({"code": code, "name": name, "reason": "行情获取失败"})
            continue

        price = float(quote.get("price") or 0)
        change_pct = float(quote.get("change_pct") or 0)
        qname = quote.get("name") or name

        if price <= 0:
            skipped.append({"code": code, "name": qname, "reason": "现价无效"})
            continue
        if change_pct >= max_chg:
            skipped.append({
                "code": code,
                "name": qname,
                "reason": f"涨幅 {change_pct:.2f}% ≥ 上限 {max_chg}%（避涨停）",
            })
            continue

        total_assets = calc_total_assets(acc)
        cash = float(acc.get("current_cash") or 0)
        slot_budget = total_assets * max_pos / max(max_h, 1)
        slot_budget = min(slot_budget, cash * 0.95)
        shares = calc_shares(slot_budget, price, board)
        if shares <= 0:
            skipped.append({
                "code": code,
                "name": qname,
                "reason": f"预算不足（¥{slot_budget:.0f} / 价{price}）",
            })
            continue

        score = cand.get("total_score")
        reason = f"auto_open_{regime}_score{score}" if score is not None else f"auto_open_{regime}"
        if dry_run:
            held.add(code)
            # 模拟扣减现金，便于连续估算多只
            cost = shares * price * 1.001
            acc["current_cash"] = cash - cost
            acc.setdefault("positions", {})[code] = {
                "name": qname, "shares": shares, "avg_price": price,
            }
            buys.append({
                "code": code,
                "name": qname,
                "shares": shares,
                "price": price,
                "score": score,
                "dry_run": True,
                "budget": round(slot_budget, 2),
            })
            continue

        ok, detail = execute_buy(account_id, code, qname, shares, price, reason)
        if ok:
            held.add(code)
            buys.append({
                "code": code,
                "name": qname,
                "shares": detail.get("shares", shares),
                "price": detail.get("exec_price", detail.get("query_price", price)),
                "score": score,
                "result": detail,
            })
        else:
            skipped.append({
                "code": code,
                "name": qname,
                "reason": detail.get("error") or "买入失败",
                "detail": detail,
            })

    verb = "模拟买入" if dry_run else "买入"
    if buys:
        status, message = "ok", f"{verb} {len(buys)} 只（regime={regime}, 仓位≤{max_pos:.0%}, 最多{max_h}）"
    elif skipped:
        status, message = "skip", f"未成交（尝试 {len(skipped)} 只被过滤/失败）"
    else:
        status, message = "skip", "无可买标的"

    return {
        "status": status,
        "message": message,
        "regime": regime,
        "max_position": max_pos,
        "max_holdings": max_h,
        "slots_needed": need,
        "buys": buys,
        "skipped": skipped,
    }


def _load_candidates(path: str | None) -> list:
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        raw = sys.stdin.read().strip()
        if not raw:
            return []
        data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("candidates") or data.get("top") or []
    return []


def main():
    parser = argparse.ArgumentParser(description="按市场环境自动建仓")
    parser.add_argument("--account", default=None, help="账户 ID")
    parser.add_argument("--candidates-file", default=None, help="候选 JSON 文件")
    parser.add_argument("--dry-run", action="store_true", help="只计算不下单")
    args = parser.parse_args()
    aid = resolve_account_id(args.account or os.environ.get("STOCK_ACCOUNT_ID"))
    cands = _load_candidates(args.candidates_file)
    result = pick_and_buy(aid, cands, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("status") in ("ok", "skip") else 1)


if __name__ == "__main__":
    main()
