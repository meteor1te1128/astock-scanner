"""合成数据单元测试（v2）：不依赖网络，验证核心业务逻辑。"""
import numpy as np
import pandas as pd
import pytest

from src.indicators import macd, ma
from src.screener import evaluate_stock, prepare, run_screen
from src.backtest import simulate_stock, summarize, build_extra_ok
from src.fundamental import (score_row, attribute_drop, report_period_for,
                             periods_back)


def make_df(closes, volume=1e6, amount=5e8, turnover=5.0):
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": closes, "high": [c * 1.02 for c in closes],
        "low": [c * 0.98 for c in closes], "close": closes,
        "volume": [volume] * n, "amount": [amount] * n, "turnover": [turnover] * n})


def wave_closes(n=160):
    t = np.arange(n)
    return list(10 + t * 0.05 + np.sin(t / 6) * 0.8)


def fresh_red_df():
    """反向定位：截断到最后一个「红柱首日且价在MA20上」的交叉日。"""
    full = prepare(make_df(wave_closes()))
    ok = [i for i in range(61, len(full))
          if full["hist"].iloc[i] > 0 >= full["hist"].iloc[i - 1]
          and full["close"].iloc[i] > full["ma20"].iloc[i]]
    assert ok, "构造失败"
    df = make_df(wave_closes()[: ok[-1] + 1])
    df.loc[df.index[-1], "volume"] = 3e6
    return prepare(df)


# ---------- MACD ----------

def test_macd_matches_manual_ewm():
    close = pd.Series(np.linspace(10, 20, 100) + np.sin(np.arange(100)))
    dif, dea, hist = macd(close)
    dif_m = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    dea_m = dif_m.ewm(span=9, adjust=False).mean()
    assert np.allclose(dif, dif_m) and np.allclose(hist, (dif_m - dea_m) * 2)


# ---------- 筛选器：pass / reject 原因逐条 ----------

def GOOD():
    return dict(mcap_yi=200.0, industry="电池",
                fund={"score": 75.0, "flags": []})


def test_pass_case():
    r = evaluate_stock(fresh_red_df(), "示例股份", 0.0, **GOOD())
    assert r and r["status"] == "pass" and r["reasons"] == [] and r["industry"] == "电池"


def test_no_signal_returns_none():
    flat = prepare(make_df([10.0] * 80))
    assert evaluate_stock(flat, "示例股份", 0.0, **GOOD()) is None


@pytest.mark.parametrize("mutate,kw,expect", [
    (lambda d: d.assign(volume=d["volume"].mask(d.index == len(d) - 1, 1.0)),
     {}, "缩量"),
    (lambda d: d.assign(amount=d["amount"].mask(d.index == len(d) - 1, 1e8)),
     {}, "不足3亿"),
    (lambda d: d, {"index_pct_today": 99.0}, "弱于沪深300"),
    (lambda d: d.assign(turnover=d["turnover"].mask(d.index == len(d) - 1, 1.0)),
     {}, "换手率1.0%过低"),
])
def test_reject_reasons_market(mutate, kw, expect):
    g = GOOD()
    r = evaluate_stock(mutate(fresh_red_df()), "示例股份",
                       kw.get("index_pct_today", 0.0), mcap_yi=g["mcap_yi"],
                       industry=g["industry"], fund=g["fund"])
    assert r["status"] == "reject" and any(expect in x for x in r["reasons"])


def test_reject_small_mcap_st_and_new():
    df = fresh_red_df()
    r = evaluate_stock(df, "示例股份", 0.0, mcap_yi=30.0, fund=None)
    assert any("市值30亿过小" == x for x in r["reasons"])
    r = evaluate_stock(df, "*ST示例", 0.0, **GOOD())
    assert any("ST" in x for x in r["reasons"])
    r = evaluate_stock(df.iloc[-50:].reset_index(drop=True), "次新股份", 0.0, **GOOD())
    assert any("上市不足60" in x for x in r["reasons"])


def test_reject_fundamental():
    r = evaluate_stock(fresh_red_df(), "示例股份", 0.0, mcap_yi=200.0,
                       fund={"score": 35.0, "flags": ["净利润大幅下滑"]})
    assert "净利润大幅下滑" in r["reasons"]
    assert any("基本面分过低" in x for x in r["reasons"])


def test_hot_turnover_is_warning_not_reject():
    df = fresh_red_df()
    df.loc[df.index[-1], "turnover"] = 30.0
    r = evaluate_stock(df, "示例股份", 0.0, **GOOD())
    assert r["status"] == "pass" and any("过热" in w for w in r["warnings"])


def test_missing_turnover_mcap_fund_tolerated():
    df = fresh_red_df()
    df["turnover"] = np.nan
    r = evaluate_stock(df, "示例股份", 0.0, mcap_yi=None, fund=None)
    assert r["status"] == "pass"  # 数据缺失不冤杀


def test_run_screen_order_and_grouping():
    a = fresh_red_df()[["date", "open", "high", "low", "close", "volume",
                        "amount", "turnover"]].assign(code="600001", name="甲")
    b = a.copy().assign(code="600002", name="乙", amount=9e8)
    c = a.copy().assign(code="600003", name="*ST丙")
    alld = pd.concat([a, b, c])
    out = run_screen(alld, 0.0, mcap_map={"600001": 100, "600002": 100, "600003": 100})
    assert list(out["code"]) == ["600002", "600001", "600003"]
    assert list(out["status"]) == ["pass", "pass", "reject"]


# ---------- 回测 ----------

def test_backtest_t1():
    df = prepare(make_df(wave_closes(200)))
    trades = simulate_stock(df, False, None)
    assert trades and all(t["exit_i"] > t["entry_i"] for t in trades)


def test_backtest_stop_loss():
    sig = fresh_red_df()
    last = float(sig["close"].iloc[-1])
    crash = make_df([last * 0.9, last * 0.8, last * 0.7])
    df = pd.concat([sig[crash.columns], crash], ignore_index=True)
    df["date"] = pd.date_range("2026-01-01", periods=len(df)).strftime("%Y-%m-%d")
    df = prepare(df)
    trades = simulate_stock(df, False, None)
    assert trades and trades[-1]["reason"] == "止损"
    assert trades[-1]["ret"] == pytest.approx(-0.05 - 0.002, abs=0.01)


def test_extra_ok_gates_filtered_rule():
    raw = make_df(wave_closes(200))
    raw["volume"] = np.linspace(1e6, 2e6, len(raw))  # 递增量能，保证「放量」可成立
    df = prepare(raw).assign(code="600001")
    idx0 = pd.Series(0.0, index=df.index)
    base = simulate_stock(df, True, idx0,
                          extra_ok=build_extra_ok(df, 200.0, None))
    assert base, "对照组应有交易"
    low_t = df.assign(turnover=1.0)
    assert simulate_stock(low_t, True, idx0,
                          extra_ok=build_extra_ok(low_t, 200.0, None)) == []
    assert simulate_stock(df, True, idx0,
                          extra_ok=build_extra_ok(df, 30.0, None)) == []
    bad_fund = {p: {"600001": {"score": 30.0, "flags": []}}
                for p in periods_back(12, "2026-12-31")}
    assert simulate_stock(df, True, idx0,
                          extra_ok=build_extra_ok(df, 200.0, bad_fund)) == []


def test_summarize_math():
    s = summarize([{"ret": 0.1, "hold_days": 2, "reason": "转绿"},
                   {"ret": -0.05, "hold_days": 1, "reason": "止损"}])
    assert s["n"] == 2 and s["win_rate"] == 50.0
    assert s["total_ret"] == pytest.approx((1.1 * 0.95 - 1) * 100, abs=0.1)


# ---------- 报告期日历 ----------

@pytest.mark.parametrize("d,expect", [
    ("2026-03-15", "20250930"), ("2026-04-30", "20250930"),
    ("2026-05-02", "20260331"), ("2026-09-15", "20260630"),
    ("2026-11-20", "20260930"), ("2027-01-05", "20260930"),
])
def test_report_period_for(d, expect):
    assert report_period_for(d) == expect


def test_periods_back_chain():
    ps = periods_back(5, "2026-07-06")
    assert ps == ["20260331", "20251231", "20250930", "20250630", "20250331"]


# ---------- 基本面评分与归因 ----------

def test_score_good_beats_bad():
    good = score_row(dict(roe=18, margin=40, margin_prev=38, rev_g=25, profit_g=30,
                          debt_ratio=35, ocf_ratio=1.1, recv_g=20, goodwill_ratio=2))
    bad = score_row(dict(roe=1, margin=15, margin_prev=25, rev_g=-15, profit_g=-40,
                         debt_ratio=85, ocf_ratio=-0.5, recv_g=60, goodwill_ratio=35))
    assert good["score"] > 75 > 40 > bad["score"]
    assert not good["flags"] and len(bad["flags"]) >= 3


def test_attribution_rules():
    assert attribute_drop(-25, 70, []) == "情绪杀"
    assert attribute_drop(-25, 70, ["净利润大幅下滑"]) == "基本面杀"
    assert attribute_drop(-25, 45, []) == "存疑"
    assert attribute_drop(-5, 30, ["现金流质量差"]) == ""


# ---------- 页面渲染冒烟测试 ----------

def test_render_pages(tmp_path, monkeypatch):
    from src import render
    monkeypatch.setattr(render, "DOCS", tmp_path)
    rows = pd.DataFrame([
        {"code": "600001", "name": "示<b>例", "industry": "电池", "close": 20.5,
         "pct": 1.2, "amount_yi": 8.0, "turnover": 30.0, "mcap_yi": 200.0,
         "hist": 0.05, "above_ma20_pct": 2.0, "status": "pass",
         "reasons": [], "warnings": ["换手率30.0%过热"], "fund_score": 70.0},
        {"code": "600002", "name": "被划掉", "industry": "化工", "close": 9.9,
         "pct": -1.0, "amount_yi": 1.0, "turnover": 1.0, "mcap_yi": 20.0,
         "hist": 0.01, "above_ma20_pct": None, "status": "reject",
         "reasons": ["换手率1.0%过低", "市值20亿过小"], "warnings": [],
         "fund_score": None}])
    render.render_screen(rows, "2026-07-06 17:35", 0.42)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "&lt;b&gt;" in html and "<b>例" not in html          # XSS 转义
    assert "line-through" in html and 'class="card rej"' in html  # 划掉样式
    assert "换手率1.0%过低" in html and "市值20亿过小" in html      # 原因标注
    assert "过热" in html and "chip warn" in html                 # 黄色警告
    assert 'id="pmin"' in html and 'id="ind"' in html            # 筛选控件
    assert "themeBtn" in html and "localStorage" in html         # 主题切换
    assert "data-theme" in html and "<option>电池</option>" in html
    render.render_fundamental(pd.DataFrame(), "t")
    render.render_backtest(None, "t")
    assert (tmp_path / "fundamental.html").exists() and (tmp_path / "backtest.html").exists()
