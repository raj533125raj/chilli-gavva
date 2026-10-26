import unittest

import drill_math


class DrillMathTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(drill_math.add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
