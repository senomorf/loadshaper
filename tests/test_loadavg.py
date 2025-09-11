import sys
from pathlib import Path
from unittest import mock
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from loadshaper import read_loadavg


def test_read_loadavg_valid_file():
    """Test reading valid /proc/loadavg content"""
    mock_content = "0.75 0.65 0.55 2/147 12345\n"
    with mock.patch("builtins.open", mock.mock_open(read_data=mock_content)):
        load_1min, load_5min, load_15min, per_core_load = read_loadavg()
        
    assert load_1min == 0.75
    assert load_5min == 0.65 
    assert load_15min == 0.55
    # per_core_load should be load_1min / N_WORKERS (defaults to os.cpu_count())
    import os
    expected_per_core = 0.75 / (os.cpu_count() or 1)
    assert per_core_load == pytest.approx(expected_per_core)


def test_read_loadavg_file_error():
    """Test handling file read errors"""
    with mock.patch("builtins.open", side_effect=FileNotFoundError):
        load_1min, load_5min, load_15min, per_core_load = read_loadavg()
        
    assert load_1min == 0.0
    assert load_5min == 0.0
    assert load_15min == 0.0
    assert per_core_load == 0.0


def test_read_loadavg_invalid_format():
    """Test handling invalid /proc/loadavg format"""
    mock_content = "invalid format\n"
    with mock.patch("builtins.open", mock.mock_open(read_data=mock_content)):
        load_1min, load_5min, load_15min, per_core_load = read_loadavg()
        
    assert load_1min == 0.0
    assert load_5min == 0.0
    assert load_15min == 0.0
    assert per_core_load == 0.0


def test_read_loadavg_insufficient_values():
    """Test handling insufficient values in /proc/loadavg"""
    mock_content = "0.75\n"  # Only one value instead of three
    with mock.patch("builtins.open", mock.mock_open(read_data=mock_content)):
        load_1min, load_5min, load_15min, per_core_load = read_loadavg()
        
    assert load_1min == 0.0
    assert load_5min == 0.0
    assert load_15min == 0.0
    assert per_core_load == 0.0


def test_read_loadavg_zero_cpus():
    """Test handling zero CPU count edge case"""
    mock_content = "1.5 1.2 1.0 2/147 12345\n"
    with mock.patch("builtins.open", mock.mock_open(read_data=mock_content)):
        # Mock N_WORKERS to be 0 to test the edge case
        import loadshaper
        original_n_workers = loadshaper.N_WORKERS
        loadshaper.N_WORKERS = 0
        try:
            load_1min, load_5min, load_15min, per_core_load = read_loadavg()
            assert load_1min == 1.5
            assert load_5min == 1.2
            assert load_15min == 1.0
            assert per_core_load == 1.5  # Should fall back to raw load when cpu_count is 0
        finally:
            loadshaper.N_WORKERS = original_n_workers