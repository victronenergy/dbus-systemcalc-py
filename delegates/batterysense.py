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
				'/FirmwareVersion',
				'/Link/NetworkMode',
				'/Link/VoltageSense',
				'/Link/BatteryCurrent',
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
		#self._distribute_battery_current() # Disabled for now
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
			self._dbusmonitor.set_value_async(service, '/Link/VoltageSense', sense_voltage)
			voltagesense_written = 1

		vebus_path = self._dbusservice['/VebusService']
		if vebus_path is not None and \
			vebus_path != sense_voltage_service and \
			self._dbusmonitor.get_value(vebus_path, '/FirmwareFeatures/BolUBatAndTBatSense') == 1:
			self._dbusmonitor.set_value_async(vebus_path, '/BatterySense/Voltage',
				sense_voltage)
			voltagesense_written = 1

		return voltagesense_written

	def _distribute_battery_current(self):
		# The voltage service is either auto-selected, with a battery service being preferred, or it is explicity
		# selected by the user. If this service is a battery service, then we can use the system battery current
		# as an absolute value and copy it to the solar chargers.
		sense_voltage_service = self._dbusservice['/Dc/Battery/VoltageService']
		if sense_voltage_service is None:
			return
		battery_current = self._dbusservice['/Dc/Battery/Current'] if (sense_voltage_service.split('.')[2] == 'battery') else None
		for service in self._dbusmonitor.get_service_list('com.victronenergy.solarcharger'):
			# Skip for old firmware versions to save some dbus traffic
			if battery_current is not None and (
					(self._dbusmonitor.get_value(service, '/FirmwareVersion') or 0) & 0x0FFF >= 0x0141):
				self._dbusmonitor.set_value_async(service, '/Link/BatteryCurrent', battery_current)

	def _distribute_sense_temperature(self):
		sense_temp = self._dbusservice['/Dc/Battery/Temperature']
		if sense_temp is None:
			return 0

		sense_temp_service = self._dbusservice['/Dc/Battery/TemperatureService']

		# Write the tempeature to all solar chargers.
		written = 0
		for charger in self._dbusmonitor.get_service_list('com.victronenergy.solarcharger'):
			# Don't write the temperature back to its source
			if charger == sense_temp_service:
				continue

			# We use /Link/NetworkMode to detect Hub support in the
			# solarcharger.
			if self._dbusmonitor.get_value(charger, '/Link/NetworkMode') is None:
				continue

			# VE.Can chargers don't have this path, and we will cheerfully
			# ignore any errors coming from them.
			self._dbusmonitor.set_value_async(charger,
				'/Link/TemperatureSense', sense_temp)
			written = 1

		# Also update the multi
		vebus = self._dbusservice['/VebusService']
		if vebus is not None and vebus != sense_temp_service and self._dbusmonitor.get_value(vebus,
				'/FirmwareFeatures/BolUBatAndTBatSense') == 1:
			self._dbusmonitor.set_value_async(vebus, '/BatterySense/Temperature',
				sense_temp)
			written = 1
		return written
