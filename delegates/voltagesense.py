import gobject
from dbus.exceptions import DBusException
from delegates.base import SystemCalcDelegate

# Victron packages
from ve_utils import exit_on_error


class VoltageSense(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._timer = None

	def get_input(self):
		return [
			('com.victronenergy.solarcharger', [
				'/Link/NetworkMode',
				'/Link/VoltageSense']),
			('com.victronenergy.vebus', [
				'/Dc/0/Voltage',
				'/BatterySense/Voltage',
				'/FirmwareFeatures/BolUBatAndTBatSense']),
			('com.victronenergy.settings', [
				'/Settings/SystemSetup/SharedVoltageSense',
				'/Settings/Services/Bol'])]

	def get_settings(self):
		return [
			('enabled', "/Settings/SystemSetup/SharedVoltageSense", 1, 0, 0),
		]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/SolarChargerVoltageSense', value=0)
		self._timer = gobject.timeout_add(3000, exit_on_error, self._on_timer)

	def _on_timer(self):
		bol_support = self._dbusmonitor.get_value(
			'com.victronenergy.settings', '/Settings/Services/Bol') == 1
		self._dbusservice['/Control/SolarChargerVoltageSense'] = \
			int(self._settings['enabled'] and bol_support) and \
			self._distribute_sense_voltage()
		return True

	def _distribute_sense_voltage(self):
		sense_voltage = self._dbusservice['/Dc/Battery/Voltage']
		sense_voltage_service = self._dbusservice['/Dc/Battery/VoltageService']
		if sense_voltage is None or sense_voltage_service is None:
			return 0
		voltagesense_written = 0
		for service in self._dbusmonitor.get_service_list('com.victronenergy.solarcharger'):
			if service == sense_voltage_service:
				continue
			# There's now way (yet) to send the sense voltage to VE.Can chargers.
			if self._dbusmonitor.get_value(service, '/Mgmt/Connection') == 'VE.Can':
				continue
			# We use /Link/NetworkMode to detect Hub support in the solarcharger. Existence of this item
			# implies existence of the other /Link/* fields.
			if self._dbusmonitor.get_value(service, '/Link/NetworkMode') is None:
				continue
			try:
				self._dbusmonitor.set_value(service, '/Link/VoltageSense', sense_voltage)
				voltagesense_written = 1
			except DBusException:
				pass
		vebus_path = self._dbusservice['/VebusService']
		if vebus_path is not None and \
			vebus_path != sense_voltage_service and \
			self._dbusmonitor.get_value(vebus_path, '/FirmwareFeatures/BolUBatAndTBatSense') == 1:
			try:
				self._dbusmonitor.set_value(vebus_path, '/BatterySense/Voltage',
					sense_voltage)
				voltagesense_written = 1
			except DBusException:
				pass
		return voltagesense_written
