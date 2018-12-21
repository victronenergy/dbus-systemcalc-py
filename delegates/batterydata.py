import json
import gobject
from collections import defaultdict
from itertools import chain
from delegates.base import SystemCalcDelegate


# names, custom names, origin, voltage, current, soc, and-so-forth.
# Also state (discharging or charging)

class BatteryTracker(object):
	_paths = (
		'/Dc/0/Voltage',
		'/Dc/0/Current',
		'/Dc/0/Power',
		'/Dc/0/Temperature',
		'/Soc',
		'/TimeToGo',
		'/ProductName',
		'/CustomName')

	def __init__(self, service, instance, monitor):
		self.service = service
		self.instance = instance
		self.monitor = monitor
		self._tracked = { k: None for k in self._paths }

	@property
	def valid(self):
		# It is valid if it has at least a voltage
		return self._tracked['/Dc/0/Voltage'] is not None

	@property
	def name(self):
		return self._tracked['/CustomName'] or self._tracked['/ProductName']

	def update(self):
		changed = False
		for k, v in self._tracked.iteritems():
			n = self.monitor.get_value(self.service, k)
			if n != v:
				self._tracked[k] = n
				changed = True
		return changed

	def _data(self):
		power = self._tracked['/Dc/0/Power']
		return {
			'id': self.service,
			'instance': self.instance,
			'voltage': self._tracked['/Dc/0/Voltage'],
			'current': self._tracked['/Dc/0/Current'],
			'power': power,
			'temperature': self._tracked['/Dc/0/Temperature'],
			'soc': self._tracked['/Soc'],
			'timetogo': self._tracked['/TimeToGo'],
			'name': self.name,
			'state': None if power is None else (1 if power > 30 else (2 if power < 30 else 0))
		}

	def data(self):
		return { k: v for k, v in self._data().iteritems() if v is not None }

class SecondaryBatteryTracker(BatteryTracker):
	""" Used to track the starter battery where available. """

	def __new__(cls, service, monitor, channel):
		instance = super(SecondaryBatteryTracker, cls).__new__(cls, service, None, monitor)
		instance._paths = (
			'/Dc/{}/Voltage'.format(channel),
			'/Dc/{}/Current'.format(channel),
			'/CustomName', '/ProductName')
		return instance

	def __init__(self, service, monitor, channel):
		super(SecondaryBatteryTracker, self).__init__(service, None, monitor)
		self.channel = channel
		self.id = '{}:{}'.format(self.service, self.channel)

	@property
	def valid(self):
		# It is valid if it has at least a voltage
		return self._tracked['/Dc/{}/Voltage'.format(self.channel)] is not None

	def _data(self):
		return {
			'id': self.id,
			'voltage': self._tracked['/Dc/{}/Voltage'.format(self.channel)],
			'current': self._tracked['/Dc/{}/Current'.format(self.channel)],
			'name': "{} #{}".format(self.name, self.channel)
		}

class BatteryData(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self.batteries = defaultdict(list)
		self.changed = False

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Batteries', value=None)
		self._timer = gobject.timeout_add(5000, self._on_timer)

	def device_added(self, service, instance, do_service_change=True):
		if service.startswith('com.victronenergy.battery.'):
			self.batteries[service].extend((
				BatteryTracker(service, instance, self._dbusmonitor),
				SecondaryBatteryTracker(service, self._dbusmonitor, 1)))
			self.changed = True
		elif service.startswith('com.victronenergy.charger.'):
			self.batteries[service].extend((
				SecondaryBatteryTracker(service, self._dbusmonitor, 0),
				SecondaryBatteryTracker(service, self._dbusmonitor, 1),
				SecondaryBatteryTracker(service, self._dbusmonitor, 2)))
			self.changed = True

	def device_removed(self, service, instance):
		if service in self.batteries:
			del self.batteries[service]
			self.changed = True

	def update_values(self, newvalues=None):
		self.changed = any(
			[tracker.update() for tracker in chain.from_iterable(self.batteries.itervalues())]) or \
			self.changed

	def _on_timer(self):
		if self.changed:
			# Update the json
			active = self._dbusservice['/ActiveBatteryService']
			is_active = lambda x: self._dbusservice['/ActiveBatteryService'] == "{}/{}".format('.'.join(x.service.split('.')[:3]), x.instance)
			self._dbusservice['/Batteries'] = json.dumps([
				dict(tracked.data(), active_battery_service=is_active(tracked)) for tracked in chain.from_iterable(self.batteries.itervalues()) if tracked.valid
			])
			self.changed = False
		return True
