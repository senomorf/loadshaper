import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from loadshaper import nic_utilization_pct


def test_nic_utilization_invalid_inputs_return_none():
    assert nic_utilization_pct(None, (1, 1), 1, 100) is None
    assert nic_utilization_pct((1, 1), None, 1, 100) is None
    assert nic_utilization_pct((1, 1), (2, 2), -1, 100) is None
    assert nic_utilization_pct((1, 1), (2, 2), 1, 0) is None


def test_nic_utilization_normal_case():
    prev = (0, 0)
    cur = (1_250_000, 0)  # 10 Mbps over 1 second on a 100 Mbps link
    assert nic_utilization_pct(prev, cur, 1, 100) == pytest.approx(10.0)
