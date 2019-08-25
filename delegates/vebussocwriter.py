from dbus.exceptions import DBusException
import gobject
import itertools
import logging

# Victron packages
from ve_utils import exit_on_error

from delegates.base import SystemCalcDelegate

class VebusSocWriter(SystemCalcDelegate):
	# Note that there are 2 categories of hub2 assistants: v1xx/v2xx firmware and v3xx/v4xx firmware.
	# Both versions have problems with writing the SoC (the assistants themselves adjust the SoC from time
	# to time for internal bookkeeping). For the assistants mentioned below there is no way of detecting the
	# presence of the hub-2 assistant apart from the assistant ID.
	# There is a plan to prevent this list from growing any further: new hub-2 assistant would identify
	# themselves, and the mk2 service will create /Hub2 path will be created in the vebus service. However,
	# no assistant has been released supporting this feature (the mk2 service already supports this).
	# Therefore it has not been implemented here.
	_hub2_assistant_ids = \
		{0x0134, 0x0135, 0x0137, 0x0138, 0x013A, 0x141, 0x0146, 0x014D, 0x015F, 0x0160, 0x0165}

	def __init__(self):
		SystemCalcDelegate.__init__(self)
		gobject.idle_add(exit_on_error, lambda: not self._write_vebus_soc())
		gobject.timeout_add(10000, exit_on_error, self._write_vebus_soc)

	def get_input(self):
		return [('com.victronenergy.vebus', [
			'/Devices/0/Assistants',
			'/ExtraBatteryCurrent',
			'/Hub2',
			'/Soc'])]

	def get_output(self):
		return [('/Control/ExtraBatteryCurrent', {'gettext': '%s'})]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/VebusSoc', value=0)

	def update_values(self, newvalues):
		vebus_service = newvalues.get('/VebusService')
		current_written = 0
		if vebus_service is not None:
			# Writing the extra charge current to the Multi serves two purposes:
			# 1) Also take the charge current from the MPPT into account in the VE.Bus SOC algorithm.
			# 2) The bulk timer in the Multi only runs when the battery is being charged, ie charge-current
			#    is positive. And in ESS Optimize mode, the Multi itself is not charging.
			#    So without knowing that the MPPT is charging, the bulk timer will never run, and absorption
			#    will then be very short.
			#
			# Always write the extra current, even if there is no solarcharger present. We need this because
			# once an SoC is written to the vebus service, the vebus device will stop adjusting its SoC until
			# an extra current is written.

			# Take only the charge current. Total current includes output on the load output terminals
			total_charge_current = newvalues.get('/Dc/Pv/ChargeCurrent', 0)
			try:
				charge_current = self._dbusmonitor.get_value(vebus_service, '/ExtraBatteryCurrent')
				if charge_current is not None:
					self._dbusmonitor.set_value_async(vebus_service, '/ExtraBatteryCurrent', total_charge_current)
					current_written = 1
			except DBusException:
				pass
		newvalues['/Control/ExtraBatteryCurrent'] = current_written

	def _write_vebus_soc(self):
		vebus_service = self._dbusservice['/VebusService']
		soc_written = 0
		if vebus_service is not None:
			if self._must_write_soc(vebus_service):
				soc = self._dbusservice['/Dc/Battery/Soc']
				if soc is not None:
					logging.debug("writing this soc to vebus: %d", soc)
					try:
						# Vebus service may go offline while we write this SoC
						self._dbusmonitor.set_value_async(vebus_service, '/Soc', soc)
						soc_written = 1
					except DBusException:
						pass
		self._dbusservice['/Control/VebusSoc'] = soc_written
		return True

	def _must_write_soc(self, vebus_service):
		active_battery_service = self._dbusservice['/ActiveBatteryService']
		if active_battery_service is None or active_battery_service.startswith('com.victronenergy.vebus'):
			return False
		if self._dbusmonitor.get_value(vebus_service, '/Hub2') is not None:
			return True
		# Writing SoC to the vebus service is not allowed when a hub-2 assistant is present, so we have to
		# check the list of assistant IDs.
		# Note that /Devices/0/Assistants provides a list of bytes which can be empty. It can also be invalid
		# (empty list of ints). An empty list of bytes is not interpreted as an invalid value. This allows
		# us to distinguish between an empty list and an invalid value.
		value = self._dbusmonitor.get_value(vebus_service, '/Devices/0/Assistants')
		if value is None:
			# List of assistants is not yet available, so we don't know which assistants are present. Return
			# False just in case a hub-2 assistant is in use.
			return False
		ids = set(i[0] | i[1] * 256 for i in itertools.izip(
			itertools.islice(value, 0, None, 2),
			itertools.islice(value, 1, None, 2)))
		if len(set(ids).intersection(VebusSocWriter._hub2_assistant_ids)) > 0:
			return False
		return True
