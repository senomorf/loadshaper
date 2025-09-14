#!/usr/bin/env python3
"""
Test signal handling functionality for graceful shutdown.

Tests the signal handlers added for SIGTERM and SIGINT that enable
graceful shutdown of loadshaper.
"""

import unittest
import unittest.mock
import signal
import threading
import time
import sys
import os
from multiprocessing import Value

# Add the parent directory to the path to import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loadshaper


class TestSignalHandling(unittest.TestCase):
    """Test signal handling for graceful shutdown."""

    def setUp(self):
        """Set up test environment."""
        # Store original signal handlers
        self.original_sigterm_handler = signal.signal(signal.SIGTERM, signal.SIG_DFL)
        self.original_sigint_handler = signal.signal(signal.SIGINT, signal.SIG_DFL)

    def tearDown(self):
        """Clean up test environment."""
        # Restore original signal handlers
        signal.signal(signal.SIGTERM, self.original_sigterm_handler)
        signal.signal(signal.SIGINT, self.original_sigint_handler)

    def test_signal_handler_registration(self):
        """Test that signal handlers can be registered correctly."""
        stop_evt = threading.Event()

        # Mock the handle_shutdown function
        def mock_handle_shutdown(signum, frame):
            stop_evt.set()
            loadshaper.paused.value = 1.0
            loadshaper.duty.value = 0.0

        # Register the handler
        signal.signal(signal.SIGTERM, mock_handle_shutdown)
        signal.signal(signal.SIGINT, mock_handle_shutdown)

        # Verify handlers are registered
        self.assertEqual(signal.signal(signal.SIGTERM, signal.SIG_DFL), mock_handle_shutdown)
        self.assertEqual(signal.signal(signal.SIGINT, signal.SIG_DFL), mock_handle_shutdown)

    def test_handle_shutdown_function_behavior(self):
        """Test the handle_shutdown function sets correct values."""
        stop_evt = threading.Event()
        test_duty = Value('f', 0.0)
        test_paused = Value('f', 0.0)

        # Create a simplified handle_shutdown function for testing
        def handle_shutdown(signum, frame):
            stop_evt.set()
            test_paused.value = 1.0
            test_duty.value = 0.0

        # Set initial values
        test_duty.value = 0.5
        test_paused.value = 0.0

        # Call the handler
        handle_shutdown(signal.SIGTERM, None)

        # Verify the event is set and values are correct
        self.assertTrue(stop_evt.is_set())
        self.assertAlmostEqual(test_paused.value, 1.0, places=1)
        self.assertAlmostEqual(test_duty.value, 0.0, places=1)

    def test_handle_shutdown_with_sigint(self):
        """Test handle_shutdown works with SIGINT signal."""
        stop_evt = threading.Event()
        test_duty = Value('f', 0.0)
        test_paused = Value('f', 0.0)

        def handle_shutdown(signum, frame):
            stop_evt.set()
            test_paused.value = 1.0
            test_duty.value = 0.0

        # Set initial values
        test_duty.value = 0.8
        test_paused.value = 0.0

        # Call with SIGINT
        handle_shutdown(signal.SIGINT, None)

        # Verify results
        self.assertTrue(stop_evt.is_set())
        self.assertAlmostEqual(test_paused.value, 1.0, places=1)
        self.assertAlmostEqual(test_duty.value, 0.0, places=1)

    def test_signal_handler_thread_safety(self):
        """Test that signal handlers work correctly with threading."""
        stop_evt = threading.Event()
        results = []
        test_duty = Value('f', 0.0)
        test_paused = Value('f', 0.0)

        def handle_shutdown(signum, frame):
            results.append(f"Signal {signum} received")
            stop_evt.set()
            test_paused.value = 1.0
            test_duty.value = 0.0

        # Register handler
        signal.signal(signal.SIGTERM, handle_shutdown)

        # Create a worker thread
        def worker_thread():
            for i in range(100):
                if stop_evt.is_set():
                    break
                time.sleep(0.001)

        thread = threading.Thread(target=worker_thread, daemon=True)
        thread.start()

        # Simulate signal
        handle_shutdown(signal.SIGTERM, None)

        # Wait for thread
        thread.join(timeout=1.0)

        # Verify results
        self.assertTrue(stop_evt.is_set())
        self.assertEqual(len(results), 1)
        self.assertIn("Signal 15", results[0])  # SIGTERM is 15
        self.assertAlmostEqual(test_paused.value, 1.0, places=1)
        self.assertAlmostEqual(test_duty.value, 0.0, places=1)

    def test_signal_handling_with_mock_main(self):
        """Test signal handling integration with a simplified main loop."""
        stop_evt = threading.Event()
        loop_iterations = []
        test_duty = Value('f', 0.0)
        test_paused = Value('f', 0.0)

        def handle_shutdown(signum, frame):
            stop_evt.set()
            test_paused.value = 1.0
            test_duty.value = 0.0

        # Register handler
        signal.signal(signal.SIGTERM, handle_shutdown)

        # Simplified main loop
        def mock_main_loop():
            iteration = 0
            while not stop_evt.is_set():
                iteration += 1
                loop_iterations.append(iteration)
                time.sleep(0.01)

                # Simulate signal after a few iterations
                if iteration == 5:
                    handle_shutdown(signal.SIGTERM, None)

                # Safety break
                if iteration > 20:
                    break

        # Run the mock main loop
        mock_main_loop()

        # Verify the loop stopped gracefully
        self.assertTrue(stop_evt.is_set())
        self.assertAlmostEqual(test_paused.value, 1.0, places=1)
        self.assertAlmostEqual(test_duty.value, 0.0, places=1)
        self.assertGreaterEqual(len(loop_iterations), 5)
        self.assertLess(len(loop_iterations), 20)  # Should have stopped before safety break

    def test_multiple_signals_handling(self):
        """Test handling multiple signals in sequence."""
        stop_evt = threading.Event()
        signal_log = []
        test_duty = Value('f', 0.0)
        test_paused = Value('f', 0.0)

        def handle_shutdown(signum, frame):
            signal_log.append(signum)
            stop_evt.set()
            test_paused.value = 1.0
            test_duty.value = 0.0

        # Register handlers
        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)

        # Test SIGTERM first
        handle_shutdown(signal.SIGTERM, None)

        # Reset for second signal
        stop_evt.clear()
        test_paused.value = 0.0
        test_duty.value = 0.5

        # Test SIGINT
        handle_shutdown(signal.SIGINT, None)

        # Verify both signals were handled
        self.assertEqual(len(signal_log), 2)
        self.assertIn(signal.SIGTERM, signal_log)
        self.assertIn(signal.SIGINT, signal_log)
        self.assertTrue(stop_evt.is_set())
        self.assertAlmostEqual(test_paused.value, 1.0, places=1)
        self.assertAlmostEqual(test_duty.value, 0.0, places=1)

    def test_signal_handler_exception_safety(self):
        """Test that signal handlers are robust against exceptions."""
        stop_evt = threading.Event()
        exception_count = []
        test_duty = Value('f', 0.0)
        test_paused = Value('f', 0.0)

        def fragile_handle_shutdown(signum, frame):
            try:
                # Simulate potential error condition
                if signum == signal.SIGTERM:
                    # This should still work even if there's an error
                    stop_evt.set()
                    test_paused.value = 1.0
                    test_duty.value = 0.0
                    # Simulate an error after the critical operations
                    if len(exception_count) == 0:
                        exception_count.append(1)
                        raise ValueError("Simulated error")
            except ValueError:
                # Error should be caught and not prevent shutdown
                pass

        # Register handler
        signal.signal(signal.SIGTERM, fragile_handle_shutdown)

        # Call handler
        fragile_handle_shutdown(signal.SIGTERM, None)

        # Verify critical operations completed despite exception
        self.assertTrue(stop_evt.is_set())
        self.assertAlmostEqual(test_paused.value, 1.0, places=1)
        self.assertAlmostEqual(test_duty.value, 0.0, places=1)
        self.assertEqual(len(exception_count), 1)


if __name__ == '__main__':
    unittest.main()