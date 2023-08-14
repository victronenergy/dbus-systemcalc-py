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

class Multi(SystemCalcDelegate):
	def __init__(self):
		super(Multi, self).__init__()
		self.multis = {}
		self.multi = None # The actual Multi that is connected and working
		self.vebus_service = None # The VE.Bus service, Multi could be offline

		# Determine if this platform has a built-in MK2/3. Maxi-GX
		# and generic (Raspberry Pi) does not.
		self.has_onboard_mkx = get_product_id() not in ('C009', 'C003')

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)

	def get_input(self):
		return [('com.victronenergy.vebus', [
				'/Interfaces/Mk2/Connection',
				'/Ac/ActiveIn/ActiveInput',
				'/Ac/NumberOfAcInputs'])]

	def get_output(self):
		return [
			('/VebusService', {'gettext': '%s'}),
			('/VebusInstance', {'gettext': '%s'})]

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
		if self.has_onboard_mkx:
			multis = [m for m in self.multis.values() if m.onboard]
		else:
			multis = self.multis.values()

		multis = sorted(multis, key=lambda x: x.instance)
		if multis and multis[0].connected:
			self.multi = multis[0]
		else:
			self.multi = None

		if multis:
			self.vebus_service = multis[0]
		else:
			self.vebus_service = None

	def update_values(self, newvalues):
		# If there are multis connected, but for some reason none is selected
		# or the current selected one is no longer connected, try to set
		# a new one.
		if self.multis and (self.multi is None or not self.multi.connected):
			self._set_multi()

		newvalues['/VebusService'] = getattr(self.multi, 'service', None)
		newvalues['/VebusInstance'] = getattr(self.multi, 'instance', None)
