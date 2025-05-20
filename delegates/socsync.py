from dbus.exceptions import DBusException
from delegates.base import SystemCalcDelegate
from delegates.multi import Multi
from sc_utils import safeadd

def service_is_battery(service):
	return service.split('.')[2] == 'battery'

class SocSync(SystemCalcDelegate):
	""" This is similar to VebusSocWriter, but for InverterRS. """
	def __init__(self, sc):
		super(SocSync, self).__init__()
		self.systemcalc = sc
		self.vecan = set()
		self.solarchargers = set()

	def get_input(self):
		return [
			('com.victronenergy.vecan', [
				'/Link/Soc',
				'/Link/ExtraBatteryCurrent']),
			('com.victronenergy.solarcharger', [
				'/Dc/0/Current',
				'/Load/I',
				'/Mgmt/Connection'
		])]

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.vecan.'):
			self.vecan.add(service)
		elif service.startswith(
				'com.victronenergy.solarcharger.') and self._dbusmonitor.get_value(
				service, '/Mgmt/Connection') != 'VE.Can':
			self.solarchargers.add(service)

	def device_removed(self, service, instance):
		self.vecan.discard(service)
		self.solarchargers.discard(service)

	def _service_is_vecan(self, service):
		# acsystem gets its values from VE.Can, so is considered vecan.
		return service.split('.')[2] == 'acsystem' or \
			self._dbusmonitor.get_value(service, '/Mgmt/Connection') == 'VE.Can'

	def update_values(self, newvalues):
		# Sync SOC with all non-VE.Bus inverter-chargers
		batteryservice = self.systemcalc.batteryservice
		if batteryservice is not None:
			soc = newvalues.get('/Dc/Battery/Soc', None)
			if soc is not None and (
					not self._service_is_vecan(batteryservice)
					or service_is_battery(batteryservice)):
				for service in self.vecan:
					# In case service goes down while we write, ignore
					# exception
					try:
						self._dbusmonitor.set_value_async(service, '/Link/Soc', soc)
					except DBusException:
						pass

		# Sync ExtraBatteryCurrent, but only consider currents from
		# VE.Direct chargers and the Multi
		pv_current = 0
		for service in self.solarchargers:
			pv_current = safeadd(pv_current,
				self._dbusmonitor.get_value(service, '/Dc/0/Current', 0),
				-(self._dbusmonitor.get_value(service, '/Load/I', 0) or 0))

		# Add current from Multi
		multi = Multi.instance.multi
		pv_current = safeadd(pv_current,
			getattr(multi, 'dc_current', None))

		for service in self.vecan:
			try:
				self._dbusmonitor.set_value_async(service,
					'/Link/ExtraBatteryCurrent', pv_current)
			except DBusException:
				pass
			else:
				# This control flag is created by the VebusSocWriter delegate.
				# We set the same one in case the extra battery current was
				# successfully written to an Inverter RS.
				newvalues['/Control/ExtraBatteryCurrent'] |= 1
