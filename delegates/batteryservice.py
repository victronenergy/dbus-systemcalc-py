from dbus.exceptions import DBusException
from delegates.base import SystemCalcDelegate
from sc_utils import reify

class Battery(object):
	def __init__(self, monitor, service, instance):
		self.monitor = monitor
		self.service = service
		self.instance = instance

	@property
	def is_bms(self):
		return self.monitor.get_value(self.service,
			'/Info/MaxChargeVoltage') is not None

	@reify
	def device_instance(self):
		""" Returns the DeviceInstance of this device. """
		return self.monitor.get_value(self.service, '/DeviceInstance')

	@property
	def maxchargecurrent(self):
		""" Returns maxumum charge current published by the BMS. """
		return self.monitor.get_value(self.service, '/Info/MaxChargeCurrent')

	@property
	def chargevoltage(self):
		""" Returns charge voltage published by the BMS. """
		return self.monitor.get_value(self.service, '/Info/MaxChargeVoltage')

	@property
	def batterylowvoltage(self):
		""" Returns battery low voltage published by the BMS. """
		return self.monitor.get_value(self.service, '/Info/BatteryLowVoltage')

	@property
	def maxdischargecurrent(self):
		""" Returns max discharge current published by the BMS. """
		return self.monitor.get_value(self.service, '/Info/MaxDischargeCurrent')

	@property
	def voltage(self):
		""" Returns current voltage of battery. """
		return self.monitor.get_value(self.service, '/Dc/0/Voltage')

	@reify
	def product_id(self):
		""" Returns Product ID of battery. """
		return self.monitor.get_value(self.service, '/ProductId')

	@property
	def capacity(self):
		""" Capacity of battery, if defined. """
		return self.monitor.get_value(self.service, '/InstalledCapacity')

class BatteryService(SystemCalcDelegate):
	""" Keeps track of the (auto-)selected bms service. """
	BMSSERVICE_DEFAULT = -1
	BMSSERVICE_NOBMS = -255

	def __init__(self, sc):
		super(BatteryService, self).__init__()
		self.systemcalc = sc
		self._batteries = {}
		self.bms = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(BatteryService, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/ActiveBmsService', value=None)

	def get_input(self):
		return [
			('com.victronenergy.battery', [
				'/DeviceInstance',
				'/Info/MaxChargeVoltage',
				'/Info/BatteryLowVoltage',
				'/Info/MaxChargeCurrent',
				'/Info/MaxDischargeCurrent',
				'/Dc/0/Voltage',
				'/ProductId',
				'/InstalledCapacity']),
		]

	def get_settings(self):
		return [
			('bmsinstance', '/Settings/SystemSetup/BmsInstance', BatteryService.BMSSERVICE_DEFAULT, 0, 0)
		]

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.battery.'):
			self._batteries[instance] = Battery(self._dbusmonitor, service, instance)
			self._dbusmonitor.track_value(service, "/Info/MaxChargeVoltage", self._set_bms)
			self._set_bms()

	def device_removed(self, service, instance):
		if service.startswith('com.victronenergy.battery.') and instance in self._batteries:
			del self._batteries[instance]
			self._set_bms()

	def battery_service_changed(self, auto, oldservice, newservice):
		self._set_bms()

	def settings_changed(self, setting, oldvalue, newvalue):
		if setting == 'bmsinstance':
			self._set_bms()

	@property
	def selected_bms_instance(self):
		return self._settings['bmsinstance']

	@property
	def batteries(self):
		return self._batteries.values()

	@property
	def bmses(self):
		return [b for b in self._batteries.values() if b.is_bms]

	def _set_bms(self, *args, **kwargs):
		# Disabled
		if self.selected_bms_instance == BatteryService.BMSSERVICE_NOBMS:
			self.bms = None
			self._dbusservice['/ActiveBmsService'] = None
			return


		# Explicit selection
		if self.selected_bms_instance != BatteryService.BMSSERVICE_DEFAULT:
			try:
				b = self._batteries[int(self.selected_bms_instance)]
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
		bmses = self.bmses
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
