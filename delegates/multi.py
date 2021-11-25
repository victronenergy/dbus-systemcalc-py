from delegates.base import SystemCalcDelegate

class Service(object):
	def __init__(self, monitor, service, instance):
		self.monitor = monitor
		self.service = service
		self.instance = instance

	@property
	def connected(self):
		return self.monitor.get_value(self.service, '/Connected') == 1

	@property
	def active_input(self):
		return self.monitor.get_value(self.service, '/Ac/ActiveIn/ActiveInput')

	@property
	def number_of_inputs(self):
		return self.monitor.get_value(self.service, '/Ac/NumberOfAcInputs')

class Multi(SystemCalcDelegate):
	def __init__(self):
		super(Multi, self).__init__()
		self.multis = {}
		self.multi = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)

	def get_input(self):
		return [('com.victronenergy.vebus', [
				'/Ac/ActiveIn/ActiveInput',
				'/Ac/NumberOfAcInputs'])]

	def get_output(self):
		return [('/VebusService', {'gettext': '%s'})]

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.vebus.'):
			self.multis[service] = Service(self._dbusmonitor, service, instance)
			self._dbusmonitor.track_value(service, "/Connected", self._set_multi)
			self._set_multi()

	def device_removed(self, service, instance):
		if service in self.multis:
			del self.multis[service]
			self._set_multi()

	def _set_multi(self, *args, **kwargs):
		multis = [m for m in self.multis.values() if m.connected]
		if multis:
			self.multi = sorted(multis, key=lambda x: x.instance)[0]
		else:
			self.multi = None

	def update_values(self, newvalues):
		# If there are multis connected, but for some reason none is selected
		# or the current selected one is no longer connected, try to set
		# a new one.
		if self.multis and (self.multi is None or not self.multi.connected):
			self._set_multi()

		newvalues['/VebusService'] = getattr(self.multi, 'service', None)
