import sys
import os
import time
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from loadshaper import MetricsStorage


class TestMetricsStorage:
    @pytest.fixture
    def temp_db(self):
        """Create a temporary database file for testing."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        yield db_path
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)

    def test_init_creates_database(self, temp_db):
        """Test that MetricsStorage properly initializes the database."""
        storage = MetricsStorage(temp_db)
        assert storage.db_path == temp_db
        assert os.path.exists(temp_db)

    def test_init_fails_on_permission_error(self):
        """Test that MetricsStorage fails when path is not writable (no fallback)."""
        # Try to create storage with a non-existent/non-writable path
        with pytest.raises(FileNotFoundError) as exc_info:
            MetricsStorage("/non/existent/path/metrics.db")
        assert "A persistent volume must be mounted" in str(exc_info.value)

    def test_store_sample_basic(self, temp_db):
        """Test basic sample storage functionality."""
        storage = MetricsStorage(temp_db)
        
        result = storage.store_sample(50.0, 70.0, 15.5, 0.8)
        assert result is True
        
        # Check that we have one sample
        count = storage.get_sample_count()
        assert count == 1

    def test_store_multiple_samples(self, temp_db):
        """Test storing multiple samples over time."""
        storage = MetricsStorage(temp_db)
        
        # Store samples with slight time gaps
        samples = [
            (30.0, 50.0, 10.0, 0.5),
            (40.0, 60.0, 15.0, 0.7),
            (35.0, 55.0, 12.0, 0.6),
            (45.0, 65.0, 18.0, 0.9),
            (50.0, 70.0, 20.0, 1.0)
        ]
        
        for cpu, mem, net, load in samples:
            storage.store_sample(cpu, mem, net, load)
            time.sleep(0.001)  # Small delay to ensure different timestamps
        
        count = storage.get_sample_count()
        assert count == 5

    def test_get_percentile_invalid_metric(self, temp_db):
        """Test percentile calculation with invalid metric name."""
        storage = MetricsStorage(temp_db)
        storage.store_sample(50.0, 70.0, 15.5, 0.8)
        
        result = storage.get_percentile('invalid_metric')
        assert result is None

    def test_get_percentile_no_data(self, temp_db):
        """Test percentile calculation when no data exists."""
        storage = MetricsStorage(temp_db)
        
        result = storage.get_percentile('cpu')
        assert result is None

    def test_get_percentile_single_value(self, temp_db):
        """Test percentile calculation with a single value."""
        storage = MetricsStorage(temp_db)
        storage.store_sample(50.0, 70.0, 15.5, 0.8)
        
        result = storage.get_percentile('cpu', 95.0)
        assert result == 50.0

    def test_get_percentile_calculation(self, temp_db):
        """Test percentile calculation with known data."""
        storage = MetricsStorage(temp_db)
        
        # Store 100 samples with values 1.0 to 100.0
        for i in range(1, 101):
            storage.store_sample(float(i), float(i), float(i), float(i)/100.0)
            time.sleep(0.001)
        
        # 95th percentile of 1-100 should be 95
        cpu_p95 = storage.get_percentile('cpu', 95.0)
        assert cpu_p95 == pytest.approx(95.0, rel=1e-2)
        
        # 50th percentile (median) should be around 50.5
        cpu_p50 = storage.get_percentile('cpu', 50.0)
        assert cpu_p50 == pytest.approx(50.5, rel=1e-2)
        
        # Test different metrics
        mem_p95 = storage.get_percentile('mem', 95.0)
        assert mem_p95 == pytest.approx(95.0, rel=1e-2)
        
        net_p95 = storage.get_percentile('net', 95.0)
        assert net_p95 == pytest.approx(95.0, rel=1e-2)
        
        load_p95 = storage.get_percentile('load', 95.0)
        assert load_p95 == pytest.approx(0.95, rel=1e-2)

    def test_cleanup_old_data(self, temp_db):
        """Test cleanup of old data."""
        storage = MetricsStorage(temp_db)
        
        # Store some old samples (8 days ago)
        old_time = time.time() - (8 * 24 * 3600)
        
        # Manually insert old data
        import sqlite3
        conn = sqlite3.connect(temp_db)
        for i in range(5):
            conn.execute(
                "INSERT INTO metrics (timestamp, cpu_pct, mem_pct, net_pct, load_avg) VALUES (?, ?, ?, ?, ?)",
                (old_time + i, 30.0, 50.0, 10.0, 0.5)
            )
        conn.commit()
        conn.close()
        
        # Store some recent samples
        for i in range(3):
            storage.store_sample(40.0, 60.0, 15.0, 0.7)
            time.sleep(0.001)
        
        # Should have 8 total samples
        assert storage.get_sample_count(30) == 8  # Check within 30 days
        
        # Cleanup old data (7 days)
        deleted = storage.cleanup_old(7)
        assert deleted == 5
        
        # Should have only 3 samples left
        assert storage.get_sample_count() == 3

    def test_get_sample_count_with_time_filter(self, temp_db):
        """Test sample count with different time filters."""
        storage = MetricsStorage(temp_db)
        
        # Store samples at different times
        current_time = time.time()
        
        # Manually insert samples at specific times
        import sqlite3
        conn = sqlite3.connect(temp_db)
        
        # 5 samples from 10 days ago
        old_time = current_time - (10 * 24 * 3600)
        for i in range(5):
            conn.execute(
                "INSERT INTO metrics (timestamp, cpu_pct, mem_pct, net_pct, load_avg) VALUES (?, ?, ?, ?, ?)",
                (old_time + i, 30.0, 50.0, 10.0, 0.5)
            )
        
        # 3 samples from 3 days ago
        recent_time = current_time - (3 * 24 * 3600)
        for i in range(3):
            conn.execute(
                "INSERT INTO metrics (timestamp, cpu_pct, mem_pct, net_pct, load_avg) VALUES (?, ?, ?, ?, ?)",
                (recent_time + i, 40.0, 60.0, 15.0, 0.7)
            )
        
        conn.commit()
        conn.close()
        
        # Check counts with different time filters
        assert storage.get_sample_count(15) == 8  # All samples
        assert storage.get_sample_count(7) == 3   # Only recent samples
        assert storage.get_sample_count(1) == 0   # No samples from last day

    def test_thread_safety(self, temp_db):
        """Test that MetricsStorage is thread-safe."""
        storage = MetricsStorage(temp_db)
        results = []
        
        def worker(thread_id):
            """Worker function that stores samples."""
            for i in range(10):
                result = storage.store_sample(
                    float(thread_id * 10 + i),
                    float(thread_id * 10 + i),
                    float(thread_id * 10 + i),
                    float(thread_id * 10 + i) / 100.0
                )
                results.append(result)
                time.sleep(0.001)
        
        # Start multiple threads
        threads = []
        for i in range(3):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        # Wait for all threads to complete
        for t in threads:
            t.join()
        
        # All operations should succeed
        assert all(results)
        assert storage.get_sample_count() == 30

    def test_database_init_failure_handling(self):
        """Test handling of database initialization failures."""
        # Try to create storage with invalid path that will fail
        with pytest.raises(FileNotFoundError) as exc_info:
            MetricsStorage("/dev/null/invalid")
        assert "A persistent volume must be mounted" in str(exc_info.value)
        
    def test_complete_database_failure_handling(self):
        """Test handling when database creation fails completely."""
        # Try with non-writable directory to trigger file not found error first
        with pytest.raises(FileNotFoundError):
            MetricsStorage("/some/path")

        # For corruption/access issues, it should raise RuntimeError during _init_db
        with tempfile.TemporaryDirectory() as tmpdir:
            import unittest.mock

            # Mock only the database creation to fail, not directory access
            with unittest.mock.patch('sqlite3.connect', side_effect=Exception("Mock database failure")):
                with pytest.raises(RuntimeError) as exc_info:
                    MetricsStorage(os.path.join(tmpdir, "test.db"))
                assert "Cannot create metrics database" in str(exc_info.value)

    def test_percentile_edge_cases(self, temp_db):
        """Test percentile calculation edge cases."""
        storage = MetricsStorage(temp_db)
        
        # Store two values for edge case testing
        storage.store_sample(10.0, 10.0, 10.0, 0.1)
        time.sleep(0.001)
        storage.store_sample(20.0, 20.0, 20.0, 0.2)
        
        # Test 0th percentile (minimum)
        assert storage.get_percentile('cpu', 0.0) == 10.0
        
        # Test 100th percentile (maximum)
        assert storage.get_percentile('cpu', 100.0) == 20.0
        
        # Test 50th percentile (between values)
        p50 = storage.get_percentile('cpu', 50.0)
        assert p50 == 15.0  # Should interpolate between 10 and 20

    def test_metrics_with_null_values(self, temp_db):
        """Test handling of null/None values in metrics."""
        storage = MetricsStorage(temp_db)
        
        # Store sample with some None values (shouldn't happen in practice, but test robustness)
        import sqlite3
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO metrics (timestamp, cpu_pct, mem_pct, net_pct, load_avg) VALUES (?, ?, ?, ?, ?)",
            (time.time(), 50.0, None, 15.0, 0.8)
        )
        conn.commit()
        conn.close()
        
        # Valid metric should work
        cpu_p95 = storage.get_percentile('cpu')
        assert cpu_p95 == 50.0
        
        # Metric with NULL should return None
        mem_p95 = storage.get_percentile('mem')
        assert mem_p95 is None

    def test_default_path_requires_persistent_directory(self):
        """Test that MetricsStorage() with default path requires persistent directory."""
        # Mock os.path.isdir to return False for the persistent directory
        with patch('os.path.isdir') as mock_isdir:
            mock_isdir.return_value = False

            with pytest.raises(FileNotFoundError) as exc_info:
                MetricsStorage()  # Use default path

            # Should check for the persistent directory
            mock_isdir.assert_called_with('/var/lib/loadshaper')
            assert "A persistent volume must be mounted" in str(exc_info.value)