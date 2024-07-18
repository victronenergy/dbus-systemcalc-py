from itertools import count
from delegates.base import SystemCalcDelegate
from delegates.multi import Multi

class AcSource(object):
	def __init__(self, monitor, service, instance):
		self.monitor = monitor
		self.service = service
		self.instance = instance

	@property
	def product_id(self):
		return self.monitor.get_value(self.service, '/ProductId')

	@property
	def device_type(self):
		return self.monitor.get_value(self.service, '/DeviceType')

class GridMeter(AcSource):
	@property
	def device_type(self):
		# If grid meter has no DeviceType, use 0 as a generic marker.
		return self.monitor.get_value(self.service, '/DeviceType', 0)

class InverterCharger(AcSource):
	@property
	def active_input(self):
		return self.monitor.get_value(self.service, '/Ac/ActiveIn/ActiveInput')

	@property
	def number_of_inputs(self):
		return self.monitor.get_value(self.service, '/Ac/NumberOfAcInputs')

	@property
	def input_types(self):
		return [(i, self.monitor.get_value(self.service,
			'/Ac/In/{}/Type'.format(i+1))) for i in range(self.number_of_inputs or 0)]

class AcInputs(SystemCalcDelegate):
	def __init__(self):
		super(AcInputs, self).__init__()
		self.gridmeters = {}
		self.gensetmeters = {}
		self.inverterchargers = {}
		self.gridmeter = None
		self.gensetmeter = None
		self.invertercharger = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)

	def get_input(self):
		return [('com.victronenergy.acsystem', [
				'/Ac/ActiveIn/ActiveInput',
				'/Ac/NumberOfAcInputs',
				'/Ac/In/1/Type',
				'/Ac/In/2/Type'])]

	def get_output(self):
		return [('/Ac/In/0/ServiceName', {'gettext': '%s'}),
				('/Ac/In/0/ServiceType', {'gettext': '%s'}),
				('/Ac/In/0/DeviceInstance', {'gettext': '%d'}),
				('/Ac/In/0/VrmDeviceInstance', {'gettext': '%d'}),
				('/Ac/In/0/Source', {'gettext': '%d'}),
				('/Ac/In/0/Connected', {'gettext': '%d'}),
				('/Ac/In/1/ServiceName', {'gettext': '%s'}),
				('/Ac/In/1/ServiceType', {'gettext': '%s'}),
				('/Ac/In/1/DeviceInstance', {'gettext': '%d'}),
				('/Ac/In/1/VrmDeviceInstance', {'gettext': '%d'}),
				('/Ac/In/1/Source', {'gettext': '%d'}),
				('/Ac/In/1/Connected', {'gettext': '%d'}),
				('/Ac/In/NumberOfAcInputs', {'gettext': '%d'}),
		]

	def device_added(self, service, instance, *args):
		# Look for grid and genset
		if service.startswith('com.victronenergy.grid.'):
			self.gridmeters[service] = GridMeter(self._dbusmonitor, service, instance)
			self._set_gridmeter()
		elif service.startswith('com.victronenergy.genset.'):
			self.gensetmeters[service] = AcSource(self._dbusmonitor, service, instance)
			self._set_gensetmeter()
		elif service.startswith('com.victronenergy.acsystem.'):
			self.inverterchargers[service] = InverterCharger(self._dbusmonitor, service, instance)
			self._set_invertercharger()

	def device_removed(self, service, instance):
		if service in self.gridmeters:
			del self.gridmeters[service]
			self._set_gridmeter()
		if service in self.gensetmeters:
			del self.gensetmeters[service]
			self._set_gensetmeter()
		if service in self.inverterchargers:
			del self.inverterchargers[service]
			self._set_invertercharger()

	def _get_meter(self, meters):
		if meters:
			return sorted(meters.values(), key=lambda x: x.instance)[0]
		return None

	def _set_gridmeter(self):
		self.gridmeter = self._get_meter(self.gridmeters)

	def _set_gensetmeter(self):
		self.gensetmeter = self._get_meter(self.gensetmeters)

	def _set_invertercharger(self):
		self.invertercharger = self._get_meter(self.inverterchargers)

	def input_tree(self, inp, service, instance, typ, active):
		# Historical hackery requires the device instance of vebus
		# on ttyO1 (ie a CCGX) to be zero. Reflect that here even
		# though ideally such hackery must die.
		vrminstance = 0 if service.endswith('.vebus.ttyO1') else instance
		return {
			'/Ac/In/{}/ServiceName'.format(inp): service,
			'/Ac/In/{}/ServiceType'.format(inp):
				service.split('.')[2] if service is not None else None,
			'/Ac/In/{}/DeviceInstance'.format(inp): instance,
			'/Ac/In/{}/VrmDeviceInstance'.format(inp): vrminstance,
			'/Ac/In/{}/Source'.format(inp): typ,
			'/Ac/In/{}/Connected'.format(inp): active
		}

	def update_values(self, newvalues):
		multi = Multi.instance.multi or self.invertercharger
		input_count = 0
		if multi is None:
			# This is a system without an inverter/charger. If there is a
			# grid meter or a genset, we can display that. This works because
			# the meter itself is powered by the grid/genset, so if it shows
			# up on dbus, we can assume it is connected. We assume the first
			# one found is actually active, with grid taking priority.
			sources = zip(
				[x for x in (self.gridmeter, self.gensetmeter) if x is not None],
				(1, 2))
			for source, t in sources:
				newvalues.update(self.input_tree(input_count, source.service, source.instance, t, input_count==0))
				input_count += 1
			newvalues['/Ac/In/NumberOfAcInputs'] = input_count
		else:
			for i, t in getattr(multi, 'input_types', ()):
				if t is None or (not 0 < t < 4): # Input is marked "Not available", or invalid
					continue

				source = self.gridmeter if t in (1, 3) else self.gensetmeter

				if source is None:
					# Use vebus or inverter/charger
					newvalues.update(self.input_tree(input_count, multi.service, multi.instance, t, int(multi.active_input == i)))
				else:
					active = getattr(multi, 'active_input', None) == i
					newvalues.update(self.input_tree(input_count, source.service, source.instance, t, int(active)))
				input_count += 1

			newvalues['/Ac/In/NumberOfAcInputs'] = input_count
