import logging
import gobject
from dbus.exceptions import DBusException
from delegates.base import SystemCalcDelegate
from ve_utils import exit_on_error
from sc_utils import safeadd

class BydCurrentSense(SystemCalcDelegate):
	def __init__(self, sc):
		super(BydCurrentSense, self).__init__()
		self.systemcalc = sc
		self.batteries = set()
		self._timer = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(BydCurrentSense, self).set_sources(dbusmonitor, settings, dbusservice)
		self._timer = gobject.timeout_add(3000, exit_on_error, self._on_timer)

	def device_added(self, service, instance, do_service_change=True):
		if service.startswith('com.victronenergy.battery.') and \
			self._dbusmonitor.get_value(service, '/ProductId') == 0xB00A:
			logging.info('BYD battery service appeared: %s' % service)
			self.batteries.add(service)

	def device_removed(self, service, instance):
		if service in self.batteries:
			self.batteries.discard(service)

	def _on_timer(self):
		# If there is more than one BYD battery, we can't do this.
		if len(self.batteries) != 1:
			return True

		battery = next(iter(self.batteries))

		# If this battery is not the selected battery service, there is no
		# point in improving the SOC. Only do this if the BYD battery is
		# the battery service.
		if battery != self.systemcalc._batteryservice:
			return True

		# We cannot use the current from the battery monitor, because
		# 1) that is probably ourselves, 2) it could be another battery, 3) if
		# it is a BMV, we already have good SOC tracking and this is pointless.
		# So calculate the battery current by taking the vebus DC current and
		# adding solarcharger current.
		battery_current = safeadd(self._dbusservice['/Dc/Vebus/Current'],
			self._dbusservice['/Dc/Pv/Current'])

		if battery_current is not None:
			self._dbusmonitor.set_value_async(battery, '/Sense/Current', float(battery_current))

		return True
