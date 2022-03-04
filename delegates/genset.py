from delegates.base import SystemCalcDelegate
from datetime import datetime
from time import time

ts_to_str = lambda x: datetime.fromtimestamp(x).strftime('%Y-%m-%d %H:%M:%S')

PREFIX = '/Ac/Genset'


class GensetStartStop(SystemCalcDelegate):
	""" Relay a unified view of what generator start/stop is doing. This
	    clears up the distinction between relay/fisherpanda as well. """

	def get_input(self):
		return [('com.victronenergy.generator', [
				'/RunningByConditionCode',
				'/Runtime',
				'/LastStartTime'])]

	def get_output(self):
		return [('{}/Runtime'.format(PREFIX), {'gettext': '%d'}),
				('{}/RunningByConditionCode'.format(PREFIX), {'gettext': '%d'}),
		]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('{}/LastStartTime'.format(PREFIX), None,
			gettextcallback=lambda p, v: ts_to_str(v) if v is not None else '---')

	@property
	def starttime(self):
		try:
			return self._dbusservice['{}/LastStartTime'.format(PREFIX)]
		except KeyError:
			return None

	@starttime.setter
	def starttime(self, v):
		self._dbusservice['{}/LastStartTime'.format(PREFIX)] = v

	def update_values(self, newvalues):
		for service in sorted(self._dbusmonitor.get_service_list('com.victronenergy.generator')):
			rbc = self._dbusmonitor.get_value(service, '/RunningByConditionCode')
			if rbc is not None:
				if self._dbusservice[PREFIX + '/RunningByConditionCode'] == 0 and rbc > 0:
					# Generator was started, update LastStartTime
					self.starttime = int(time())

				newvalues[PREFIX + '/RunningByConditionCode'] = rbc

				# Update runtime in 10 second increments, we don't need more than that
				rt = self._dbusmonitor.get_value(service, '/Runtime')
				newvalues[PREFIX + '/Runtime'] = None if rt is None else 10 * (rt // 10)
				break
