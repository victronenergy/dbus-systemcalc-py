import gobject
from dbus.exceptions import DBusException
from delegates.base import SystemCalcDelegate

# Victron packages
from ve_utils import exit_on_error

# Write temperature this often (in 3-second units)
TEMPERATURE_INTERVAL = 3

class BatterySense(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._timer = None
		self.tick = TEMPERATURE_INTERVAL

	def get_input(self):
		return [
			('com.victronenergy.solarcharger', [
				'/Link/NetworkMode',
				'/Link/VoltageSense',
				'/Link/TemperatureSense']),
			('com.victronenergy.vebus', [
				'/Dc/0/Voltage',
				'/BatterySense/Voltage',
				'/BatterySense/Temperature',
				'/FirmwareFeatures/BolUBatAndTBatSense']),
			('com.victronenergy.settings', [
				'/Settings/SystemSetup/SharedVoltageSense',
				'/Settings/Services/Bol'])]

	def get_settings(self):
		return [
			('vsense', "/Settings/SystemSetup/SharedVoltageSense", 1, 0, 0),
			('tsense', "/Settings/SystemSetup/SharedTemperatureSense", 1, 0, 0),
			('bol', '/Settings/Services/Bol', 0, 0, 1)
		]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/SolarChargerVoltageSense', value=0)
		self._dbusservice.add_path('/Control/SolarChargerTemperatureSense', value=0)
		self._timer = gobject.timeout_add(3000, exit_on_error, self._on_timer)

	def _on_timer(self):
		self._dbusservice['/Control/SolarChargerVoltageSense'] = \
			int(self._settings['vsense'] and self._settings['bol']) and \
			self._distribute_sense_voltage()
		if self.tick == 0:
			self._dbusservice['/Control/SolarChargerTemperatureSense'] = \
				int(self._settings['tsense'] and self._settings['bol']) and \
				self._distribute_sense_temperature()
		self.tick = (self.tick - 1) % TEMPERATURE_INTERVAL
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

	def _distribute_sense_temperature(self):
		sense_temp = self._dbusservice['/Dc/Battery/Temperature']
		if sense_temp is None:
			return 0

		sense_temp_service = self._dbusservice['/Dc/Battery/TemperatureService']

		# Write the tempeature to all solar chargers. Since we do not (yet)
		# use a solarcharger as a temperature source, we don't have to
		# explicitly exclude the potential source as destination.
		written = 0
		for charger in self._dbusmonitor.get_service_list('com.victronenergy.solarcharger'):
			# Don't write the temperature back to its source
			if charger == sense_temp_service: continue

			# We use /Link/NetworkMode to detect Hub support in the
			# solarcharger.
			if self._dbusmonitor.get_value(charger, '/Link/NetworkMode') is None:
				continue

			# VE.Can chargers don't have this path, and we will cheerfully
			# ignore any errors coming from them.
			try:
				self._dbusmonitor.set_value(charger,
					'/Link/TemperatureSense', sense_temp)
				written = 1
			except DBusException:
				pass

		# Also update the multi
		vebus = self._dbusservice['/VebusService']
		if vebus is not None and vebus != sense_temp_service and self._dbusmonitor.get_value(vebus,
				'/FirmwareFeatures/BolUBatAndTBatSense') == 1:
			try:
				self._dbusmonitor.set_value(vebus, '/BatterySense/Temperature',
					sense_temp)
				written = 1
			except DBusException:
				pass
		return written
