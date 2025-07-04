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

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(PvOpMon, self).set_sources(dbusmonitor, settings, dbusservice)

		self._dbusservice.add_path('/Pv/Disabled', value=0, writeable=True,
			onsetcallback=self._dbus_path_set)

	def _dbus_path_set(self, p, v):
		#logger.info("Dbus set: Setting pv disabled to: {}".format(v))
		disabled:bool = int(v) > 0
		self._dbusservice["/Pv/Disabled"] = int(disabled) 
		self._pv_disabled.set(disabled)

		#Tell hub4 (for acpv), DVCC (solar chargers) and eventually acsystem (MultiRS) about the desired PV State.
		#DVCC will read the pv_disabled property. 
		#TODO: Create a internal timer that is reading our Expiring Value once per second. 
		#      if DVCC is disabled, so the ttl will expire as well. 
		self._dbusmonitor.set_value_async("com.victronenergy.hub4", "/Pv/Disable", int(disabled))
		self._dbusmonitor.set_value_async("com.victronenergy.acsystem", "/Pv/Disable", int(disabled))

		return True 

	@property
	def pv_disabled(self) -> bool:
		'''
			Flag, if dess requests to disable PV. Defaults to False. Will timeout after 20 reads.
		'''
		disabled = self._pv_disabled.get() or False
		#logger.info("Retrieved pv disabled as: {} ({} reads left)".format(disabled, self._pv_disabled._ttl))

		#in case the setting times out, revert dbus key as well. 
		if self._dbusservice["/Pv/Disabled"] == 1 and self._pv_disabled._ttl == 0:
			#logger.info("Reverting dbus path to 0 as value timed out.")

			#also restore operation for hub4 and multi rs
			self._dbusmonitor.set_value_async("com.victronenergy.hub4", "/Pv/Disable", 0)
			self._dbusmonitor.set_value_async("com.victronenergy.acsystem", "/Pv/Disable", 0)
			self._dbusservice["/Pv/Disabled"] = 0

		return disabled

	def get_output(self):
		return []

	def update_values(self, newvalues):
		pass
