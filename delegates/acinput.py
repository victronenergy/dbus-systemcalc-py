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

class AcInputs(SystemCalcDelegate):
	def __init__(self):
		super(AcInputs, self).__init__()
		self.gridmeters = {}
		self.gensetmeters = {}
		self.gridmeter = None
		self.gensetmeter = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)

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
			self.gridmeters[service] = AcSource(self._dbusmonitor, service, instance)
			self._set_gridmeter()
		elif service.startswith('com.victronenergy.genset.'):
			self.gensetmeters[service] = AcSource(self._dbusmonitor, service, instance)
			self._set_gensetmeter()

	def device_removed(self, service, instance):
		if service in self.gridmeters:
			del self.gridmeters[service]
			self._set_gridmeter()
		if service in self.gensetmeters:
			del self.gensetmeters[service]
			self._set_gensetmeter()

	def _get_meter(self, meters):
		if meters:
			return sorted(meters.values(), key=lambda x: x.instance)[0]
		return None

	def _set_gridmeter(self):
		self.gridmeter = self._get_meter(self.gridmeters)

	def _set_gensetmeter(self):
		self.gensetmeter = self._get_meter(self.gensetmeters)

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
		multi = Multi.instance.multi
		number_of_inputs = getattr(multi, 'number_of_inputs', None) or 0
		inputs = [(i, self._dbusmonitor.get_value('com.victronenergy.settings',
			'/Settings/SystemSetup/AcInput{}'.format(i + 1))) for i in range(number_of_inputs)]
		source_count = 0
		for i, t in inputs:
			if t is None or (not 0 < t < 4): # Input is marked "Not available", or invalid
				continue

			source = self.gridmeter if t in (1, 3) else self.gensetmeter

			if source is None:
				# Use vebus
				if multi is not None:
					newvalues.update(self.input_tree(source_count, multi.service, multi.instance, t, int(multi.active_input == i)))
				else:
					newvalues.update(self.input_tree(source_count, None, None, t, 0))
			else:
				active = getattr(multi, 'active_input', None) == i
				newvalues.update(self.input_tree(source_count, source.service, source.instance, t, int(active)))
			source_count += 1

		newvalues['/Ac/In/NumberOfAcInputs'] = source_count
