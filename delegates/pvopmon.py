from delegates.base import SystemCalcDelegate
from sc_utils import ExpiringValue
import logging
logger = logging.getLogger(__name__)
#
# Delegate to monitor the path /system/0/Dc/Pv/Disabled and if set to true,
# instructs dvcc, hub4 and multirs inverters about the request to disable solar production.
#
class PvOpMon(SystemCalcDelegate):
	def __init__(self, sc):
		super(PvOpMon, self).__init__()
		self.systemcalc = sc
		self._pv_disabled = ExpiringValue(20, False)
		self._acsystem0 = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(PvOpMon, self).set_sources(dbusmonitor, settings, dbusservice)

		self._dbusservice.add_path('/Pv/Disable', value=0, writeable=True,
			onsetcallback=self._dbus_path_set)

	def get_input(self):
		return [
			('com.victronenergy.hub4', ['/Pv/Disable']),
			('com.victronenergy.acsystem', ['/Pv/Disable']),
			('com.victronenergy.shelly',  ['/Pv/Disable'])
		]

	def _dbus_path_set(self, p, v):
		#logger.info("Dbus set: Setting pv disabled to: {}".format(v))
		disabled:bool = int(v) > 0
		self._dbusservice["/Pv/Disable"] = int(disabled)

		#Tell hub4 (for acpv), DVCC (solar chargers) and acsystem (MultiRS) about the desired PV shutdown.
		#DVCC will read the pv_disabled property. dbus-shelly can provide shelly based inverters it can turn off.
		self._pv_disabled.set(disabled)
		self._dbusmonitor.set_value_async("com.victronenergy.hub4", "/Pv/Disable", int(disabled))
		if self._acsystem0:
			self._dbusmonitor.set_value_async(self._acsystem0, "/Pv/Disable", int(disabled))
		self._dbusmonitor.set_value_async("com.victronenergy.shelly", "/Pv/Disable", int(disabled))

		return True

	@property
	def pv_disabled(self) -> bool:
		'''
			Flag, if dess requests to disable PV. Defaults to False. Will timeout after 20 reads.
			DVCC will read continiously and timeout the value when no longer set.
		'''
		disabled = self._pv_disabled.get() or False
		#logger.info("Retrieved pv disabled as: {} ({} reads left)".format(disabled, self._pv_disabled._ttl))

		#in case the setting times out, revert dbus path as well.
		if self._dbusservice["/Pv/Disable"] == 1 and self._pv_disabled._ttl == 0:
			#logger.info("Reverting dbus path to 0 as value timed out.")

			#also restore operation for hub4 and multi rs
			self._dbusmonitor.set_value_async("com.victronenergy.hub4", "/Pv/Disable", 0)
			if self._acsystem0:
				self._dbusmonitor.set_value_async(self._acsystem0, "/Pv/Disable", 0)
			self._dbusmonitor.set_value_async("com.victronenergy.shelly", "/Pv/Disable", 0)
			self._dbusservice["/Pv/Disable"] = 0

		return disabled

	def get_output(self):
		return []

	def update_values(self, newvalues):
		pass

	def device_added(self, service, instance, *args, **kwargs):
		service_type = service.split('.')[2]
		if service_type == 'acsystem':
			if self._dbusmonitor.get_value(service, '/DeviceInstance') == 0:
				self._acsystem0 = service

	def device_removed(self, service, instance, *args, **kwargs):
		service_type = service.split('.')[2]
		if service_type == 'acsystem':
			if service == self._acsystem0:
				self._acsystem0 = None
