"""net.retry 重试逻辑测试。用假函数模拟网络/代码错误，不触网。"""
import pytest
import requests.exceptions as rex

from src.net import retry, is_network_error


class Counter:
    """记录调用次数、可编排每次抛什么的假接口。"""
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)  # 每次调用要做的事：异常实例或返回值
        self.calls = 0

    def __call__(self, *a, **k):
        i = self.calls
        self.calls += 1
        item = self.outcomes[i]
        if isinstance(item, BaseException):
            raise item
        return item


def no_sleep(_):
    pass


# ---------- is_network_error 分类 ----------

@pytest.mark.parametrize("exc", [
    rex.ConnectionError("Connection aborted"),
    rex.Timeout("Read timed out"),
    ConnectionError("reset"),
    TimeoutError(),
    rex.ChunkedEncodingError("x"),
])
def test_network_errors_identified(exc):
    assert is_network_error(exc) is True


@pytest.mark.parametrize("exc", [
    KeyError("missing column"),
    ValueError("bad value"),
    RuntimeError("字段缺失"),
    TypeError("x"),
])
def test_code_errors_not_network(exc):
    assert is_network_error(exc) is False


def test_remote_disconnected_text_fallback():
    # 模拟截图里的实际错误文本形态
    exc = Exception("('Connection aborted.', RemoteDisconnected('Remote end "
                    "closed connection without response'))")
    assert is_network_error(exc) is True


# ---------- retry 行为 ----------

def test_success_first_try_no_retry():
    fn = Counter(["ok"])
    assert retry(fn, _sleep=no_sleep) == "ok"
    assert fn.calls == 1


def test_network_error_then_success():
    fn = Counter([rex.ConnectionError("aborted"), "ok"])
    assert retry(fn, _sleep=no_sleep) == "ok"
    assert fn.calls == 2  # 第一次失败，第二次成功


def test_network_error_all_fail_raises_last():
    err = rex.ConnectionError("aborted")
    fn = Counter([err, err, err])
    with pytest.raises(rex.ConnectionError):
        retry(fn, _sleep=no_sleep)
    assert fn.calls == 3  # 恰好重试 3 次


def test_code_error_not_retried():
    fn = Counter([KeyError("missing"), "ok"])
    with pytest.raises(KeyError):
        retry(fn, _sleep=no_sleep)
    assert fn.calls == 1  # 代码错误立即抛出，绝不重试


def test_backoff_sequence_is_exponential():
    slept = []
    err = rex.ConnectionError("x")
    fn = Counter([err, err, err])
    with pytest.raises(rex.ConnectionError):
        retry(fn, _sleep=slept.append)
    assert slept == [2, 4]  # 第1次失败睡2s，第2次睡4s，第3次失败不再睡


def test_args_and_kwargs_forwarded():
    def fn(a, b, c=0):
        return a + b + c
    assert retry(fn, 1, 2, c=3, _sleep=no_sleep) == 6


def test_returns_none_passthrough():
    # None 是合法返回值（如个股无数据），不应被当作失败
    fn = Counter([None])
    assert retry(fn, _sleep=no_sleep) is None
    assert fn.calls == 1
