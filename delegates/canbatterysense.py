import logging
from gi.repository import GLib
from dbus.exceptions import DBusException
from delegates.base import SystemCalcDelegate
from delegates.batteryservice import BatteryService
from ve_utils import exit_on_error
from sc_utils import safeadd

class CanBatterySense(SystemCalcDelegate):
	def get_input(self):
		return [
			('com.victronenergy.battery', [
				'/Sense/Voltage',
				'/Sense/Current',
				'/Sense/Temperature',
				'/Sense/Soc'])
		]

	def get_settings(self):
		return [
			('canbmssense', '/Settings/SystemSetup/CanBmsSense', 0, 0, 1)
		]

	def update_values(self, newvalues):
		bms = BatteryService.instance.bms
		batteryservice = BatteryService.instance.batteryservice

		# TODO Make this a setting. For now, do it only for BatriumV,
		# and only use a battery service that has a proper SOC indication.
		if self._settings['canbmssense'] == 1 and \
				bms is not None and \
				batteryservice is not None and \
				bms.service != batteryservice.service and \
				batteryservice.soc is not None:
			# Copy sense data across
			self._dbusmonitor.set_value_async(bms.service, '/Sense/Voltage', batteryservice.voltage)
			self._dbusmonitor.set_value_async(bms.service, '/Sense/Current', batteryservice.current)
			if batteryservice.temperature is not None:
				self._dbusmonitor.set_value_async(bms.service, '/Sense/Temperature', batteryservice.temperature)
			self._dbusmonitor.set_value_async(bms.service, '/Sense/Soc', batteryservice.soc)
