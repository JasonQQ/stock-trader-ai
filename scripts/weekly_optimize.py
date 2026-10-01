#!/usr/bin/env python3
"""
周度因子权重优化（周五 16:00）
- 向 initial_weights 回归 regression_rate（默认 3%）
- 边界约束 [min, max]
输出 JSON 结果
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))
DATA_DIR = Path(__file__).parent.parent / "data"
WEIGHTS_FILE = DATA_DIR / "scoring_weights.json"


def optimize():
    if not WEIGHTS_FILE.exists():
        return {"error": f"权重文件不存在: {WEIGHTS_FILE}"}

    with open(WEIGHTS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    weights = dict(data.get("weights") or {})
    initial = dict(data.get("initial_weights") or weights)
    bounds = data.get("bounds") or {"min": 5, "max": 40}
    rate = float(data.get("regression_rate", 0.03))
    lo, hi = float(bounds.get("min", 5)), float(bounds.get("max", 40))

    before = dict(weights)
    after = {}
    for k, cur in weights.items():
        target = float(initial.get(k, cur))
        cur_f = float(cur)
        # 向初始值回归
        nxt = cur_f + (target - cur_f) * rate
        nxt = max(lo, min(hi, nxt))
        after[k] = round(nxt, 2)

    data["weights"] = after
    data["last_updated"] = datetime.now(CST).strftime("%Y-%m-%d")
    data["last_optimize"] = datetime.now(CST).isoformat()

    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return {
        "status": "ok",
        "regression_rate": rate,
        "bounds": {"min": lo, "max": hi},
        "before": before,
        "after": after,
        "updated_at": data["last_optimize"],
    }


if __name__ == "__main__":
    result = optimize()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)
