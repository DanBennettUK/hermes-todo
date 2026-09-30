"""Mount actual plugin components in React with explicit Hermes SDK test doubles."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DesktopRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(
        (ROOT / "tests/desktop/node_modules/react-test-renderer").is_dir(),
        "Run npm ci --prefix tests/desktop to enable mounted React tests",
    )
    def test_mounted_lifecycles(self):
        result = subprocess.run(
            ["node", "--test", "tests/desktop/lifecycle.test.cjs"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
