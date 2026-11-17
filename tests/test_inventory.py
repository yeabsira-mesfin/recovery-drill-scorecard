import unittest

from drill import inventory


class InventoryTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(inventory({"records": []}), {"records": 0})

    def test_fixture(self):
        self.assertEqual(inventory({"input": {"records": [{}, {}]}}), {"records": 2})

    def test_invalid_record(self):
        with self.assertRaises(ValueError):
            inventory({"records": [None]})
