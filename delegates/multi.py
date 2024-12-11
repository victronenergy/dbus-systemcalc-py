import dbus
from ve_utils import get_product_id
from delegates.base import SystemCalcDelegate

class Service(object):
	def __init__(self, monitor, service, instance):
		self.monitor = monitor
		self.service = service
		self.instance = instance

	@property
	def connected(self):
		return self.monitor.get_value(self.service, '/Connected') == 1

	@property
	def active_input(self):
		return self.monitor.get_value(self.service, '/Ac/ActiveIn/ActiveInput')

	@property
	def number_of_inputs(self):
		return self.monitor.get_value(self.service, '/Ac/NumberOfAcInputs')

	@property
	def dc_current(self):
		return self.monitor.get_value(self.service, '/Dc/0/Current')

	@property
	def input_types(self):
		return [(i, self.monitor.get_value('com.victronenergy.settings',
			'/Settings/SystemSetup/AcInput{}'.format(i + 1))) for i in range(self.number_of_inputs or 0)]

	@property
	def port(self):
		return self.monitor.get_value(self.service, '/Interfaces/Mk2/Connection') or ''

	@property
	def onboard(self):
		return not self.port.startswith('/dev/ttyUSB')

	@property
	def has_ess_assistant(self):
		return self.monitor.get_value(self.service, '/Hub4/AssistantId') == 5

	@property
	def gridparallel(self):
		return self.has_ess_assistant

	@property
	def feedback_enabled(self):
		# This path does not exist on systems with no ESS assistant and will
		# return False
		return self.monitor.get_value(self.service, '/Hub4/DoNotFeedInOvervoltage') == 0

	def set_ignore_ac(self, inp, ignore):
		if inp not in (0, 1):
			raise ValueError(inp)
		self.monitor.set_value_async(self.service, '/Ac/Control/IgnoreAcIn{}'.format(inp + 1),
			dbus.Int32(ignore, variant_level=1))

	def ac_in_available(self, inp):
		if inp not in range(0, self.number_of_inputs or 0):
			raise ValueError(inp)
		v = self.monitor.get_value(self.service, '/Ac/State/AcIn{}Available'.format(inp + 1))
		if v is None:
			raise NotImplementedError("ac_in_available")
		return v == 1

class Multi(SystemCalcDelegate):
	def __init__(self):
		super(Multi, self).__init__()
		self.multis = {}
		self.multi = None # The actual Multi that is connected and working
		self.vebus_service = None # The VE.Bus service, Multi could be offline
		self.othermultis = [] # Second and third devices

		# Determine if this platform has a built-in MK2/3. Maxi-GX
		# and generic (Raspberry Pi) does not.
		self.has_onboard_mkx = get_product_id() not in (
			'C009', 'C00D', 'C010', 'C003')

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)

	def get_input(self):
		return [('com.victronenergy.vebus', [
				'/Interfaces/Mk2/Connection',
				'/Ac/ActiveIn/ActiveInput',
				'/Ac/NumberOfAcInputs',
				'/Ac/Control/IgnoreAcIn1',
				'/Ac/Control/IgnoreAcIn2',
				'/Ac/State/AcIn1Available',
				'/Ac/State/AcIn2Available',
				'/Hub4/AssistantId',
				'/Hub4/DoNotFeedInOvervoltage'])]

	def get_output(self):
		return [
			('/VebusService', {'gettext': '%s'}),
			('/VebusInstance', {'gettext': '%s'}),
			('/Dc/Vebus/Current', {'gettext': '%.1F A'}),
			('/Dc/Vebus/Power', {'gettext': '%.0F W'}),
			('/Devices/NumberOfVebusDevices', {'gettext': '%s'})]

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.vebus.'):
			self.multis[service] = Service(self._dbusmonitor, service, instance)
			self._dbusmonitor.track_value(service, "/Connected", self._set_multi)
			self._set_multi()

	def device_removed(self, service, instance):
		if service in self.multis:
			del self.multis[service]
			self._set_multi()

	def _set_multi(self, *args, **kwargs):
		# If platform has an onboard mkx, use only that as VE.Bus service.
		# On other platforms, use the Multi with the lowest DeviceInstance.
		# List of all VE.Bus interfaces, onboard ones first.
		multis = sorted(self.multis.values(),
			key=lambda x: (not x.onboard, x.instance))

		main = multis[0] if multis else None

		if main is None:
			self.multi = self.vebus_service = None
			self.othermultis.clear()
		else:
			# If this platform has an onboard mkx, the main multi must be
			# onboard
			if self.has_onboard_mkx:
				if main.onboard:
					self.multi = main if main.connected else None
					self.vebus_service = main
					self.othermultis = multis[1:]
				else:
					self.multi = self.vebus_service = None
					self.othermultis = multis[:]
			else:
				self.multi = main if main.connected else None
				self.vebus_service = main
				self.othermultis = multis[1:]

	def update_values(self, newvalues):
		# If there are multis connected, but for some reason none is selected
		# or the current selected one is no longer connected, try to set
		# a new one.
		if self.multis and (self.multi is None or not self.multi.connected):
			self._set_multi()

		service = getattr(self.multi, 'service', None)
		newvalues['/VebusService'] = service
		newvalues['/VebusInstance'] = getattr(self.multi, 'instance', None)
		newvalues['/Devices/NumberOfVebusDevices'] = len(self.multis)

		if service is not None:
			dc_current = self._dbusmonitor.get_value(service, '/Dc/0/Current')
			newvalues['/Dc/Vebus/Current'] = dc_current

			dc_power = self._dbusmonitor.get_value(service, '/Dc/0/Power')
			if dc_power is None:
				try:
					dc_power =  dc_current * self._dbusmonitor.get_value(
						service, '/Dc/0/Voltage')
				except TypeError:
					pass
			newvalues['/Dc/Vebus/Power'] = dc_power
