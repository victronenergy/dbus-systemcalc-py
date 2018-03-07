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
