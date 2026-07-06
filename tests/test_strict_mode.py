"""严格模式集成测试：验证网络错误在各调用点的传播行为符合用户决策。
用 monkeypatch 替换 akshare，不触网。"""
import sys
import types

import pandas as pd
import pytest
import requests.exceptions as rex


def _fake_akshare(monkeypatch, **funcs):
    """注入一个假的 akshare 模块，funcs 指定每个接口的行为。"""
    fake = types.ModuleType("akshare")
    for name, impl in funcs.items():
        setattr(fake, name, impl)
    monkeypatch.setitem(sys.modules, "akshare", fake)
    return fake


def test_index_hist_raises_after_retries(monkeypatch):
    """指数：网络错误重试后仍失败 → 抛出（整个任务失败）。"""
    from src import data_store

    def always_fail(**k):
        raise rex.ConnectionError("Connection aborted")

    _fake_akshare(monkeypatch, index_zh_a_hist=always_fail)
    monkeypatch.setattr("src.net.time.sleep", lambda _: None)
    with pytest.raises(rex.ConnectionError):
        data_store.index_hist()


def test_index_hist_recovers_on_retry(monkeypatch):
    """指数：先失败后成功 → 正常返回。"""
    from src import data_store
    calls = {"n": 0}

    def flaky(**k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise rex.ConnectionError("aborted")
        return pd.DataFrame({"日期": ["2026-01-02"], "收盘": [4000.0]})

    _fake_akshare(monkeypatch, index_zh_a_hist=flaky)
    monkeypatch.setattr("src.net.time.sleep", lambda _: None)
    out = data_store.index_hist()
    assert list(out.columns) == ["date", "close"] and calls["n"] == 2


def test_hist_one_raises_on_network_but_none_on_empty(monkeypatch):
    """个股：空数据返回None（正常）；网络错误重试后抛出（严格模式）。"""
    from src import data_store
    monkeypatch.setattr("src.net.time.sleep", lambda _: None)

    # 空数据 → None
    _fake_akshare(monkeypatch, stock_zh_a_hist=lambda **k: pd.DataFrame())
    assert data_store._hist_one("000001", "20260101") is None

    # 网络错误 → 抛出
    def fail(**k):
        raise rex.ConnectionError("Remote end closed connection")
    _fake_akshare(monkeypatch, stock_zh_a_hist=fail)
    with pytest.raises(rex.ConnectionError):
        data_store._hist_one("000001", "20260101")


def test_refresh_codes_propagates_failure(monkeypatch):
    """严格模式核心：某只个股网络失败 → refresh_codes 不吞，向上抛出中断任务。"""
    from src import data_store
    monkeypatch.setattr("src.net.time.sleep", lambda _: None)
    monkeypatch.setattr("src.data_store.time.sleep", lambda _: None)

    def fail(**k):
        raise rex.ConnectionError("aborted")
    _fake_akshare(monkeypatch, stock_zh_a_hist=fail)

    store = pd.DataFrame(columns=data_store.COLS)
    with pytest.raises(rex.ConnectionError):
        data_store.refresh_codes(store, ["000001"], {"000001": "平安"},
                                 "20260101", budget_sec=999)


def test_fund_pull_retries_network_but_not_field_error(monkeypatch):
    """财报：网络错误重试；字段缺失立即抛RuntimeError不重试。"""
    from src import fundamental
    monkeypatch.setattr("src.net.time.sleep", lambda _: None)

    # 字段缺失 → RuntimeError，且只调用一次（不重试）
    calls = {"n": 0}
    def missing_field(date=None):
        calls["n"] += 1
        return pd.DataFrame({"股票代码": ["600000"]})  # 缺很多字段
    fake = types.ModuleType("akshare")
    fake.stock_yjbb_em = missing_field
    monkeypatch.setitem(sys.modules, "akshare", fake)
    with pytest.raises(RuntimeError):
        fundamental._fetch_period("20260331")
    assert calls["n"] == 1  # 字段错误不重试
