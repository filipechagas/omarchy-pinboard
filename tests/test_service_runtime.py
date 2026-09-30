"""Exercise the real Quickshell process boundary when Quickshell is installed."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).parents[1]


@unittest.skipUnless(shutil.which("quickshell"), "Quickshell is not installed")
class ServiceRuntimeTests(unittest.TestCase):
    def test_tag_autocomplete_survives_unavailable_suggestions_and_refreshes(self):
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run(
            ["quickshell", "-p", str(ROOT / "tags-repro.qml"), "--no-color"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(
            "PASS: autocomplete loads before suggestions, survives saves and failures, and recovers on retry",
            output,
        )
        self.assertNotIn("FAIL:", output)

    def test_token_errors_and_timeouts_release_the_service(self):
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run(
            ["quickshell", "-p", str(ROOT / "service-repro.qml"), "--no-color"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("PASS: invalid, stalled and orphaned token requests complete; service recovers", output)
        self.assertNotIn("FAIL:", output)


if __name__ == "__main__":
    unittest.main()
