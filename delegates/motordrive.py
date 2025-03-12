from delegates.base import SystemCalcDelegate

PREFIX = '/MotorDrive'

class MotorDrive(SystemCalcDelegate):
	""" Collect electric motor drive data. """
	def get_input(self):
		return [('com.victronenergy.motordrive', [
				'/Dc/0/Voltage',
				'/Dc/0/Current',
				'/Dc/0/Power',
				'/Motor/Rpm'])]

	def get_output(self):
		return [(PREFIX + '/Power', {'gettext': '%dW'}),
				(PREFIX + '/Voltage', {'gettext': '%.1fV'}),
				(PREFIX + '/Current', {'gettext': '%.2fA'}),
				(PREFIX + '/Rpm', {'gettext': '%drpm'}),
		]

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.motordrive.'):
			self._settings['electricpropulsionenabled'] = 1

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)

	def update_values(self, newvalues):
		# Pick the first motordrive service we find
		for service in sorted(self._dbusmonitor.get_service_list('com.victronenergy.motordrive')):
			newvalues[PREFIX + '/Voltage'] = self._dbusmonitor.get_value(service, '/Dc/0/Voltage')
			newvalues[PREFIX + '/Current'] = self._dbusmonitor.get_value(service, '/Dc/0/Current')
			newvalues[PREFIX + '/Rpm'] = self._dbusmonitor.get_value(service, '/Motor/Rpm')

			# Not sure power is available, calculate it if not
			newvalues[PREFIX + '/Power'] = self._dbusmonitor.get_value(service, '/Dc/0/Power')
			if newvalues[PREFIX + '/Power'] is None:
				newvalues[PREFIX + '/Power'] = newvalues[PREFIX + '/Voltage'] * newvalues[PREFIX + '/Current']

			break
