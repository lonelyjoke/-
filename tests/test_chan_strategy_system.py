from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "chan_strategy_system" / "backtest.py"
UI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "chan_strategy_system" / "ui.py"
CORE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "chan_strategy_system" / "chan_core.py"
BATCH_PATH = Path(__file__).resolve().parents[1] / "scripts" / "chan_strategy_system" / "batch_v2.py"


def _load_module():
    pytest.importorskip("wbt")
    spec = importlib.util.spec_from_file_location("chan_strategy_backtest", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_third_buy_position_derives_signals():
    mod = _load_module()
    pos = mod.build_third_buy_position("000001.SZ", "30分钟")
    signals = list(pos.unique_signals)
    assert "30分钟_D1_三买辅助V230228_三买_任意_任意_0" in signals
    assert "30分钟_D1_表里关系V230101_向下_任意_任意_0" in signals
    assert "30分钟_D1_涨跌停V230331_涨停_任意_任意_0" in signals


def test_mock_backtest_smoke(tmp_path):
    mod = _load_module()
    cfg = mod.RunConfig(
        mode="mock",
        pool="custom",
        symbols="000001.SZ",
        start_date="20200101",
        end_date="20200801",
        backtest_start="20200601",
        limit=1,
        cache_dir=str(tmp_path / "cache"),
        output_dir=str(tmp_path / "reports"),
    )
    run_dir = mod.run_backtest(cfg)
    assert (run_dir / "report.md").exists()
    assert (run_dir / "portfolio_weights.csv").exists()


def test_ui_symbol_validation():
    spec = importlib.util.spec_from_file_location("chan_strategy_ui", UI_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module._validate_symbol("002202.sz") == "002202.SZ"
    with pytest.raises(ValueError):
        module._validate_symbol("金风科技")


def test_ui_v2_defaults_to_quick_mode():
    spec = importlib.util.spec_from_file_location("chan_strategy_ui_args", UI_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    args = module._build_backtest_args({"symbol": "002202.SZ", "analysis_version": "v2", "mode": "mock"})
    assert "--speed-mode" in args
    assert args[args.index("--speed-mode") + 1] == "quick"
    assert args[args.index("--analysis-freqs") + 1] == "日线,60分钟,30分钟"


def test_v2_signal_classification():
    spec = importlib.util.spec_from_file_location("chan_strategy_core", CORE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module.classify_candidate("30分钟_D1B_BUY1_BUY1V221126", "一买_5笔_任意_0") == "一买"
    assert module.classify_candidate("30分钟_D1W9T2_第二买卖点V240524", "二买_任意_任意_0") == "二买"
    assert module.classify_candidate("30分钟_D1_三买辅助V230228", "三买_6笔_任意_0") == "三买"
    assert module.classify_candidate("30分钟_D1_三买辅助V230228", "其他_任意_任意_0") is None


def test_batch_v2_custom_symbols_and_index_defaults():
    spec = importlib.util.spec_from_file_location("chan_strategy_batch_v2", BATCH_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module.POOL_INDEX_CODES["hs300"] == "000300.SH"
    symbols = module._parse_custom_symbols("002202.sz, 002202.SZ,600036.sh")
    assert symbols == ["002202.SZ", "600036.SH"]


def test_batch_v2_resume_dir_reuses_existing_directory(tmp_path):
    spec = importlib.util.spec_from_file_location("chan_strategy_batch_v2_resume", BATCH_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    batch_dir = tmp_path / "v2_batch_20260614_120245"
    batch_dir.mkdir()
    cfg = module.BatchConfig(output_dir=str(tmp_path), resume_dir=str(batch_dir))
    assert module._new_batch_dir(cfg) == batch_dir
    assert (batch_dir / "batch_config.json").exists()

    marker_path = module._progress_path(batch_dir, "000001.SZ")
    module._write_marker(marker_path, {"symbol": "000001.SZ", "status": "done", "run_dir": str(batch_dir)})
    markers = module.load_progress_markers(batch_dir)
    assert markers["000001.SZ"]["status"] == "done"
