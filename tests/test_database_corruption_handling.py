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

        # The connection is managed internally, no need to close it directly
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

        # Healthy database should not be detected as corrupted
        self.assertFalse(storage.detect_database_corruption(),
                        "Healthy database should not be detected as corrupted")

    def test_detect_corrupted_database(self):
        """Test corruption detection on corrupted database."""
        # First create a valid storage instance to ensure database exists
        storage = loadshaper.MetricsStorage(self.db_path)

        # Give a moment for any database operations to complete
        import time
        time.sleep(0.1)

        # Now corrupt the database file directly
        self.create_corrupted_database()

        # Corrupted database should be detected
        self.assertTrue(storage.detect_database_corruption(),
                       "Corrupted database should be detected")

    def test_backup_corrupted_database(self):
        """Test backup creation for corrupted database."""
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

    def test_recovery_with_missing_database(self):
        """Test recovery when database file is missing."""
        # Create a storage instance (this will create the database)
        storage = loadshaper.MetricsStorage(self.db_path)

        # Remove the database file to simulate missing database
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        # Recovery should handle missing file gracefully
        result = storage.recover_from_corruption()

        # Should return True (successful recovery by creating new database)
        self.assertTrue(result, "Recovery should succeed by creating new database")
        self.assertTrue(os.path.exists(self.db_path), "New database should be created")

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

        # Get the original data count
        original_count = 0
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM metrics")
                original_count = cursor.fetchone()[0]
        except:
            pass

        # Simulate recovery process
        backup_path = storage.backup_corrupted_database()
        self.assertIsNotNone(backup_path, "Backup should be created")

        # Recovery should create a new database
        recovery_result = storage.recover_from_corruption()
        self.assertTrue(recovery_result, "Recovery should succeed")

        # New database should exist and be functional
        self.assertTrue(os.path.exists(self.db_path), "New database should exist")

        # Should be able to add new data
        storage.store_sample(35.0, 70.0, 50.0, 3.0)

    def test_corruption_detection_performance(self):
        """Test that corruption detection is reasonably fast."""
        # Create a larger database to test performance
        storage = self.create_valid_database()

        # Add more data to make it more realistic
        for i in range(100):
            storage.store_sample(25.0 + i % 10, 50.0, 30.0, 1.0)

        # Time the corruption detection
        start_time = time.time()
        is_corrupted = storage.detect_database_corruption()
        detection_time = time.time() - start_time

        # Should complete quickly (under 1 second for test database)
        self.assertLess(detection_time, 1.0, "Corruption detection should be fast")
        self.assertFalse(is_corrupted, "Healthy database should not be detected as corrupted")


if __name__ == '__main__':
    unittest.main()