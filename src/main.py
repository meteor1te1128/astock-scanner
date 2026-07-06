"""入口。用法：
  python -m src.main daily      每日：交易日校验 → 更新数据 → 筛选 → 基本面 → 生成页面
  python -m src.main backtest   手动：全市场回测两套规则（含基本面历史匹配）→ 生成回测页
"""
import sys

import pandas as pd

from . import data_store, render
from .screener import run_screen


def _updated_str():
    return pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M（北京时间）")


def daily():
    idx = data_store.index_hist()
    today = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d")
    bootstrap = not data_store.DATA.exists()
    if str(idx["date"].iloc[-1]) != today and not bootstrap:
        print(f"今日 {today} 非交易日（指数最新日期 {idx['date'].iloc[-1]}），跳过更新")
        return

    data = data_store.update()
    mcap_map = data_store.load_meta()
    industry_map = data_store.ensure_industry()
    idx_pct = (float(idx["close"].iloc[-1]) / float(idx["close"].iloc[-2]) - 1) * 100

    scores, fund_map = pd.DataFrame(), {}
    try:  # 基本面失败不影响筛选页（fund_map 为空则跳过基本面划掉项）
        from .fundamental import fetch_fundamentals, to_fund_map
        scores = fetch_fundamentals()
        fund_map = to_fund_map(scores)
    except Exception as e:
        print(f"基本面拉取失败（今日候选页将不含基本面划掉项）：{e}")

    allrows = run_screen(data, idx_pct, mcap_map, industry_map, fund_map)
    n_ok = 0 if allrows.empty else int((allrows["status"] == "pass").sum())
    print(f"触发信号 {len(allrows)} 只，合格 {n_ok} 只")
    render.render_screen(allrows, _updated_str(), idx_pct)

    if not scores.empty:
        from .fundamental import attribute_drop
        drop = {}
        for code, g in data.groupby("code"):
            g = g.sort_values("date")
            if len(g) >= 60:
                drop[code] = (float(g["close"].iloc[-1]) / float(g["close"].iloc[-60])
                              - 1) * 100
        scores["label"] = [attribute_drop(drop.get(r["code"], 0.0), r["score"], r["flags"])
                           for _, r in scores.iterrows()]
    render.render_fundamental(scores, _updated_str())

    bt, period = render.load_backtest_json()
    render.render_backtest(bt, _updated_str(), period)


def backtest():
    from .backtest import run_backtest
    data = data_store._load()
    if data.empty:
        data = data_store.update()
    idx = data_store.index_hist()
    mcap_map = data_store.load_meta()
    fund_hist = {}
    try:
        from .fundamental import fund_history
        fund_hist = fund_history(8)
    except Exception as e:
        print(f"基本面历史拉取失败，回测将不含基本面条件：{e}")
    result = run_backtest(data, idx, mcap_map, fund_hist or None)
    dates = sorted(data["date"].astype(str).unique())
    period = f"{dates[0]} ~ {dates[-1]}"
    render.save_backtest_json(result, period)
    render.render_backtest(result, _updated_str(), period)
    print(result)


if __name__ == "__main__":
    {"daily": daily, "backtest": backtest}[sys.argv[1]]()
