import gobject
from dbus.exceptions import DBusException
from delegates.base import SystemCalcDelegate

# Victron packages
from ve_utils import exit_on_error


class BatterySense(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._timer = None

	def get_input(self):
		return [
			('com.victronenergy.solarcharger', [
				'/Link/NetworkMode',
				'/Link/TemperatureSense']),
			('com.victronenergy.vebus', [
				'/Dc/0/Voltage',
				'/BatterySense/Temperature',
				'/FirmwareFeatures/BolUBatAndTBatSense']),
			('com.victronenergy.settings', [
				'/Settings/SystemSetup/SharedTemperatureSense',
				'/Settings/Services/Bol'])]

	def get_settings(self):
		return [
			('tsense', "/Settings/SystemSetup/SharedTemperatureSense", 1, 0, 0),
			('bol', '/Settings/Services/Bol', 0, 0, 1)
		]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/SolarChargerTemperatureSense', value=0)
		self._timer = gobject.timeout_add(3000, exit_on_error, self._on_timer)

	def _on_timer(self):
		self._dbusservice['/Control/SolarChargerTemperatureSense'] = \
			int(self._settings['tsense'] and self._settings['bol']) and \
			self._distribute_sense_temperature()
		return True

	def _distribute_sense_temperature(self):
		sense_temp = self._dbusservice['/Dc/Battery/Temperature']
		if sense_temp is None:
			return 0

		# Write the tempeature to all solar chargers. Since we do not (yet)
		# use a solarcharger as a temperature source, we don't have to
		# explicitly exclude the potential source as destination.
		written = 0
		for charger in self._dbusmonitor.get_service_list('com.victronenergy.solarcharger'):
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
		if vebus is not None and self._dbusmonitor.get_value(vebus,
				'/FirmwareFeatures/BolUBatAndTBatSense') == 1:
			try:
				self._dbusmonitor.set_value(vebus, '/BatterySense/Temperature',
					sense_temp)
				written = 1
			except DBusException:
				pass
		return written
