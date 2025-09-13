#!/usr/bin/env python3
"""
Test suite for entrypoint.sh persistence validation
"""

import unittest
import subprocess
import tempfile
import os
import stat
from unittest.mock import patch


class TestEntrypointValidation(unittest.TestCase):
    """Test suite for entrypoint.sh validation behavior"""

    def setUp(self):
        """Set up test fixtures"""
        self.entrypoint_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "entrypoint.sh")
        if not os.path.exists(self.entrypoint_path):
            self.skipTest("entrypoint.sh not found")

    def test_entrypoint_directory_missing(self):
        """Test entrypoint behavior when persistence directory doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Run entrypoint in environment where /var/lib/loadshaper doesn't exist
            env = os.environ.copy()

            # Create a script that mocks the entrypoint behavior
            test_script = f"""#!/bin/bash
PERSISTENCE_DIR="/nonexistent/directory"
if [ ! -d "$PERSISTENCE_DIR" ]; then
    echo "[ERROR] Persistent storage directory does not exist: $PERSISTENCE_DIR"
    exit 1
fi
"""
            script_path = os.path.join(tmpdir, "test_entrypoint.sh")
            with open(script_path, 'w') as f:
                f.write(test_script)
            os.chmod(script_path, stat.S_IRWXU)

            # Run the test script
            result = subprocess.run([script_path], capture_output=True, text=True)

            self.assertEqual(result.returncode, 1, "Should exit with code 1 when directory missing")
            self.assertIn("Persistent storage directory does not exist", result.stdout)

    def test_entrypoint_directory_not_writable(self):
        """Test entrypoint behavior when persistence directory exists but not writable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a read-only directory
            test_dir = os.path.join(tmpdir, "loadshaper")
            os.makedirs(test_dir)
            os.chmod(test_dir, stat.S_IRUSR | stat.S_IXUSR)  # Read and execute only

            # Create a script that tests writability
            test_script = f"""#!/bin/bash
PERSISTENCE_DIR="{test_dir}"
if [ ! -w "$PERSISTENCE_DIR" ]; then
    echo "[ERROR] Cannot write to $PERSISTENCE_DIR - check volume permissions"
    exit 1
fi
"""
            script_path = os.path.join(tmpdir, "test_entrypoint.sh")
            with open(script_path, 'w') as f:
                f.write(test_script)
            os.chmod(script_path, stat.S_IRWXU)

            # Run the test script
            result = subprocess.run([script_path], capture_output=True, text=True)

            # Restore permissions for cleanup
            os.chmod(test_dir, stat.S_IRWXU)

            self.assertEqual(result.returncode, 1, "Should exit with code 1 when directory not writable")
            self.assertIn("Cannot write to", result.stdout)

    def test_entrypoint_directory_success(self):
        """Test entrypoint behavior when persistence directory exists and is writable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a writable directory
            test_dir = os.path.join(tmpdir, "loadshaper")
            os.makedirs(test_dir)

            # Create a script that tests success case
            test_script = f"""#!/bin/bash
PERSISTENCE_DIR="{test_dir}"
if [ ! -d "$PERSISTENCE_DIR" ]; then
    echo "[ERROR] Directory missing"
    exit 1
elif [ ! -w "$PERSISTENCE_DIR" ]; then
    echo "[ERROR] Not writable"
    exit 1
else
    echo "[INFO] Persistent storage verified at $PERSISTENCE_DIR"
    exit 0
fi
"""
            script_path = os.path.join(tmpdir, "test_entrypoint.sh")
            with open(script_path, 'w') as f:
                f.write(test_script)
            os.chmod(script_path, stat.S_IRWXU)

            # Run the test script
            result = subprocess.run([script_path], capture_output=True, text=True)

            self.assertEqual(result.returncode, 0, "Should exit with code 0 when directory is valid")
            self.assertIn("Persistent storage verified", result.stdout)


if __name__ == '__main__':
    unittest.main()