"""The code in README.md and in the docstrings runs, against a stand-in for the MetaTrader5 package."""

import doctest
import importlib
import re
import subprocess
import sys
import time
import types
from datetime import date
from pathlib import Path

import pytest

import oracle
from brokerclock import ServerZone

README = Path(__file__).resolve().parent.parent / "README.md"
BLOCKS = re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), flags=re.DOTALL)
MODULES = ["brokerclock", "brokerclock._zone", "brokerclock._detect", "brokerclock._live", "brokerclock.pandas"]


def fake_metatrader() -> types.ModuleType:
    """Fixed H1 EURUSD bars and live ticks of a GMT+2/+3 broker that follows US daylight saving."""
    np = pytest.importorskip("numpy")
    bars = oracle.forex_bars("US", 2, date(2025, 1, 5), 54)
    rates = np.zeros(len(bars), dtype=[("time", "<i8"), ("open", "<f8"), ("close", "<f8")])
    rates["time"] = bars
    rates["open"] = rates["close"] = 1.08
    sent = []

    def symbol_info_tick(symbol: str) -> types.SimpleNamespace:
        now = time.time()
        server = now + oracle.offset("US", 2, int(now))
        return types.SimpleNamespace(time=int(server), time_msc=int(server * 1000), bid=1.08, ask=1.0801)

    mt5 = types.ModuleType("MetaTrader5")
    mt5.TIMEFRAME_H1 = 16385
    mt5.TRADE_ACTION_PENDING = 5
    mt5.ORDER_TYPE_BUY_LIMIT = 2
    mt5.ORDER_TIME_SPECIFIED = 2
    mt5.initialize = lambda: True
    mt5.copy_rates_from_pos = lambda symbol, timeframe, start, count: rates[-count:]
    mt5.copy_rates_range = lambda symbol, timeframe, start, end: rates[(rates["time"] >= start) & (rates["time"] < end)]
    mt5.symbol_info_tick = symbol_info_tick
    mt5.order_send = sent.append
    mt5.sent = sent
    return mt5


def test_readme_has_examples():
    assert len(BLOCKS) == 4


def test_readme_examples_run(monkeypatch, capsys):
    pytest.importorskip("pandas")
    mt5 = fake_metatrader()
    monkeypatch.setitem(sys.modules, "MetaTrader5", mt5)
    namespace: dict[str, object] = {}
    for block in BLOCKS:
        exec(compile(block, str(README), "exec"), namespace)
    output = capsys.readouterr().out
    assert namespace["zone"] == ServerZone.us()
    assert "GMT+2/+3 (US daylight saving)" in output
    assert "live ticks agree with" in output
    march = namespace["march"]["time"]
    assert march[0] == oracle.epoch(date(2025, 3, 3))  # Monday 00:00 on the server is Sunday 17:00 New York
    assert march[-1] == oracle.epoch(date(2025, 4, 1), 2)  # 23:00 UTC on 31 March, at GMT+3
    [request] = mt5.sent
    assert request["expiration"] == ServerZone.us().server_timestamp(namespace["expiry"])


@pytest.mark.parametrize("name", MODULES)
def test_docstring_examples(name):
    if name == "brokerclock.pandas":
        pytest.importorskip("pandas")
    result = doctest.testmod(importlib.import_module(name))
    assert result.failed == 0
    assert result.attempted > 0


def test_importing_brokerclock_loads_neither_metatrader_nor_pandas():
    code = "import sys, brokerclock; assert not {'MetaTrader5', 'pandas'} & set(sys.modules), sys.modules.keys()"
    subprocess.run([sys.executable, "-c", code], check=True)
