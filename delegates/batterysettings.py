from delegates.base import SystemCalcDelegate
from delegates.dvcc import Dvcc

# Battery IDs
BATTERY_BYD = 0xB00A
BATTERY_LYNX_ION = 0x0142

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
		if newservice is not None and newservice.startswith('com.victronenergy.battery.'):
			self.apply_battery_settings(newservice)

	def apply_battery_settings(self, service):
		pid = self._dbusmonitor.get_value(service, '/ProductId')

		# Set good settings for known batteries. Force SVS off and DVCC on
		# for some batteries.
		if pid in (BATTERY_BYD, BATTERY_LYNX_ION):
			self._settings['vsense'] = 4 | (self._settings['vsense'] & 1)
			self._settings['tsense'] = 4 | (self._settings['tsense'] & 1)
			self._settings['bol'] = 6 | (self._settings['bol'] & 1)
		else:
			for s in ('vsense', 'tsense', 'bol'):
				# If it was forced, remove the force bit
				if self._settings[s] & 4:
					self._settings[s] &= 1
