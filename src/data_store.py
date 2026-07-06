"""行情数据仓库（data/history.parquet）+ 元数据（市值/行业）。

  - history.parquet：日线含换手率（新列，旧数据自动迁移补 NaN）
  - meta.parquet：每日更新的 code→总市值（亿）
  - industry.parquet：code→东财行业板块，每 7 天刷新一次（约 86 次请求）
"""
import time
from pathlib import Path

import pandas as pd

from .net import retry

_ROOT = Path(__file__).resolve().parent.parent / "data"
DATA = _ROOT / "history.parquet"
META = _ROOT / "meta.parquet"
INDUSTRY = _ROOT / "industry.parquet"
COLS = ["code", "name", "date", "open", "high", "low", "close",
        "volume", "amount", "turnover"]


def _spot():
    import akshare as ak
    df = retry(ak.stock_zh_a_spot_em)
    ren = {"代码": "code", "名称": "name", "今开": "open", "最高": "high",
           "最低": "low", "最新价": "close", "成交量": "volume",
           "成交额": "amount", "换手率": "turnover", "总市值": "mcap"}
    missing = [c for c in ren if c not in df.columns]
    if missing:
        raise RuntimeError(f"实时快照接口字段变化，缺少 {missing}，"
                           f"实际字段: {list(df.columns)[:25]}")
    df = df.rename(columns=ren)[list(ren.values())].copy()
    for c in list(ren.values())[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"])


def _hist_one(code: str, start: str) -> pd.DataFrame | None:
    """拉取单只个股历史。返回 None 仅代表【该股无数据】（停牌/退市/次新）。
    网络错误经 retry() 重试；重试后仍失败则抛出（严格模式：由调用方决定终止任务）。"""
    import akshare as ak

    def _pull():
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start, adjust="qfq")
        if df is None or df.empty:
            return None  # 空数据是正常业务情况，不重试、不报错
        ren = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
               "收盘": "close", "成交量": "volume", "成交额": "amount",
               "换手率": "turnover"}
        df = df.rename(columns=ren)
        if "turnover" not in df.columns:
            df["turnover"] = pd.NA
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[["date", "open", "high", "low", "close",
                   "volume", "amount", "turnover"]]

    return retry(_pull)


def _load() -> pd.DataFrame:
    if DATA.exists():
        df = pd.read_parquet(DATA)
        for c in COLS:            # 旧版数据迁移：缺列补 NaN
            if c not in df.columns:
                df[c] = pd.NA
        return df[COLS]
    return pd.DataFrame(columns=COLS)


def _save(df: pd.DataFrame):
    _ROOT.mkdir(exist_ok=True)
    df["date"] = df["date"].astype(str)
    cutoff = (pd.Timestamp.now() - pd.Timedelta(days=420)).strftime("%Y-%m-%d")
    df = df[df["date"] >= cutoff]
    df.drop_duplicates(["code", "date"], keep="last").to_parquet(DATA, index=False)


def load_meta() -> dict:
    """code → 总市值（亿）。"""
    if META.exists():
        m = pd.read_parquet(META)
        return dict(zip(m["code"], m["mcap_yi"]))
    return {}


def load_industry() -> dict:
    if INDUSTRY.exists():
        m = pd.read_parquet(INDUSTRY)
        return dict(zip(m["code"], m["industry"]))
    return {}


def ensure_industry(force: bool = False) -> dict:
    """行业板块映射，7 天内的缓存直接用。失败不阻塞主流程。"""
    if INDUSTRY.exists() and not force:
        age = time.time() - INDUSTRY.stat().st_mtime
        if age < 7 * 86400:
            return load_industry()
    import akshare as ak
    rows = []
    try:
        boards = retry(ak.stock_board_industry_name_em)
        col = "板块名称" if "板块名称" in boards.columns else boards.columns[1]
        for bname in boards[col].tolist():
            try:
                cons = retry(ak.stock_board_industry_cons_em, symbol=bname)
                ccol = "代码" if "代码" in cons.columns else cons.columns[1]
                rows += [{"code": str(c), "industry": bname} for c in cons[ccol]]
            except Exception:
                pass  # 单个板块失败仅影响该板块股票的"板块"显示标签，非选股条件
            time.sleep(0.5)
    except Exception as e:
        print(f"行业板块拉取失败（用旧缓存/留空，不影响选股）：{e}")
        return load_industry()
    if rows:
        _ROOT.mkdir(exist_ok=True)
        pd.DataFrame(rows).drop_duplicates("code").to_parquet(INDUSTRY, index=False)
    return load_industry()


def refresh_codes(store, codes, names, start, budget_sec):
    t0 = time.time()
    frames = [store[~store["code"].isin(codes)]]
    done = []
    for code in codes:
        if time.time() - t0 > budget_sec:
            frames.append(store[store["code"].isin(set(codes) - set(done))])
            print(f"时间预算用完，本次刷新 {len(done)}/{len(codes)} 只，其余下次继续")
            break
        h = _hist_one(code, start)
        if h is not None:
            h["code"], h["name"] = code, names.get(code, "")
            frames.append(h)
        done.append(code)
        time.sleep(0.3)
    else:
        print(f"刷新完成 {len(done)} 只")
    return pd.concat(frames, ignore_index=True)


def update(weekday: int | None = None) -> pd.DataFrame:
    store = _load()
    spot = _spot()
    _ROOT.mkdir(exist_ok=True)
    pd.DataFrame({"code": spot["code"],
                  "mcap_yi": spot["mcap"] / 1e8}).to_parquet(META, index=False)
    names = dict(zip(spot["code"], spot["name"]))
    start = (pd.Timestamp.now() - pd.Timedelta(days=400)).strftime("%Y%m%d")
    all_codes = sorted(spot["code"].tolist())

    if store.empty:
        print(f"首次运行：全量拉取 {len(all_codes)} 只股票历史……")
        store = refresh_codes(store, all_codes, names, start, budget_sec=290 * 60)
        _save(store)
        return _load()

    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    snap = spot.copy()
    snap["date"] = today
    store = pd.concat([store[store["date"] != today], snap[COLS]], ignore_index=True)

    wd = pd.Timestamp.now().weekday() if weekday is None else weekday
    bucket = [c for c in all_codes if int(c[-1]) % 5 == wd % 5]
    have = set(store["code"].unique())
    new_codes = [c for c in all_codes if c not in have]
    store = refresh_codes(store, sorted(set(bucket) | set(new_codes)), names, start,
                          budget_sec=45 * 60)
    _save(store)
    return _load()


def index_hist(symbol: str = "000300") -> pd.DataFrame:
    """沪深300 指数历史。网络错误经 retry() 重试；重试后仍失败则抛出，
    使整个任务失败（用户确认：指数关系到"强于沪深300"过滤条件，不容缺失）。"""
    import akshare as ak
    start = (pd.Timestamp.now() - pd.Timedelta(days=420)).strftime("%Y%m%d")
    df = retry(ak.index_zh_a_hist, symbol=symbol, period="daily", start_date=start)
    df = df.rename(columns={"日期": "date", "收盘": "close"})
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df[["date", "close"]]
