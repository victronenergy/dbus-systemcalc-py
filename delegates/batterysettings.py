from delegates.base import SystemCalcDelegate
from delegates.dvcc import Dvcc
from delegates.batteryservice import BatteryService

# Battery IDs
BATTERY_BMZ = 0xB005
BATTERY_PYLONTECH = 0xB009
BATTERY_BYD = 0xB00A
BATTERY_BYD_L = 0xB015
BATTERY_BYD_PREMIUM = 0xB019
BATTERY_DISCOVER_AES = 0xB016
BATTERY_FREEDOMWON = 0xB014
BATTERY_BLUENOVA = 0xB020
BATTERY_LYNX_SMART_BMS_500 = 0xA3E5
BATTERY_LYNX_SMART_BMS_1000 = 0xA3E6
BATTERY_BSLBATT = 0xB021
BATTERY_ETOWER = 0xB024
BATTERY_CEGASA = 0xB028
BATTERY_HUBBLE = 0xB051
BATTERY_PELIO_L = 0xB029
BATTERY_WECO = 0xB02A
BATTERY_FINDREAMS = 0xB02B

class BatterySettings(SystemCalcDelegate):
	""" Manages battery settings for known batteries. At present
	    it forces DVCC and SVS use for some batteries. """
	def __init__(self, sc):
		super(BatterySettings, self).__init__()
		self.systemcalc = sc

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(BatterySettings, self).set_sources(dbusmonitor, settings, dbusservice)
		BatteryService.instance.add_bms_changed_callback(self.apply_battery_settings)

	def apply_battery_settings(self, service):
		pid = None if service is None else self._dbusmonitor.get_value(service, '/ProductId')

		# Set good settings for known batteries. Force SVS off and DVCC on
		# for some batteries.
		if pid in (BATTERY_PYLONTECH, BATTERY_BYD, BATTERY_BYD_L, BATTERY_BYD_PREMIUM,
				BATTERY_DISCOVER_AES, BATTERY_BLUENOVA,
				BATTERY_BSLBATT, BATTERY_BMZ, BATTERY_CEGASA, BATTERY_PELIO_L):
			self._settings['vsense'] = 2 # Forced Off
			self._settings['tsense'] = 2 # Forced Off
			self._settings['bol'] = 3 # Forced on
		elif pid in (BATTERY_FREEDOMWON, BATTERY_ETOWER, BATTERY_HUBBLE, BATTERY_FINDREAMS, BATTERY_WECO):
			if self._settings['vsense'] & 2:
				self._settings['vsense'] &= 1 # Remove setting if it was forced
			self._settings['tsense'] = 2 # Forced Off
			self._settings['bol'] = 3 # Forced on
		elif pid in (BATTERY_LYNX_SMART_BMS_500, BATTERY_LYNX_SMART_BMS_1000):
			self._settings['vsense'] = 3 # Forced on
			self._settings['tsense'] = 2 # Forced Off
			self._settings['bol'] = 3 # Forced on
		else:
			for s in ('vsense', 'tsense', 'bol'):
				# If it was forced, remove the force bit
				if self._settings[s] & 2:
					self._settings[s] &= 1
