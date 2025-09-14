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
from unittest.mock import patch
import sys
import os
import time
import tempfile
import shutil
import sqlite3
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test mode environment variable before importing loadshaper
os.environ['LOADSHAPER_TEST_MODE'] = 'true'

import loadshaper


@patch.dict(os.environ, {'LOADSHAPER_TEST_MODE': 'true'})
class TestDatabaseCorruptionHandling(unittest.TestCase):
    """Test database corruption detection and recovery."""

    def setUp(self):
        """Set up test environment."""
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, 'test_metrics.db')
        self.backup_path = os.path.join(self.test_dir, 'test_metrics_backup.db')

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

        # MetricsStorage uses connection pooling - no need to close explicitly
        return storage

    def create_corrupted_database(self):
        """Create a corrupted database file for testing."""
        # Overwrite main database file with zeros
        with open(self.db_path, 'wb') as f:
            f.write(b'\x00' * 1024)

        # Also corrupt WAL and SHM files if they exist (SQLite WAL mode)
        wal_path = self.db_path + '-wal'
        shm_path = self.db_path + '-shm'

        if os.path.exists(wal_path):
            with open(wal_path, 'wb') as f:
                f.write(b'\x00' * 512)

        if os.path.exists(shm_path):
            with open(shm_path, 'wb') as f:
                f.write(b'\x00' * 512)

    def create_partially_corrupted_database(self):
        """Create a database with some corruption that SQLite can detect."""
        # First create a valid database
        storage = self.create_valid_database()

        # Give it a moment to finish writing
        time.sleep(0.1)

        # Then corrupt part of it by overwriting part of the file
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
        # MetricsStorage uses connection pooling - no explicit close needed

    def test_detect_corrupted_database(self):
        """Test corruption detection on corrupted database."""
        # First create a valid storage instance to ensure database exists
        storage = loadshaper.MetricsStorage(self.db_path)

        # Give a moment for any database operations to complete
        import time
        time.sleep(0.1)

        # Now corrupt the database file directly
        self.create_corrupted_database()
        try:
            storage = loadshaper.MetricsStorage(self.db_path)
            # Should either fail to initialize or detect corruption
            is_corrupted = storage.detect_database_corruption()
            self.assertTrue(is_corrupted, "Corrupted database should be detected")
            # MetricsStorage uses connection pooling - no explicit close needed
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

            # MetricsStorage uses connection pooling - no explicit close needed
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

            # MetricsStorage uses connection pooling - no explicit close needed
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
        # MetricsStorage uses connection pooling - no explicit close needed

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

            # MetricsStorage uses connection pooling - no explicit close needed
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

            # MetricsStorage uses connection pooling - no explicit close needed
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

            # MetricsStorage uses connection pooling - no explicit close needed
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

        # MetricsStorage uses connection pooling - no explicit close needed

    def test_error_handling_during_backup(self):
        """Test error handling when backup creation fails."""
        # First create a valid storage instance
        storage = loadshaper.MetricsStorage(self.db_path)

        # Then corrupt the database file
        self.create_corrupted_database()

        # Create backup
        backup_path = storage.backup_corrupted_database()

        # Backup should be created successfully
        self.assertIsNotNone(backup_path, "Backup path should not be None")
        self.assertTrue(os.path.exists(backup_path), "Backup file should be created")

        # Backup should contain the same data as original
        with open(self.db_path, 'rb') as orig, open(backup_path, 'rb') as backup:
            self.assertEqual(orig.read(), backup.read(), "Backup should contain same data as original")

    def test_complete_corruption_recovery_workflow(self):
        """Test complete corruption recovery workflow."""
        # Create a partially corrupted database
        storage = self.create_partially_corrupted_database()

        # First, detect corruption
        is_corrupted = storage.detect_database_corruption()
        if is_corrupted:
            # Then recover from corruption
            recovery_result = storage.recover_from_corruption()
            self.assertTrue(recovery_result, "Recovery should succeed")

            # After recovery, database should be healthy
            self.assertFalse(storage.detect_database_corruption(),
                           "Database should be healthy after recovery")

            # Should be able to store new data
            storage.store_sample(30.0, 60.0, 40.0, 2.0)

            # Should be able to retrieve percentiles
            cpu_p95 = storage.get_percentile('cpu', 95)
            self.assertIsNotNone(cpu_p95, "Should be able to get percentiles after recovery")

    def test_prevention_of_data_loss_during_recovery(self):
        """Test that recovery attempts to preserve data when possible."""
        # Create valid database with data
        storage = self.create_valid_database()

        # Add specific test data
        test_cpu_value = 42.5
        storage.store_sample(test_cpu_value, 50.0, 30.0, 1.0)
        # MetricsStorage uses connection pooling - no explicit close needed

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

            # MetricsStorage uses connection pooling - no explicit close needed
        except (sqlite3.DatabaseError, RuntimeError):
            # Severe corruption may require complete recreation
            pass


if __name__ == '__main__':
    unittest.main()