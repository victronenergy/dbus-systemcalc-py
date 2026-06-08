from datetime import datetime, timedelta
from gi.repository import GLib # type: ignore
from delegates.base import SystemCalcDelegate
from delegates.chargecontrol import ChargeControl
import logging
logger = logging.getLogger(__name__)

# DynamicEss-Delegate is just a light-relay for the DynamicEss Service (com.victronenergy.dynamicess)
# It acts as proxy to mimic the former full DynamicEss delegate for compatibility reasons.
# delegate will first report /Available.
# if DESS is turned on, venus-platform will start the dynamicess-service.
# if the service passes all tests, it will report /Ready.
# delegate then will try to acquire charge control, if it can, it will report this via /ChargeControlAcquired
# service itself then will enter operation state and report /Active.
class DynamicEss(SystemCalcDelegate, ChargeControl):
	control_priority = 0
	_get_time = datetime.now

	def __init__(self):
		super(DynamicEss, self).__init__()
		self._service_paths = [
			'/Capabilities',
			'/NumberOfSchedules',
			'/Active',
			'/TargetSoc',
			'/WindowSoc',
			'/MinimumSoc',
			'/ErrorCode',
			'/LastScheduledStart',
			'/LastScheduledEnd',
			'/ChargeRate',
			'/WindowSlot',
			'/ReactiveStrategy',
			'/EvcsGxFlags',
			'/Strategy',
			'/WorkingSocPrecision',
			'/Restrictions',
			'/AllowGridFeedIn',
			'/Flags',
			'/Ready',
			'/AvailableOverhead',
			'/ChargeHysteresis',
			'/DischargeHysteresis',
		]

		self.ac_system_service = None
		self.vebus_service = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(DynamicEss, self).set_sources(dbusmonitor, settings, dbusservice)

		#Create all the paths the regular service is serving to be able to relay them.
		for path in self._service_paths:
			self._dbusservice.add_path(f"/DynamicEss{path}", value=None)

		#Extra path for ChargeControl
		self._dbusservice.add_path('/DynamicEss/Available', value=None)
		self._dbusservice.add_path('/DynamicEss/ChargeControlAcquired', value=0)

	def get_settings(self):
		# Settings for DynamicEss - nothing needed here, service handles that.
		return [ ]

	def get_input(self):
		return [
			("com.victronenergy.dynamicess", self._service_paths),
			("com.victronenergy.settings", ["/Settings/DynamicEss/Mode"]),
			("com.victronenergy.vebus", ["/Hub4/AssistantId"]),
			("com.victronenergy.acsystem", ["/Capabilities/HasDynamicEssSupport"])
		]

	def get_output(self):
		#we added all required paths manually to the underlaying dbus service.
		return []

	def device_added(self, name, instance, *args):
		if name.startswith('com.victronenergy.vebus.'):
			self.vebus_service = (instance, name)
		elif name.startswith('com.victronenergy.acsystem.'):
			if self.ac_system_service is None:
				self.ac_system_service = (instance, name)
			else:
				if instance < self.ac_system_service[0]:
					self.ac_system_service = (instance, name)

		self._validate_availability()

	def device_removed(self, name, instance):
		if name.startswith('com.victronenergy.vebus.'):
			self.vebus_service = None
		elif name.startswith('com.victronenergy.acsystem.'):
			if self.ac_system_service is not None:
				if instance == self.ac_system_service[0]:
					self.ac_system_service = None

		self._validate_availability()

	def _validate_availability(self):
		"""
			Validates if DynamicEss is available on this system.
		"""
		if self.vebus_service is not None and self.ac_system_service is None:
			#vebus. Check ESS assistant presence.
			self.available =  self._dbusmonitor.get_value(self.vebus_service[1], "/Hub4/AssistantId") == 5

		elif self.ac_system_service is not None and self.vebus_service is None:
			#ac system. Check if we have an ESS connected to the AC system.
			self.available = self._dbusmonitor.get_value(self.ac_system_service[1], "/Capabilities/HasDynamicEssSupport") == 1

		else:
			#we have neither or both, this should not happen, but if it does, DESS is not available.
			self.available = 0

	def update_values(self, newvalues):
		#strictly relay all values from the main service, except the ones the delegate has under control.
		#(Available, ChargeControlAcquired)
		for path in self._service_paths:
			self._dbusservice[f"/DynamicEss{path}"] = self._dbusmonitor.get_value("com.victronenergy.dynamicess", path)

		if not self.available:
			#we are not supposed to run, make sure we are deactivated and have no control.
			self.deactivate()
			return

		#check, if dynamicess is supposed to be running.
		# Old buy/sell states now also means off
		dess_enabled = (self._dbusmonitor.get_value("com.victronenergy.settings", "/Settings/DynamicEss/Mode") or 0) not in (0, 2, 3)

		if not dess_enabled:
			self.deactivate()
			return

		#service not ready?
		if not self._dbusmonitor.get_value("com.victronenergy.dynamicess", "/Ready"):
			self.deactivate()
			return

		#down here, we are supposed to run, make sure we have control and
		#indicate this to the actual service.
		if not self.has_control():
			if self.acquire_control():
				logger.log(logging.INFO, "DynamicEss has acquired charge control.")
				self.charge_control_acquired = 1

			else:
				#failed to aquire control. This should never happen.
				self.deactivate()

	def deactivate(self):
		"""
			Deactivate DynamicEss, release control and reset all paths to default values.
		"""
		self.charge_control_acquired = 0
		self.release_control()

	@property
	def available(self):
		return self._dbusservice['/DynamicEss/Available']

	@available.setter
	def available(self, value):
		self._dbusservice['/DynamicEss/Available'] = int(value)

	@property
	def charge_control_acquired(self):
		return self._dbusservice['/DynamicEss/ChargeControlAcquired']

	@charge_control_acquired.setter
	def charge_control_acquired(self, value):
		self._dbusservice['/DynamicEss/ChargeControlAcquired'] = int(value)

