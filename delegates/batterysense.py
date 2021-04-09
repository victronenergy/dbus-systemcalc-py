from collections import namedtuple
from itertools import chain
import gobject
from dbus.exceptions import DBusException
from delegates.base import SystemCalcDelegate
from delegates.dvcc import Dvcc

# Victron packages
from ve_utils import exit_on_error

# Write temperature this often (in 3-second units)
TEMPERATURE_INTERVAL = 3

class TemperatureSensor(namedtuple('TemperatureSensor', ('service', 'path', 'instance', 'isvalid'))):
	@property
	def valid(self):
		return self.isvalid()

	@property
	def service_class(self):
		return '.'.join(self.service.split('.')[:3])

	@property
	def instance_service_name(self):
		return '{}/{}'.format(self.service_class, self.instance)

class DedicatedSensor(TemperatureSensor):
	""" This represents a ADC sensor, as opposed to one attached to the battery
	    service, solar charger or Multi. """

class BatterySense(SystemCalcDelegate):
	TEMPSERVICE_DEFAULT = 'default'
	TEMPSERVICE_NOSENSOR = 'nosensor'

	VSENSE_OFF = 0
	VSENSE_ON = 1

	ISENSE_USER_DISABLED = 0
	ISENSE_EXT_CONTROL = 1
	ISENSE_NO_CHARGERS = 2
	ISENSE_NO_MONITOR = 3
	ISENSE_ENABLED = 4

	def __init__(self, sc):
		super(BatterySense, self).__init__()
		self.systemcalc = sc
		self._timer = None
		self.temperaturesensors = {}
		self.tick = TEMPERATURE_INTERVAL

	def get_input(self):
		return [
			('com.victronenergy.solarcharger', [
				'/FirmwareVersion',
				'/Link/NetworkMode',
				'/Link/VoltageSense',
				'/Link/BatteryCurrent',
				'/Link/TemperatureSense',
				'/Dc/0/Temperature']),
			('com.victronenergy.inverter', [
				'/Link/BatteryCurrent',
				'/Link/VoltageSense',
				'/Link/TemperatureSense',
				'/Dc/0/Temperature']),
			('com.victronenergy.vecan', [
				'/Link/VoltageSense',
				'/Link/TemperatureSense',
				'/Link/BatteryCurrent']),
			('com.victronenergy.vebus', [
				'/Dc/0/Voltage',
				'/BatterySense/Voltage',
				'/BatterySense/Temperature',
				'/FirmwareFeatures/BolUBatAndTBatSense',
				'/Dc/0/Temperature']),
			('com.victronenergy.settings', [
				'/Settings/SystemSetup/SharedVoltageSense',
				'/Settings/Services/Bol']),
			('com.victronenergy.battery', [
				'/Dc/0/Temperature']),
			('com.victronenergy.temperature', [
				'/Temperature', '/TemperatureType'])
		]

	def get_settings(self):
		return [
			('vsense', "/Settings/SystemSetup/SharedVoltageSense", 1, 0, 0),
			('tsense', "/Settings/SystemSetup/SharedTemperatureSense", 1, 0, 0),
			('isense', "/Settings/SystemSetup/BatteryCurrentSense", 1, 0, 0),
			('temperatureservice', '/Settings/SystemSetup/TemperatureService', "default", 0, 0)
		]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/SolarChargerVoltageSense', value=0)
		self._dbusservice.add_path('/Control/BatteryVoltageSense', value=0)
		self._dbusservice.add_path('/Control/BatteryCurrentSense', value=0)
		self._dbusservice.add_path('/Control/SolarChargerTemperatureSense', value=0)
		self._dbusservice.add_path('/AvailableTemperatureServices', value=None)
		self._dbusservice.add_path('/AutoSelectedTemperatureService', value=None)
		self._dbusservice.add_path('/Dc/Battery/TemperatureService', value=None)
		self._dbusservice.add_path('/Dc/Battery/Temperature', value=None, gettextcallback=lambda p, v: '{:.1F} C'.format(v))
		self._timer = gobject.timeout_add(3000, exit_on_error, self._on_timer)

	@property
	def temperature_service(self):
		return self._settings['temperatureservice']

	@property
	def has_vsense(self):
		# 0b00  = Off
		# 0b01  = On
		# 0b10  = Forced off
		# 0b11  = Forced on
		v = self._settings['vsense']
		return bool(v & 1)

	@property
	def has_tsense(self):
		# Same schema as has_vsense above
		v = self._settings['tsense']
		return bool(v & 1)

	@property
	def has_isense(self):
		return bool(self._settings['isense'])

	def nice_name(self, service):
		name = self._dbusmonitor.get_value(service, '/ProductName')
		connection = self._dbusmonitor.get_value(service, '/Mgmt/Connection')
		if service in self.temperaturesensors:
			ob = self.temperaturesensors[service]
			if isinstance(ob, DedicatedSensor) and ob.valid:
				try:
					n = int(connection.split()[-1])
				except (ValueError, IndexError):
					pass
				else:
					return "Battery temperature sensor ({})".format(n)

		return '{} on {}'.format(name, connection)

	def update_temperature_sensors(self, *args):
		services = {
			self.TEMPSERVICE_DEFAULT: 'Automatic',
			self.TEMPSERVICE_NOSENSOR: 'No sensor'}

		for sensor in self.temperaturesensors.itervalues():
			if sensor.valid:
				name = self._dbusmonitor.get_value(sensor.service, '/ProductName')
				services[sensor.instance_service_name+sensor.path] = self.nice_name(sensor.service)

		self._dbusservice['/AvailableTemperatureServices'] = services

	def _find_device_instance(self, serviceclass, instance):
		di = {(s.service_class, s.instance): s.service for s in self.temperaturesensors.values()}
		return di.get((serviceclass, instance))

	def _determine_temperature(self):
		# Business Logic:
		# 1. Use the selected service
		#   - list batteries, solar chargers (with /Dc/0/Temperature) and
		#     temperature sensors with type battery.
		# 2. If selected service == default, use the existing mechanism
		#    - the battery service if it has temperature.

		# Explicitly disabled
		temperature_service = self.temperature_service
		if temperature_service == self.TEMPSERVICE_NOSENSOR:
			return None, None

		# Selected battery service
		if temperature_service != self.TEMPSERVICE_DEFAULT:
			try:
				serviceclass, instance, path = temperature_service.split('/', 2)
				instance = int(instance)
			except ValueError:
				return None, None
			else:
				s = self._find_device_instance(serviceclass, instance)
				if s is not None and self.temperaturesensors[s].valid:
					return self._dbusmonitor.get_value(s, '/'+path), s
				else:
					return None, None

		# Default: Use battery service
		if self.systemcalc._batteryservice is not None:
			t = self._dbusmonitor.get_value(self.systemcalc._batteryservice,
				'/Dc/0/Temperature')
			if t is not None:
				return t, self.systemcalc._batteryservice

		return None, None

	def device_added(self, service, instance, *args):
		# Devices that can serve as temperature sensors
		if service.startswith('com.victronenergy.battery.') or \
				service.startswith('com.victronenergy.vebus.') or \
				service.startswith('com.victronenergy.solarcharger.') or \
				service.startswith('com.victronenergy.inverter.'):
			self.temperaturesensors[service] = TemperatureSensor(service,
				'/Dc/0/Temperature', instance,
				lambda s=service: self._dbusmonitor.get_value(s, '/Dc/0/Temperature') is not None)
			self._dbusmonitor.track_value(service, '/Dc/0/Temperature', self.update_temperature_sensors)
			self.update_temperature_sensors()
		elif service.startswith('com.victronenergy.temperature.'):
			self.temperaturesensors[service] = DedicatedSensor(service,
				'/Temperature', instance,
				lambda s=service: self._dbusmonitor.get_value(s, '/TemperatureType') == 0)
			self._dbusmonitor.track_value(service, '/TemperatureType', self.update_temperature_sensors)
			self.update_temperature_sensors()

	def device_removed(self, service, instance):
		if service in self.temperaturesensors:
			del self.temperaturesensors[service]
			self.update_temperature_sensors()

	def _on_timer(self):
		# Distribute the voltage if svs and dvcc is on
		self._dbusservice['/Control/BatteryVoltageSense'], \
		self._dbusservice['/Control/SolarChargerVoltageSense'] = \
			self._distribute_sense_voltage(
				self.has_vsense and Dvcc.instance.has_dvcc)

		# Tell the solarchargers what the battery current is for tail
		# detection.
		if self.has_isense and Dvcc.instance.has_dvcc:
			self._dbusservice['/Control/BatteryCurrentSense'] = \
				self._distribute_battery_current()
		else:
			self._dbusservice['/Control/BatteryCurrentSense'] = BatterySense.ISENSE_USER_DISABLED

		# Distribute the temperature, but this can be done less frequently,
		# every TEMPERATURE_INTERVAL ticks (9 seconds total).
		if self.tick == 0:
			self._dbusservice['/Control/SolarChargerTemperatureSense'] = \
				int(self.has_tsense and Dvcc.instance.has_dvcc) and \
				self._distribute_sense_temperature()
		self.tick = (self.tick - 1) % TEMPERATURE_INTERVAL
		return True

	def _distribute_sense_voltage(self, has_vsense):
		sense_voltage = self._dbusservice['/Dc/Battery/Voltage']
		sense_voltage_service = self._dbusservice['/Dc/Battery/VoltageService']
		if sense_voltage is None or sense_voltage_service is None:
			return self.VSENSE_OFF, self.VSENSE_OFF

		multi_written = self.VSENSE_OFF
		charger_written = self.VSENSE_OFF

		# Sync the Multi's voltage first
		vebus_path = self._dbusservice['/VebusService']
		if has_vsense and vebus_path is not None and \
			vebus_path != sense_voltage_service and \
			self._dbusmonitor.get_value(vebus_path, '/FirmwareFeatures/BolUBatAndTBatSense') == 1:
			self._dbusmonitor.set_value_async(vebus_path, '/BatterySense/Voltage',
				sense_voltage)
			multi_written = self.VSENSE_ON

		# If this is an ESS system, switch to using the multi as a voltage
		# reference.
		if vebus_path is not None and Dvcc.instance.has_ess_assistant:
			sense_voltage = self._dbusmonitor.get_value(vebus_path, '/Dc/0/Voltage')
			sense_voltage_service = vebus_path
			if sense_voltage is None or sense_voltage_service is None:
				return multi_written, charger_written
		elif not has_vsense:
			return multi_written, charger_written

		# Forward voltage sense to solarchargers and supporting inverters
		for service in chain(self._dbusmonitor.get_service_list('com.victronenergy.solarcharger'),
			self._dbusmonitor.get_service_list('com.victronenergy.inverter')):
			if service == sense_voltage_service:
				continue
			if not self._dbusmonitor.seen(service, '/Link/VoltageSense'):
				continue
			self._dbusmonitor.set_value_async(service, '/Link/VoltageSense', sense_voltage)
			charger_written = self.VSENSE_ON

		# Only forward to the VE.Can if the voltage is not comming from it.
		vecan = self._dbusmonitor.get_service_list('com.victronenergy.vecan')
		if len(vecan):
			sense_origin = self._dbusmonitor.get_value(sense_voltage_service, '/Mgmt/Connection')
			if sense_origin and sense_origin != 'VE.Can':
				for _ in vecan.iterkeys():
					self._dbusmonitor.set_value_async(_, '/Link/VoltageSense', sense_voltage)
				charger_written = self.VSENSE_ON

		return multi_written, charger_written

	def _distribute_battery_current(self):
		# No point if we're running ESS, then the Multi decides.
		if Dvcc.instance.has_ess_assistant:
			return BatterySense.ISENSE_EXT_CONTROL

		if self._dbusservice['/Control/SolarChargeVoltage']:
			return BatterySense.ISENSE_EXT_CONTROL

		# The voltage service is either auto-selected, with a battery service
		# being preferred, or it is explicity selected by the user. If this
		# service is a battery service, then we can use the system battery
		# current as an absolute value and copy it to the solar chargers.
		sense_voltage_service = self._dbusservice['/Dc/Battery/VoltageService']
		if sense_voltage_service is None:
			return BatterySense.ISENSE_NO_MONITOR
		battery_current = self._dbusservice['/Dc/Battery/Current'] if (sense_voltage_service.split('.')[2] == 'battery') else None
		if battery_current is None:
			return BatterySense.ISENSE_NO_MONITOR

		sent = BatterySense.ISENSE_NO_CHARGERS
		for service in self._dbusmonitor.get_service_list(
			'com.victronenergy.solarcharger').keys() + self._dbusmonitor.get_service_list(
			'com.victronenergy.vecan').keys() + self._dbusmonitor.get_service_list(
			'com.victronenergy.inverter').keys():
			if not self._dbusmonitor.seen(service, '/Link/BatteryCurrent'):
				continue # No such feature on this charger
			self._dbusmonitor.set_value_async(service, '/Link/BatteryCurrent', battery_current)
			sent = BatterySense.ISENSE_ENABLED
		return sent

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

			# VE.Can chargers don't have this path, so only set it when it has been seen
			if self._dbusmonitor.seen(charger, '/Link/TemperatureSense'):
				self._dbusmonitor.set_value_async(charger, '/Link/TemperatureSense', sense_temp)
			written = 1

		# Write to supporting inverters
		for charger in self._dbusmonitor.get_service_list('com.victronenergy.inverter'):
			# Don't write the temperature back to its source
			if charger == sense_temp_service:
				continue

			if self._dbusmonitor.seen(charger, '/Link/TemperatureSense'):
				self._dbusmonitor.set_value_async(charger, '/Link/TemperatureSense', sense_temp)
			written = 1

		# Also update the multi
		vebus = self._dbusservice['/VebusService']
		if vebus is not None and vebus != sense_temp_service and self._dbusmonitor.seen(vebus, '/BatterySense/Temperature'):
			self._dbusmonitor.set_value_async(vebus, '/BatterySense/Temperature',
				sense_temp)
			written = 1

		# Update vecan only if there is one..
		vecan = self._dbusmonitor.get_service_list('com.victronenergy.vecan')
		if len(vecan):
			sense_origin = self._dbusmonitor.get_value(sense_temp_service, '/Mgmt/Connection')
			if sense_origin and sense_origin != 'VE.Can':
				for _ in vecan.iterkeys():
					self._dbusmonitor.set_value_async(_, '/Link/TemperatureSense', sense_temp)
				written = 1

		return written

	def update_values(self, newvalues):
		# Get a temperature value and service
		self._dbusservice['/Dc/Battery/Temperature'], temperature_service = self._determine_temperature()
		self._dbusservice['/Dc/Battery/TemperatureService'] = temperature_service
		self._dbusservice['/AutoSelectedTemperatureService'] = None if temperature_service is None else \
			self.nice_name(temperature_service)
