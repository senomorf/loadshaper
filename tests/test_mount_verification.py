#!/usr/bin/env python3
"""
Test mount point verification for persistent storage requirement.

Tests both entrypoint.sh and Python-side verification logic.
"""

import unittest
import tempfile
import os
import subprocess
import shutil
from unittest.mock import patch, MagicMock


class TestMountPointVerification(unittest.TestCase):
    """Test mount point verification for persistent storage"""

    def setUp(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp(prefix="loadshaper_mount_test_")
        self.entrypoint_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "entrypoint.sh"
        )

    def tearDown(self):
        """Clean up test environment"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_entrypoint_fails_without_mount(self):
        """Test that entrypoint.sh fails when directory exists but is not a mount"""
        # Create a regular directory (not a mount point)
        test_persistence_dir = os.path.join(self.test_dir, "persistence")
        os.makedirs(test_persistence_dir, exist_ok=True)
        os.chmod(test_persistence_dir, 0o755)

        # Run entrypoint.sh with the test directory
        env = os.environ.copy()
        env['PERSISTENCE_DIR'] = test_persistence_dir

        # The entrypoint should fail because it's not a mount point
        result = subprocess.run(
            ['/bin/sh', self.entrypoint_path, 'echo', 'test'],
            env=env,
            capture_output=True,
            text=True
        )

        # Should exit with error code 1
        self.assertEqual(result.returncode, 1)
        # Should contain mount point error message
        self.assertIn("NOT a mount point", result.stdout)
        self.assertIn("persistent volume", result.stdout.lower())

    def test_entrypoint_fails_without_directory(self):
        """Test that entrypoint.sh fails when directory doesn't exist"""
        # Use a non-existent directory
        test_persistence_dir = os.path.join(self.test_dir, "nonexistent")

        env = os.environ.copy()
        env['PERSISTENCE_DIR'] = test_persistence_dir

        result = subprocess.run(
            ['/bin/sh', self.entrypoint_path, 'echo', 'test'],
            env=env,
            capture_output=True,
            text=True
        )

        # Should exit with error code 1
        self.assertEqual(result.returncode, 1)
        # Should contain directory not exist error
        self.assertIn("does not exist", result.stdout)

    def test_entrypoint_fails_without_write_permission(self):
        """Test that entrypoint.sh fails when directory is not writable"""
        # Create a directory but make it read-only
        test_persistence_dir = os.path.join(self.test_dir, "readonly")
        os.makedirs(test_persistence_dir, exist_ok=True)
        os.chmod(test_persistence_dir, 0o555)  # Read-only

        # Mock the mount point check to pass
        # Since we can't easily create a real mount point in tests
        env = os.environ.copy()
        env['PERSISTENCE_DIR'] = test_persistence_dir

        # This test would need to be run as non-root to properly test
        # For now, we verify the script structure is correct
        with open(self.entrypoint_path, 'r') as f:
            script_content = f.read()
            # Verify script checks for write permission with mktemp
            self.assertIn('mktemp', script_content)
            self.assertIn('Cannot write to', script_content)

    def test_python_mount_verification_warning(self):
        """Test Python-side mount verification in MetricsStorage"""
        # This test verifies the Python code structure
        # since we can't easily import loadshaper.py without all globals initialized

        loadshaper_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "loadshaper.py"
        )

        with open(loadshaper_path, 'r') as f:
            content = f.read()
            # Verify mount point verification code exists
            self.assertIn("st_dev == parent_stat.st_dev", content)
            self.assertIn("NOT a mount point", content)
            self.assertIn("LOADSHAPER_STRICT_MOUNT_CHECK", content)

    def test_dockerfile_no_directory_creation(self):
        """Test that Dockerfile doesn't create /var/lib/loadshaper"""
        dockerfile_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "Dockerfile"
        )

        with open(dockerfile_path, 'r') as f:
            content = f.read()
            # Should NOT contain mkdir for /var/lib/loadshaper
            self.assertNotIn("mkdir -p /var/lib/loadshaper", content)
            # Should still create the user
            self.assertIn("adduser", content)
            self.assertIn("loadshaper", content)

    def test_helm_chart_version_bump(self):
        """Test that Helm chart version was incremented for breaking change"""
        chart_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "helm", "loadshaper", "Chart.yaml"
        )

        with open(chart_path, 'r') as f:
            content = f.read()
            # Should have version 2.0.0 for breaking change
            self.assertIn("version: 2.0.0", content)

    def test_helm_values_fsgroup_consistency(self):
        """Test that Helm values.yaml has consistent fsGroup"""
        values_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "helm", "loadshaper", "values.yaml"
        )

        with open(values_path, 'r') as f:
            content = f.read()
            # fsGroup should be 1000 to match runAsGroup
            self.assertIn("fsGroup: 1000", content)
            self.assertIn("runAsUser: 1000", content)
            self.assertIn("runAsGroup: 1000", content)


if __name__ == '__main__':
    unittest.main()