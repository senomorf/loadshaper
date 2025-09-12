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
        self.assertEqual(shape_name, 'Oracle-Unknown-8CPU-64.0GB')
        self.assertEqual(template_file, 'e2-1-micro.env')  # Falls back to conservative template

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
CPU_TARGET_PCT=35
MEM_TARGET_PCT=25
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
                'CPU_TARGET_PCT': '35',
                'MEM_TARGET_PCT': '25', 
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
        template = {'CPU_TARGET_PCT': '30'}
        with patch.dict(os.environ, {'CPU_TARGET_PCT': '50'}):
            result = loadshaper.getenv_with_template('CPU_TARGET_PCT', '25', template)
            self.assertEqual(result, '50')  # ENV wins

    def test_getenv_with_template_template_priority(self):
        """Test that template values have second priority."""
        template = {'CPU_TARGET_PCT': '30'}
        # Ensure env var is not set
        with patch.dict(os.environ, {}, clear=True):
            result = loadshaper.getenv_with_template('CPU_TARGET_PCT', '25', template)
            self.assertEqual(result, '30')  # Template wins

    def test_getenv_with_template_default_fallback(self):
        """Test that default values are used when env and template don't have the value."""
        template = {}
        with patch.dict(os.environ, {}, clear=True):
            result = loadshaper.getenv_with_template('CPU_TARGET_PCT', '25', template)
            self.assertEqual(result, '25')  # Default wins

    def test_getenv_int_with_template_type_conversion(self):
        """Test conversion to int through getenv_int_with_template."""
        template = {'CPU_TARGET_PCT': '30'}
        with patch.dict(os.environ, {}, clear=True):
            result = loadshaper.getenv_int_with_template('CPU_TARGET_PCT', 25, template)
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
            template_content = '''CPU_TARGET_PCT=25
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
                    self.assertEqual(config['CPU_TARGET_PCT'], '25')
                    self.assertEqual(config['MEM_TARGET_PCT'], '0')
                    
                    # Test configuration priority
                    with patch.dict(os.environ, {'CPU_TARGET_PCT': '40'}):
                        result = loadshaper.getenv_int_with_template('CPU_TARGET_PCT', 30, config)
                        self.assertEqual(result, 40)  # ENV override
                        
                os.unlink(tf.name)

    def test_a1_flex_complete_flow(self):
        """Test complete flow for A1.Flex-1 detection and configuration loading."""
        # Mock Oracle environment with A1.Flex characteristics
        with patch.object(loadshaper, '_detect_oracle_environment', return_value=True), \
             patch.object(loadshaper, '_get_system_specs', return_value=(1, 6.0)):
            
            # Create a mock A1.Flex template
            template_content = '''CPU_TARGET_PCT=35
MEM_TARGET_PCT=25
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
                    self.assertEqual(config['MEM_TARGET_PCT'], '25')  # A1.Flex uses 25% for safety margin above Oracle's 20% threshold
                    
                os.unlink(tf.name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
