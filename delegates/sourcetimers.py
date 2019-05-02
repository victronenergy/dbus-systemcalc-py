from time import time
import gobject

# Victron packages
from ve_utils import exit_on_error
from delegates.base import SystemCalcDelegate

class SourceTimers(SystemCalcDelegate):
	""" Watches the active input, and based on settings determines how much
	    time was spent on Grid/Generator/Inverter or Off. """
	_paths = {
		1: '/Timers/TimeOnGrid',
		2: '/Timers/TimeOnGenerator',
		3: '/Timers/TimeOnGrid', # Shore is deemed to be the same as grid
		0xF0: '/Timers/TimeOnInverter'
	}

	# So we can override it in testing
	_get_time = lambda s: int(time())

	def __init__(self):
		super(SourceTimers, self).__init__()
		self._timer = None
		self._lastrun = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(SourceTimers, self).set_sources(dbusmonitor, settings, dbusservice)
		for p in set(self._paths.itervalues()):
			self._dbusservice.add_path(p, value=0)
		self._dbusservice.add_path('/Timers/TimeOff', value=0)
		self._on_timer()
		self._timer = gobject.timeout_add(10000, exit_on_error, self._on_timer)

	@property
	def elapsed(self):
		now = self._get_time()
		try:
			return 0 if self._lastrun is None else now - self._lastrun
		finally:
			self._lastrun = now

	def _on_timer(self):
		try:
			active_in = self._dbusservice['/Ac/ActiveIn/Source']
			system_state = self._dbusservice['/SystemState/State']
		except KeyError:
			self._lastrun = self._get_time()
			return True

		if system_state == 0:
			path = '/Timers/TimeOff'
		else:
			path = self._paths.get(active_in, '/Timers/TimeOnInverter')

		self._dbusservice[path] += self.elapsed
		return True
