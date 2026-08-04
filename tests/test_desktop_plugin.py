from __future__ import annotations

import unittest
from pathlib import Path


PLUGIN_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "desktop-plugin"
    / "hermes-todo"
    / "plugin.js"
)


class HermesTodoDesktopContractTests(unittest.TestCase):
    def test_remote_board_uses_profile_scoped_plugin_rest(self) -> None:
        source = PLUGIN_SOURCE.read_text(encoding="utf-8")

        self.assertIn("return ctx.rest(path, options)", source)
        self.assertEqual(source.count("ctx.rest("), 1)
        self.assertNotIn("hermesDesktop?.api", source)
        self.assertNotIn("/api/plugins/", source)
        self.assertNotIn("globalThis.window", source)


if __name__ == "__main__":
    unittest.main()
