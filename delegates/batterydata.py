import json
import gobject
from delegates.base import SystemCalcDelegate


# names, custom names, origin, voltage, current, soc, and-so-forth.
# Also state (discharging or charging)

class Tracker(object):
	_paths = (
		'/Dc/0/Voltage',
		'/Dc/0/Current',
		'/Dc/0/Power',
		'/Dc/0/Temperature',
		'/Soc',
		'/ProductName',
		'/CustomName')

	def __init__(self, service, instance, monitor):
		self.service = service
		self.instance = instance
		self.monitor = monitor
		self._tracked = { k: None for k in self._paths }

	def update(self):
		changed = False
		for k, v in self._tracked.iteritems():
			n = self.monitor.get_value(self.service, k)
			if isinstance(n, float):
				n = round(n, 1)
			if n != v:
				self._tracked[k] = n
				changed = True
		return changed

	def data(self):
		power = self._tracked['/Dc/0/Power']
		return {
			'id': self.service,
			'instance': self.instance,
			'voltage': self._tracked['/Dc/0/Voltage'],
			'current': self._tracked['/Dc/0/Current'],
			'power': power,
			'temperature': self._tracked['/Dc/0/Temperature'],
			'soc': self._tracked['/Soc'],
			'name': self._tracked['/CustomName'] or self._tracked['/ProductName'],
			'state': None if power is None else (1 if power > 30 else (2 if power < 30 else 0))
		}

class BatteryData(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self.batteries = {}
		self.changed = False

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Batteries', value=None)
		self._timer = gobject.timeout_add(3000, self._on_timer)

	def device_added(self, service, instance, do_service_change=True):
		if service.startswith('com.victronenergy.battery.'):
			self.batteries[service] = Tracker(service, instance, self._dbusmonitor)
			self.changed = True

	def device_removed(self, service, instance):
		if service in self.batteries:
			del self.batteries[service]
			self.changed = True

	def update_values(self, newvalues=None):
		self.changed = any(
			[tracker.update() for tracker in self.batteries.itervalues()]) or \
			self.changed

	def _on_timer(self):
		if self.changed:
			# Update the json
			active = self._dbusservice['/ActiveBatteryService']
			is_active = lambda x: self._dbusservice['/ActiveBatteryService'] == "{}/{}".format('.'.join(x.service.split('.')[:3]), x.instance)
			self._dbusservice['/Batteries'] = json.dumps([
				dict(tracked.data(), active_battery_service=is_active(tracked)) for tracked in self.batteries.itervalues()
			])
			self.changed = False
		return True
