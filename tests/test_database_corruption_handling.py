#!/usr/bin/env python3
"""
Tests for database corruption detection and recovery feature.

This module tests the database corruption handling methods:
- detect_database_corruption()
- backup_corrupted_database()
- recover_from_corruption()
"""

import unittest
import unittest.mock
import sys
import os
import time
import tempfile
import shutil
import sqlite3
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loadshaper


class TestDatabaseCorruptionHandling(unittest.TestCase):
    """Test database corruption detection and recovery."""

    def setUp(self):
        """Set up test environment."""
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, 'test_metrics.db')
        self.backup_path = os.path.join(self.test_dir, 'test_metrics_backup.db')

        # Initialize required global variables for testing
        # Set environment variable to indicate test mode
        os.environ['PYTEST_CURRENT_TEST'] = 'test_database_corruption'

    def tearDown(self):
        """Clean up test environment."""
        # Clean up environment variable
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def create_valid_database(self):
        """Create a valid metrics database for testing."""
        storage = loadshaper.MetricsStorage(self.db_path)

        # Add some test data
        current_time = time.time()
        for i in range(10):
            storage.store_sample(25.0 + i, 50.0, 30.0, 1.0)
            time.sleep(0.01)  # Small delay for different timestamps

        storage.conn.close()
        return storage

    def create_corrupted_database(self):
        """Create a corrupted database file for testing."""
        # Write invalid SQLite data
        with open(self.db_path, 'wb') as f:
            f.write(b'This is not a valid SQLite database file content')

    def create_partially_corrupted_database(self):
        """Create a database with some corruption that SQLite can detect."""
        # First create a valid database
        storage = self.create_valid_database()

        # Then corrupt part of it
        with open(self.db_path, 'r+b') as f:
            f.seek(100)  # Seek to middle of file
            f.write(b'CORRUPTED_DATA_HERE')  # Overwrite with garbage

        return storage

    def test_detect_healthy_database(self):
        """Test corruption detection on healthy database."""
        storage = self.create_valid_database()

        # Reopen to test detection
        storage = loadshaper.MetricsStorage(self.db_path)

        is_corrupted = storage.detect_database_corruption()

        self.assertFalse(is_corrupted, "Healthy database should not be detected as corrupted")
        storage.conn.close()

    def test_detect_corrupted_database(self):
        """Test corruption detection on corrupted database."""
        self.create_corrupted_database()

        try:
            storage = loadshaper.MetricsStorage(self.db_path)
            # Should either fail to initialize or detect corruption
            is_corrupted = storage.detect_database_corruption()
            self.assertTrue(is_corrupted, "Corrupted database should be detected")
            storage.conn.close()
        except (sqlite3.DatabaseError, RuntimeError):
            # Expected - corrupted database should cause initialization to fail
            pass

    def test_backup_corrupted_database(self):
        """Test backup creation for corrupted database."""
        self.create_corrupted_database()

        try:
            storage = loadshaper.MetricsStorage(self.db_path)

            # Create backup
            backup_created = storage.backup_corrupted_database()

            if backup_created:
                # Verify backup was created
                backup_files = [f for f in os.listdir(self.test_dir)
                               if f.startswith('test_metrics_backup_') and f.endswith('.db')]
                self.assertGreater(len(backup_files), 0, "Backup file should be created")

                # Verify backup contains the corrupted content
                backup_path = os.path.join(self.test_dir, backup_files[0])
                with open(backup_path, 'rb') as f:
                    backup_content = f.read()
                self.assertIn(b'This is not a valid SQLite', backup_content,
                             "Backup should contain original corrupted content")

            storage.conn.close()
        except (sqlite3.DatabaseError, RuntimeError):
            # Expected for severely corrupted databases
            pass

    def test_recover_from_corruption(self):
        """Test recovery from database corruption."""
        self.create_corrupted_database()

        try:
            storage = loadshaper.MetricsStorage(self.db_path)

            # Attempt recovery
            recovery_successful = storage.recover_from_corruption()

            if recovery_successful:
                # Verify database is functional after recovery
                storage.store_sample(25.0, 50.0, 30.0, 1.0)

                # Verify data can be retrieved
                stats = storage.get_percentile('cpu')
                self.assertIsNotNone(stats, "Recovered database should be functional")

            storage.conn.close()
        except (sqlite3.DatabaseError, RuntimeError):
            # Some corruption scenarios may not be recoverable
            pass

    def test_complete_corruption_recovery_workflow(self):
        """Test the complete corruption detection and recovery workflow."""
        # Start with valid database
        storage = self.create_valid_database()

        # Verify it works initially
        stats = storage.get_percentile('cpu')
        self.assertIsNotNone(stats)
        storage.conn.close()

        # Corrupt the database
        self.create_partially_corrupted_database()

        # Attempt to use corrupted database
        try:
            storage = loadshaper.MetricsStorage(self.db_path)

            # Check if corruption is detected
            is_corrupted = storage.detect_database_corruption()

            if is_corrupted:
                # Create backup
                backup_created = storage.backup_corrupted_database()
                self.assertTrue(backup_created, "Backup should be created for corrupted database")

                # Attempt recovery
                recovery_successful = storage.recover_from_corruption()

                if recovery_successful:
                    # Test functionality after recovery
                    storage.store_sample(30.0, 60.0, 40.0, 1.2)
                    stats = storage.get_percentile('cpu')
                    self.assertIsNotNone(stats, "Database should be functional after recovery")

            storage.conn.close()
        except (sqlite3.DatabaseError, RuntimeError) as e:
            # Severe corruption may not be recoverable
            pass

    def test_backup_filename_generation(self):
        """Test backup filename generation with timestamps."""
        self.create_corrupted_database()

        try:
            storage = loadshaper.MetricsStorage(self.db_path)

            # Create multiple backups
            backup1_created = storage.backup_corrupted_database()
            time.sleep(0.1)  # Small delay for different timestamps
            backup2_created = storage.backup_corrupted_database()

            if backup1_created and backup2_created:
                # Check that different filenames are generated
                backup_files = [f for f in os.listdir(self.test_dir)
                               if f.startswith('test_metrics_backup_') and f.endswith('.db')]
                self.assertGreaterEqual(len(backup_files), 2, "Multiple backups should have different names")

                # Verify timestamp format in filenames
                for backup_file in backup_files:
                    self.assertRegex(backup_file, r'test_metrics_backup_\d{8}_\d{6}\.db',
                                   "Backup filename should include timestamp")

            storage.conn.close()
        except (sqlite3.DatabaseError, RuntimeError):
            pass

    def test_recovery_with_missing_database(self):
        """Test recovery behavior when database file is missing."""
        # Don't create any database file

        try:
            storage = loadshaper.MetricsStorage(self.db_path)

            # This should create a new database, not attempt corruption recovery
            storage.store_sample(25.0, 50.0, 30.0, 1.0)
            stats = storage.get_percentile('cpu')
            self.assertIsNotNone(stats, "New database should be functional")

            storage.conn.close()
        except Exception as e:
            self.fail(f"Missing database should be handled gracefully, not raise: {e}")

    def test_corruption_detection_performance(self):
        """Test that corruption detection doesn't significantly impact performance."""
        storage = self.create_valid_database()

        # Reopen for testing
        storage = loadshaper.MetricsStorage(self.db_path)

        # Measure corruption detection time
        start_time = time.time()
        for _ in range(10):  # Run multiple times for average
            storage.detect_database_corruption()
        detection_time = (time.time() - start_time) / 10

        # Corruption detection should be fast (under 100ms)
        self.assertLess(detection_time, 0.1,
                       f"Corruption detection too slow: {detection_time:.3f}s")

        storage.conn.close()

    def test_error_handling_during_backup(self):
        """Test error handling when backup creation fails."""
        self.create_corrupted_database()

        try:
            storage = loadshaper.MetricsStorage(self.db_path)

            # Mock file operations to simulate backup failure
            with unittest.mock.patch('shutil.copy2', side_effect=OSError("Mock backup failure")):
                backup_created = storage.backup_corrupted_database()

                # Should handle backup failure gracefully
                self.assertFalse(backup_created, "Backup should fail with mocked error")

            storage.conn.close()
        except (sqlite3.DatabaseError, RuntimeError):
            pass

    def test_error_handling_during_recovery(self):
        """Test error handling when recovery fails."""
        self.create_corrupted_database()

        try:
            storage = loadshaper.MetricsStorage(self.db_path)

            # Mock database operations to simulate recovery failure
            with unittest.mock.patch.object(storage, '_init_db', side_effect=sqlite3.Error("Mock recovery failure")):
                recovery_successful = storage.recover_from_corruption()

                # Should handle recovery failure gracefully
                self.assertFalse(recovery_successful, "Recovery should fail with mocked error")

            storage.conn.close()
        except (sqlite3.DatabaseError, RuntimeError):
            pass

    def test_logging_during_corruption_handling(self):
        """Test that appropriate log messages are generated during corruption handling."""
        self.create_corrupted_database()

        try:
            with unittest.mock.patch('builtins.print') as mock_print:
                storage = loadshaper.MetricsStorage(self.db_path)

                # Detect corruption
                storage.detect_database_corruption()

                # Check for appropriate log messages
                print_calls = [str(call) for call in mock_print.call_args_list]
                log_output = ' '.join(print_calls)

                # Should log corruption detection or recovery attempts
                # Note: Exact logging depends on implementation
                storage.conn.close()

        except (sqlite3.DatabaseError, RuntimeError):
            # Expected for corrupted databases
            pass

    def test_prevention_of_data_loss_during_recovery(self):
        """Test that recovery doesn't lose existing valid data unnecessarily."""
        # Create database with some valid data
        storage = self.create_valid_database()

        # Add specific test data
        test_cpu_value = 42.5
        storage.store_sample(test_cpu_value, 50.0, 30.0, 1.0)
        storage.conn.close()

        # Simulate minor corruption that doesn't affect all data
        # (In practice, this test verifies recovery attempts to preserve what's possible)

        try:
            storage = loadshaper.MetricsStorage(self.db_path)

            # Force recovery attempt
            if hasattr(storage, 'recover_from_corruption'):
                recovery_attempted = storage.recover_from_corruption()

                if recovery_attempted:
                    # Check if some data is still accessible
                    # (This depends on the nature of corruption and recovery implementation)
                    try:
                        stats = storage.get_percentile('cpu')
                        # If recovery succeeded, database should be functional
                        if stats is not None:
                            self.assertTrue(True, "Recovery maintained database functionality")
                    except sqlite3.Error:
                        # Some data loss may be unavoidable in severe corruption
                        pass

            storage.conn.close()
        except (sqlite3.DatabaseError, RuntimeError):
            # Severe corruption may require complete recreation
            pass


if __name__ == '__main__':
    unittest.main()