#!/usr/bin/env python3
"""
Test suite for entrypoint.sh persistence validation
"""

import unittest
import subprocess
import tempfile
import os
import stat

class TestEntrypointValidation(unittest.TestCase):
    """Test suite for entrypoint.sh validation behavior"""

    def setUp(self):
        """Set up test fixtures"""
        # Path to the entrypoint script relative to this test file
        self.entrypoint_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "entrypoint.sh")
        )
        if not os.path.exists(self.entrypoint_path):
            self.skipTest("entrypoint.sh not found")

    def run_entrypoint(self, persistence_dir, command=["echo", "success"]):
        """Helper to run the entrypoint.sh script with a specific environment."""
        env = os.environ.copy()
        env["PERSISTENCE_DIR"] = persistence_dir

        # The command to run is the entrypoint script followed by the app command
        process_args = [self.entrypoint_path] + command

        return subprocess.run(
            process_args,
            capture_output=True,
            text=True,
            env=env,
            check=False  # Do not raise exception on non-zero exit codes
        )

    def test_entrypoint_directory_missing(self):
        """Test entrypoint behavior when persistence directory doesn't exist."""
        non_existent_dir = "/nonexistent/directory/for/testing"
        result = self.run_entrypoint(non_existent_dir)

        self.assertEqual(result.returncode, 1, "Should exit with code 1 when directory is missing")
        self.assertIn("Persistent storage directory does not exist", result.stdout)
        self.assertIn(non_existent_dir, result.stdout)

    def test_entrypoint_directory_not_writable(self):
        """Test entrypoint behavior when persistence directory exists but is not writable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a read-only directory
            test_dir = os.path.join(tmpdir, "loadshaper_ro")
            os.makedirs(test_dir)
            os.chmod(test_dir, stat.S_IRUSR | stat.S_IXUSR)  # Read and execute only for owner

            try:
                result = self.run_entrypoint(test_dir)
                self.assertEqual(result.returncode, 1, "Should exit with code 1 when directory not writable")
                # Now the entrypoint first checks if it's a mount point, so we'll see that error first
                # since test directories are not mount points
                self.assertIn("NOT a mount point", result.stdout)
            finally:
                # Restore permissions to allow cleanup
                os.chmod(test_dir, stat.S_IRWXU)

    def test_entrypoint_directory_not_mount(self):
        """Test entrypoint behavior when persistence directory exists but is not a mount point."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, "loadshaper_ok")
            os.makedirs(test_dir)

            result = self.run_entrypoint(test_dir)

            # Should fail because it's not a mount point
            self.assertEqual(result.returncode, 1, f"Should exit with code 1 when not a mount point. Stdout: {result.stdout}")
            self.assertIn("NOT a mount point", result.stdout)
            self.assertIn("persistent volume", result.stdout.lower())

    def test_entrypoint_messages_contain_required_info(self):
        """Test that error messages contain helpful information for users."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, "loadshaper_test")
            os.makedirs(test_dir)

            # Run entrypoint script (will fail due to not being a mount)
            result = self.run_entrypoint(test_dir)

            # Check that error messages contain Docker Compose configuration example
            self.assertIn("volumes:", result.stdout)
            self.assertIn("loadshaper-metrics", result.stdout)
            self.assertIn("/var/lib/loadshaper", result.stdout)

if __name__ == '__main__':
    unittest.main()