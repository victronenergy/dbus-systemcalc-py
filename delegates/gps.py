from gi.repository import GLib
from delegates.base import SystemCalcDelegate
import math
from datetime import datetime

SPEED_EMA_TIME_CONSTANT = 1.0  # seconds

class Gps(SystemCalcDelegate):

	_get_time = datetime.now

	def __init__(self):
		super(Gps, self).__init__()
		self.gpses = set()
		self.last_time = None
		self.speed_ema = None

	def get_output(self):
		return [('/GpsSpeed', {'gettext': '%dm/s'})]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(Gps, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/GpsService', value=None)

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.gps.'):
			self.gpses.add((instance, service))
			self._dbusmonitor.track_value(service, "/Fix", self.update)
			GLib.idle_add(self.update)

	def device_removed(self, service, instance):
		self.gpses.discard((instance, service))
		self.update()

	def get_input(self):
		return [('com.victronenergy.gps', [
				'/DeviceInstance',
				'/Fix',
				'/Speed'])]

	def update(self, *args):
		for instance, service in sorted(self.gpses):
			fix = self._dbusmonitor.get_value(service, '/Fix')
			if fix:
				if self._dbusservice['/GpsService'] != service:
					self.last_time = None
					self.speed_ema = None
				self._dbusservice['/GpsService'] = service
				break
		else:
			self._dbusservice['/GpsService'] = None

	def update_values(self, newvalues):
		if self._dbusservice['/GpsService'] is not None:
			speed = self._dbusmonitor.get_value(
				self._dbusservice['/GpsService'], '/Speed')
			if speed is None:
				self.last_time = None
				self.speed_ema = None
			elif self.last_time is None:
				self.last_time = self._get_time()
				self.speed_ema = speed
			else:
				now = self._get_time()
				dt = (now - self.last_time).total_seconds()
				alpha = 1 - math.exp(-dt / SPEED_EMA_TIME_CONSTANT)
				self.speed_ema = alpha * speed + (1 - alpha) * self.speed_ema
				self.last_time = now
			newvalues['/GpsSpeed'] = self.speed_ema
