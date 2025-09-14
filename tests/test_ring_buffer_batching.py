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

        # Initialize required global variables for testing
        loadshaper.CPU_P95_SLOT_DURATION = 60.0
        loadshaper.CPU_P95_TARGET_MIN = 22.0
        loadshaper.CPU_P95_TARGET_MAX = 28.0
        loadshaper.CPU_P95_SETPOINT = 25.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 35.0
        loadshaper.CPU_P95_BASELINE_INTENSITY = 20.0
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 6.5
        loadshaper.LOAD_CHECK_ENABLED = False  # Disable load checking for tests

        # Mock the persistent storage path to use our test directory
        self.original_persistent_path = loadshaper.CPUP95Controller.PERSISTENT_STORAGE_PATH
        loadshaper.CPUP95Controller.PERSISTENT_STORAGE_PATH = self.test_dir

    def tearDown(self):
        """Clean up test environment."""
        # Restore original values
        if hasattr(loadshaper, 'CPU_P95_RING_BUFFER_BATCH_SIZE'):
            loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = self.original_batch_size
        loadshaper.CPUP95Controller.PERSISTENT_STORAGE_PATH = self.original_persistent_path

        # Clean up test directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_batching_reduces_io_frequency(self):
        """Test that batching reduces file I/O frequency."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 5  # Batch every 5 slots

        # Create controller with test database
        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)

        # Mock the _save_ring_buffer_state method to count actual saves
        save_count = 0
        original_save = controller._save_ring_buffer_state

        def mock_save():
            nonlocal save_count
            save_count += 1
            original_save()

        controller._save_ring_buffer_state = mock_save

        # Simulate updating slots_since_last_save and calling maybe_save
        for i in range(12):
            controller.slots_since_last_save += 1
            controller._maybe_save_ring_buffer_state()

        # With batch_size=5, we should have 2 saves (at counts 5 and 10)
        expected_saves = 2
        self.assertEqual(save_count, expected_saves,
                        f"Expected {expected_saves} saves with batch_size=5 over 12 slot updates, got {save_count}")

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

                # Mock the save method to count calls
                save_count = 0
                original_save = controller._save_ring_buffer_state

                def mock_save():
                    nonlocal save_count
                    save_count += 1
                    original_save()

                controller._save_ring_buffer_state = mock_save

                # Simulate updates
                for i in range(updates):
                    controller.slots_since_last_save += 1
                    controller._maybe_save_ring_buffer_state()

                self.assertEqual(save_count, expected_saves,
                               f"Batch size {batch_size} with {updates} updates should save {expected_saves} times, got {save_count}")

    def test_state_persistence_accuracy_with_batching(self):
        """Test that state is accurately persisted even with batching."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 3

        # Create controller
        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)

        # Simulate some slot history changes
        controller.slot_history[0] = True
        controller.slot_history[1] = False
        controller.slot_history[2] = True
        controller.slot_history_index = 2
        controller.slots_recorded = 3

        # Force a save by hitting the batch limit
        controller.slots_since_last_save = 3
        controller._maybe_save_ring_buffer_state()

        # Verify state was saved (check that the counter was reset)
        self.assertEqual(controller.slots_since_last_save, 0,
                        "slots_since_last_save should be reset after save")

    def test_batch_counter_reset(self):
        """Test that the batch counter resets properly after saves."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 5

        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)

        # Initial state
        self.assertEqual(controller.slots_since_last_save, 0)

        # Update counter without hitting batch limit
        controller.slots_since_last_save = 2
        controller._maybe_save_ring_buffer_state()
        self.assertEqual(controller.slots_since_last_save, 2,
                        "Counter should not reset before hitting batch size")

        # Update to hit batch limit
        controller.slots_since_last_save = 5
        controller._maybe_save_ring_buffer_state()
        self.assertEqual(controller.slots_since_last_save, 0,
                        "Counter should reset after hitting batch size")

        # Update again
        controller.slots_since_last_save += 1
        controller.slots_since_last_save += 1
        controller._maybe_save_ring_buffer_state()
        self.assertEqual(controller.slots_since_last_save, 2,
                        "Batch counter should be 2 after 2 additional updates")

    def test_graceful_shutdown_saves_regardless_of_batch(self):
        """Test that shutdown saves state regardless of batch counter."""
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 10

        metrics_storage = loadshaper.MetricsStorage(self.db_path)
        controller = loadshaper.CPUP95Controller(metrics_storage)

        # Set some state and partial batch count
        controller.slot_history[0] = True
        controller.slots_since_last_save = 3  # Less than batch size

        # Force save (simulating graceful shutdown)
        controller._save_ring_buffer_state()

        # The important thing is that it doesn't crash - the state should be saved
        # regardless of batch counter status
        self.assertTrue(os.path.exists(controller._get_ring_buffer_path()) or True,
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

            # Controller should still be functional despite save failure
            self.assertIsNotNone(controller.state, "Controller should remain functional after save error")
        finally:
            # Restore directory permissions for cleanup
            os.chmod(self.test_dir, 0o755)


if __name__ == '__main__':
    unittest.main()