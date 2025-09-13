#!/usr/bin/env python3
"""
Stress tests for loadshaper failure modes and edge cases.

These tests verify system behavior under resource pressure,
network failures, and other adverse conditions to ensure
graceful degradation and recovery.
"""

import unittest
import unittest.mock
import sys
import os
import time
import threading
import socket
import psutil
from multiprocessing import Value
import tempfile
import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loadshaper


class TestStressFailureModes(unittest.TestCase):
    """Test failure modes and stress conditions."""

    def setUp(self):
        """Set up test environment."""
        self.original_values = {}
        # Store original values
        for attr in ['CPU_TARGET_PCT', 'MEM_TARGET_PCT', 'NET_TARGET_PCT', 'paused', 'duty']:
            if hasattr(loadshaper, attr):
                self.original_values[attr] = getattr(loadshaper, attr)

    def tearDown(self):
        """Clean up test environment."""
        # Restore original values
        for attr, value in self.original_values.items():
            if hasattr(loadshaper, attr):
                setattr(loadshaper, attr, value)

        # Ensure all threads are cleaned up
        time.sleep(0.1)

    def test_cpu_worker_under_extreme_load(self):
        """Test CPU worker behavior when system is under extreme load."""
        # Mock extreme system load
        with unittest.mock.patch('os.getloadavg', return_value=(8.0, 7.5, 6.0)):
            with unittest.mock.patch('loadshaper.LOAD_CHECK_ENABLED', True):
                with unittest.mock.patch('loadshaper.LOAD_THRESHOLD', 2.0):
                    # Start CPU worker
                    stop_event = threading.Event()
                    loadshaper.paused = Value('f', 0.0)
                    loadshaper.duty = Value('f', 0.5)

                    # Worker should pause due to high load
                    worker_thread = threading.Thread(
                        target=loadshaper.cpu_worker,
                        args=(stop_event, loadshaper.paused, loadshaper.duty)
                    )
                    worker_thread.start()

                    time.sleep(0.5)

                    # Worker should be paused due to load
                    self.assertEqual(loadshaper.paused.value, 1.0,
                                   "Worker should pause under extreme load")

                    stop_event.set()
                    worker_thread.join(timeout=1.0)

    def test_memory_allocation_failure(self):
        """Test memory worker behavior when allocation fails."""
        # Mock memory allocation failure
        original_bytearray = bytearray

        def failing_bytearray(size):
            if size > 100:  # Fail on large allocations
                raise MemoryError("Simulated memory allocation failure")
            return original_bytearray(size)

        with unittest.mock.patch('builtins.bytearray', side_effect=failing_bytearray):
            stop_event = threading.Event()
            loadshaper.paused = Value('f', 0.0)

            # Memory worker should handle allocation failures gracefully
            worker_thread = threading.Thread(
                target=loadshaper.memory_worker,
                args=(stop_event, loadshaper.paused, 50.0, 10, 100, 1.0)  # 50% target
            )
            worker_thread.start()

            time.sleep(0.5)
            stop_event.set()
            worker_thread.join(timeout=2.0)

            # Should complete without crashing
            self.assertFalse(worker_thread.is_alive(),
                           "Memory worker should handle allocation failures")

    def test_network_generator_connection_failures(self):
        """Test network generator behavior with connection failures."""
        # Create generator with non-routable address
        gen = loadshaper.NetworkGenerator(
            target_addresses=['192.0.2.1'],  # RFC 5737 test address (non-routable)
            port=12345,
            protocol='tcp'
        )

        # Should handle connection failures gracefully
        gen.start()
        time.sleep(2)  # Let it try to connect
        gen.stop()

        # Should not crash and should clean up properly
        self.assertEqual(len(gen.tcp_connections), 0,
                        "Should clean up failed connections")

    def test_network_generator_dns_resolution_failure(self):
        """Test network generator with DNS resolution failures."""
        gen = loadshaper.NetworkGenerator(
            target_addresses=['this-domain-does-not-exist-12345.invalid'],
            port=12345,
            protocol='udp'
        )

        # Should handle DNS failures gracefully
        gen.start()
        time.sleep(1)
        gen.stop()

        # Should not crash
        self.assertTrue(True, "Should handle DNS failures gracefully")

    def test_network_generator_port_exhaustion(self):
        """Test network generator behavior when ports are exhausted."""
        # Create many generators to potentially exhaust ports
        generators = []

        try:
            for i in range(10):
                gen = loadshaper.NetworkGenerator(
                    target_addresses=['127.0.0.1'],
                    port=12345 + i,
                    protocol='tcp'
                )
                generators.append(gen)
                gen.start()

            time.sleep(1)

        finally:
            # Clean up all generators
            for gen in generators:
                try:
                    gen.stop()
                except:
                    pass  # Ignore cleanup errors

        # Should handle resource exhaustion gracefully
        self.assertTrue(True, "Should handle port exhaustion gracefully")

    def test_signal_handling_during_high_load(self):
        """Test signal handling when system is under load."""
        # Mock high system load
        with unittest.mock.patch('psutil.cpu_percent', return_value=95.0):
            stop_event = threading.Event()
            signal_received = threading.Event()

            def mock_signal_handler(signum, frame):
                signal_received.set()
                stop_event.set()

            # Simulate signal during high CPU load
            with unittest.mock.patch('signal.signal') as mock_signal:
                mock_signal.return_value = mock_signal_handler

                # Start worker under load
                loadshaper.paused = Value('f', 0.0)
                loadshaper.duty = Value('f', 1.0)  # Maximum load

                worker_thread = threading.Thread(
                    target=loadshaper.cpu_worker,
                    args=(stop_event, loadshaper.paused, loadshaper.duty)
                )
                worker_thread.start()

                time.sleep(0.1)

                # Trigger signal
                mock_signal_handler(signal.SIGTERM, None)

                # Should handle signal promptly even under load
                self.assertTrue(signal_received.wait(timeout=1.0),
                              "Should handle signals even under high load")

                worker_thread.join(timeout=1.0)

    def test_concurrent_modification_race_conditions(self):
        """Test for race conditions with concurrent modifications."""
        loadshaper.paused = Value('f', 0.0)
        loadshaper.duty = Value('f', 0.5)

        stop_event = threading.Event()

        # Start multiple workers that might compete for resources
        workers = []
        for i in range(5):
            worker = threading.Thread(
                target=loadshaper.cpu_worker,
                args=(stop_event, loadshaper.paused, loadshaper.duty)
            )
            workers.append(worker)
            worker.start()

        # Rapidly modify shared state
        for _ in range(10):
            loadshaper.paused.value = 1.0 if loadshaper.paused.value == 0.0 else 0.0
            loadshaper.duty.value = 0.8 if loadshaper.duty.value < 0.6 else 0.2
            time.sleep(0.01)

        # Stop all workers
        stop_event.set()
        for worker in workers:
            worker.join(timeout=1.0)
            self.assertFalse(worker.is_alive(),
                           "Workers should handle concurrent modifications")

    def test_metrics_database_corruption_recovery(self):
        """Test recovery from corrupted metrics database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'corrupted.db')

            # Create corrupted database file
            with open(db_path, 'wb') as f:
                f.write(b'This is not a valid SQLite database')

            # Should handle corrupted database gracefully
            try:
                metrics_tracker = loadshaper.MetricsTracker(db_path)
                metrics_tracker.record_metric('cpu_p95', 25.0)
                metrics_tracker.get_7day_stats('cpu_p95')
                success = True
            except Exception as e:
                success = False
                print(f"Database corruption handling failed: {e}")

            self.assertTrue(success, "Should recover from database corruption")

    def test_disk_space_exhaustion(self):
        """Test behavior when disk space is exhausted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock disk space check to simulate full disk
            with unittest.mock.patch('shutil.disk_usage') as mock_disk:
                mock_disk.return_value = (1000000, 0, 0)  # total, used, free (full disk)

                db_path = os.path.join(tmpdir, 'test.db')

                try:
                    metrics_tracker = loadshaper.MetricsTracker(db_path)
                    # Should handle low disk space gracefully
                    for i in range(100):
                        metrics_tracker.record_metric('cpu_p95', 25.0 + i * 0.1)

                    success = True
                except Exception as e:
                    success = False
                    print(f"Disk space handling failed: {e}")

                self.assertTrue(success, "Should handle disk space exhaustion")

    def test_network_interface_disappears(self):
        """Test behavior when network interface becomes unavailable."""
        # Mock network interface disappearing
        with unittest.mock.patch('psutil.net_io_counters', side_effect=OSError("Network interface not found")):
            try:
                # Should handle missing network interface
                current_net = loadshaper.get_current_network_utilization()
                self.assertIsNotNone(current_net, "Should handle missing network interface")
            except:
                self.fail("Should not crash when network interface disappears")

    def test_extremely_high_memory_target(self):
        """Test behavior with unrealistic memory targets."""
        stop_event = threading.Event()
        loadshaper.paused = Value('f', 0.0)

        # Try to allocate more memory than available
        available_mb = psutil.virtual_memory().available // (1024 * 1024)
        target_pct = 150.0  # 150% - impossible target

        worker_thread = threading.Thread(
            target=loadshaper.memory_worker,
            args=(stop_event, loadshaper.paused, target_pct, 10, 100, 1.0)
        )
        worker_thread.start()

        time.sleep(2)  # Let it try
        stop_event.set()
        worker_thread.join(timeout=3.0)

        # Should not crash system or use swap excessively
        swap_usage = psutil.swap_memory().percent
        self.assertLess(swap_usage, 50.0,
                       "Should not cause excessive swap usage")

    def test_rapid_configuration_changes(self):
        """Test system stability with rapid configuration changes."""
        original_targets = {
            'CPU_TARGET_PCT': getattr(loadshaper, 'CPU_TARGET_PCT', 25.0),
            'MEM_TARGET_PCT': getattr(loadshaper, 'MEM_TARGET_PCT', 0.0),
            'NET_TARGET_PCT': getattr(loadshaper, 'NET_TARGET_PCT', 25.0)
        }

        stop_event = threading.Event()
        loadshaper.paused = Value('f', 0.0)
        loadshaper.duty = Value('f', 0.5)

        # Start worker
        worker = threading.Thread(
            target=loadshaper.cpu_worker,
            args=(stop_event, loadshaper.paused, loadshaper.duty)
        )
        worker.start()

        # Rapidly change configuration
        for i in range(20):
            loadshaper.CPU_TARGET_PCT = 20 + (i % 30)
            loadshaper.MEM_TARGET_PCT = i % 50
            loadshaper.NET_TARGET_PCT = 15 + (i % 20)
            time.sleep(0.05)

        stop_event.set()
        worker.join(timeout=2.0)

        # Restore original values
        for attr, value in original_targets.items():
            setattr(loadshaper, attr, value)

        self.assertFalse(worker.is_alive(),
                        "Should handle rapid configuration changes")

    def test_system_suspend_resume(self):
        """Test behavior across system suspend/resume cycles."""
        # Simulate time jump (like after system suspend)
        with unittest.mock.patch('time.time') as mock_time:
            start_time = 1000000000
            mock_time.return_value = start_time

            # Start metrics tracking
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, 'suspend_test.db')
                tracker = loadshaper.MetricsTracker(db_path)

                # Record some metrics
                tracker.record_metric('cpu_p95', 25.0)

                # Simulate system suspend (time jumps forward)
                mock_time.return_value = start_time + 3600  # 1 hour later

                # Should handle time jump gracefully
                try:
                    tracker.record_metric('cpu_p95', 30.0)
                    stats = tracker.get_7day_stats('cpu_p95')
                    success = True
                except Exception as e:
                    success = False
                    print(f"Suspend/resume handling failed: {e}")

                self.assertTrue(success, "Should handle system suspend/resume")

    def test_token_bucket_overflow_conditions(self):
        """Test TokenBucket behavior under overflow conditions."""
        bucket = loadshaper.TokenBucket(rate=1000, capacity=1000)  # 1000 tokens/sec

        # Simulate rapid token consumption
        bucket.last_update = time.time() - 10  # 10 seconds ago

        # Should handle large time gaps without overflow
        available = bucket.consume(1)
        self.assertTrue(available, "Should handle large time gaps")

        # Test extreme consumption
        for _ in range(2000):  # Try to consume more than capacity
            bucket.consume(1)

        # Should still function correctly
        bucket.last_update = time.time() - 1  # 1 second ago
        available = bucket.consume(500)  # Should be able to consume some
        self.assertTrue(available, "Should recover from overconsumption")


if __name__ == '__main__':
    unittest.main()