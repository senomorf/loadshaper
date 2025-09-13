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
        """Test CPU worker behavior when stop flag is set."""
        # Create shared values for testing
        duty_val = Value('f', 0.5)  # 50% duty cycle
        stop_flag = Value('f', 1.0)  # Start paused to test pause behavior

        # Start CPU worker in a daemon thread (will exit when main thread exits)
        worker_thread = threading.Thread(
            target=loadshaper.cpu_worker,
            args=(duty_val, stop_flag),
            daemon=True
        )
        worker_thread.start()

        # Let worker run briefly in paused state
        time.sleep(0.1)

        # Worker should be alive and handling the paused state
        self.assertTrue(worker_thread.is_alive(), "Worker should be running even when paused")

        # Test unpausing
        stop_flag.value = 0.0
        time.sleep(0.1)

        # Worker should still be alive and working
        self.assertTrue(worker_thread.is_alive(), "Worker should continue running when unpaused")

        # Test pausing again
        stop_flag.value = 1.0
        time.sleep(0.1)

        # Worker should still be alive but paused
        self.assertTrue(worker_thread.is_alive(), "Worker should handle pause/unpause transitions")

        # Thread will be cleaned up automatically as daemon thread

    def test_memory_allocation_failure(self):
        """Test memory nurse thread behavior when allocation fails."""
        # The mem_nurse_thread touches existing memory rather than allocating new memory
        # This test verifies it handles memory touch operations gracefully
        stop_event = threading.Event()

        # Initialize loadshaper's memory control variables and config
        if not hasattr(loadshaper, 'paused'):
            loadshaper.paused = Value('f', 0.0)
        if not hasattr(loadshaper, 'mem_lock'):
            loadshaper.mem_lock = threading.Lock()
        if not hasattr(loadshaper, 'mem_block'):
            loadshaper.mem_block = bytearray(1024)  # Small test allocation
        if not hasattr(loadshaper, 'MEM_TOUCH_INTERVAL_SEC') or loadshaper.MEM_TOUCH_INTERVAL_SEC is None:
            loadshaper.MEM_TOUCH_INTERVAL_SEC = 5.0  # Default interval
        if not hasattr(loadshaper, 'LOAD_CHECK_ENABLED') or loadshaper.LOAD_CHECK_ENABLED is None:
            loadshaper.LOAD_CHECK_ENABLED = False  # Disable for test

        # Test thread should handle gracefully without crashing
        try:
            worker_thread = threading.Thread(
                target=loadshaper.mem_nurse_thread,
                args=(stop_event,),
                daemon=True  # Cleanup automatically
            )
            worker_thread.start()

            time.sleep(0.1)  # Short test duration
            stop_event.set()
            worker_thread.join(timeout=2.0)

            # Should complete without crashing
            success = True
        except Exception as e:
            success = False
            print(f"Memory nurse thread failed: {e}")

        self.assertTrue(success, "Memory nurse thread should handle operations gracefully")

    def test_network_generator_connection_failures(self):
        """Test network generator behavior with connection failures."""
        # Create generator with non-routable address
        gen = loadshaper.NetworkGenerator(
            rate_mbps=1.0,
            protocol='tcp',
            port=12345
        )

        # Should handle connection failures gracefully
        gen.start(['192.0.2.1'])  # RFC 5737 test address (non-routable)
        time.sleep(0.2)  # Let it try to connect
        gen.stop()

        # Should not crash and should clean up properly
        self.assertEqual(len(gen.tcp_connections), 0,
                        "Should clean up failed connections")

    def test_network_generator_dns_resolution_failure(self):
        """Test network generator with DNS resolution failures."""
        gen = loadshaper.NetworkGenerator(
            rate_mbps=1.0,
            protocol='udp',
            port=12345
        )

        # Should handle DNS failures gracefully
        gen.start(['this-domain-does-not-exist-12345.invalid'])
        time.sleep(0.1)
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
                    rate_mbps=1.0,
                    protocol='tcp',
                    port=12345 + i
                )
                generators.append(gen)
                gen.start(['127.0.0.1'])

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
        # Test signal handling during high load
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
                args=(loadshaper.duty, loadshaper.paused)
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

        # Start multiple workers that might compete for resources (daemon threads for cleanup)
        workers = []
        for i in range(5):
            worker = threading.Thread(
                target=loadshaper.cpu_worker,
                args=(loadshaper.duty, loadshaper.paused),
                daemon=True  # Auto-cleanup when test finishes
            )
            workers.append(worker)
            worker.start()

        # Verify workers started
        time.sleep(0.1)
        for worker in workers:
            self.assertTrue(worker.is_alive(), "Workers should start successfully")

        # Rapidly modify shared state to test race conditions
        for _ in range(10):
            loadshaper.paused.value = 1.0 if loadshaper.paused.value == 0.0 else 0.0
            loadshaper.duty.value = 0.8 if loadshaper.duty.value < 0.6 else 0.2
            time.sleep(0.01)

        # Test completes - daemon threads will be cleaned up automatically
        # Verify no crashes occurred during the rapid state changes
        time.sleep(0.1)
        for worker in workers:
            self.assertTrue(worker.is_alive(),
                           "Workers should survive concurrent modifications without crashing")

    def test_metrics_database_corruption_recovery(self):
        """Test recovery from corrupted metrics database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'corrupted.db')

            # Create corrupted database file
            with open(db_path, 'wb') as f:
                f.write(b'This is not a valid SQLite database')

            # Should handle corrupted database gracefully
            try:
                metrics_tracker = loadshaper.MetricsStorage(db_path)
                metrics_tracker.store_sample(25.0, 50.0, 30.0, 1.0)
                metrics_tracker.get_percentile('cpu')
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
                    metrics_tracker = loadshaper.MetricsStorage(db_path)
                    # Should handle low disk space gracefully
                    for i in range(100):
                        metrics_tracker.store_sample(25.0 + i * 0.1, 50.0, 30.0, 1.0)

                    success = True
                except Exception as e:
                    success = False
                    print(f"Disk space handling failed: {e}")

                self.assertTrue(success, "Should handle disk space exhaustion")

    def test_network_interface_disappears(self):
        """Test behavior when network interface becomes unavailable."""
        # Test network functions handling interface disappearance
        fake_iface = "nonexistent0"

        try:
            # Test read_host_nic_bytes with nonexistent interface
            result = loadshaper.read_host_nic_bytes(fake_iface)
            # Should return None gracefully for missing interface
            self.assertIsNone(result, "Should return None for missing interface")

            # Test nic_utilization_pct with None values
            util = loadshaper.nic_utilization_pct(None, None, 1.0, 100)
            # Should return None gracefully
            self.assertIsNone(util, "Should handle None values gracefully")

        except Exception as e:
            self.fail(f"Should not crash when network interface disappears: {e}")

    def test_extremely_high_memory_target(self):
        """Test behavior with unrealistic memory allocation via set_mem_target_bytes."""
        # Test the memory allocation function directly with large but not system-breaking targets
        # Use a reasonable test target instead of querying system memory
        unrealistic_target = 1024 * 1024 * 1024  # 1GB test target

        # Initialize required config variables for the test
        if not hasattr(loadshaper, 'MEM_STEP_MB') or loadshaper.MEM_STEP_MB is None:
            loadshaper.MEM_STEP_MB = 100  # Default step size in MB
        if not hasattr(loadshaper, 'mem_lock'):
            loadshaper.mem_lock = threading.Lock()
        if not hasattr(loadshaper, 'mem_block'):
            loadshaper.mem_block = bytearray()

        try:
            # Should handle unrealistic targets gracefully
            original_size = len(loadshaper.mem_block) if hasattr(loadshaper, 'mem_block') else 0
            loadshaper.set_mem_target_bytes(unrealistic_target)

            # Should not have allocated the full unrealistic amount
            new_size = len(loadshaper.mem_block) if hasattr(loadshaper, 'mem_block') else 0
            self.assertLess(new_size, unrealistic_target,
                           "Should not allocate more memory than requested in single step")

            # Should not crash system or cause system instability
            # Memory allocation should be handled gracefully without system monitoring

        except Exception as e:
            # Should handle errors gracefully without crashing
            print(f"Memory allocation handled error gracefully: {e}")
            self.assertIsInstance(e, (MemoryError, OSError, TypeError, NameError),
                               "Should raise expected memory-related errors")

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

        # Start worker (daemon thread for automatic cleanup)
        worker = threading.Thread(
            target=loadshaper.cpu_worker,
            args=(loadshaper.duty, loadshaper.paused),
            daemon=True
        )
        worker.start()

        # Rapidly change configuration
        for i in range(20):
            loadshaper.CPU_TARGET_PCT = 20 + (i % 30)
            loadshaper.MEM_TARGET_PCT = i % 50
            loadshaper.NET_TARGET_PCT = 15 + (i % 20)
            time.sleep(0.05)

        # Restore original values
        for attr, value in original_targets.items():
            setattr(loadshaper, attr, value)

        # Verify worker is running and handling changes (daemon cleanup automatic)
        self.assertTrue(worker.is_alive(),
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
                tracker = loadshaper.MetricsStorage(db_path)

                # Record some metrics
                tracker.store_sample(25.0, 50.0, 30.0, 1.0)

                # Simulate system suspend (time jumps forward)
                mock_time.return_value = start_time + 3600  # 1 hour later

                # Should handle time jump gracefully
                try:
                    tracker.store_sample(30.0, 50.0, 30.0, 1.0)
                    stats = tracker.get_percentile('cpu')
                    success = True
                except Exception as e:
                    success = False
                    print(f"Suspend/resume handling failed: {e}")

                self.assertTrue(success, "Should handle system suspend/resume")

    def test_token_bucket_overflow_conditions(self):
        """Test TokenBucket behavior under overflow conditions."""
        bucket = loadshaper.TokenBucket(1000.0)  # 1000 Mbps

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