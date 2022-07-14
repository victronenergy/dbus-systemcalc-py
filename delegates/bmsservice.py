from dbus.exceptions import DBusException
from delegates.base import SystemCalcDelegate

class Battery(object):
	def __init__(self, monitor, service, instance):
		self.monitor = monitor
		self.service = service
		self.instance = instance

	@property
	def is_bms(self):
		return self.monitor.get_value(self.service,
			'/Info/MaxChargeVoltage') is not None

class BmsService(SystemCalcDelegate):
	""" Keeps track of the (auto-)selected bms service. """
	BMSSERVICE_DEFAULT = -1
	BMSSERVICE_NOBMS = -255

	def __init__(self, sc):
		super(BmsService, self).__init__()
		self.systemcalc = sc
		self.batteries = {}
		self.bms = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(BmsService, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/ActiveBmsService', value=None)

	def get_input(self):
		return [
			('com.victronenergy.battery', [
				'/Info/MaxChargeVoltage']),
		]

	def get_settings(self):
		return [
			('bmsinstance', '/Settings/SystemSetup/BmsInstance', BmsService.BMSSERVICE_DEFAULT, 0, 0)
		]

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.battery.'):
			self.batteries[instance] = Battery(self._dbusmonitor, service, instance)
			self._dbusmonitor.track_value(service, "/Info/MaxChargeVoltage", self._set_bms)
			self._set_bms()

	def device_removed(self, service, instance):
		if service.startswith('com.victronenergy.battery.') and instance in self.batteries:
			del self.batteries[instance]
			self._set_bms()
	
	def battery_service_changed(self, auto, oldservice, newservice):
		self._set_bms()

	def settings_changed(self, setting, oldvalue, newvalue):
		if setting == 'bmsinstance':
			self._set_bms()

	@property
	def selected_bms_instance(self):
		return self._settings['bmsinstance']

	def _set_bms(self, *args, **kwargs):
		# Disabled
		if self.selected_bms_instance == BmsService.BMSSERVICE_NOBMS:
			self.bms = None
			self._dbusservice['/ActiveBmsService'] = None
			return


		# Explicit selection
		if self.selected_bms_instance != BmsService.BMSSERVICE_DEFAULT:
			try:
				b = self.batteries[int(self.selected_bms_instance)]
			except (ValueError, KeyError):
				self.bms = None
				self._dbusservice['/ActiveBmsService'] = None
			else:
				if b.is_bms:
					self.bms = b
					self._dbusservice['/ActiveBmsService'] = b.service
				else:
					self.bms = None
					self._dbusservice['/ActiveBmsService'] = None
			return

		# Automatic selection. Try the main battery service first, hence
		# hardcoded instance = -1
		bmses = [b for b in self.batteries.values() if b.is_bms]
		if self.systemcalc.batteryservice is not None and \
				self.systemcalc.batteryservice.startswith('com.victronenergy.battery.'):
			b = Battery(self._dbusmonitor, self.systemcalc.batteryservice, -1)
			if b.is_bms:
				bmses.append(b)
				
		if bmses:
			self.bms = sorted(bmses, key=lambda x: x.instance)[0]
			self._dbusservice['/ActiveBmsService'] = self.bms.service
		else:
			self.bms = None
			self._dbusservice['/ActiveBmsService'] = None
