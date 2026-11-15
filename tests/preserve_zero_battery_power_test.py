import unittest
import context
import patches
from base import TestSystemCalcBase
from delegates.batterydata import BatteryTracker, SecondaryBatteryTracker

class ZeroPowerTest(TestSystemCalcBase):
	def test_zero_is_preserved_on_primary_and_secondary_channels(self):
		for channel in (0,):
			for current in (5, None):
				with self.subTest(channel=channel, current=current):
					service = 'com.victronenergy.battery.zero%d' % channel
					self._add_device(service, {'/DeviceInstance': channel,
						'/Dc/%d/Voltage' % channel: 12,
						'/Dc/%d/Current' % channel: current,
						'/Dc/%d/Power' % channel: 0})
					tracker = (BatteryTracker(service, channel, self._monitor) if channel == 0
						else SecondaryBatteryTracker(service, channel, self._monitor, channel))
					tracker.update()
					self.assertEqual(tracker.data()['power'], 0)
					self._remove_device(service)

	def test_missing_power_still_uses_current_and_voltage(self):
		service = 'com.victronenergy.battery.zero'
		self._add_device(service, {'/Dc/0/Voltage': 12, '/Dc/0/Current': 5})
		tracker = BatteryTracker(service, 0, self._monitor)
		tracker.update()
		self.assertEqual(tracker.data()['power'], 60)
