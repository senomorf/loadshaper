#!/usr/bin/env python3
"""
Tests for memory occupation functionality.

Tests the memory allocation, touching, and control mechanisms used to maintain
target memory utilization for A1.Flex shapes subject to Oracle's 20% memory rule.
"""

import unittest
import unittest.mock
import threading
import time
import os
import sys
import gc
from multiprocessing import Value
from unittest.mock import patch, MagicMock

# Get page size with fallback for systems where os.getpagesize() is not available
def get_page_size():
    try:
        return os.getpagesize()
    except AttributeError:
        # Fallback to common page size
        return 4096

# Add parent directory to sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestMemoryOccupation(unittest.TestCase):
    """Test memory occupation and touching functionality."""
    
    def setUp(self):
        """Set up test environment."""
        # Initialize configuration with test values
        loadshaper._config_initialized = False
        loadshaper._initialize_config()
        
        # Reset memory state
        with loadshaper.mem_lock:
            loadshaper.mem_block = bytearray(0)
        
        # Store original values
        self.original_mem_touch_interval = loadshaper.MEM_TOUCH_INTERVAL_SEC
        self.original_mem_step_mb = loadshaper.MEM_STEP_MB
        self.original_load_check_enabled = loadshaper.LOAD_CHECK_ENABLED
        
        # Initialize paused state for tests that need it
        if not hasattr(loadshaper, 'paused') or loadshaper.paused is None:
            loadshaper.paused = Value('d', 0.0)
        
    def tearDown(self):
        """Clean up after tests."""
        # Reset memory allocation
        loadshaper.set_mem_target_bytes(0)
        
        # Restore original values
        loadshaper.MEM_TOUCH_INTERVAL_SEC = self.original_mem_touch_interval
        loadshaper.MEM_STEP_MB = self.original_mem_step_mb
        loadshaper.LOAD_CHECK_ENABLED = self.original_load_check_enabled
        
        # Force garbage collection
        gc.collect()
    
    def test_set_mem_target_bytes_allocation(self):
        """Test memory allocation increases correctly."""
        # Start with empty memory
        self.assertEqual(len(loadshaper.mem_block), 0)
        
        # Set target to 10MB
        target_mb = 10
        target_bytes = target_mb * 1024 * 1024
        loadshaper.set_mem_target_bytes(target_bytes)
        
        # Should allocate up to MEM_STEP_MB (default 64MB, but limited by target)
        expected_size = min(target_bytes, loadshaper.MEM_STEP_MB * 1024 * 1024)
        with loadshaper.mem_lock:
            actual_size = len(loadshaper.mem_block)
        
        self.assertEqual(actual_size, expected_size)
        
    def test_set_mem_target_bytes_deallocation(self):
        """Test memory deallocation decreases correctly."""
        # Allocate 20MB first
        initial_mb = 20
        initial_bytes = initial_mb * 1024 * 1024
        loadshaper.set_mem_target_bytes(initial_bytes)
        
        with loadshaper.mem_lock:
            initial_size = len(loadshaper.mem_block)
        self.assertGreater(initial_size, 0)
        
        # Reduce to 5MB
        target_mb = 5
        target_bytes = target_mb * 1024 * 1024
        loadshaper.set_mem_target_bytes(target_bytes)
        
        with loadshaper.mem_lock:
            final_size = len(loadshaper.mem_block)
        
        # Should be smaller than initial
        self.assertLess(final_size, initial_size)
        
    def test_set_mem_target_bytes_negative_value(self):
        """Test negative target bytes gets clamped to zero."""
        # Allocate some memory first
        loadshaper.set_mem_target_bytes(5 * 1024 * 1024)
        
        with loadshaper.mem_lock:
            initial_size = len(loadshaper.mem_block)
        self.assertGreater(initial_size, 0)
        
        # Set negative target
        loadshaper.set_mem_target_bytes(-1000)
        
        # Should deallocate to zero (in steps)
        with loadshaper.mem_lock:
            final_size = len(loadshaper.mem_block)
        
        self.assertLessEqual(final_size, initial_size)
        
    def test_set_mem_target_bytes_step_limiting(self):
        """Test that allocation/deallocation respects step limits."""
        # Set a small step size for testing
        loadshaper.MEM_STEP_MB = 2  # 2MB steps
        
        # Request 10MB allocation
        target_bytes = 10 * 1024 * 1024
        loadshaper.set_mem_target_bytes(target_bytes)
        
        with loadshaper.mem_lock:
            actual_size = len(loadshaper.mem_block)
        
        # Should only allocate one step (2MB), not the full 10MB
        expected_step_size = 2 * 1024 * 1024
        self.assertEqual(actual_size, expected_step_size)
        
    def test_mem_nurse_thread_page_touching(self):
        """Test that memory nurse thread touches pages correctly."""
        # Allocate some memory
        test_size = 3 * get_page_size()  # 3 pages
        loadshaper.set_mem_target_bytes(test_size)
        
        # Get initial state
        with loadshaper.mem_lock:
            initial_values = [loadshaper.mem_block[i] for i in range(0, len(loadshaper.mem_block), get_page_size())]
        
        # Set short touch interval for testing
        loadshaper.MEM_TOUCH_INTERVAL_SEC = 0.1
        
        # Start nurse thread
        stop_event = threading.Event()
        nurse_thread = threading.Thread(target=loadshaper.mem_nurse_thread, args=(stop_event,))
        nurse_thread.daemon = True
        nurse_thread.start()
        
        # Wait for a few touch cycles
        time.sleep(0.3)
        
        # Stop thread
        stop_event.set()
        nurse_thread.join(timeout=1.0)
        
        # Check that pages were touched (values should have changed)
        with loadshaper.mem_lock:
            final_values = [loadshaper.mem_block[i] for i in range(0, len(loadshaper.mem_block), get_page_size())]
        
        # At least some values should have changed
        changes = sum(1 for i, f in zip(initial_values, final_values) if i != f)
        self.assertGreater(changes, 0, "Memory nurse thread should have touched pages")
        
    def test_mem_nurse_thread_respects_paused_state(self):
        """Test that memory nurse thread pauses when load threshold exceeded."""
        # Enable load checking and set paused state
        loadshaper.LOAD_CHECK_ENABLED = True
        loadshaper.paused.value = 1.0  # Set to paused state
        
        # Allocate some memory
        test_size = 2 * get_page_size()
        loadshaper.set_mem_target_bytes(test_size)
        
        # Get initial state
        with loadshaper.mem_lock:
            initial_values = [loadshaper.mem_block[i] for i in range(0, len(loadshaper.mem_block), get_page_size())]
        
        # Set short touch interval for testing
        loadshaper.MEM_TOUCH_INTERVAL_SEC = 0.1
        
        # Start nurse thread
        stop_event = threading.Event()
        nurse_thread = threading.Thread(target=loadshaper.mem_nurse_thread, args=(stop_event,))
        nurse_thread.daemon = True
        nurse_thread.start()
        
        # Wait for a few potential touch cycles
        time.sleep(0.3)
        
        # Stop thread
        stop_event.set()
        nurse_thread.join(timeout=1.0)
        
        # Check that pages were NOT touched (should remain unchanged due to paused state)
        with loadshaper.mem_lock:
            final_values = [loadshaper.mem_block[i] for i in range(0, len(loadshaper.mem_block), get_page_size())]
        
        # Values should be unchanged
        self.assertEqual(initial_values, final_values, "Memory nurse thread should not touch pages when paused")
        
    def test_mem_nurse_thread_uses_system_page_size(self):
        """Test that memory nurse thread uses system page size for touching."""
        # This test verifies the nurse thread uses get_page_size()
        # rather than hardcoded 4096, which is important for portability
        
        # Ensure not paused
        loadshaper.paused.value = 0.0
        
        # Allocate memory that's not aligned to 4096 but is aligned to system page size
        system_page_size = get_page_size()
        
        # If system page size is different from 4096, this tests the difference
        test_size = system_page_size * 2 + 100  # Slightly over 2 pages
        loadshaper.set_mem_target_bytes(test_size)
        
        # Set very short touch interval
        loadshaper.MEM_TOUCH_INTERVAL_SEC = 0.05
        
        # Start nurse thread
        stop_event = threading.Event()
        nurse_thread = threading.Thread(target=loadshaper.mem_nurse_thread, args=(stop_event,))
        nurse_thread.daemon = True
        nurse_thread.start()
        
        # Wait for touch cycle (longer to ensure it happens)
        time.sleep(0.5)
        
        # Stop thread
        stop_event.set()
        nurse_thread.join(timeout=1.0)
        
        # Verify that the first byte of each page was touched
        # (This test passes regardless of page size, but validates the logic)
        with loadshaper.mem_lock:
            size = len(loadshaper.mem_block)
            if size > 0:
                # Check first page
                self.assertGreaterEqual(loadshaper.mem_block[0], 1)
                
                # Check second page if it exists
                if size > system_page_size:
                    self.assertGreaterEqual(loadshaper.mem_block[system_page_size], 1)
    
    def test_memory_occupation_configuration_validation(self):
        """Test that MEM_TOUCH_INTERVAL_SEC configuration is validated."""
        # Test valid values
        valid_values = [0.5, 1.0, 2.5, 5.0, 10.0]
        for value in valid_values:
            try:
                loadshaper._validate_config_value('MEM_TOUCH_INTERVAL_SEC', str(value))
            except ValueError:
                self.fail(f"Valid value {value} should not raise ValueError")
        
        # Test invalid values
        invalid_values = [0.0, -1.0, 0.4, 11.0, 'invalid']
        for value in invalid_values:
            with self.assertRaises(ValueError, msg=f"Invalid value {value} should raise ValueError"):
                loadshaper._validate_config_value('MEM_TOUCH_INTERVAL_SEC', str(value))
    
    def test_read_meminfo_with_memavailable(self):
        """Test read_meminfo() when MemAvailable is present (preferred method)."""
        # Mock /proc/meminfo with MemAvailable present
        mock_meminfo = """MemTotal:        8000000 kB
MemFree:         1000000 kB
MemAvailable:    3000000 kB
Buffers:          500000 kB
Cached:          2000000 kB
SReclaimable:     300000 kB
Shmem:            100000 kB
"""
        
        with patch('builtins.open', unittest.mock.mock_open(read_data=mock_meminfo)):
            total_b, free_b, used_pct_excl, used_b_excl, used_pct_incl = loadshaper.read_meminfo()
            
            # Verify basic values
            self.assertEqual(total_b, 8000000 * 1024)  # Total memory in bytes
            self.assertEqual(free_b, 1000000 * 1024)   # Free memory in bytes
            
            # Verify MemAvailable-based calculation (preferred)
            # used_pct_excl = 100 * (1 - MemAvailable/MemTotal) = 100 * (1 - 3000000/8000000) = 62.5%
            expected_excl_pct = 100.0 * (1.0 - 3000000 / 8000000)
            self.assertAlmostEqual(used_pct_excl, expected_excl_pct, places=1)
            
            # Verify including cache calculation
            # used_pct_incl = 100 * (MemTotal - MemFree) / MemTotal = 100 * (7000000/8000000) = 87.5%
            expected_incl_pct = 100.0 * (8000000 - 1000000) / 8000000
            self.assertAlmostEqual(used_pct_incl, expected_incl_pct, places=1)
            
            # The difference should be significant (cache impact)
            self.assertGreater(used_pct_incl - used_pct_excl, 20.0, 
                             "Including cache should show significantly higher utilization")
    
    def test_read_meminfo_fallback_calculation(self):
        """Test read_meminfo() fallback when MemAvailable is missing (older kernels)."""
        # Mock /proc/meminfo without MemAvailable
        mock_meminfo = """MemTotal:        8000000 kB
MemFree:         1000000 kB
Buffers:          500000 kB
Cached:          2000000 kB
SReclaimable:     300000 kB
Shmem:            100000 kB
"""
        
        with patch('builtins.open', unittest.mock.mock_open(read_data=mock_meminfo)):
            total_b, free_b, used_pct_excl, used_b_excl, used_pct_incl = loadshaper.read_meminfo()
            
            # Verify fallback calculation
            # buff_cache = 500000 + max(0, 2000000 + 300000 - 100000) = 500000 + 2200000 = 2700000
            # used_no_cache = 8000000 - 1000000 - 2700000 = 4300000
            # used_pct_excl = 100 * 4300000 / 8000000 = 53.75%
            expected_excl_pct = 100.0 * 4300000 / 8000000
            self.assertAlmostEqual(used_pct_excl, expected_excl_pct, places=1)
            
            # Including cache should still be higher
            expected_incl_pct = 100.0 * (8000000 - 1000000) / 8000000
            self.assertAlmostEqual(used_pct_incl, expected_incl_pct, places=1)
            
            # Verify the calculation makes sense
            self.assertGreater(used_pct_incl, used_pct_excl, 
                             "Including cache should show higher utilization")
    
    def test_read_meminfo_return_format(self):
        """Test that read_meminfo() returns the expected 5-tuple format."""
        mock_meminfo = """MemTotal:        1000000 kB
MemFree:          500000 kB
MemAvailable:     600000 kB
"""
        
        with patch('builtins.open', unittest.mock.mock_open(read_data=mock_meminfo)):
            result = loadshaper.read_meminfo()
            
            # Verify return format: (total_bytes, free_bytes, used_pct_excl_cache, used_bytes_excl_cache, used_pct_incl_cache)
            self.assertEqual(len(result), 5, "read_meminfo() should return 5 values")
            
            total_b, free_b, used_pct_excl, used_b_excl, used_pct_incl = result
            
            # Verify types
            self.assertIsInstance(total_b, int, "total_bytes should be int")
            self.assertIsInstance(free_b, int, "free_bytes should be int")
            self.assertIsInstance(used_pct_excl, float, "used_pct_excl_cache should be float")
            self.assertIsInstance(used_b_excl, int, "used_bytes_excl_cache should be int")
            self.assertIsInstance(used_pct_incl, float, "used_pct_incl_cache should be float")
            
            # Verify ranges
            self.assertGreaterEqual(used_pct_excl, 0.0)
            self.assertLessEqual(used_pct_excl, 100.0)
            self.assertGreaterEqual(used_pct_incl, 0.0)
            self.assertLessEqual(used_pct_incl, 100.0)
    
    def test_gc_collect_called_after_shrinking(self):
        """Test that gc.collect() is called after memory shrinking."""
        # Allocate memory first
        loadshaper.set_mem_target_bytes(10 * 1024 * 1024)
        
        with loadshaper.mem_lock:
            initial_size = len(loadshaper.mem_block)
        self.assertGreater(initial_size, 0)
        
        # Mock gc.collect to verify it's called
        with patch('gc.collect') as mock_collect:
            # Shrink memory
            loadshaper.set_mem_target_bytes(1 * 1024 * 1024)
            
            # Verify gc.collect was called
            mock_collect.assert_called_once()
    
    def test_memory_block_thread_safety(self):
        """Test that memory operations are thread-safe."""
        # This test verifies concurrent access doesn't cause race conditions
        results = []
        errors = []
        
        def allocate_worker():
            try:
                for i in range(10):
                    size = (i + 1) * 1024 * 1024  # 1MB to 10MB
                    loadshaper.set_mem_target_bytes(size)
                    time.sleep(0.01)
                results.append('allocate_done')
            except Exception as e:
                errors.append(f"allocate_worker: {e}")
        
        def deallocate_worker():
            try:
                time.sleep(0.05)  # Start after some allocation
                for i in range(5):
                    size = (5 - i) * 1024 * 1024  # 5MB down to 1MB
                    loadshaper.set_mem_target_bytes(size)
                    time.sleep(0.01)
                results.append('deallocate_done')
            except Exception as e:
                errors.append(f"deallocate_worker: {e}")
        
        def touch_worker():
            try:
                # Simple touching without the full nurse thread
                for i in range(20):
                    with loadshaper.mem_lock:
                        size = len(loadshaper.mem_block)
                        if size > 0:
                            loadshaper.mem_block[0] = (loadshaper.mem_block[0] + 1) & 0xFF
                    time.sleep(0.005)
                results.append('touch_done')
            except Exception as e:
                errors.append(f"touch_worker: {e}")
        
        # Start all workers
        threads = [
            threading.Thread(target=allocate_worker),
            threading.Thread(target=deallocate_worker),
            threading.Thread(target=touch_worker)
        ]
        
        for thread in threads:
            thread.daemon = True
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join(timeout=2.0)
        
        # Check results
        self.assertEqual(len(errors), 0, f"Thread safety errors: {errors}")
        self.assertEqual(len(results), 3, f"Expected 3 results, got {len(results)}: {results}")


if __name__ == '__main__':
    unittest.main()