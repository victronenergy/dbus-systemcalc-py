import context
import patches
from base import TestSystemCalcBase
from delegates.batterydata import BatteryTracker


class ZeroPowerTest(TestSystemCalcBase):
	def test_zero_is_preserved_with_or_without_current(self):
		for current in (5, None):
			with self.subTest(current=current):
				service = 'com.victronenergy.battery.zero'
				self._add_device(service, {'/Dc/0/Voltage': 12,
					'/Dc/0/Current': current, '/Dc/0/Power': 0})
				tracker = BatteryTracker(service, 0, self._monitor)
				tracker.update()
				self.assertEqual(tracker.data()['power'], 0)
				self._remove_device(service)

	def test_missing_power_still_uses_current_and_voltage(self):
		service = 'com.victronenergy.battery.zero'
		self._add_device(service, {'/Dc/0/Voltage': 12, '/Dc/0/Current': 5})
		tracker = BatteryTracker(service, 0, self._monitor)
		tracker.update()
		self.assertEqual(tracker.data()['power'], 60)
