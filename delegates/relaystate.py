from gi.repository import GLib
import logging
import os
import traceback
from glob import glob
from functools import partial

# Victron packages
from ve_utils import exit_on_error

from delegates.base import SystemCalcDelegate

class RelayState(SystemCalcDelegate):
	RELAY_GLOB = '/dev/gpio/relay_*'

	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._relays = {}

	def get_input(self):
		return [
			('com.victronenergy.settings', [
				 '/Settings/Relay/Function'])] # Managed by the gui

	def get_settings(self):
		return [
			('/Relay/0/State', '/Settings/Relay/0/InitialState', 0, 0, 1),
			('/Relay/1/State', '/Settings/Relay/1/InitialState', 0, 0, 1)
		]

	@property
	def relay_function(self):
		return self._dbusmonitor.get_value('com.victronenergy.settings',
			'/Settings/Relay/Function')

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		relays = sorted(glob(self.RELAY_GLOB))

		if len(relays) == 0:
			logging.info('No relays found')
			return

		self._relays.update({i: os.path.join(r, 'value') \
			for i, r in enumerate(relays) })

		GLib.idle_add(exit_on_error, self._init_relay_state)
		for idx in self._relays.keys():
			self._dbusservice.add_path(f'/Relay/{idx}/State', value=None, writeable=True,
				onchangecallback=partial(self._on_relay_state_changed, idx))

		logging.info('Relays found: {}'.format(', '.join(self._relays.values())))

	def _init_relay_state(self):
		if self.relay_function is None:
			return True # Try again on the next idle event

		for idx, path in self._relays.items():
			if self.relay_function != 2 and idx == 0:
				continue # Skip primary relay if function is not manual
			dbus_path = f'/Relay/{idx}/State'
			try:
				state = self._settings[dbus_path]
			except KeyError:
				pass
			else:
				self._dbusservice[dbus_path] = state
				self.__on_relay_state_changed(idx, state)

		# Sync state back to dbus
		self._update_relay_state()

		# Watch changes and update dbus. Do we still need this?
		GLib.timeout_add(5000, exit_on_error, self._update_relay_state)
		return False

	def _update_relay_state(self):
		# @todo EV Do we still need this? Maybe only at startup?
		for idx, file_path in self._relays.items():
			try:
				with open(file_path, 'rt') as r:
					state = int(r.read().strip())
					self._dbusservice[f'/Relay/{idx}/State'] = state
			except (IOError, ValueError):
				traceback.print_exc()
		return True

	def __on_relay_state_changed(self, idx, state):
		try:
			path = self._relays[idx]
			with open(path, 'wt') as w:
				w.write(str(state))
		except IOError:
			traceback.print_exc()
			return False
		return True

	def _on_relay_state_changed(self, idx, dbus_path, value):
		try:
			state = int(bool(value))
		except ValueError:
			traceback.print_exc()
			return False
		try:
			return self.__on_relay_state_changed(idx, state)
		finally:
			# Remember the state to restore after a restart
			self._settings[f'/Relay/{idx}/State'] = state
