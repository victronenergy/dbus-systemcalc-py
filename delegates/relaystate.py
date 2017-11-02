import gobject
import logging
import os
import traceback

# Victron packages
from sc_utils import gpio_paths
from ve_utils import exit_on_error

from delegates.base import SystemCalcDelegate

class RelayState(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._relays = {}

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		relays = gpio_paths('/etc/venus/relays')
		if len(relays) == 0:
			logging.info('No relays found')
			return
		i = 0
		for r in relays:
			path = os.path.join(r, 'value')
			dbus_path = '/Relay/{}/State'.format(i)
			self._relays[dbus_path] = path
			self._dbusservice.add_path(dbus_path, value=None, writeable=True,
				onchangecallback=self._on_relay_state_changed)
			i += 1
		logging.info('Relays found: {}'.format(', '.join(self._relays.values())))
		gobject.idle_add(exit_on_error, lambda: not self._update_relay_state())
		gobject.timeout_add(5000, exit_on_error, self._update_relay_state)

	def _update_relay_state(self):
		# @todo EV Do we still need this? Maybe only at startup?
		for dbus_path, file_path in self._relays.items():
			try:
				with open(file_path, 'rt') as r:
					state = int(r.read().strip())
					self._dbusservice[dbus_path] = state
			except (IOError, ValueError):
				traceback.print_exc()
		return True

	def _on_relay_state_changed(self, dbus_path, value):
		try:
			path = self._relays[dbus_path]
			with open(path, 'wt') as w:
				w.write('1' if int(value) == 1 else '0')
			return True
		except (IOError, ValueError):
			traceback.print_exc()
			return False
