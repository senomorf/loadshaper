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
        loadshaper.CPU_P95_SLOT_DURATION = 60.0
        loadshaper.CPU_P95_TARGET_MIN = 22.0
        loadshaper.CPU_P95_TARGET_MAX = 28.0
        loadshaper.CPU_P95_SETPOINT = 25.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 35.0
        loadshaper.CPU_P95_BASELINE_INTENSITY = 20.0
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 6.5
        loadshaper.LOAD_CHECK_ENABLED = False  # Disable load checking for tests

        # Initialize all config to prevent None errors
        loadshaper._initialize_config()

    def tearDown(self):
        """Clean up test environment."""
        # Restore original values
        if hasattr(loadshaper, 'CPU_P95_RING_BUFFER_BATCH_SIZE'):
            loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = self.original_batch_size

        if self.original_ring_buffer_path is not None:
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

        # Mock file operations to count ring buffer saves (including thread-safe temp files)
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

        # Apply the mock
        import builtins
        builtins.open = mock_open

        # Simulate 12 slot completions (should trigger 2 saves with batch_size=5)
        for i in range(12):
            controller._end_current_slot()

        # With batch_size=5, we should have 2 saves (at counts 5 and 10)
        expected_saves = 2

        # Restore original open function
        builtins.open = original_open

        self.assertEqual(write_count, expected_saves,
                        f"Expected {expected_saves} saves with batch_size=5 over 12 slot updates, got {write_count}")

    def test_different_batch_sizes(self):
        """Test different batch sizes."""
        test_cases = [
            (1, 10, 10),   # No batching: 10 updates = 10 saves
            (3, 10, 3),    # Batch every 3: 10 updates = 3 saves (at counts 3, 6, 9)
            (10, 5, 0),    # Large batch: 5 updates = 0 saves (not enough for batch)
        ]

        for batch_size, updates, expected_saves in test_cases:
            with self.subTest(batch_size=batch_size, updates=updates):
                loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = batch_size

                # Create fresh controller
                metrics_storage = loadshaper.MetricsStorage(self.db_path)
                controller = loadshaper.CPUP95Controller(metrics_storage)

                # Mock file operations to count ring buffer saves (including thread-safe temp files)
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

                # Apply the mock
                import builtins
                builtins.open = mock_open

                # Simulate updates via slot completions
                for i in range(updates):
                    controller._end_current_slot()

                # Restore original open function
                builtins.open = original_open

                self.assertEqual(write_count, expected_saves,
                               f"Batch size {batch_size} with {updates} updates should save {expected_saves} times, got {write_count}")

    def test_state_persistence_accuracy_with_batching(self):
        """Test that state is accurately persisted even with batching."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 3

        # Create controller
        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)

        # Enable test-mode persistence and add several decisions via slots
        controller.test_mode = True
        controller.ring_buffer_path = self.ring_buffer_path
        decisions = [True, False, True, True, False, False, True]  # 7 decisions
        for decision in decisions:
            controller.current_slot_is_high = decision
            controller._end_current_slot()

        # Force final save to ensure all decisions are persisted
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
        """Test that the batch counter resets properly after saves."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 5

        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)

        # Initial state
        self.assertEqual(controller.slots_since_last_save, 0)

        # Process exactly 4 slot completions (batch size is 5)
        for i in range(4):
            controller._end_current_slot()

        # Counter should be 4 before hitting batch limit
        self.assertEqual(controller.slots_since_last_save, 4,
                        "Counter should reflect 4 slots before save")

        # Process one more to hit batch limit and trigger reset
        controller._end_current_slot()
        self.assertEqual(controller.slots_since_last_save, 0,
                        "Counter should reset after hitting batch size")

        # Process 2 more slot completions
        for i in range(2):
            controller._end_current_slot()
        self.assertEqual(controller.slots_since_last_save, 2,
                        "Batch counter should be 2 after 2 additional updates")

    def test_graceful_shutdown_saves_regardless_of_batch(self):
        """Test that shutdown saves state regardless of batch counter."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 10

        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)

        # Set test mode to use test directory for ring buffer
        controller.test_mode = True
        controller.ring_buffer_path = self.ring_buffer_path

        # Set some state and partial batch count
        controller.slot_history[0] = True
        controller.slots_since_last_save = 3  # Less than batch size

        # Force save (simulating graceful shutdown)
        controller._save_ring_buffer_state()

        # The important thing is that it doesn't crash - the state should be saved
        # regardless of batch counter status
        self.assertTrue(os.path.exists(self.ring_buffer_path),
                       "Shutdown should save state regardless of batch size")

    def test_error_handling_during_batched_save(self):
        """Test error handling when batched saves fail."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 2

        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)

        # Make the directory read-only to cause save failure
        os.chmod(self.test_dir, 0o444)

        try:
            # Try to trigger a save - should handle error gracefully
            controller.slots_since_last_save = 2
            controller._maybe_save_ring_buffer_state()

            # Controller should continue functioning after failed save
            controller.update_state(27.0)
            # Verify controller is still functioning by checking its attributes
            self.assertIsNotNone(controller.current_target_intensity)
        finally:
            # Restore directory permissions for cleanup
            os.chmod(self.test_dir, 0o755)


if __name__ == '__main__':
    unittest.main()
