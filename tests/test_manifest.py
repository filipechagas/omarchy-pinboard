import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "manifest.json"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_required_manifest_contract(self):
        self.assertEqual(self.manifest["schemaVersion"], 1)
        for field in ("id", "name", "version", "author", "description"):
            self.assertIsInstance(self.manifest[field], str)
            self.assertTrue(self.manifest[field].strip())
        self.assertIsInstance(self.manifest["kinds"], list)
        self.assertTrue(self.manifest["kinds"])
        self.assertIsInstance(self.manifest["entryPoints"], dict)

    def test_plugin_id_is_safe_and_unreserved(self):
        plugin_id = self.manifest["id"]
        self.assertRegex(plugin_id, ID_PATTERN)
        self.assertNotIn("..", plugin_id)
        self.assertFalse(plugin_id.startswith("omarchy."))

    def test_each_kind_has_a_safe_existing_entry_point(self):
        entry_points = self.manifest["entryPoints"]
        expected_keys = {
            "bar-widget": "barWidget",
            "bar": "bar",
            "panel": "panel",
            "overlay": "overlay",
            "menu": "menu",
            "service": "service",
        }
        for kind in self.manifest["kinds"]:
            with self.subTest(kind=kind):
                self.assertIn(kind, expected_keys)
                key = expected_keys[kind]
                self.assertIn(key, entry_points)
                relative = Path(entry_points[key])
                self.assertFalse(relative.is_absolute())
                self.assertNotIn("..", relative.parts)
                self.assertTrue((ROOT / relative).is_file())

    def test_repository_contains_no_symlinks(self):
        symlinks = [
            path.relative_to(ROOT)
            for path in ROOT.rglob("*")
            if ".git" not in path.parts and path.is_symlink()
        ]
        self.assertEqual(symlinks, [])


if __name__ == "__main__":
    unittest.main()
