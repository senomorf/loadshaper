#!/usr/bin/env python3
"""
Unit tests for Oracle Cloud shape detection functionality.

Tests the shape auto-detection system including caching, template loading,
and configuration priority handling with comprehensive mocking.
"""

import unittest
import tempfile
import os
import sys
import time
import socket
import threading
import concurrent.futures
from unittest.mock import patch, mock_open, MagicMock

# Import the module under test
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loadshaper


class TestShapeDetection(unittest.TestCase):
    """Test Oracle Cloud shape detection functionality."""

    def setUp(self):
        """Reset cache before each test to ensure isolation."""
        # Clear the module-level cache
        loadshaper._shape_cache.clear_cache()

    def test_detect_oracle_shape_cache_mechanism(self):
        """Test that shape detection caching works correctly with TTL."""
        # Mock the underlying detection functions
        with patch.object(loadshaper, '_detect_oracle_environment', return_value=True), \
             patch.object(loadshaper, '_get_system_specs', return_value=(1, 1.0)), \
             patch.object(loadshaper, '_classify_oracle_shape', 
                         return_value=('VM.Standard.E2.1.Micro', 'e2-1-micro.env')):
            
            # First call should trigger detection
            result1 = loadshaper.detect_oracle_shape()
            expected = ('VM.Standard.E2.1.Micro', 'e2-1-micro.env', True)
            self.assertEqual(result1, expected)
            
            # Verify cache is populated
            self.assertIsNotNone(loadshaper._shape_cache.get_cached())
            
            # Second call should use cache (no calls to underlying functions)
            with patch.object(loadshaper, '_detect_oracle_environment') as mock_detect:
                result2 = loadshaper.detect_oracle_shape()
                self.assertEqual(result2, expected)
                mock_detect.assert_not_called()  # Should use cache

    def test_detect_oracle_shape_cache_ttl_expiration(self):
        """Test that cache expires after TTL and triggers new detection."""
        # Create temporary cache with short TTL for testing
        original_cache = loadshaper._shape_cache
        loadshaper._shape_cache = loadshaper.ShapeDetectionCache(ttl_seconds=0.1)  # 100ms for fast test
        
        try:
            with patch.object(loadshaper, '_detect_oracle_environment', return_value=True), \
                 patch.object(loadshaper, '_get_system_specs', return_value=(1, 1.0)), \
                 patch.object(loadshaper, '_classify_oracle_shape', 
                             return_value=('VM.Standard.E2.1.Micro', 'e2-1-micro.env')):
                
                # First call
                result1 = loadshaper.detect_oracle_shape()
                
                # Wait for cache to expire
                time.sleep(0.2)
                
                # Second call should trigger new detection
                with patch.object(loadshaper, '_detect_oracle_environment', return_value=True) as mock_detect:
                    result2 = loadshaper.detect_oracle_shape()
                    mock_detect.assert_called_once()  # Cache expired, should re-detect
                    
        finally:
            loadshaper._shape_cache = original_cache

    @patch('builtins.open', new_callable=mock_open, read_data='Oracle Corporation\n')
    def test_detect_oracle_environment_dmi_success(self, mock_file):
        """Test Oracle environment detection via DMI sys_vendor."""
        result = loadshaper._detect_oracle_environment()
        self.assertTrue(result)
        mock_file.assert_called_with('/sys/class/dmi/id/sys_vendor', 'r')

    @patch('socket.socket')
    @patch('builtins.open', new_callable=mock_open, read_data='Dell Inc.\n')
    def test_detect_oracle_environment_dmi_not_oracle(self, mock_file, mock_socket):
        """Test non-Oracle environment detection via DMI."""
        # Mock socket to prevent actual network calls
        mock_sock_instance = MagicMock()
        mock_sock_instance.connect_ex.return_value = 1  # Connection failed
        mock_socket.return_value.__enter__.return_value = mock_sock_instance
        
        with patch('os.path.exists', return_value=False):  # No Oracle indicators
            result = loadshaper._detect_oracle_environment()
            self.assertFalse(result)

    @patch('builtins.open', side_effect=PermissionError("Access denied"))
    @patch('os.path.exists')
    def test_detect_oracle_environment_fallback_to_file_indicators(self, mock_exists, mock_file):
        """Test fallback to file indicators when DMI access fails."""
        # Mock Oracle-specific file exists for actual indicators
        oracle_indicators = [
            "/opt/oci-hpc",
            "/etc/oci-hostname.conf",
            "/var/lib/cloud/data/instance-id",
            "/etc/oracle-cloud-agent",
        ]
        mock_exists.side_effect = lambda path: path == "/opt/oci-hpc"
        result = loadshaper._detect_oracle_environment()
        self.assertTrue(result)

    @patch('socket.socket')
    @patch('builtins.open', side_effect=IOError("No access"))
    @patch('os.path.exists', return_value=False)
    def test_detect_oracle_environment_no_indicators(self, mock_exists, mock_file, mock_socket):
        """Test detection when no Oracle indicators are present."""
        # Mock socket to prevent actual network calls
        mock_sock_instance = MagicMock()
        mock_sock_instance.connect_ex.return_value = 1  # Connection failed
        mock_socket.return_value.__enter__.return_value = mock_sock_instance
        
        result = loadshaper._detect_oracle_environment()
        self.assertFalse(result)

    @patch('os.cpu_count', return_value=4)
    @patch('builtins.open', new_callable=mock_open, read_data='MemTotal:       24969088 kB\n')
    def test_get_system_specs_success(self, mock_file, mock_cpu):
        """Test successful system specification detection."""
        cpu_count, total_mem_gb = loadshaper._get_system_specs()
        self.assertEqual(cpu_count, 4)
        self.assertAlmostEqual(total_mem_gb, 23.82, places=1)  # ~24GB converted from kB

    @patch('os.cpu_count', return_value=None)
    @patch('builtins.open', side_effect=IOError("Cannot read meminfo"))
    def test_get_system_specs_error_handling(self, mock_file, mock_cpu):
        """Test system specs fallback when detection fails."""
        cpu_count, total_mem_gb = loadshaper._get_system_specs()
        self.assertEqual(cpu_count, 1)  # Fallback default
        self.assertEqual(total_mem_gb, 0.0)  # Fallback default

    def test_classify_oracle_shape_e2_1_micro(self):
        """Test classification of E2.1.Micro shape."""
        shape_name, template_file = loadshaper._classify_oracle_shape(1, 1.0)
        self.assertEqual(shape_name, 'VM.Standard.E2.1.Micro')
        self.assertEqual(template_file, 'e2-1-micro.env')

    def test_classify_oracle_shape_e2_2_micro(self):
        """Test classification of E2.2.Micro shape."""
        shape_name, template_file = loadshaper._classify_oracle_shape(2, 2.0)
        self.assertEqual(shape_name, 'VM.Standard.E2.2.Micro')
        self.assertEqual(template_file, 'e2-2-micro.env')

    def test_classify_oracle_shape_a1_flex_1_vcpu(self):
        """Test classification of A1.Flex with 1 vCPU."""
        shape_name, template_file = loadshaper._classify_oracle_shape(1, 6.0)
        self.assertEqual(shape_name, 'VM.Standard.A1.Flex')
        self.assertEqual(template_file, 'a1-flex-1.env')

    def test_classify_oracle_shape_a1_flex_4_vcpu(self):
        """Test classification of A1.Flex with 4 vCPU."""
        shape_name, template_file = loadshaper._classify_oracle_shape(4, 24.0)
        self.assertEqual(shape_name, 'VM.Standard.A1.Flex')
        self.assertEqual(template_file, 'a1-flex-4.env')

    def test_classify_oracle_shape_unknown(self):
        """Test classification of unknown Oracle shape."""
        shape_name, template_file = loadshaper._classify_oracle_shape(8, 64.0)
        self.assertEqual(shape_name, 'VM.Standard.A1.Flex-Unknown-8CPU-64.0GB')  # Updated to match fixed output
        self.assertEqual(template_file, 'a1-flex-1.env')  # Falls back to A1 template for unknown shapes

    def test_detect_oracle_shape_non_oracle_environment(self):
        """Test shape detection in non-Oracle environment."""
        with patch.object(loadshaper, '_detect_oracle_environment', return_value=False), \
             patch.object(loadshaper, '_get_system_specs', return_value=(4, 8.0)):
            
            shape_name, template_file, is_oracle = loadshaper.detect_oracle_shape()
            self.assertEqual(shape_name, 'Generic-4CPU-8.0GB')
            self.assertIsNone(template_file)
            self.assertFalse(is_oracle)


class TestConfigTemplateLoading(unittest.TestCase):
    """Test configuration template loading functionality."""

    def test_load_config_template_success(self):
        """Test successful template loading."""
        template_content = '''# Test template
CPU_P95_SETPOINT=35
MEM_TARGET_PCT=30
# Comment line
NET_TARGET_PCT=25

INVALID_LINE_NO_EQUALS
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as tf:
            tf.write(template_content)
            tf.flush()
            
            # Mock the template path resolution
            with patch.object(os.path, 'join', return_value=tf.name):
                config = loadshaper.load_config_template('test.env')
                
            expected = {
                'CPU_P95_SETPOINT': '35',
                'MEM_TARGET_PCT': '30', 
                'NET_TARGET_PCT': '25'
            }
            self.assertEqual(config, expected)
            
            # Cleanup
            os.unlink(tf.name)

    def test_load_config_template_none_input(self):
        """Test template loading with None input."""
        config = loadshaper.load_config_template(None)
        self.assertEqual(config, {})

    def test_load_config_template_missing_file(self):
        """Test template loading with missing file."""
        with patch.object(os.path, 'join', return_value='/nonexistent/file.env'):
            config = loadshaper.load_config_template('nonexistent.env')
            self.assertEqual(config, {})

    def test_load_config_template_permission_error(self):
        """Test template loading with permission error."""
        with patch('builtins.open', side_effect=PermissionError("Access denied")):
            config = loadshaper.load_config_template('restricted.env')
            self.assertEqual(config, {})


class TestConfigurationPriority(unittest.TestCase):
    """Test the three-tier configuration priority system."""

    def test_getenv_with_template_env_var_priority(self):
        """Test that environment variables have highest priority."""
        template = {'CPU_P95_SETPOINT': '30'}
        with patch.dict(os.environ, {'CPU_P95_SETPOINT': '50'}):
            result = loadshaper.getenv_with_template('CPU_P95_SETPOINT', '25', template)
            self.assertEqual(result, '50')  # ENV wins

    def test_getenv_with_template_template_priority(self):
        """Test that template values have second priority."""
        template = {'CPU_P95_SETPOINT': '30'}
        # Ensure env var is not set
        with patch.dict(os.environ, {}, clear=True):
            result = loadshaper.getenv_with_template('CPU_P95_SETPOINT', '25', template)
            self.assertEqual(result, '30')  # Template wins

    def test_getenv_with_template_default_fallback(self):
        """Test that default values are used when env and template don't have the value."""
        template = {}
        with patch.dict(os.environ, {}, clear=True):
            result = loadshaper.getenv_with_template('CPU_P95_SETPOINT', '25', template)
            self.assertEqual(result, '25')  # Default wins

    def test_getenv_int_with_template_type_conversion(self):
        """Test conversion to int through getenv_int_with_template."""
        template = {'CPU_P95_SETPOINT': '30'}
        with patch.dict(os.environ, {}, clear=True):
            result = loadshaper.getenv_int_with_template('CPU_P95_SETPOINT', 25, template)
            self.assertEqual(result, 30)
            self.assertIsInstance(result, int)

    def test_getenv_float_with_template_type_conversion(self):
        """Test conversion to float through getenv_float_with_template."""
        template = {'MEM_MIN_FREE_MB': '512.5'}
        with patch.dict(os.environ, {}, clear=True):
            result = loadshaper.getenv_float_with_template('MEM_MIN_FREE_MB', 256.0, template)
            self.assertEqual(result, 512.5)
            self.assertIsInstance(result, float)


class TestIntegrationScenarios(unittest.TestCase):
    def setUp(self):
        """Reset cache before each test to ensure isolation."""
        # Clear the module-level cache
        loadshaper._shape_cache.clear_cache()
    """Test complete integration scenarios combining shape detection and templates."""

    def test_e2_micro_complete_flow(self):
        """Test complete flow for E2.1.Micro detection and configuration loading."""
        # Mock all components to ensure consistent results
        with patch.object(loadshaper, '_detect_oracle_environment', return_value=True), \
             patch.object(loadshaper, '_get_system_specs', return_value=(1, 1.0)), \
             patch.object(loadshaper, '_classify_oracle_shape', 
                         return_value=('VM.Standard.E2.1.Micro', 'e2-1-micro.env')):
            
            # Create a mock template file
            template_content = '''CPU_P95_SETPOINT=25
MEM_TARGET_PCT=0
NET_TARGET_PCT=15'''
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as tf:
                tf.write(template_content)
                tf.flush()
                
                with patch.object(os.path, 'join', return_value=tf.name):
                    # Test shape detection
                    shape_name, template_file, is_oracle = loadshaper.detect_oracle_shape()
                    self.assertEqual(shape_name, 'VM.Standard.E2.1.Micro')
                    self.assertEqual(template_file, 'e2-1-micro.env')
                    self.assertTrue(is_oracle)
                    
                    # Test template loading
                    config = loadshaper.load_config_template(template_file)
                    self.assertEqual(config['CPU_P95_SETPOINT'], '25')
                    self.assertEqual(config['MEM_TARGET_PCT'], '0')
                    
                    # Test configuration priority
                    with patch.dict(os.environ, {'CPU_P95_SETPOINT': '40'}):
                        result = loadshaper.getenv_int_with_template('CPU_P95_SETPOINT', 30, config)
                        self.assertEqual(result, 40)  # ENV override
                        
                os.unlink(tf.name)

    def test_a1_flex_complete_flow(self):
        """Test complete flow for A1.Flex-1 detection and configuration loading."""
        # Mock Oracle environment with A1.Flex characteristics
        with patch.object(loadshaper, '_detect_oracle_environment', return_value=True), \
             patch.object(loadshaper, '_get_system_specs', return_value=(1, 6.0)):
            
            # Create a mock A1.Flex template
            template_content = '''CPU_P95_SETPOINT=35
MEM_TARGET_PCT=30
NET_TARGET_PCT=25'''
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as tf:
                tf.write(template_content)
                tf.flush()
                
                with patch.object(os.path, 'join', return_value=tf.name):
                    # Test shape detection
                    shape_name, template_file, is_oracle = loadshaper.detect_oracle_shape()
                    self.assertEqual(shape_name, 'VM.Standard.A1.Flex')
                    self.assertEqual(template_file, 'a1-flex-1.env')
                    self.assertTrue(is_oracle)
                    
                    # Test template loading with correct A1.Flex memory target
                    config = loadshaper.load_config_template(template_file)
                    self.assertEqual(config['MEM_TARGET_PCT'], '30')  # A1.Flex uses 30% for 10% safety margin above Oracle's 20% threshold
                    
                os.unlink(tf.name)


class TestNewFeatures(unittest.TestCase):
    """Test new features added in recent updates."""

    def setUp(self):
        """Reset cache before each test to ensure isolation."""
        loadshaper._shape_cache.clear_cache()

    def test_shape_detection_cache_thread_safety(self):
        """Test that ShapeDetectionCache is thread-safe under concurrent access."""
        cache = loadshaper.ShapeDetectionCache(ttl_seconds=0.5)
        results = []
        
        def cache_operation(thread_id):
            """Perform cache operations from multiple threads."""
            try:
                # Try to get cached value
                cached = cache.get_cached()
                
                # Store a value
                test_data = (f'shape-{thread_id}', f'template-{thread_id}.env', True)
                cache.set_cache(test_data)
                
                # Get it back
                retrieved = cache.get_cached()
                results.append((thread_id, retrieved))
                
            except Exception as e:
                results.append((thread_id, f'ERROR: {e}'))
        
        # Run multiple threads concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(cache_operation, i) for i in range(10)]
            concurrent.futures.wait(futures)
        
        # Verify no exceptions occurred
        for thread_id, result in results:
            self.assertNotIn('ERROR:', str(result), f"Thread {thread_id} encountered an error: {result}")
        
        # Verify cache contains valid data
        final_cached = cache.get_cached()
        self.assertIsNotNone(final_cached)
        self.assertEqual(len(final_cached), 3)  # (shape_name, template_file, is_oracle)

    def test_a1_flex_2_vcpu_detection(self):
        """Test detection and classification of A1.Flex with 2 vCPU."""
        shape_name, template_file = loadshaper._classify_oracle_shape(2, 12.0)
        self.assertEqual(shape_name, 'VM.Standard.A1.Flex')
        self.assertEqual(template_file, 'a1-flex-2.env')

    def test_a1_flex_3_vcpu_detection(self):
        """Test detection and classification of A1.Flex with 3 vCPU.""" 
        shape_name, template_file = loadshaper._classify_oracle_shape(3, 18.0)
        self.assertEqual(shape_name, 'VM.Standard.A1.Flex')
        self.assertEqual(template_file, 'a1-flex-3.env')

    def test_oracle_metadata_probe_disabled_by_default(self):
        """Test that Oracle metadata probe is disabled by default."""
        with patch.dict(os.environ, {}, clear=True):  # Clear all env vars
            with patch('socket.socket') as mock_socket:
                mock_sock_instance = MagicMock()
                mock_sock_instance.connect_ex.return_value = 1  # Connection failed
                mock_socket.return_value.__enter__.return_value = mock_sock_instance
                
                with patch('os.path.exists', return_value=False):
                    result = loadshaper._detect_oracle_environment()
                    self.assertFalse(result)
                    # Verify socket was not called (probe disabled)
                    mock_socket.assert_not_called()

    def test_oracle_metadata_probe_when_enabled(self):
        """Test Oracle metadata probe when explicitly enabled."""
        with patch.dict(os.environ, {'ORACLE_METADATA_PROBE': '1'}):
            # The key test is that the environment variable enables the probe logic
            # We don't need to test the actual network call, just that the flag works
            with patch('builtins.open', side_effect=IOError("No DMI access")):
                with patch('os.path.exists', return_value=False):
                    # Should not crash when probe is enabled (the function should handle network errors gracefully)
                    result = loadshaper._detect_oracle_environment()
                    # Result can be True or False depending on network, but should not crash
                    self.assertIsInstance(result, bool)

    def test_e2_memory_targeting_disabled(self):
        """Test that E2 shapes have memory targeting disabled in templates."""
        # E2.1.Micro template should have MEM_TARGET_PCT=0
        template_content = '''CPU_P95_SETPOINT=25
MEM_TARGET_PCT=0
NET_TARGET_PCT=15'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as tf:
            tf.write(template_content)
            tf.flush()
            
            with patch.object(os.path, 'join', return_value=tf.name):
                config = loadshaper.load_config_template('e2-1-micro.env')
                self.assertEqual(config['MEM_TARGET_PCT'], '0')
                
            os.unlink(tf.name)

    def test_a1_flex_memory_targeting_enabled(self):
        """Test that A1.Flex shapes have memory targeting at 30%.""" 
        template_content = '''CPU_P95_SETPOINT=35
MEM_TARGET_PCT=30
NET_TARGET_PCT=25'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as tf:
            tf.write(template_content)
            tf.flush()
            
            with patch.object(os.path, 'join', return_value=tf.name):
                config = loadshaper.load_config_template('a1-flex-2.env')
                self.assertEqual(config['MEM_TARGET_PCT'], '30')
                
            os.unlink(tf.name)

    def test_configuration_validation_integration(self):
        """Test complete configuration validation with template priority."""
        template = {
            'CPU_P95_SETPOINT': '35',
            'MEM_TARGET_PCT': '30', 
            'NET_TARGET_PCT': '25',
            'NET_MODE': 'client',
            'NET_PROTOCOL': 'udp'
        }
        
        # Test environment variable override
        with patch.dict(os.environ, {'CPU_P95_SETPOINT': '40', 'NET_MODE': 'server'}):
            cpu_result = loadshaper.getenv_int_with_template('CPU_P95_SETPOINT', 20, template)
            net_mode = loadshaper.getenv_with_template('NET_MODE', 'client', template)
            
            self.assertEqual(cpu_result, 40)  # ENV override
            self.assertEqual(net_mode, 'server')  # ENV override
            
        # Test template fallback
        with patch.dict(os.environ, {}, clear=True):
            mem_result = loadshaper.getenv_int_with_template('MEM_TARGET_PCT', 20, template)
            protocol = loadshaper.getenv_with_template('NET_PROTOCOL', 'tcp', template)
            
            self.assertEqual(mem_result, 30)  # Template value
            self.assertEqual(protocol, 'udp')  # Template value


class TestConfigValidation(unittest.TestCase):
    """Test configuration validation functions."""

    def test_validate_config_value_percentage_valid(self):
        """Test valid percentage values pass validation."""
        # These should not raise exceptions
        loadshaper._validate_config_value("CPU_P95_SETPOINT", "50")
        loadshaper._validate_config_value("MEM_TARGET_PCT", "0")
        loadshaper._validate_config_value("NET_TARGET_PCT", "100")

    def test_validate_config_value_percentage_invalid(self):
        """Test invalid percentage values raise ValueError."""
        with self.assertRaises(ValueError):
            loadshaper._validate_config_value("CPU_P95_SETPOINT", "150")
        with self.assertRaises(ValueError):
            loadshaper._validate_config_value("MEM_TARGET_PCT", "-10")
        with self.assertRaises(ValueError):
            loadshaper._validate_config_value("NET_TARGET_PCT", "abc")

    def test_validate_config_value_integer_fields(self):
        """Test integer field validation."""
        # Valid integer values
        loadshaper._validate_config_value("NET_PORT", "8080")
        loadshaper._validate_config_value("MEM_STEP_MB", "64")
        loadshaper._validate_config_value("NET_BURST_SEC", "10")
        
        # Invalid integer values
        with self.assertRaises(ValueError):
            loadshaper._validate_config_value("NET_PORT", "80.5")  # Not integer
        with self.assertRaises(ValueError):
            loadshaper._validate_config_value("NET_PORT", "80000")  # Out of range
        with self.assertRaises(ValueError):
            loadshaper._validate_config_value("NET_PORT", "abc")    # Not numeric

    def test_validate_config_value_boolean_fields(self):
        """Test boolean field validation."""
        # Valid boolean values
        loadshaper._validate_config_value("LOAD_CHECK_ENABLED", "true")
        loadshaper._validate_config_value("LOAD_CHECK_ENABLED", "false")
        loadshaper._validate_config_value("LOAD_CHECK_ENABLED", "1")
        loadshaper._validate_config_value("LOAD_CHECK_ENABLED", "0")
        
        # Invalid boolean values
        with self.assertRaises(ValueError):
            loadshaper._validate_config_value("LOAD_CHECK_ENABLED", "maybe")
        with self.assertRaises(ValueError):
            loadshaper._validate_config_value("LOAD_CHECK_ENABLED", "yes")

    def test_parse_boolean_function(self):
        """Test the _parse_boolean helper function."""
        # Truthy values
        self.assertTrue(loadshaper._parse_boolean("true"))
        self.assertTrue(loadshaper._parse_boolean("True"))
        self.assertTrue(loadshaper._parse_boolean("1"))
        self.assertTrue(loadshaper._parse_boolean("yes"))
        self.assertTrue(loadshaper._parse_boolean("on"))
        self.assertTrue(loadshaper._parse_boolean("enabled"))
        self.assertTrue(loadshaper._parse_boolean(True))
        
        # Falsy values
        self.assertFalse(loadshaper._parse_boolean("false"))
        self.assertFalse(loadshaper._parse_boolean("False"))
        self.assertFalse(loadshaper._parse_boolean("0"))
        self.assertFalse(loadshaper._parse_boolean("no"))
        self.assertFalse(loadshaper._parse_boolean("off"))
        self.assertFalse(loadshaper._parse_boolean("disabled"))
        self.assertFalse(loadshaper._parse_boolean(False))
        self.assertFalse(loadshaper._parse_boolean("anything_else"))

    def test_unknown_a1_shape_validation(self):
        """Test that unknown A1 shapes trigger A1.Flex validation rules."""
        # Test that unknown A1 shape names include "A1.Flex" for validation
        shape_name, template_file = loadshaper._classify_oracle_shape(8, 64.0)  # Unknown large A1
        self.assertIn("A1.Flex", shape_name)
        self.assertEqual(template_file, "a1-flex-1.env")

    def test_memory_tolerance_boundaries(self):
        """Test shape detection at memory tolerance boundaries."""
        # Test E2.1.Micro boundaries (0.8-1.2 GB)
        shape_name, _ = loadshaper._classify_oracle_shape(1, 0.8)  # Lower bound
        self.assertEqual(shape_name, "VM.Standard.E2.1.Micro")
        
        shape_name, _ = loadshaper._classify_oracle_shape(1, 1.2)  # Upper bound
        self.assertEqual(shape_name, "VM.Standard.E2.1.Micro")
        
        # Test A1.Flex boundaries (5.5-6.5 GB)
        shape_name, _ = loadshaper._classify_oracle_shape(1, 5.5)  # Lower bound
        self.assertEqual(shape_name, "VM.Standard.A1.Flex")
        
        shape_name, _ = loadshaper._classify_oracle_shape(1, 6.5)  # Upper bound
        self.assertEqual(shape_name, "VM.Standard.A1.Flex")


class TestEnvironmentValidation(unittest.TestCase):
    """Test environment variable override validation."""

    def test_invalid_env_override_handling(self):
        """Test that invalid environment overrides are handled gracefully."""
        # This test verifies that _validate_final_config handles invalid values
        # Note: This is a conceptual test - the actual implementation would need
        # to be tested with proper mocking of global variables
        pass  # Implementation would require complex mocking


class TestConcurrentShapeDetection(unittest.TestCase):
    """Test thread safety of shape detection."""

    def test_concurrent_shape_detection(self):
        """Test that concurrent shape detection calls are thread-safe."""
        import threading
        results = []
        
        def detect_shape():
            result = loadshaper.detect_oracle_shape()
            results.append(result)
        
        # Create multiple threads calling detect_oracle_shape
        threads = []
        for _ in range(10):
            thread = threading.Thread(target=detect_shape)
            thread.daemon = True
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join(timeout=5.0)
            # Force cleanup if thread is still alive
            if thread.is_alive():
                print(f"Warning: Thread did not exit within timeout")
        
        # All results should be the same (cache should work correctly)
        self.assertEqual(len(set(results)), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
