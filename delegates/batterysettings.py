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
BATTERY_LYNX_SMART_BMS_500_NG = 0xA3E4
BATTERY_LYNX_SMART_BMS_1000 = 0xA3E6
BATTERY_LYNX_SMART_BMS_1000_NG = 0xA3E7
BATTERY_PARALLEL_BMS = 0xA3E3
BATTERY_BSLBATT = 0xB021
BATTERY_ETOWER = 0xB024
BATTERY_CEGASA = 0xB028
BATTERY_HUBBLE = 0xB051
BATTERY_PELIO_L = 0xB029
BATTERY_WECO = 0xB02A
BATTERY_FINDREAMS = 0xB02B
BATTERY_METERBOOST = 0xB02E
BATTERY_ZYC = 0xB01A
BATTERY_PYTES = 0xB01B
BATTERY_LEOCH = 0xB01D
BATTERY_LBSA = 0xB01E
BATTERY_SUNWODA = 0xB01F
BATTERY_BATRIUM_D = 0xB038
BATTERY_SOLUNA = 0xB02F
BATTERY_TAB = 0xB052

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
				BATTERY_DISCOVER_AES, BATTERY_PYTES, BATTERY_LEOCH, BATTERY_LBSA,
				BATTERY_BSLBATT, BATTERY_BMZ, BATTERY_CEGASA, BATTERY_PELIO_L, BATTERY_ZYC,
				BATTERY_SUNWODA, BATTERY_TAB):
			self._settings['vsense'] = 2 # Forced Off
			self._settings['tsense'] = 2 # Forced Off
			self._settings['bol'] = 3 # Forced on
		elif pid in (BATTERY_FREEDOMWON, BATTERY_ETOWER, BATTERY_HUBBLE, BATTERY_FINDREAMS, BATTERY_WECO, BATTERY_BLUENOVA):
			if self._settings['vsense'] & 2:
				self._settings['vsense'] &= 1 # Remove setting if it was forced
			self._settings['tsense'] = 2 # Forced Off
			self._settings['bol'] = 3 # Forced on
		elif pid in (BATTERY_LYNX_SMART_BMS_500, BATTERY_LYNX_SMART_BMS_1000,
				BATTERY_LYNX_SMART_BMS_500_NG, BATTERY_LYNX_SMART_BMS_1000_NG,
				BATTERY_PARALLEL_BMS, BATTERY_METERBOOST, BATTERY_BATRIUM_D, BATTERY_SOLUNA):
			self._settings['vsense'] = 3 # Forced on
			self._settings['tsense'] = 2 # Forced Off
			self._settings['bol'] = 3 # Forced on
		else:
			for s in ('vsense', 'tsense', 'bol'):
				# If it was forced, remove the force bit
				if self._settings[s] & 2:
					self._settings[s] &= 1
