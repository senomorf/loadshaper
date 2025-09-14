#!/usr/bin/env python3
"""
Tests for ring buffer batching optimization feature.

This module tests the CPU_P95_RING_BUFFER_BATCH_SIZE feature that batches
ring buffer state saves to reduce I/O frequency and improve performance.
"""

import unittest
import unittest.mock
import sys
import os
import time
import tempfile
import json
import shutil
from multiprocessing import Value

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loadshaper


class TestRingBufferBatching(unittest.TestCase):
    """Test ring buffer batching optimization."""

    def setUp(self):
        """Set up test environment."""
        self.test_dir = tempfile.mkdtemp()
        self.ring_buffer_path = os.path.join(self.test_dir, 'p95_ring_buffer.json')
        self.db_path = os.path.join(self.test_dir, 'test_metrics.db')

        # Store original values
        self.original_batch_size = getattr(loadshaper, 'CPU_P95_RING_BUFFER_BATCH_SIZE', None)
        self.original_ring_buffer_path = getattr(loadshaper, 'RING_BUFFER_PATH', None)
        self.original_persistence_dir = os.environ.get('PERSISTENCE_DIR')

        # Set PERSISTENCE_DIR for test to use test directory
        os.environ['PERSISTENCE_DIR'] = self.test_dir

        # Initialize required global variables for testing
        loadshaper.CPU_P95_SLOT_DURATION = 60.0  # Default slot duration
        loadshaper.CPU_P95_TARGET_MIN = 22.0
        loadshaper.CPU_P95_TARGET_MAX = 28.0
        loadshaper.CPU_P95_SETPOINT = 25.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 35.0
        loadshaper.CPU_P95_BASELINE_INTENSITY = 20.0
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 6.5  # Required for exceedance calculations

        # Initialize all config to prevent None errors
        loadshaper._initialize_config()

    def tearDown(self):
        """Clean up test environment."""
        # Restore original values
        if hasattr(loadshaper, 'CPU_P95_RING_BUFFER_BATCH_SIZE'):
            loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = self.original_batch_size
        if hasattr(loadshaper, 'RING_BUFFER_PATH'):
            loadshaper.RING_BUFFER_PATH = self.original_ring_buffer_path

        # Restore PERSISTENCE_DIR environment variable
        if self.original_persistence_dir is not None:
            os.environ['PERSISTENCE_DIR'] = self.original_persistence_dir
        elif 'PERSISTENCE_DIR' in os.environ:
            del os.environ['PERSISTENCE_DIR']

        # Clean up test directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_batching_reduces_io_frequency(self):
        """Test that batching reduces file I/O frequency."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 5  # Batch every 5 slots

        # Create controller with test database
        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)
        controller.ring_buffer_path = self.ring_buffer_path
        controller.slots_since_last_save = 0
        controller.test_mode = True

        # Mock file operations to count I/O
        write_count = 0
        original_open = open

        def mock_open(*args, **kwargs):
            nonlocal write_count
            if len(args) > 0 and args[0] and 'w' in str(args[1:]):
                # Check for ring buffer path or any temp file pattern
                if (args[0] == self.ring_buffer_path or
                    args[0] == self.ring_buffer_path + '.tmp' or
                    (args[0].startswith(self.ring_buffer_path) and '.tmp' in args[0])):
                    write_count += 1
            return original_open(*args, **kwargs)

        with unittest.mock.patch('builtins.open', side_effect=mock_open):
            # Simulate 12 slot completions (should trigger 2 saves with batch_size=5)
            for i in range(12):
                controller._end_current_slot()

        # With batch_size=5, we should have 2 saves (at slots 5 and 10)
        # Slot 12 wouldn't trigger save yet
        expected_saves = 2
        self.assertEqual(write_count, expected_saves,
                        f"Expected {expected_saves} saves with batch_size=5 over 12 slots, got {write_count}")

    def test_different_batch_sizes(self):
        """Test different batch sizes."""
        test_cases = [
            (1, 10, 10),   # No batching: 10 updates = 10 saves
            (3, 10, 3),    # Batch every 3: 10 updates = 3 saves (at slots 3, 6, 9)
            (10, 5, 0),    # Large batch: 5 updates = 0 saves (not enough for batch)
        ]

        for batch_size, updates, expected_saves in test_cases:
            with self.subTest(batch_size=batch_size, updates=updates):
                # Clean up from previous test
                if os.path.exists(self.ring_buffer_path):
                    os.remove(self.ring_buffer_path)

                loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = batch_size

                metrics_storage = loadshaper.MetricsStorage(self.db_path)
                controller = loadshaper.CPUP95Controller(metrics_storage)
                controller.ring_buffer_path = self.ring_buffer_path
                controller.slots_since_last_save = 0
                controller.test_mode = True

                # Count actual file writes
                write_count = 0
                original_open = open

                def mock_open(*args, **kwargs):
                    nonlocal write_count
                    if len(args) > 0 and args[0] and 'w' in str(args[1:]):
                        # Check for ring buffer path or any temp file pattern
                        if (args[0] == self.ring_buffer_path or
                            args[0] == self.ring_buffer_path + '.tmp' or
                            (args[0].startswith(self.ring_buffer_path) and '.tmp' in args[0])):
                            write_count += 1
                    return original_open(*args, **kwargs)

                with unittest.mock.patch('builtins.open', side_effect=mock_open):
                    for i in range(updates):
                        controller._end_current_slot()

                self.assertEqual(write_count, expected_saves,
                               f"Batch size {batch_size} with {updates} updates should save {expected_saves} times, got {write_count}")

    def test_state_persistence_accuracy_with_batching(self):
        """Test that batched saves don't lose state accuracy."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 3

        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)
        controller.ring_buffer_path = self.ring_buffer_path
        controller.slots_since_last_save = 0
        controller.test_mode = True

        # Add several decisions
        decisions = [True, False, True, True, False, False, True]  # 7 decisions
        for decision in decisions:
            controller.slot_history[controller.slot_history_index] = decision
            controller.slot_history_index = (controller.slot_history_index + 1) % controller.slot_history_size
            controller.slots_since_last_save += 1
            controller._maybe_save_ring_buffer_state()

        # Force final save
        controller._save_ring_buffer_state()

        # Verify saved state contains all decisions
        self.assertTrue(os.path.exists(self.ring_buffer_path))

        with open(self.ring_buffer_path, 'r') as f:
            saved_state = json.load(f)

        # Count decisions in saved state
        saved_decisions = [slot for slot in saved_state['slot_history'] if slot is not None]
        expected_high_decisions = sum(decisions)
        actual_high_decisions = sum(saved_decisions)

        self.assertEqual(actual_high_decisions, expected_high_decisions,
                        f"Expected {expected_high_decisions} high decisions, saved state has {actual_high_decisions}")

    def test_batch_counter_reset(self):
        """Test that batch counter resets after save."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 4

        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)
        controller.ring_buffer_path = self.ring_buffer_path
        controller.slots_since_last_save = 0
        controller.test_mode = True

        # Process exactly batch_size slot completions
        for i in range(4):
            controller._end_current_slot()

        # Counter should be reset to 0 after save
        self.assertEqual(controller.slots_since_last_save, 0,
                        "Batch counter should reset to 0 after save")

        # Process 2 more slot completions
        for i in range(2):
            controller._end_current_slot()

        # Counter should be 2
        self.assertEqual(controller.slots_since_last_save, 2,
                        "Batch counter should be 2 after 2 additional updates")

    def test_graceful_shutdown_saves_regardless_of_batch(self):
        """Test that shutdown saves state even if batch not reached."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 10  # Large batch size

        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)
        controller.ring_buffer_path = self.ring_buffer_path
        controller.slots_since_last_save = 0
        controller.test_mode = True

        # Add only 3 updates (less than batch size)
        for i in range(3):
            controller.update_state(25.0)
            controller._maybe_save_ring_buffer_state()

        # Verify no save happened yet
        self.assertFalse(os.path.exists(self.ring_buffer_path),
                        "No save should happen before batch size reached")

        # Call shutdown method (should save regardless of batch)
        controller.shutdown()

        # Verify save happened
        self.assertTrue(os.path.exists(self.ring_buffer_path),
                       "Shutdown should save state regardless of batch size")

    def test_batch_size_configuration_validation(self):
        """Test batch size configuration validation."""
        # Test default value
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = None
        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)
        controller.ring_buffer_path = self.ring_buffer_path
        controller.test_mode = True

        # Should use default value
        batch_size = controller.slots_since_last_save if hasattr(controller, 'batch_size') else 10
        # Verify default behavior (we can't directly access the batch size logic,
        # but we can test that it doesn't crash with None value)
        controller.update_state(25.0)
        controller._maybe_save_ring_buffer_state()

        # Test various valid values
        for test_batch_size in [1, 5, 10, 100]:
            loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = test_batch_size
            metrics_storage_test = loadshaper.MetricsStorage(self.db_path)
            controller_test = loadshaper.CPUP95Controller(metrics_storage_test)
            controller_test.ring_buffer_path = self.ring_buffer_path
            controller_test.test_mode = True
            # Should not crash with any positive integer
            controller_test.update_state(25.0)
            controller_test._maybe_save_ring_buffer_state()

    def test_performance_impact_measurement(self):
        """Test that batching improves performance (timing test)."""
        # This test measures actual performance impact
        # Note: Results may vary based on system performance

        iterations = 50

        # Test without batching (batch_size=1)
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 1
        metrics_storage1 = loadshaper.MetricsStorage(self.db_path)
        controller1 = loadshaper.CPUP95Controller(metrics_storage1)
        controller1.ring_buffer_path = os.path.join(self.test_dir, 'no_batch.json')
        controller1.test_mode = True

        start_time = time.time()
        for i in range(iterations):
            controller1.update_state(25.0)
            controller1._maybe_save_ring_buffer_state()
        no_batch_time = time.time() - start_time

        # Test with batching (batch_size=10)
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 10
        metrics_storage2 = loadshaper.MetricsStorage(self.db_path)
        controller2 = loadshaper.CPUP95Controller(metrics_storage2)
        controller2.ring_buffer_path = os.path.join(self.test_dir, 'with_batch.json')
        controller2.test_mode = True

        start_time = time.time()
        for i in range(iterations):
            controller2.update_state(25.0)
            controller2._maybe_save_ring_buffer_state()
        batch_time = time.time() - start_time

        # Batching should be faster or at least not significantly slower
        # Allow some variance due to system factors
        self.assertLessEqual(batch_time, no_batch_time * 1.2,
                           f"Batching should not be significantly slower: batch={batch_time:.4f}s, no_batch={no_batch_time:.4f}s")

    def test_error_handling_during_batched_save(self):
        """Test error handling during batched save operations."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 2

        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)
        controller.ring_buffer_path = self.ring_buffer_path
        controller.test_mode = True

        # Mock file operations to simulate I/O error
        with unittest.mock.patch('builtins.open', side_effect=IOError("Mock I/O error")):
            # Should not crash when save fails
            controller.update_state(25.0)
            controller.update_state(26.0)  # This should trigger save
            controller._maybe_save_ring_buffer_state()

        # Controller should continue functioning after failed save
        controller.update_state(27.0)
        # Verify controller is still functioning by checking its attributes
        self.assertIsNotNone(controller.current_target_intensity)


if __name__ == '__main__':
    unittest.main()