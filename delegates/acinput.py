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

	@property
	def gridparallel(self):
		# Multi-RS units can run grid-parallel
		return True

	@property
	def feedback_enabled(self):
		# Multi-RS always feeds excess PV into grid
		return True

class AcInputs(SystemCalcDelegate):
	def __init__(self):
		super(AcInputs, self).__init__()
		self.gridmeters = {}
		self.gensetmeters = {}
		self.inverterchargers = {}
		self.acloads = set() # Things that indicate the presence of loads
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
					'/Ac/In/2/Type']),
				('com.victronenergy.grid', [
					'/ProductId',
					'/DeviceType', 
				]),
				('com.victronenergy.settings', [
					'/Settings/CGwacs/PreventFeedback'
				]),
				('com.victronenergy.heatpump', [
					'/ProductId']),
		]

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
				('/Ac/ActiveIn/GridParallel', {'gettext': '%d'}),
				('/Ac/ActiveIn/FeedbackEnabled', {'gettext': '%d'}),
				('/Ac/ActiveIn/ServiceType', {'gettext': '%s'}),
				('/Ac/HasAcLoads', {'gettext': '%s'}),
		]

	def device_added(self, service, instance, *args):
		# This is only ever called for services starting with
		# com.victronenergy.
		t = service.split('.')[2]
		if t == 'grid':
			self.gridmeters[service] = GridMeter(self._dbusmonitor, service, instance)
			self._set_gridmeter()
		elif t == 'genset':
			self.gensetmeters[service] = AcSource(self._dbusmonitor, service, instance)
			self._set_gensetmeter()
		elif t == 'acsystem':
			self.inverterchargers[service] = InverterCharger(self._dbusmonitor, service, instance)
			self._set_invertercharger()
		elif t in ('inverter', 'evcharger', 'acload', 'heatpump', 'vebus'):
			# If any of the above services exist, we have some kind of AC
			# system.
			self.acloads.add(service)

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

		self.acloads.discard(service)

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

	def ac_feedin_enabled(self):
		return self._dbusmonitor.get_value('com.victronenergy.settings',
			'/Settings/CGwacs/PreventFeedback') == 0

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
		newvalues['/Ac/ActiveIn/GridParallel'] = 0
		newvalues['/Ac/ActiveIn/FeedbackEnabled'] = 0
		newvalues['/Ac/HasAcLoads'] = int(len(self.acloads) + len(self.inverterchargers) > 0)
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
				active = input_count == 0
				newvalues.update(self.input_tree(input_count, source.service, source.instance, t, int(active)))
				if active:
					newvalues['/Ac/ActiveIn/ServiceType'] = source.service.split(".")[2]
				input_count += 1
			newvalues['/Ac/In/NumberOfAcInputs'] = input_count
		else:
			for i, t in getattr(multi, 'input_types', ()):
				if t is None or (not 0 < t < 4): # Input is marked "Not available", or invalid
					continue

				source = self.gridmeter if t in (1, 3) else self.gensetmeter

				active = multi.active_input == i
				if active and t in (1, 3) and multi.gridparallel:
					newvalues['/Ac/ActiveIn/GridParallel'] = 1
					newvalues['/Ac/ActiveIn/FeedbackEnabled'] = int(
						multi.feedback_enabled or self.ac_feedin_enabled())
				if source is None:
					# Use vebus or inverter/charger
					newvalues.update(self.input_tree(input_count, multi.service, multi.instance, t, int(active)))
					if active:
						newvalues['/Ac/ActiveIn/ServiceType'] = multi.service.split(".")[2]
				else:
					newvalues.update(self.input_tree(input_count, source.service, source.instance, t, int(active)))
					if active:
						newvalues['/Ac/ActiveIn/ServiceType'] = source.service.split(".")[2]
				input_count += 1

			newvalues['/Ac/In/NumberOfAcInputs'] = input_count
