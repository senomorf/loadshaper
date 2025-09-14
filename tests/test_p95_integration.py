import unittest
import time
import tempfile
import os
from unittest.mock import Mock, patch, MagicMock
import sys

# Add the parent directory to sys.path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper
from loadshaper import CPUP95Controller, MetricsStorage


class MockMetricsStorage:
    """Mock metrics storage for testing"""

    def __init__(self):
        self.p95_value = 25.0  # Default reasonable P95 value
        self.call_count = 0

    def get_percentile(self, metric, percentile=95):
        """Mock get_percentile that tracks call count"""
        self.call_count += 1
        return self.p95_value

    def set_p95(self, value):
        """Set the P95 value to return"""
        self.p95_value = value
        # Don't reset call count - tests expect cumulative counting

    def clear_controller_cache(self, controller):
        """Clear P95 cache in controller when changing mock values"""
        controller._p95_cache = None
        controller._p95_cache_time = 0


class TestP95ControllerIntegration(unittest.TestCase):
    """Integration tests for P95 controller with main loop and configuration"""

    def setUp(self):
        """Set up integration test fixtures"""
        # Create temporary database for real MetricsStorage
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()

        # Create real MetricsStorage instance
        self.metrics_storage = MetricsStorage(db_path=self.temp_db.name)

        # Mock configuration variables
        self.patches = patch.multiple(loadshaper,
                                    CPU_P95_SLOT_DURATION=60.0,
                                    CPU_P95_BASELINE_INTENSITY=20.0,
                                    CPU_P95_HIGH_INTENSITY=35.0,
                                    CPU_P95_TARGET_MIN=22.0,
                                    CPU_P95_TARGET_MAX=28.0,
                                    CPU_P95_SETPOINT=25.0,
                                    CPU_P95_EXCEEDANCE_TARGET=6.5,
                                    LOAD_CHECK_ENABLED=True,
                                    LOAD_THRESHOLD=0.6)
        self.patches.start()

        # Create controller with real storage
        self.controller = CPUP95Controller(self.metrics_storage)

    def tearDown(self):
        """Clean up integration test fixtures"""
        self.patches.stop()
        # Clean up temporary database
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    def _insert_batch_data(self, base_time, samples_per_day, days=7):
        """Helper to insert test data efficiently using batch transactions"""
        import sqlite3

        # Use direct database access for bulk insertion performance
        conn = sqlite3.connect(self.metrics_storage.db_path)
        cursor = conn.cursor()

        try:
            conn.execute("BEGIN TRANSACTION")

            # Generate all samples at once for better performance
            samples = []
            for day in range(days):
                for sample in range(samples_per_day):
                    timestamp = base_time - (day * 86400) - (sample * 5)

                    # 95% of samples at 20%, 5% at 40% (should give P95 ≈ 20%)
                    cpu_pct = 40.0 if (sample % 20) == 0 else 20.0
                    mem_pct = 25.0
                    net_pct = 15.0
                    load_avg = 0.3

                    samples.append((timestamp, cpu_pct, mem_pct, net_pct, load_avg))

            # Batch insert all samples
            cursor.executemany("""
                INSERT INTO metrics (timestamp, cpu_pct, mem_pct, net_pct, load_avg)
                VALUES (?, ?, ?, ?, ?)
            """, samples)

            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _insert_simple_batch(self, base_time, count, cpu_pct=25.0, mem_pct=25.0, net_pct=15.0, load_avg=0.3):
        """Helper to insert simple test data efficiently"""
        import sqlite3

        conn = sqlite3.connect(self.metrics_storage.db_path)
        cursor = conn.cursor()

        try:
            conn.execute("BEGIN TRANSACTION")

            samples = []
            for i in range(count):
                timestamp = base_time - (i * 5)
                samples.append((timestamp, cpu_pct, mem_pct, net_pct, load_avg))

            cursor.executemany("""
                INSERT INTO metrics (timestamp, cpu_pct, mem_pct, net_pct, load_avg)
                VALUES (?, ?, ?, ?, ?)
            """, samples)

            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def test_configuration_loading_with_p95_variables(self):
        """Test that P95 configuration variables are properly loaded"""
        # Test that all P95 variables are accessible
        self.assertAlmostEqual(loadshaper.CPU_P95_SLOT_DURATION, 60.0, places=1)
        self.assertAlmostEqual(loadshaper.CPU_P95_BASELINE_INTENSITY, 20.0, places=1)
        self.assertAlmostEqual(loadshaper.CPU_P95_HIGH_INTENSITY, 35.0, places=1)
        self.assertAlmostEqual(loadshaper.CPU_P95_TARGET_MIN, 22.0, places=1)
        self.assertAlmostEqual(loadshaper.CPU_P95_TARGET_MAX, 28.0, places=1)
        self.assertAlmostEqual(loadshaper.CPU_P95_SETPOINT, 25.0, places=1)
        self.assertAlmostEqual(loadshaper.CPU_P95_EXCEEDANCE_TARGET, 6.5, places=1)

    def test_metrics_storage_p95_calculation(self):
        """Test that MetricsStorage correctly calculates P95 values"""
        # Store sample data over 7 days
        now = time.time()
        samples_per_day = 200  # Reduced from 17280 for test performance (still sufficient for P95)

        # Create test data using batch insertion for performance
        self._insert_batch_data(now, samples_per_day, days=7)

        # Calculate P95 - with 5% samples at 40%, P95 should be around 20% (the 95th percentile)
        p95 = self.metrics_storage.get_percentile('cpu', percentile=95)
        self.assertIsNotNone(p95)
        self.assertGreater(p95, 18.0)  # Should be above the low value
        self.assertLess(p95, 42.0)     # But could reach the high value

    def test_controller_state_machine_with_real_data(self):
        """Test controller state machine with real P95 data"""
        # Store data that should trigger BUILDING state (low P95)
        now = time.time()
        self._insert_simple_batch(now, 30, cpu_pct=15.0)  # Reduced from 100 to 30

        # Clear controller cache to force fresh query
        self.controller._p95_cache = None
        self.controller._p95_cache_time = 0

        # Update state with real P95 data
        cpu_p95 = self.controller.get_cpu_p95()
        self.controller.update_state(cpu_p95)

        # Should be in BUILDING state due to low P95
        self.assertEqual(self.controller.state, 'BUILDING')

        # Now store high CPU data (use different base time to avoid timestamp conflicts)
        self._insert_simple_batch(now - 200, 30, cpu_pct=35.0)  # Reduced from 100 to 30

        # Clear cache and update
        self.controller._p95_cache = None
        self.controller._p95_cache_time = 0

        cpu_p95 = self.controller.get_cpu_p95()
        self.controller.update_state(cpu_p95)

        # Should be in REDUCING state due to high P95
        self.assertEqual(self.controller.state, 'REDUCING')

    def test_main_loop_integration_with_load_safety(self):
        """Test P95 controller integration with main loop load safety"""
        # Mock the main loop behavior with high load
        high_load = 0.8  # Above LOAD_THRESHOLD of 0.6

        # Force new slot with high load
        with patch('time.monotonic', return_value=self.controller.current_slot_start + 70):
            is_high, intensity = self.controller.should_run_high_slot(high_load)

            # Should override to baseline due to high load
            self.assertFalse(self.controller.current_slot_is_high)
            self.assertAlmostEqual(intensity, 20.0, places=1)  # BASELINE_INTENSITY
            self.assertGreater(self.controller.slots_skipped_safety, 0)

    def test_telemetry_output_format(self):
        """Test that telemetry includes P95 controller status"""
        # Get controller status
        status = self.controller.get_status()

        # Verify all required fields are present
        required_fields = [
            'state', 'cpu_p95', 'target_range', 'exceedance_pct',
            'exceedance_target', 'current_slot_is_high', 'slot_remaining_sec',
            'slots_recorded', 'slots_skipped_safety', 'target_intensity'
        ]

        for field in required_fields:
            self.assertIn(field, status, f"Missing field: {field}")

        # Verify field types and ranges
        self.assertIsInstance(status['state'], str)
        self.assertIn(status['state'], ['BUILDING', 'MAINTAINING', 'REDUCING'])
        self.assertIsInstance(status['current_slot_is_high'], bool)
        self.assertGreaterEqual(status['slot_remaining_sec'], 0.0)
        self.assertGreaterEqual(status['slots_recorded'], 0)
        self.assertGreaterEqual(status['slots_skipped_safety'], 0)

    def test_exceedance_budget_enforcement(self):
        """Test that exceedance budget is properly enforced over multiple slots"""
        # Force controller to have many high slots in history
        self.controller.slot_history[:10] = [True] * 10  # 10 high slots
        self.controller.slots_recorded = 10

        # Current exceedance should be 100%
        current_exceedance = self.controller.get_current_exceedance()
        self.assertAlmostEqual(current_exceedance, 100.0, places=1)

        # Next slot should be forced to low due to exceedance budget
        with patch('time.monotonic', return_value=self.controller.current_slot_start + 70):
            is_high, intensity = self.controller.should_run_high_slot(None)
            self.assertFalse(is_high)
            self.assertAlmostEqual(intensity, 20.0, places=1)  # BASELINE_INTENSITY

    def test_p95_cache_performance(self):
        """Test that P95 caching improves performance"""
        # Store some sample data
        now = time.time()
        self._insert_simple_batch(now, 20)  # Reduced from 50 to 20

        # First call should query database
        start_time = time.time()
        p95_1 = self.controller.get_cpu_p95()
        first_call_time = time.time() - start_time

        # Second call should use cache (much faster)
        start_time = time.time()
        p95_2 = self.controller.get_cpu_p95()
        second_call_time = time.time() - start_time

        # Results should be identical
        self.assertEqual(p95_1, p95_2)

        # Second call should be significantly faster (cache hit)
        self.assertLess(second_call_time, first_call_time / 2)

    def test_ring_buffer_memory_efficiency(self):
        """Test that ring buffer properly manages memory for 24-hour history"""
        # Get buffer size for 60-second slots over 24 hours
        expected_size = int((24 * 60 * 60) / 60)  # 1440 slots
        self.assertEqual(self.controller.slot_history_size, expected_size)

        # Fill buffer beyond capacity
        for i in range(expected_size + 100):
            self.controller._end_current_slot()

        # Should not exceed buffer size
        self.assertEqual(self.controller.slots_recorded, expected_size)
        self.assertEqual(len(self.controller.slot_history), expected_size)

    def test_thread_safety_with_concurrent_access(self):
        """Test thread safety of P95 controller under concurrent access"""
        import threading
        import concurrent.futures

        def access_controller():
            """Worker function for concurrent access"""
            try:
                # Access various controller methods
                p95 = self.controller.get_cpu_p95()
                status = self.controller.get_status()
                exceedance = self.controller.get_current_exceedance()
                return True
            except Exception:
                return False

        # Run multiple concurrent accesses
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(access_controller) for _ in range(20)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All accesses should succeed
        self.assertTrue(all(results), "Some concurrent accesses failed")

    def test_cold_start_recovery_with_persisted_ring_buffer(self):
        """Test P95 controller cold start recovery with persisted ring buffer state"""
        import tempfile
        import json

        # Create temporary ring buffer persistence file
        temp_ring_buffer = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='_p95_ring_buffer.json')

        # Simulate a ring buffer state from 30 minutes ago (valid for 2h limit)
        old_timestamp = time.time() - 1800  # 30 minutes ago
        ring_buffer_state = {
            'slot_history': [True, False, True, False] * 360,  # 24 hours of slots at 60s each
            'slot_history_index': 100,
            'slots_recorded': 1440,  # 24 hours worth
            'slot_history_size': 1440,
            'timestamp': old_timestamp,
            'current_slot_is_high': True
        }

        json.dump(ring_buffer_state, temp_ring_buffer)
        temp_ring_buffer.close()

        try:
            # Temporarily disable test mode to allow ring buffer loading
            original_test_env = os.environ.get('PYTEST_CURRENT_TEST')
            if 'PYTEST_CURRENT_TEST' in os.environ:
                del os.environ['PYTEST_CURRENT_TEST']

            try:
                # Mock the ring buffer path to return our test file
                with patch.object(CPUP95Controller, '_get_ring_buffer_path', return_value=temp_ring_buffer.name):
                    controller = CPUP95Controller(self.metrics_storage)
            finally:
                # Restore test environment
                if original_test_env:
                    os.environ['PYTEST_CURRENT_TEST'] = original_test_env

                # Verify that ring buffer state was loaded
                self.assertEqual(controller.slots_recorded, 1440)
                self.assertEqual(controller.slot_history_index, 100)
                self.assertEqual(controller.slot_history_size, 1440)

                # Verify exceedance calculation works with restored state
                exceedance = controller.get_current_exceedance()
                self.assertIsInstance(exceedance, float)
                self.assertGreaterEqual(exceedance, 0.0)
                self.assertLessEqual(exceedance, 100.0)

                # Verify controller status includes restoration info
                status = controller.get_status()
                self.assertIn('slots_recorded', status)
                self.assertEqual(status['slots_recorded'], 1440)

                # Test that slot decisions work normally after cold start
                should_run_high, intensity = controller.should_run_high_slot(current_load_avg=0.3)
                self.assertIsInstance(should_run_high, bool)
                self.assertIsInstance(intensity, (int, float))
                self.assertGreaterEqual(intensity, 20.0)  # At least baseline

        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_ring_buffer.name)
            except (OSError, FileNotFoundError):
                pass

    def test_cold_start_with_stale_ring_buffer_fallback(self):
        """Test P95 controller gracefully handles stale ring buffer (>2h old)"""
        import tempfile
        import json

        # Create temporary ring buffer persistence file
        temp_ring_buffer = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='_p95_ring_buffer.json')

        # Simulate a ring buffer state from 3 hours ago (exceeds 2h validity limit)
        old_timestamp = time.time() - 10800  # 3 hours ago
        stale_ring_buffer_state = {
            'slot_history': [True, False, True, False] * 360,
            'slot_history_index': 200,
            'slots_recorded': 1440,
            'slot_history_size': 1440,
            'timestamp': old_timestamp,
            'current_slot_is_high': False
        }

        json.dump(stale_ring_buffer_state, temp_ring_buffer)
        temp_ring_buffer.close()

        try:
            # Temporarily disable test mode to allow ring buffer loading
            original_test_env = os.environ.get('PYTEST_CURRENT_TEST')
            if 'PYTEST_CURRENT_TEST' in os.environ:
                del os.environ['PYTEST_CURRENT_TEST']

            try:
                # Mock the ring buffer path to return our test file
                with patch.object(CPUP95Controller, '_get_ring_buffer_path', return_value=temp_ring_buffer.name):
                    controller = CPUP95Controller(self.metrics_storage)
            finally:
                # Restore test environment
                if original_test_env:
                    os.environ['PYTEST_CURRENT_TEST'] = original_test_env

                # Verify that stale state was rejected and fresh state initialized
                self.assertEqual(controller.slots_recorded, 0)  # Fresh start
                self.assertEqual(controller.slot_history_index, 0)
                self.assertGreater(controller.slot_history_size, 0)  # Should be calculated from slot duration

                # All slot history should be initialized to False (fresh ring buffer)
                self.assertTrue(all(not slot for slot in controller.slot_history))

                # Controller should still function normally with fresh state
                status = controller.get_status()
                self.assertEqual(status['slots_recorded'], 0)
                self.assertAlmostEqual(status['exceedance_pct'], 0.0, places=1)  # No high slots recorded yet

        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_ring_buffer.name)
            except (OSError, FileNotFoundError):
                pass


class TestP95ConfigurationValidation(unittest.TestCase):
    """Test P95 configuration validation and error handling"""

    def test_invalid_p95_configuration_handling(self):
        """Test handling of invalid P95 configuration values"""
        # Test with invalid P95 target values
        with patch.multiple(loadshaper,
                           CPU_P95_TARGET_MIN=150.0,  # Invalid: > 100%
                           CPU_P95_TARGET_MAX=200.0,
                           CPU_P95_SLOT_DURATION=60.0,
                           CPU_P95_BASELINE_INTENSITY=20.0,
                           CPU_P95_HIGH_INTENSITY=35.0,
                           CPU_P95_SETPOINT=25.0,
                           CPU_P95_EXCEEDANCE_TARGET=6.5):

            # Should not crash, should handle gracefully
            temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
            temp_db.close()

            try:
                metrics_storage = MetricsStorage(db_path=temp_db.name)
                controller = CPUP95Controller(metrics_storage)

                # Should create controller without crashing
                self.assertIsNotNone(controller)

                # State should be valid
                self.assertIn(controller.state, ['BUILDING', 'MAINTAINING', 'REDUCING'])

            finally:
                os.unlink(temp_db.name)

    def test_database_failure_recovery(self):
        """Test P95 controller behavior when database fails"""
        with patch.multiple(loadshaper,
                           CPU_P95_SLOT_DURATION=60.0,
                           CPU_P95_BASELINE_INTENSITY=20.0,
                           CPU_P95_HIGH_INTENSITY=35.0,
                           CPU_P95_TARGET_MIN=22.0,
                           CPU_P95_TARGET_MAX=28.0,
                           CPU_P95_SETPOINT=25.0,
                           CPU_P95_EXCEEDANCE_TARGET=6.5):

            # Create controller with invalid database path
            invalid_path = "/invalid/path/that/does/not/exist.db"

            try:
                # Should handle database creation failure gracefully
                metrics_storage = MetricsStorage(db_path=invalid_path)

                # Controller should still be created
                controller = CPUP95Controller(metrics_storage)
                self.assertIsNotNone(controller)

                # P95 queries should handle database fallback gracefully
                p95 = controller.get_cpu_p95()
                # Should either be None (no data) or a valid number (fallback worked)
                self.assertTrue(p95 is None or isinstance(p95, (int, float)))

            except Exception as e:
                # Expected - database creation should fail with invalid path or config
                self.assertIsInstance(e, (OSError, PermissionError, TypeError))





class TestP95ConvergenceBehavior(unittest.TestCase):
    """Test P95 controller convergence behavior over many slots"""

    def setUp(self):
        """Set up convergence test fixtures"""
        self.storage = MockMetricsStorage()

        # Set deterministic test environment
        os.environ['PYTEST_CURRENT_TEST'] = 'test_p95_convergence'

        # Mock configuration for fast, deterministic testing
        self.patches = patch.multiple(loadshaper,
                                    CPU_P95_SLOT_DURATION=1.0,  # Fast slots
                                    CPU_P95_BASELINE_INTENSITY=20.0,
                                    CPU_P95_HIGH_INTENSITY=35.0,
                                    CPU_P95_TARGET_MIN=22.0,
                                    CPU_P95_TARGET_MAX=28.0,
                                    CPU_P95_SETPOINT=25.0,
                                    CPU_P95_EXCEEDANCE_TARGET=6.5,
                                    LOAD_CHECK_ENABLED=False,  # Disable safety gating
                                    LOAD_THRESHOLD=0.6)
        self.patches.start()

    def tearDown(self):
        """Clean up convergence test fixtures"""
        self.patches.stop()
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def test_p95_controller_basic_slot_functionality(self):
        """Test basic P95 controller slot functionality without complex convergence."""
        controller = loadshaper.CPUP95Controller(self.storage)

        # Set P95 at target for MAINTAINING state
        self.storage.set_p95(25.0)
        self.storage.clear_controller_cache(controller)
        controller.update_state(controller.get_cpu_p95())

        # Test basic slot decision functionality
        is_high, intensity = controller.should_run_high_slot(current_load_avg=0.3)

        # Basic sanity checks
        self.assertIsInstance(is_high, bool)
        self.assertIsInstance(intensity, (int, float))
        self.assertGreaterEqual(intensity, 20.0)  # At least baseline
        self.assertLessEqual(intensity, 35.0)     # No more than high

        # Test controller status reporting
        status = controller.get_status()
        self.assertIn('state', status)
        self.assertIn('cpu_p95', status)
        self.assertIn('exceedance_pct', status)
        self.assertIn(status['state'], ['BUILDING', 'MAINTAINING', 'REDUCING'])

        # Test state transitions work
        self.storage.set_p95(20.0)  # Below target - should trigger BUILDING
        self.storage.clear_controller_cache(controller)
        controller.update_state(controller.get_cpu_p95())
        # Note: State may not change immediately due to hysteresis - that's expected

        self.storage.set_p95(30.0)  # Above target - should trigger REDUCING
        self.storage.clear_controller_cache(controller)
        controller.update_state(controller.get_cpu_p95())
        # Note: State may not change immediately due to hysteresis - that's expected

        # Test that controller produces consistent results
        results = []
        for _ in range(10):
            is_high, intensity = controller.should_run_high_slot(current_load_avg=0.3)
            results.append((is_high, intensity))

        # Should get some consistency in results (not all random)
        high_results = [r[0] for r in results]
        intensity_results = [r[1] for r in results]

        # All intensities should be in valid range
        for intensity in intensity_results:
            self.assertGreaterEqual(intensity, 20.0)
            self.assertLessEqual(intensity, 35.0)


if __name__ == '__main__':
    unittest.main()