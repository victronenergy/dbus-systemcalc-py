import gobject
from delegates.base import SystemCalcDelegate

class GridAlarm(SystemCalcDelegate):
	ALARM_TIMEOUT = 15000
	def __init__(self):
		super(GridAlarm, self).__init__()
		self._timer = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(GridAlarm, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Ac/Alarms/GridLost', value=None)

	def get_settings(self):
		return [
			('grid_alarm_enabled', '/Settings/Alarm/System/GridLost', 0, 0, 1),
		]

	def _raise_alarm(self):
		self._dbusservice['/Ac/Alarms/GridLost'] = 2
		self._timer = None
		return False

	def raise_alarm(self):
		if self._timer is None:
			self._timer = gobject.timeout_add(self.ALARM_TIMEOUT, self._raise_alarm)

	def cancel_alarm(self, v=0):
		if self._timer is not None:
			gobject.source_remove(self._timer)
			self._timer = None
		self._dbusservice['/Ac/Alarms/GridLost'] = v

	def update_values(self, newvalues):
		if self._settings['grid_alarm_enabled']:
			source = newvalues.get('/Ac/ActiveIn/Source')
			if source in (0xF0, 2):
				# No active input, or generator input is active. Raise the
				# alarm. An active generator will be treated as lost grid.
				# First we need to be sure we're not dealing with a Multi
				# Compact that shows Disconnected when it is off.
				vebus_path = newvalues.get('/VebusService')
				if self._dbusmonitor.get_value(vebus_path, '/Mode') == 3:
					self.raise_alarm()
				else:
					self.cancel_alarm()
			elif source in (0, 1, 3):
				# Source can be:
				# None: Multi is gone, eg during reset or startup. Do nothing.
				# 0: Not available - active input has no type configured. Assume it is grid.
				# 1: Grid - cancel the alarm.
				# 3: Shore - same as grid
				self.cancel_alarm()
		else:
			self.cancel_alarm(None)
