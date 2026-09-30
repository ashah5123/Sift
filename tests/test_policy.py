import unittest

from sift.policy import Action, Thresholds, decide_action


class TestPolicy(unittest.TestCase):
    def test_default_bands(self):
        self.assertEqual(decide_action(0.95), Action.ACT)
        self.assertEqual(decide_action(0.90), Action.ACT)
        self.assertEqual(decide_action(0.75), Action.SUGGEST)
        self.assertEqual(decide_action(0.60), Action.SUGGEST)
        self.assertEqual(decide_action(0.59), Action.SILENT)

    def test_custom_thresholds(self):
        strict = Thresholds(act=0.99, suggest=0.8)
        self.assertEqual(decide_action(0.95, strict), Action.SUGGEST)
        self.assertEqual(decide_action(0.7, strict), Action.SILENT)

    def test_invalid_thresholds(self):
        with self.assertRaises(ValueError):
            Thresholds(act=0.5, suggest=0.8)


if __name__ == "__main__":
    unittest.main()
