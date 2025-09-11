import sys
import os
import json
import time
import tempfile
import threading
import unittest.mock
from http.client import HTTPConnection
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from loadshaper import (
    MetricsStorage, HealthHandler, health_server_thread,
    CPU_STOP_PCT, MEM_STOP_PCT, NET_STOP_PCT, LOAD_THRESHOLD, LOAD_CHECK_ENABLED,
    N_WORKERS, CONTROL_PERIOD, AVG_WINDOW_SEC, CPU_TARGET_PCT, MEM_TARGET_PCT, NET_TARGET_PCT
)
from http.server import HTTPServer
from io import BytesIO


class MockRequest:
    """Mock HTTP request for testing handlers"""
    def __init__(self, path):
        self.path = path
        self.makefile = lambda mode: BytesIO()


class MockHealthHandler(HealthHandler):
    """Mock health handler that captures responses for testing"""
    def __init__(self, path, controller_state=None, metrics_storage=None):
        self.path = path
        self.controller_state = controller_state or {}
        self.metrics_storage = metrics_storage
        self.response_code = None
        self.response_headers = {}
        self.response_body = None
        self.wfile = self  # wfile should be an attribute, not a method
        
    def send_response(self, code):
        self.response_code = code
        
    def send_header(self, key, value):
        self.response_headers[key] = value
        
    def end_headers(self):
        pass
        
    def write(self, data):
        self.response_body = data


class TestHealthEndpoints:
    @pytest.fixture
    def temp_db(self):
        """Create a temporary database file for testing."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        yield db_path
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)

    @pytest.fixture
    def metrics_storage(self, temp_db):
        """Create a MetricsStorage instance for testing."""
        return MetricsStorage(temp_db)

    @pytest.fixture
    def healthy_state(self):
        """Return a healthy controller state."""
        return {
            'start_time': time.time() - 300,  # 5 minutes uptime
            'cpu_pct': 25.0,
            'cpu_avg': 30.0,
            'mem_pct': 45.0,
            'mem_avg': 50.0,
            'net_pct': 12.0,
            'net_avg': 15.0,
            'load_avg': 0.3,
            'duty': 0.5,
            'net_rate': 100.0,
            'paused': 0.0,
            'cpu_target': CPU_TARGET_PCT,
            'mem_target': MEM_TARGET_PCT,
            'net_target': NET_TARGET_PCT
        }

    @pytest.fixture
    def unhealthy_state(self):
        """Return an unhealthy controller state (system paused)."""
        return {
            'start_time': time.time() - 100,
            'cpu_pct': 95.0,
            'cpu_avg': 92.0,
            'mem_pct': 85.0,
            'mem_avg': 88.0,
            'net_pct': 55.0,
            'net_avg': 60.0,
            'load_avg': 1.2,
            'duty': 0.0,
            'net_rate': 1.0,
            'paused': 1.0,  # System is paused due to safety stop
            'cpu_target': CPU_TARGET_PCT,
            'mem_target': MEM_TARGET_PCT,
            'net_target': NET_TARGET_PCT
        }

    def test_health_endpoint_healthy(self, healthy_state, metrics_storage):
        """Test /health endpoint with healthy system state."""
        # Add some sample data to metrics storage
        metrics_storage.store_sample(25.0, 45.0, 12.0, 0.3)
        
        handler = MockHealthHandler("/health", healthy_state, metrics_storage)
        handler._handle_health()
        
        assert handler.response_code == 200
        assert handler.response_headers['Content-Type'] == 'application/json'
        assert handler.response_headers['Cache-Control'] == 'no-cache, no-store, must-revalidate'
        
        # Parse response body
        response_data = json.loads(handler.response_body.decode('utf-8'))
        
        assert response_data['status'] == 'healthy'
        assert response_data['uptime_seconds'] > 290
        assert 'timestamp' in response_data
        assert response_data['checks'] == ['all_systems_operational']
        assert response_data['metrics_storage'] == 'available'
        assert response_data['load_generation'] == 'active'

    def test_health_endpoint_unhealthy_paused(self, unhealthy_state, metrics_storage):
        """Test /health endpoint with system in safety stop."""
        handler = MockHealthHandler("/health", unhealthy_state, metrics_storage)
        handler._handle_health()
        
        assert handler.response_code == 503
        
        response_data = json.loads(handler.response_body.decode('utf-8'))
        assert response_data['status'] == 'unhealthy'
        assert 'system_paused_safety_stop' in response_data['checks']
        assert response_data['load_generation'] == 'paused'

    def test_health_endpoint_unhealthy_critical_resources(self, healthy_state, metrics_storage):
        """Test /health endpoint with critical resource usage."""
        # Modify state to have critical resource usage
        critical_state = healthy_state.copy()
        critical_state.update({
            'cpu_avg': CPU_STOP_PCT + 5,  # Above stop threshold
            'mem_avg': MEM_STOP_PCT + 10  # Above stop threshold
        })
        
        handler = MockHealthHandler("/health", critical_state, metrics_storage)
        handler._handle_health()
        
        response_data = json.loads(handler.response_body.decode('utf-8'))
        assert 'cpu_critical' in response_data['checks']
        assert 'memory_critical' in response_data['checks']

    def test_health_endpoint_degraded_storage(self, healthy_state):
        """Test /health endpoint with degraded metrics storage."""
        # Use None for metrics storage to simulate failure
        handler = MockHealthHandler("/health", healthy_state, None)
        handler._handle_health()
        
        assert handler.response_code == 200  # Still healthy, just degraded
        
        response_data = json.loads(handler.response_body.decode('utf-8'))
        assert response_data['status'] == 'healthy'
        assert 'metrics_storage_degraded' in response_data['checks']
        assert response_data['metrics_storage'] == 'degraded'


    def test_metrics_endpoint_basic(self, healthy_state, metrics_storage):
        """Test /metrics endpoint with basic functionality."""
        # Store some sample data
        for i in range(5):
            metrics_storage.store_sample(30.0 + i, 50.0 + i, 10.0 + i, 0.5 + i*0.1)
            time.sleep(0.001)
        
        handler = MockHealthHandler("/metrics", healthy_state, metrics_storage)
        handler._handle_metrics()
        
        assert handler.response_code == 200
        assert handler.response_headers['Content-Type'] == 'application/json'
        
        response_data = json.loads(handler.response_body.decode('utf-8'))
        
        # Check response structure
        assert 'timestamp' in response_data
        assert 'current' in response_data
        assert 'targets' in response_data
        assert 'configuration' in response_data
        assert 'percentiles_7d' in response_data
        
        # Check current metrics
        current = response_data['current']
        assert current['cpu_percent'] == healthy_state['cpu_pct']
        assert current['cpu_avg'] == healthy_state['cpu_avg']
        assert current['memory_percent'] == healthy_state['mem_pct']
        assert current['memory_avg'] == healthy_state['mem_avg']
        assert current['network_percent'] == healthy_state['net_pct']
        assert current['network_avg'] == healthy_state['net_avg']
        assert current['load_average'] == healthy_state['load_avg']
        assert current['duty_cycle'] == healthy_state['duty']
        assert current['network_rate_mbit'] == healthy_state['net_rate']
        assert current['paused'] == False
        
        # Check targets
        targets = response_data['targets']
        assert targets['cpu_target'] == CPU_TARGET_PCT
        assert targets['memory_target'] == MEM_TARGET_PCT
        assert targets['network_target'] == NET_TARGET_PCT
        
        # Check configuration
        config = response_data['configuration']
        assert config['cpu_stop_threshold'] == CPU_STOP_PCT
        assert config['memory_stop_threshold'] == MEM_STOP_PCT
        assert config['network_stop_threshold'] == NET_STOP_PCT
        assert config['worker_count'] == N_WORKERS
        assert config['control_period'] == CONTROL_PERIOD
        assert config['averaging_window'] == AVG_WINDOW_SEC
        
        # Check percentiles
        percentiles = response_data['percentiles_7d']
        assert 'cpu_p95' in percentiles
        assert 'memory_p95' in percentiles
        assert 'network_p95' in percentiles
        assert 'load_p95' in percentiles
        assert 'sample_count_7d' in percentiles
        assert percentiles['sample_count_7d'] == 5

    def test_metrics_endpoint_no_storage(self, healthy_state):
        """Test /metrics endpoint without metrics storage."""
        handler = MockHealthHandler("/metrics", healthy_state, None)
        handler._handle_metrics()
        
        assert handler.response_code == 200
        response_data = json.loads(handler.response_body.decode('utf-8'))
        
        # Should have current metrics but no percentiles
        assert 'current' in response_data
        assert 'targets' in response_data
        assert 'configuration' in response_data
        assert 'percentiles_7d' not in response_data

    def test_metrics_endpoint_storage_error(self, healthy_state, metrics_storage):
        """Test /metrics endpoint with storage error."""
        # Mock get_percentile to raise exception
        with unittest.mock.patch.object(metrics_storage, 'get_percentile', side_effect=Exception("Storage error")):
            handler = MockHealthHandler("/metrics", healthy_state, metrics_storage)
            handler._handle_metrics()
        
        assert handler.response_code == 200
        response_data = json.loads(handler.response_body.decode('utf-8'))
        
        # Should have error in percentiles section
        assert 'percentiles_7d' in response_data
        assert response_data['percentiles_7d']['error'] == 'Storage error'


    def test_unknown_endpoint(self, healthy_state, metrics_storage):
        """Test handling of unknown endpoints."""
        handler = MockHealthHandler("/unknown", healthy_state, metrics_storage)
        handler.do_GET()
        
        assert handler.response_code == 404
        response_data = json.loads(handler.response_body.decode('utf-8'))
        assert response_data['error'] == 'Not Found'
        assert response_data['status_code'] == 404

    def test_load_threshold_config_in_metrics(self, healthy_state, metrics_storage):
        """Test that load threshold configuration is properly reflected in metrics."""
        handler = MockHealthHandler("/metrics", healthy_state, metrics_storage)
        handler._handle_metrics()
        
        response_data = json.loads(handler.response_body.decode('utf-8'))
        config = response_data['configuration']
        
        if LOAD_CHECK_ENABLED:
            assert config['load_threshold'] == LOAD_THRESHOLD
        else:
            assert config['load_threshold'] is None

    def test_paused_state_reflection(self, healthy_state, metrics_storage):
        """Test that paused state is correctly reflected in both endpoints."""
        # Test with active state
        handler = MockHealthHandler("/metrics", healthy_state, metrics_storage)
        handler._handle_metrics()
        
        response_data = json.loads(handler.response_body.decode('utf-8'))
        assert response_data['current']['paused'] == False
        
        # Test with paused state
        paused_state = healthy_state.copy()
        paused_state['paused'] = 1.0
        
        handler = MockHealthHandler("/health", paused_state, metrics_storage)
        handler._handle_health()
        
        response_data = json.loads(handler.response_body.decode('utf-8'))
        assert response_data['load_generation'] == 'paused'
        assert response_data['status'] == 'unhealthy'

    def test_uptime_calculation(self, healthy_state, metrics_storage):
        """Test uptime calculation accuracy."""
        start_time = time.time() - 1234.5  # Specific uptime
        state_with_uptime = healthy_state.copy()
        state_with_uptime['start_time'] = start_time
        
        handler = MockHealthHandler("/health", state_with_uptime, metrics_storage)
        handler._handle_health()
        
        response_data = json.loads(handler.response_body.decode('utf-8'))
        
        # Should be approximately 1234.5 seconds (allowing for small execution time)
        assert 1234.0 <= response_data['uptime_seconds'] <= 1235.0

    def test_json_response_headers(self, healthy_state, metrics_storage):
        """Test that JSON responses have correct headers."""
        handler = MockHealthHandler("/health", healthy_state, metrics_storage)
        handler._handle_health()
        
        expected_headers = {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache, no-store, must-revalidate'
        }
        
        for key, value in expected_headers.items():
            assert handler.response_headers[key] == value
        
        # Content-Length should match body length
        expected_length = len(handler.response_body)
        assert handler.response_headers['Content-Length'] == str(expected_length)

    def test_timestamp_presence_and_format(self, healthy_state, metrics_storage):
        """Test that timestamps are present and reasonable."""
        before_time = time.time()
        
        # Test health endpoint
        handler = MockHealthHandler("/health", healthy_state, metrics_storage)
        handler._handle_health()
        
        after_time = time.time()
        response_data = json.loads(handler.response_body.decode('utf-8'))
        
        assert 'timestamp' in response_data
        assert before_time <= response_data['timestamp'] <= after_time
        
        # Test metrics endpoint
        before_time = time.time()
        handler = MockHealthHandler("/metrics", healthy_state, metrics_storage)
        handler._handle_metrics()
        
        after_time = time.time()
        response_data = json.loads(handler.response_body.decode('utf-8'))
        
        assert 'timestamp' in response_data
        assert before_time <= response_data['timestamp'] <= after_time


class TestHealthServerThread:
    """Test the health server thread functionality."""
    
    def test_health_server_disabled(self):
        """Test that health server doesn't start when disabled."""
        stop_evt = threading.Event()
        controller_state = {}
        metrics_storage = None
        
        # Mock HEALTH_ENABLED to False
        with unittest.mock.patch('loadshaper.HEALTH_ENABLED', False):
            # This should return immediately without starting server
            thread = threading.Thread(
                target=health_server_thread,
                args=(stop_evt, controller_state, metrics_storage),
                daemon=True
            )
            thread.start()
            thread.join(timeout=0.1)
            assert not thread.is_alive()

    def test_health_server_port_binding_failure(self):
        """Test handling of port binding failures."""
        stop_evt = threading.Event()
        controller_state = {}
        metrics_storage = None
        
        # Mock HTTPServer to raise OSError (port already in use)
        with unittest.mock.patch('loadshaper.HTTPServer', side_effect=OSError("Port already in use")):
            with unittest.mock.patch('loadshaper.HEALTH_ENABLED', True):
                with unittest.mock.patch('loadshaper.HEALTH_PORT', 8080):
                    # Should handle the error gracefully
                    thread = threading.Thread(
                        target=health_server_thread,
                        args=(stop_evt, controller_state, metrics_storage),
                        daemon=True
                    )
                    thread.start()
                    thread.join(timeout=0.5)
                    assert not thread.is_alive()

    def test_health_server_stop_event(self):
        """Test that health server respects stop event."""
        stop_evt = threading.Event()
        controller_state = {}
        metrics_storage = None
        
        # Create a mock server that we can control
        mock_server = unittest.mock.Mock()
        mock_server.handle_request.return_value = None
        
        with unittest.mock.patch('loadshaper.HTTPServer', return_value=mock_server):
            with unittest.mock.patch('loadshaper.HEALTH_ENABLED', True):
                thread = threading.Thread(
                    target=health_server_thread,
                    args=(stop_evt, controller_state, metrics_storage),
                    daemon=True
                )
                thread.start()
                
                # Give it a moment to start
                time.sleep(0.1)
                
                # Stop the server
                stop_evt.set()
                
                # Should stop within reasonable time
                thread.join(timeout=1.0)
                assert not thread.is_alive()
                
                # Server should have been closed
                mock_server.server_close.assert_called_once()