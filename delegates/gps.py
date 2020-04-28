from delegates.base import SystemCalcDelegate

class Gps(SystemCalcDelegate):
	def __init__(self):
		super(Gps, self).__init__()
		self.gpses = set()

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(Gps, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/GpsService', value=None)

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.gps.'):
			self.gpses.add((instance, service))
			self._dbusmonitor.track_value(service, "/Fix", self.update)
			self.update()

	def device_removed(self, service, instance):
		self.gpses.discard((instance, service))
		self.update()

	def get_input(self):
		return [('com.victronenergy.gps', [
				'/DeviceInstance',
				'/Fix'])]

	def update(self, *args):
		for instance, service in sorted(self.gpses):
			fix = self._dbusmonitor.get_value(service, '/Fix')
			if fix:
				self._dbusservice['/GpsService'] = service
				break
		else:
			self._dbusservice['/GpsService'] = None
