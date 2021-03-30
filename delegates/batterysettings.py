from delegates.base import SystemCalcDelegate
from delegates.dvcc import Dvcc

# Battery IDs
BATTERY_BYD = 0xB00A
BATTERY_BYD_L = 0xB015
BATTERY_BYD_PREMIUM = 0xB019
BATTERY_DISCOVER_AES = 0xB016
BATTERY_FREEDOMWON = 0xB014
BATTERY_BLUENOVA = 0xB020
BATTERY_LYNX_SMART_BMS_500 = 0xA3E5
BATTERY_LYNX_SMART_BMS_1000 = 0xA3E6
BATTERY_BSLBATT = 0xB021

class BatterySettings(SystemCalcDelegate):
	""" Manages battery settings for known batteries. At present
	    it forces DVCC and SVS use for some batteries. """
	def __init__(self, sc):
		super(BatterySettings, self).__init__()
		self.systemcalc = sc

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.battery.') and \
				self.systemcalc._batteryservice == service:
			self.apply_battery_settings(service)

	def battery_service_changed(self, oldservice, newservice):
		self.apply_battery_settings(newservice)

	def apply_battery_settings(self, service):
		pid = None if service is None else self._dbusmonitor.get_value(service, '/ProductId')

		# Set good settings for known batteries. Force SVS off and DVCC on
		# for some batteries.
		if pid in (BATTERY_BYD, BATTERY_BYD_L, BATTERY_BYD_PREMIUM,
				BATTERY_DISCOVER_AES, BATTERY_FREEDOMWON, BATTERY_BLUENOVA,
				BATTERY_LYNX_SMART_BMS_500, BATTERY_LYNX_SMART_BMS_1000, BATTERY_BSLBATT):
			self._settings['vsense'] = 2 # Forced Off
			self._settings['tsense'] = 2 # Forced Off
			self._settings['bol'] = 3 # Forced on
		else:
			for s in ('vsense', 'tsense', 'bol'):
				# If it was forced, remove the force bit
				if self._settings[s] & 2:
					self._settings[s] &= 1
