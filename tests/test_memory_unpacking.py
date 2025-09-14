#!/usr/bin/env python3
"""
Test suite for read_meminfo() memory unpacking to prevent regression
"""

import unittest
from unittest.mock import mock_open, patch
import loadshaper


class TestMemoryUnpacking(unittest.TestCase):
    """Test suite for memory function return value unpacking"""

    def test_read_meminfo_returns_five_values(self):
        """Test that read_meminfo returns exactly 5 values as expected by main loop."""
        # Mock /proc/meminfo content
        proc_meminfo_content = """MemTotal:       16384000 kB
MemFree:         8192000 kB
MemAvailable:    12288000 kB
Buffers:          512000 kB
Cached:          2048000 kB
SwapCached:           0 kB
Active:          4096000 kB
Inactive:        2048000 kB
"""

        with patch('builtins.open', mock_open(read_data=proc_meminfo_content)):
            result = loadshaper.read_meminfo()

            # Should return exactly 5 values
            self.assertEqual(len(result), 5, "read_meminfo() should return exactly 5 values")

            # Test unpacking like main loop does
            total_b, free_b, mem_used_no_cache_pct, used_no_cache_b, mem_used_incl_cache_pct = result

            # Verify types
            self.assertIsInstance(total_b, int, "total_b should be int")
            self.assertIsInstance(free_b, int, "free_b should be int")
            self.assertIsInstance(mem_used_no_cache_pct, float, "mem_used_no_cache_pct should be float")
            self.assertIsInstance(used_no_cache_b, int, "used_no_cache_b should be int")
            self.assertIsInstance(mem_used_incl_cache_pct, float, "mem_used_incl_cache_pct should be float")

            # Verify values are reasonable
            self.assertGreater(total_b, 0, "total_b should be positive")
            self.assertGreaterEqual(free_b, 0, "free_b should be non-negative")
            self.assertGreaterEqual(mem_used_no_cache_pct, 0, "mem_used_no_cache_pct should be non-negative")
            self.assertLessEqual(mem_used_no_cache_pct, 100, "mem_used_no_cache_pct should be <= 100")
            self.assertGreaterEqual(used_no_cache_b, 0, "used_no_cache_b should be non-negative")
            self.assertGreaterEqual(mem_used_incl_cache_pct, 0, "mem_used_incl_cache_pct should be non-negative")
            self.assertLessEqual(mem_used_incl_cache_pct, 100, "mem_used_incl_cache_pct should be <= 100")

    def test_memory_unpacking_variable_names_exist(self):
        """Test that all expected variable names are available after unpacking."""
        # Mock /proc/meminfo content
        proc_meminfo_content = """MemTotal:       8192000 kB
MemFree:         4096000 kB
MemAvailable:    6144000 kB
Buffers:          256000 kB
Cached:          1024000 kB
"""

        with patch('builtins.open', mock_open(read_data=proc_meminfo_content)):
            # Simulate main loop unpacking
            total_b, free_b, mem_used_no_cache_pct, used_no_cache_b, mem_used_incl_cache_pct = loadshaper.read_meminfo()

            # These should all exist and be usable (no NameError)
            variables_should_exist = {
                'total_b': total_b,
                'free_b': free_b,
                'mem_used_no_cache_pct': mem_used_no_cache_pct,
                'used_no_cache_b': used_no_cache_b,
                'mem_used_incl_cache_pct': mem_used_incl_cache_pct
            }

            for var_name, var_value in variables_should_exist.items():
                self.assertIsNotNone(var_value, f"{var_name} should not be None")
                # Test that we can use them in calculations (no undefined variable errors)
                try:
                    calculation_result = var_value * 1.0  # Simple test calculation
                    self.assertIsNotNone(calculation_result, f"Should be able to calculate with {var_name}")
                except Exception as e:
                    self.fail(f"Could not perform calculation with {var_name}: {e}")


if __name__ == '__main__':
    unittest.main()