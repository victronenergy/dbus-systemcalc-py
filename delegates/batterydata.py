import gobject
import json
from collections import defaultdict
from itertools import chain
from functools import partial
from sc_utils import reify, smart_dict
from delegates.base import SystemCalcDelegate

# Victron packages
from ve_utils import exit_on_error

class BatteryConfiguration(object):
	""" Holds custom mapping information about a service that corresponds to a
	    battery. """
	def __init__(self, parent, service):
		self.parent = parent
		self.service = str(service)
		self.name = None
		self.enabled = False
		self.bind_settings()

	def bind_settings(self):
		config_id = self.service.replace('.', '_')
		self.service_item = self.parent._settings.addSetting(
			"/Settings/SystemSetup/Batteries/Configuration/{}/Service".format(config_id),
			"", 0, 0, callback=partial(self.on_setting_change, "service", str))
		self.name_item = self.parent._settings.addSetting(
			"/Settings/SystemSetup/Batteries/Configuration/{}/Name".format(config_id),
			"", 0, 0, callback=partial(self.on_setting_change, "name", str))
		self.enabled_item = self.parent._settings.addSetting(
			"/Settings/SystemSetup/Batteries/Configuration/{}/Enabled".format(config_id),
			0, 0, 1, callback=partial(self.on_setting_change, "enabled", bool))
		self.service_item.set_value(self.service)
		self.enabled = bool(self.enabled_item.get_value())
		self.name = str(self.name_item.get_value())

	def on_setting_change(self, key, cast, service, path, value):
		setattr(self, key, cast(value['Value']))
		self.parent.changed = True


class BatteryTracker(object):
	_paths = (
		'/Dc/0/Voltage',
		'/Dc/0/Current',
		'/Dc/0/Power',
		'/Dc/0/Temperature',
		'/Soc',
		'/TimeToGo')

	def __init__(self, service, instance, monitor):
		self.service = service
		self.instance = instance
		self.monitor = monitor
		self.channel = None
		self._tracked = { k: None for k in self._paths }

	@property
	def valid(self):
		# It is valid if it has at least a voltage
		return self.monitor.get_value(self.service, '/Dc/0/Voltage') is not None

	@property
	def name(self):
		return self.monitor.get_value(self.service, '/CustomName') or self.monitor.get_value(self.service, '/ProductName')

	@reify
	def service_type(self):
		""" Return the third item in the dbus service, eg battery for
		    com.victronenergy.battery. """
		return self.service.split('.')[2]

	@reify
	def service_id(self):
		""" Generate an identifier that uniquely identifies the type
			of service and the instance. """
		return "{}/{}".format('.'.join(self.service.split('.')[:3]), self.instance)

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
		voltage = self._tracked['/Dc/0/Voltage']
		current = self._tracked['/Dc/0/Current']
		calculated_power = voltage * current if None not in (voltage, current) else None
		return {
			'id': self.service,
			'instance': self.instance,
			'voltage': voltage,
			'current': current,
			'power': power or calculated_power,
			'temperature': self._tracked['/Dc/0/Temperature'],
			'soc': self._tracked['/Soc'],
			'timetogo': self._tracked.get('/TimeToGo', None),
			'name': self.name,
			'state': None if power is None else (1 if power > 30 else (2 if power < -30 else 0))
		}

	def data(self):
		return { k: v for k, v in self._data().iteritems() if v is not None }

class SecondaryBatteryTracker(BatteryTracker):
	""" Used to track the starter battery where available. """

	def __new__(cls, service, instance, monitor, channel):
		instance = super(SecondaryBatteryTracker, cls).__new__(cls, service, instance, monitor)
		instance._paths = (
			'/Dc/{}/Voltage'.format(channel),
			'/Dc/{}/Current'.format(channel),
			'/Dc/{}/Power'.format(channel))
		return instance

	def __init__(self, service, instance, monitor, channel):
		super(SecondaryBatteryTracker, self).__init__(service, instance, monitor)
		self.channel = channel
		self.id = '{}:{}'.format(self.service, self.channel)

	@property
	def valid(self):
		# It is valid if it has at least a voltage
		return self.monitor.get_value(self.service, '/Dc/{}/Voltage'.format(self.channel)) is not None

	@reify
	def service_id(self):
		""" Generate an identifier that uniquely identifies the type
			of service and the instance. """
		return "{}/{}/{}".format('.'.join(self.service.split('.')[:3]), self.instance, self.channel)

	def _data(self):
		voltage = self._tracked['/Dc/{}/Voltage'.format(self.channel)]
		current = self._tracked['/Dc/{}/Current'.format(self.channel)]
		calculated_power = voltage * current if None not in (voltage, current) else None
		return {
			'id': self.id,
			'voltage': voltage,
			'current': current,
			'power': self._tracked['/Dc/{}/Power'.format(self.channel)] or calculated_power,
			'name': self.name
		}

class FischerPandaTracker(BatteryTracker):
	_paths = ('/StarterVoltage', )

	@property
	def valid(self):
		# It is valid if it has at least a voltage
		return self.monitor.get_value(self.service, '/StarterVoltage') is not None

	def _data(self):
		voltage = self._tracked['/StarterVoltage']
		return {
			'id': self.service,
			'instance': self.instance,
			'voltage': voltage,
			'name': self.name
		}

class MultiTracker(BatteryTracker):
	_paths = (
		'/Dc/0/Voltage',
		'/Dc/0/Current',
		'/Dc/0/Power',
		'/Dc/0/Temperature',
		'/Soc')

	def __init__(self, service, instance, dbusservice, monitor):
		super(MultiTracker, self).__init__(service, instance, monitor)
		self._dbusservice = dbusservice

	@property
	def valid(self):
		# The Multi is always a valid battery measurement
		return True

class BatteryData(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self.batteries = defaultdict(list)
		self.changed = False
		self.deviceschanged = False
		self.configured_batteries = {}
		self.active_battery_service = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)

		# Publish the battery configuration
		self._dbusservice.add_path('/Batteries', value=None)
		self._dbusservice.add_path('/AvailableBatteries', value=None)
		self._timer = gobject.timeout_add(5000, exit_on_error, self._on_timer)

	def device_added(self, service, instance, do_service_change=True):
		self.deviceschanged = True
		self.changed = True
		if service.startswith('com.victronenergy.battery.'):
			self.add_trackers(service,
				BatteryTracker(service, instance, self._dbusmonitor),
				SecondaryBatteryTracker(service, instance, self._dbusmonitor, 1))
		elif service.startswith('com.victronenergy.charger.'):
			self.add_trackers(service,
				SecondaryBatteryTracker(service, instance, self._dbusmonitor, 0),
				SecondaryBatteryTracker(service, instance, self._dbusmonitor, 1),
				SecondaryBatteryTracker(service, instance, self._dbusmonitor, 2))
		elif service.startswith('com.victronenergy.vebus.'):
			self.add_trackers(service,
				MultiTracker(service, instance, self._dbusservice, self._dbusmonitor))
		elif service.startswith('com.victronenergy.genset.'):
			self.add_trackers(service, FischerPandaTracker(service, instance, self._dbusmonitor))
		elif service == 'com.victronenergy.settings':
			for cb in self.configured_batteries.itervalues():
				cb.bind_settings()

	def device_removed(self, service, instance):
		if service in self.batteries:
			del self.batteries[service]
			self.changed = True
			self.deviceschanged = True

	def add_trackers(self, service, *args):
		self.batteries[service].extend(args)
		for t in args:
			if t.service_id not in self.configured_batteries:
				self.add_configured_battery(t.service_id)

	def is_enabled(self, tracker):
		return tracker.service_id in self.configured_batteries and \
			self.configured_batteries[tracker.service_id].enabled

	def config_name(self, tracker):
		try:
			return self.configured_batteries[tracker.service_id].name or None
		except (KeyError, AttributeError):
			return None

	def update_values(self, newvalues=None):
		self.changed = any([tracker.update() for tracker in chain.from_iterable(
			self.batteries.itervalues())]) or self.changed

	def add_configured_battery(self, service):
		self.configured_batteries[service] = BatteryConfiguration(
			self, service)

	def _on_timer(self):
		active = self._dbusservice['/ActiveBatteryService']
		if self.changed or self.active_battery_service != active:
			# Update the summary
			is_active = lambda x: active == x.service_id
			kwargs = lambda x: {k: v for k, v in (('active_battery_service', is_active(x)),
				('name', self.config_name(x))) if v is not None}

			self._dbusservice['/Batteries'] = [
				dict(tracked.data(), **kwargs(tracked)) \
					for tracked in chain.from_iterable(self.batteries.itervalues()) \
					if (tracked.valid and self.is_enabled(tracked)) or is_active(tracked)
			]

		if self.deviceschanged or self.active_battery_service != active:
			# This is returned as JSON, because QML won't let us pass
			# lists of objects.
			self._dbusservice['/AvailableBatteries'] = json.dumps({
				b.service_id: {
					'name': b.name,
					'channel': b.channel,
					'type': b.service_type
				} for b in chain.from_iterable(self.batteries.itervalues()) if b.valid })
			self.deviceschanged = False

			self.changed = False

		self.active_battery_service = active
		return True
