import unittest
import context
from base import MockSystemCalc
import patches

class TestScUtils(unittest.TestCase):
	def setUp(self):
		self._system_calc = MockSystemCalc()
		self._monitor = self._system_calc._dbusmonitor
		self._service = self._system_calc._dbusservice
		self._monitor.add_service('com.victronenergy.vebus.ttyO1', {
			'/BatteryOperationalLimits/MaxChargeVoltage': 56
		})
		self._monitor.add_service('com.victronenergy.battery.ttyO2', {
			'/Info/MaxChargeVoltage': 55
		})

	def test_safe_add(self):
		from sc_utils import safeadd

		self.assertTrue(safeadd() is None)
		self.assertTrue(safeadd(None, None) is None)
		self.assertTrue(safeadd(1, None) == 1)
		self.assertTrue(safeadd(1, 2, 3) == 6)
		self.assertTrue(safeadd(1, 2, 3, None) == 6)
		self.assertTrue(safeadd(0) == 0)
		self.assertTrue(safeadd(0, None) == 0)

	def test_copy_dbus_value(self):
		from sc_utils import copy_dbus_value
		copy_dbus_value(self._monitor,
			'com.victronenergy.battery.ttyO2', '/Info/MaxChargeVoltage',
			'com.victronenergy.vebus.ttyO1', '/BatteryOperationalLimits/MaxChargeVoltage')

		self.assertEqual(55,
			self._monitor.get_value('com.victronenergy.vebus.ttyO1',
			'/BatteryOperationalLimits/MaxChargeVoltage'))

		copy_dbus_value(self._monitor,
			'com.victronenergy.battery.ttyO2', '/Info/MaxChargeVoltage',
			'com.victronenergy.vebus.ttyO1', '/BatteryOperationalLimits/MaxChargeVoltage',
			offset=-1)

		self.assertEqual(54,
			self._monitor.get_value('com.victronenergy.vebus.ttyO1',
			'/BatteryOperationalLimits/MaxChargeVoltage'))

class TestExpiringValue(unittest.TestCase):
	def test_initial_value_accessible(self):
		from sc_utils import ExpiringValue
		ev = ExpiringValue(3, 42)
		self.assertEqual(42, ev.get())

	def test_value_accessible_maxage_times(self):
		from sc_utils import ExpiringValue
		ev = ExpiringValue(3, 99)
		self.assertEqual(99, ev.get())
		self.assertEqual(99, ev.get())
		self.assertEqual(99, ev.get())
		self.assertIsNone(ev.get())

	def test_value_none_after_expiry(self):
		from sc_utils import ExpiringValue
		ev = ExpiringValue(1, 7)
		ev.get()
		self.assertIsNone(ev.get())

	def test_set_resets_ttl(self):
		from sc_utils import ExpiringValue
		ev = ExpiringValue(2, 1)
		ev.get()
		ev.get()
		self.assertIsNone(ev.get())
		ev.set(2)
		self.assertEqual(2, ev.get())
		self.assertEqual(2, ev.get())
		self.assertIsNone(ev.get())

	def test_set_updates_value(self):
		from sc_utils import ExpiringValue
		ev = ExpiringValue(5, 10)
		ev.set(20)
		self.assertEqual(20, ev.get())

	def test_maxage_zero_never_returns_value(self):
		from sc_utils import ExpiringValue
		ev = ExpiringValue(0, 5)
		self.assertIsNone(ev.get())

	def test_expired_property_true_while_ttl_remaining(self):
		# 'expired' returns True when TTL > 0 (value still accessible)
		from sc_utils import ExpiringValue
		ev = ExpiringValue(2, 1)
		self.assertFalse(ev.expired)
		ev.get()
		self.assertFalse(ev.expired)
		ev.get()
		self.assertTrue(ev.expired)

	def test_expired_property_resets_after_set(self):
		from sc_utils import ExpiringValue
		ev = ExpiringValue(1, 1)
		ev.get()
		self.assertTrue(ev.expired)
		ev.set(2)
		self.assertFalse(ev.expired)
