import unittest
import context

class TestEssStates(unittest.TestCase):
	def test_safe_add(self):
		from sc_utils import safeadd

		self.assertTrue(safeadd() is None)
		self.assertTrue(safeadd(None, None) is None)
		self.assertTrue(safeadd(1, None) == 1)
		self.assertTrue(safeadd(1, 2, 3) == 6)
		self.assertTrue(safeadd(1, 2, 3, None) == 6)
		self.assertTrue(safeadd(0) == 0)
		self.assertTrue(safeadd(0, None) == 0)
