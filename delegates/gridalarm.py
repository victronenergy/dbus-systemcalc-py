import gobject
from delegates.base import SystemCalcDelegate

class GridAlarm(SystemCalcDelegate):
	ALARM_TIMEOUT = 30000
	def __init__(self):
		super(GridAlarm, self).__init__()
		# We arm the alarm only once grid power was detected.
		# TODO Disarm if the Multi is switched off.
		self.armed = False
		self._timer = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(GridAlarm, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Ac/Alarms/AcLost', value=0)

	def get_settings(self):
		return [
			('grid_alarm_enabled', '/Settings/Alarm/System/AcLost', 0, 0, 1),
		]

	def _raise_alarm(self):
		self._dbusservice['/Ac/Alarms/AcLost'] = 2
		self._timer = None
		return False

	def raise_alarm(self):
		self._timer = gobject.timeout_add(self.ALARM_TIMEOUT, self._raise_alarm)

	def cancel_alarm(self):
		if self._timer is not None:
			gobject.source_remove(self._timer)
		self._dbusservice['/Ac/Alarms/AcLost'] = 0

	def update_values(self, newvalues):
		if self._settings['grid_alarm_enabled']:
			source = newvalues.get('/Ac/ActiveIn/Source')
			if self.armed and source == 0xF0:
				# No active input, raise alarm
				self.raise_alarm()
			elif source is None:
				# This happens if there is no Multi, eg during reset
				# or startup. Disarm and don't raise an alarm during this
				# period.
				self.cancel_alarm()
				self.armed = False
			else:
				# source is either 0 (not available) or 1-3 (grid, generator,
				# shore). No alarm, but we arm only if there is AC available.
				self.cancel_alarm()
				self.armed = self.armed or (0 < source < 4)
		else:
			self.cancel_alarm()
