from delegates.base import SystemCalcDelegate

class Gps(SystemCalcDelegate):
	def __init__(self):
		super(Gps, self).__init__()
		self.gpses = set()

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(Gps, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Gps/Position/Latitude', value=None)
		self._dbusservice.add_path('/Gps/Position/Longitude', value=None)
		self._dbusservice.add_path('/Gps/Course', value=None)
		self._dbusservice.add_path('/Gps/Speed', value=None)
		self._dbusservice.add_path('/Gps/Altitude', value=None)

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.gps.'):
			self.gpses.add((instance, service))
			self._dbusmonitor.track_value(service, None, self.update)
			self.update()

	def device_removed(self, service, instance):
		self.gpses.discard((instance, service))
		self.update()

	def get_input(self):
		return [('com.victronenergy.gps', [
				'/DeviceInstance',
				'/Position/Latitude',
				'/Position/Longitude',
				'/Course',
				'/Speed',
				'/Altitude',
				'/Fix'])]

	def update(self, *args):
		for instance, service in sorted(self.gpses):
			fix = self._dbusmonitor.get_value(service, '/Fix')
			if fix:
				for p in ('/Position/Latitude', '/Position/Longitude',
						'/Course', '/Speed', '/Altitude'):
					self._dbusservice['/Gps' + p] = self._dbusmonitor.get_value(
						service, p)
				break
