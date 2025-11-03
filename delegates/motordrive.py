from gi.repository import GLib
from delegates.base import SystemCalcDelegate

PREFIX = '/MotorDrive'

class MotorDrive(SystemCalcDelegate):
	""" Collect electric motor drive data. """

	def __init__(self):
		super(MotorDrive, self).__init__()
		self.motordrives = set()

	def get_input(self):
		return [('com.victronenergy.motordrive', [
				'/DeviceInstance',
				'/Dc/0/Voltage',
				'/Dc/0/Current',
				'/Dc/0/Power',
				'/Motor/RPM'])]

	def get_output(self):
		return [(PREFIX + '/0/Service', {'gettext': '%s'}),
				(PREFIX + '/Power', {'gettext': '%dW'}),
				(PREFIX + '/Voltage', {'gettext': '%.1fV'}),
				(PREFIX + '/Current', {'gettext': '%.2fA'}),
				(PREFIX + '/0/RPM', {'gettext': '%drpm'}),
		]

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.motordrive.'):
			self.motordrives.add((instance, service))
			self._settings['electricpropulsionenabled'] = 1
			# Track a value so we get update() callbacks when data changes
			self._dbusmonitor.track_value(service, "/Dc/0/Voltage", self.update)
			GLib.idle_add(self.update)

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(MotorDrive, self).set_sources(dbusmonitor, settings, dbusservice)

	def device_removed(self, service, instance):
		self.motordrives.discard((instance, service))
		self.update()

	def update(self, *args):
		# Pick the motordrive with the lowest instance
		for instance, service in sorted(self.motordrives):
			self._dbusservice[PREFIX + '/0/Service'] = service
			break
		else:
			self._dbusservice[PREFIX + '/0/Service'] = None

	def update_values(self, newvalues):
		service = self._dbusservice[PREFIX + '/0/Service']
		if service is None:
			return

		newvalues[PREFIX + '/Voltage'] = self._dbusmonitor.get_value(service, '/Dc/0/Voltage')
		newvalues[PREFIX + '/Current'] = self._dbusmonitor.get_value(service, '/Dc/0/Current')

		# RPM of multiple drives can't be aggregated, so store it with the index.
		newvalues[PREFIX + '/0/RPM'] = self._dbusmonitor.get_value(service, '/Motor/RPM')

		# Prefer reported power; fall back to V*I if needed.
		pwr = self._dbusmonitor.get_value(service, '/Dc/0/Power')
		if pwr is None:
			v = newvalues[PREFIX + '/Voltage']
			i = newvalues[PREFIX + '/Current']
			pwr = v * i if v is not None and i is not None else None
		newvalues[PREFIX + '/Power'] = pwr
