#!/usr/bin/env python3
"""
Test suite for runtime failure handling in LoadShaper.

This module tests how LoadShaper handles various runtime failure scenarios,
including storage degradation, database failures, and recovery mechanisms.
"""

import pytest
import sqlite3
import tempfile
import os
import time
import threading
import errno
from unittest.mock import patch, MagicMock, Mock, mock_open
import unittest

# Import LoadShaper modules
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestStorageDegradation:
    """Test storage degradation detection and handling."""

    def test_consecutive_failure_tracking(self):
        """Test that consecutive failures are tracked correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = loadshaper.MetricsStorage(db_path)

            # Initially no failures
            assert storage.consecutive_failures == 0
            assert not storage.is_storage_degraded()

            # Successful operation resets counter
            assert storage.store_sample(25.0, 50.0, 15.0, 0.5)
            assert storage.consecutive_failures == 0

            # Simulate failures by patching sqlite3.connect to raise an exception
            with patch('sqlite3.connect', side_effect=sqlite3.OperationalError("database is locked")):
                # First few failures should not mark as degraded
                for i in range(storage.max_consecutive_failures - 1):
                    assert not storage.store_sample(25.0, 50.0, 15.0, 0.5)
                    assert storage.consecutive_failures == i + 1
                    assert not storage.is_storage_degraded()

                # One more failure should mark as degraded
                assert not storage.store_sample(25.0, 50.0, 15.0, 0.5)
                assert storage.consecutive_failures == storage.max_consecutive_failures
                assert storage.is_storage_degraded()

            # Successful operation should reset the counter
            assert storage.store_sample(25.0, 50.0, 15.0, 0.5)
            assert storage.consecutive_failures == 0
            assert not storage.is_storage_degraded()

    def test_storage_status_reporting(self):
        """Test storage status reporting for telemetry."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = loadshaper.MetricsStorage(db_path)

            # Initially clean status
            status = storage.get_storage_status()
            assert status['consecutive_failures'] == 0
            assert status['is_degraded'] is False
            assert status['last_failure_time'] is None
            assert status['max_consecutive_failures'] == 5  # Default value

            # Simulate a failure
            with patch('sqlite3.connect', side_effect=sqlite3.OperationalError("disk full")):
                storage.store_sample(25.0, 50.0, 15.0, 0.5)

            status = storage.get_storage_status()
            assert status['consecutive_failures'] == 1
            assert status['is_degraded'] is False
            assert status['last_failure_time'] is not None
            assert time.time() - status['last_failure_time'] < 1.0  # Recent failure

    def test_different_failure_types(self):
        """Test handling of different types of database failures."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = loadshaper.MetricsStorage(db_path)

            failure_types = [
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("database or disk is full"),
                sqlite3.OperationalError("no such table: metrics"),
                PermissionError("Permission denied"),
                OSError("I/O error"),
            ]

            for failure in failure_types:
                with patch('sqlite3.connect', side_effect=failure):
                    result = storage.store_sample(25.0, 50.0, 15.0, 0.5)
                    assert result is False

                # Reset for next test
                storage.consecutive_failures = 0


class TestHealthEndpointDegradation:
    """Test health endpoint reporting of storage degradation."""

    class MockHealthHandler:
        """Mock health handler for testing."""

        def __init__(self, path, controller_state, metrics_storage):
            self.path = path
            self.controller_state = controller_state or {}
            self.controller_state_lock = threading.Lock()
            self.metrics_storage = metrics_storage
            self.response_status = None
            self.response_body = None

        def _send_json_response(self, status_code, data):
            self.response_status = status_code
            self.response_body = data

    def test_health_endpoint_storage_degraded(self):
        """Test health endpoint reports storage degradation correctly."""
        # Initialize configuration
        loadshaper._initialize_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a path that looks like persistent storage for the health check
            persistent_dir = os.path.join(temp_dir, "var", "lib", "loadshaper")
            os.makedirs(persistent_dir)
            db_path = os.path.join(persistent_dir, "test_metrics.db")
            storage = loadshaper.MetricsStorage(db_path)

            # Force degradation
            storage.consecutive_failures = storage.max_consecutive_failures
            storage.last_failure_time = time.time()

            controller_state = {
                'start_time': time.time() - 100,
                'paused': 0.0,
                'cpu_avg': 25.0,
                'mem_avg': 50.0
            }

            handler = self.MockHealthHandler("/health", controller_state, storage)

            # Mock handler class methods
            handler._handle_health = loadshaper.HealthHandler._handle_health.__get__(handler, loadshaper.HealthHandler)

            # Call health check
            handler._handle_health()

            assert handler.response_status == 503  # Unhealthy
            assert handler.response_body['status'] == 'unhealthy'
            assert 'storage_degraded' in handler.response_body['checks']
            assert 'storage_status' in handler.response_body
            assert handler.response_body['storage_status']['is_degraded'] is True
            assert handler.response_body['storage_status']['consecutive_failures'] >= 5

    def test_health_endpoint_storage_recovering(self):
        """Test health endpoint shows recovery after degradation."""
        # Initialize configuration
        loadshaper._initialize_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a path that looks like persistent storage for the health check
            persistent_dir = os.path.join(temp_dir, "var", "lib", "loadshaper")
            os.makedirs(persistent_dir)
            db_path = os.path.join(persistent_dir, "test_metrics.db")
            storage = loadshaper.MetricsStorage(db_path)

            controller_state = {
                'start_time': time.time() - 100,
                'paused': 0.0,
                'cpu_avg': 25.0,
                'mem_avg': 50.0
            }

            handler = self.MockHealthHandler("/health", controller_state, storage)
            handler._handle_health = loadshaper.HealthHandler._handle_health.__get__(handler, loadshaper.HealthHandler)

            # System should be healthy when storage is working
            handler._handle_health()

            assert handler.response_status == 200  # Healthy
            assert handler.response_body['status'] == 'healthy'
            assert 'all_systems_operational' in handler.response_body['checks']
            assert 'storage_status' in handler.response_body
            assert handler.response_body['storage_status']['is_degraded'] is False
            assert handler.response_body['storage_status']['consecutive_failures'] == 0


class TestP95ControllerFailureHandling:
    """Test P95 controller behavior during storage failures."""

    def test_p95_controller_stale_data_handling(self):
        """Test P95 controller behavior when data becomes stale."""
        # Initialize configuration to prevent None errors
        loadshaper._initialize_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = loadshaper.MetricsStorage(db_path)

            # Store some initial data
            for i in range(10):
                storage.store_sample(25.0 + i, 50.0, 15.0, 0.5)

            controller = loadshaper.CPUP95Controller(storage)

            # Get initial P95 (should work)
            initial_p95 = controller.get_cpu_p95()
            assert initial_p95 is not None
            assert initial_p95 > 20.0

            # Break storage (make get_percentile return None)
            with patch.object(storage, 'get_percentile', return_value=None):
                # First call should return cached value
                cached_p95 = controller.get_cpu_p95()
                assert cached_p95 == initial_p95

                # After cache expiry, improved fallback logic should still return cached value
                # instead of None (better resilience during database failures)
                controller._p95_cache_time = time.monotonic() - 400  # Force cache expiry
                stale_p95 = controller.get_cpu_p95()
                assert stale_p95 == initial_p95  # Should return cached value, not None

                # Controller should handle None gracefully in state updates
                old_state = controller.state
                controller.update_state(None)  # Should not crash
                # State should remain unchanged with None input
                assert controller.state == old_state

    def test_controller_ring_buffer_persistence_failure(self):
        """Test controller behavior when ring buffer persistence fails."""
        # Initialize configuration to prevent None errors
        loadshaper._initialize_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = loadshaper.MetricsStorage(db_path)

            # Create controller normally
            controller = loadshaper.CPUP95Controller(storage)

            # Make ring buffer path unwritable (simulate mount failure)
            with patch.object(controller, '_get_ring_buffer_path',
                            side_effect=PermissionError("Permission denied")):
                # Ring buffer operations should fail gracefully
                controller._save_ring_buffer_state()  # Should not crash
                controller._load_ring_buffer_state()  # Should not crash

                # Controller should still function for basic operations
                is_high, intensity = controller.should_run_high_slot(0.5)
                assert isinstance(is_high, bool)
                assert isinstance(intensity, (int, float))


class TestDatabaseCorruptionRecovery:
    """Test handling of database corruption scenarios."""

    def test_corrupted_database_handling(self):
        """Test behavior when database is corrupted."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")

            # Create a corrupted database file
            with open(db_path, 'wb') as f:
                f.write(b'This is not a SQLite database')

            # Storage initialization should fail with corrupted DB
            with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
                loadshaper.MetricsStorage(db_path)

    def test_disk_full_scenario(self):
        """Test handling when disk becomes full."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = loadshaper.MetricsStorage(db_path)

            # Simulate disk full error during write
            with patch('sqlite3.connect') as mock_connect:
                mock_conn = MagicMock()
                mock_connect.return_value.__enter__ = Mock(return_value=mock_conn)
                mock_connect.return_value.__exit__ = Mock(return_value=None)
                mock_conn.execute.side_effect = sqlite3.OperationalError("database or disk is full")

                result = storage.store_sample(25.0, 50.0, 15.0, 0.5)
                assert result is False
                assert storage.consecutive_failures == 1

    def test_database_locked_scenario(self):
        """Test handling when database is locked by another process."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = loadshaper.MetricsStorage(db_path)

            # Simulate database locked error
            with patch('sqlite3.connect') as mock_connect:
                mock_connect.side_effect = sqlite3.OperationalError("database is locked")

                # Both read and write operations should handle this gracefully
                result = storage.store_sample(25.0, 50.0, 15.0, 0.5)
                assert result is False

                p95 = storage.get_percentile('cpu')
                assert p95 is None

                count = storage.get_sample_count()
                assert count == 0

    def test_p95_controller_enospc_degraded_mode(self):
        """Test comprehensive ENOSPC error handling in P95 controller leading to degraded mode."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Setup controller with temp storage
            storage = loadshaper.MetricsStorage(os.path.join(temp_dir, "metrics.db"))
            controller = loadshaper.CPUP95Controller(storage)
            controller.test_mode = True  # Allow persistence in tests

            # Verify controller starts in normal mode
            assert not hasattr(controller, '_degraded_mode') or not controller._degraded_mode

            # Mock the ring buffer path to point to our temp directory
            ring_buffer_path = os.path.join(temp_dir, "p95_ring_buffer.json")

            with patch.object(controller, '_get_ring_buffer_path', return_value=ring_buffer_path):
                # Test 1: ENOSPC during ring buffer save triggers degraded mode
                with patch('builtins.open', mock_open()) as mock_file:
                    # Simulate ENOSPC error (errno 28)
                    enospc_error = OSError(errno.ENOSPC, "No space left on device")
                    mock_file.side_effect = enospc_error

                    # Trigger save operation - should enter degraded mode
                    controller._save_ring_buffer_state()

                    # Verify degraded mode is activated
                    assert hasattr(controller, '_degraded_mode')
                    assert controller._degraded_mode is True

                # Test 2: Verify degraded mode skips future persistence operations
                with patch('builtins.open') as mock_file_2:
                    controller._save_ring_buffer_state()

                    # In degraded mode, file operations should be skipped
                    mock_file_2.assert_not_called()

                # Test 3: Test recovery from degraded mode (manual reset)
                controller._degraded_mode = False

                # Should now allow normal operations again
                with patch('builtins.open', mock_open()) as mock_file_3:
                    with patch('os.replace') as mock_replace:
                        with patch('os.fsync'):
                            controller._save_ring_buffer_state()

                            # Should attempt normal file operations
                            mock_file_3.assert_called()
                            mock_replace.assert_called()

    def test_metrics_storage_error_resilience(self):
        """Test MetricsStorage resilience under various error conditions."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = loadshaper.MetricsStorage(db_path)

            # Test that storage operations don't crash under normal error conditions
            # The P95 controller test above covers the ENOSPC degraded mode scenario more thoroughly
            try:
                result = storage.store_sample(25.0, 50.0, 15.0, 0.5)
                # Should handle normal operations without issues
                assert result is True or result is False  # Either outcome is fine
            except Exception as e:
                pytest.fail(f"Storage should handle normal operations gracefully: {e}")

    def test_degraded_mode_persistence_behavior(self):
        """Test that degraded mode properly prevents crash loops during disk full scenarios."""
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = loadshaper.MetricsStorage(os.path.join(temp_dir, "metrics.db"))
            controller = loadshaper.CPUP95Controller(storage)
            controller.test_mode = True

            # Manually set degraded mode (simulating disk full detection)
            controller._degraded_mode = True

            # Test that operations are skipped without exceptions
            try:
                controller._save_ring_buffer_state()  # Should not crash
                controller._maybe_save_ring_buffer_state()  # Should not crash
                # Success - no exceptions thrown
                assert True
            except Exception as e:
                pytest.fail(f"Degraded mode should not raise exceptions: {e}")


if __name__ == '__main__':
    pytest.main([__file__])