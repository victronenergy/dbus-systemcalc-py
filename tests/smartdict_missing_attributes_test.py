import unittest
import context
from sc_utils import SmartDict

class SmartDictAttributeTest(unittest.TestCase):
	def test_missing_attribute_follows_python_attribute_protocol(self):
		values = SmartDict(present=5)
		self.assertFalse(hasattr(values, 'absent'))
		self.assertEqual(getattr(values, 'absent', 7), 7)
		with self.assertRaises(AttributeError):
			values.absent
		self.assertEqual(values.present, 5)
		values.present = 6
		self.assertEqual(values['present'], 6)
