#!/usr/bin/python -u
# -*- coding: utf-8 -*-

import dbus
import functools
import gobject
import itertools
import logging
import os
import sc_utils
import signal
import sys
import traceback

# Victron packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 'ext', 'velib_python'))
from sc_utils import safeadd
from ve_utils import exit_on_error


class SystemCalcDelegate(object):
	def set_sources(self, dbusmonitor, settings, dbusservice):
		self._dbusmonitor = dbusmonitor
		self._settings = settings
		self._dbusservice = dbusservice

	def get_input(self):
		'''In derived classes this function should return the list or D-Bus paths used as input. This will be
		used to populate self._dbusmonitor. Paths should be ordered by service name.
		Example:
		def get_input(self):
			return [
				('com.victronenergy.battery', ['/ProductId']),
				('com.victronenergy.solarcharger', ['/ProductId'])]
		'''
		return []

	def get_output(self):
		'''In derived classes this function should return the list or D-Bus paths used as input. This will be
		used to create the D-Bus items in the com.victronenergy.system service. You can include a gettext
		field which will be used to format the result of the GetText reply.
		Example:
		def get_output(self):
			return [('/Hub', {'gettext': '%s'}), ('/Dc/Battery/Current', {'gettext': '%s A'})]
		'''
		return []

	def get_settings(self):
		'''In derived classes this function should return all settings (from com.victronenergy.settings)
		that are used in this class. The return value will be used to populate self._settings.
		Note that if you add a setting here, it will be created (using AddSettings of the D-Bus), if you
		do not want that, add your setting to the list returned by get_input.
		List item format: (<alias>, <path>, <default value>, <min value>, <max value>)
		def get_settings(self):
			return [('writevebussoc', '/Settings/SystemSetup/WriteVebusSoc', 0, 0, 1)]
		'''
		return []

	def update_values(self, newvalues):
		pass

	def device_added(self, service, instance, do_service_change=True):
		pass

	def device_removed(self, service, instance):
		pass


class HubTypeSelect(SystemCalcDelegate):
	def __init__(self):
		pass

	def get_input(self):
		return [
			('com.victronenergy.vebus', ['/Hub/ChargeVoltage', '/Hub4/AssistantId'])]

	def get_output(self):
		return [('/Hub', {'gettext': '%s'}), ('/SystemType', {'gettext': '%s'})]

	def device_added(self, service, instance, do_service_change=True):
		pass

	def device_removed(self, service, instance):
		pass

	def update_values(self, newvalues):
		# The code below should be executed after PV inverter data has been updated, because we need the
		# PV inverter total power to update the consumption.
		hub = None
		system_type = None
		vebus_path = newvalues.get('/VebusService')
		hub4_assistant_id = self._dbusmonitor.get_value(vebus_path, '/Hub4/AssistantId')
		if hub4_assistant_id != None:
			hub = 4
			system_type = 'ESS' if hub4_assistant_id == 5 else 'Hub-4'
		elif self._dbusmonitor.get_value(vebus_path, '/Hub/ChargeVoltage') != None or \
			newvalues.get('/Dc/Pv/Power') != None:
			hub = 1
			system_type = 'Hub-1'
		elif newvalues.get('/Ac/PvOnOutput/NumberOfPhases') != None:
			hub = 2
			system_type = 'Hub-2'
		elif newvalues.get('/Ac/PvOnGrid/NumberOfPhases') != None or \
			newvalues.get('/Ac/PvOnGenset/NumberOfPhases') != None:
			hub = 3
			system_type = 'Hub-3'
		newvalues['/Hub'] = hub
		newvalues['/SystemType'] = system_type


class Hub1Bridge(SystemCalcDelegate):
	def __init__(self, service_supervisor):
		self._solarchargers = []
		self._vecan_services = []
		self._battery_services = []
		self._timer = None
		self._service_supervisor = service_supervisor

	def get_input(self):
		return [
			('com.victronenergy.battery',
				['/Info/MaxChargeCurrent']),
			('com.victronenergy.vebus',
				['/Hub/ChargeVoltage', '/State']),
			('com.victronenergy.solarcharger',
				['/Link/NetworkMode', '/Link/ChargeVoltage', '/Link/ChargeCurrent', '/State', '/FirmwareVersion', '/Mgmt/Connection']),
			('com.victronenergy.vecan',
				['/Link/ChargeVoltage'])]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/SolarChargeVoltage', value=0)
		self._dbusservice.add_path('/Control/SolarChargeCurrent', value=0)

	def device_added(self, service, instance, do_service_change=True):
		service_type = service.split('.')[2]
		if service_type == 'solarcharger':
			self._solarchargers.append(service)
			self._update_solarchargers()
		elif service_type == 'vecan':
			self._vecan_services.append(service)
			self._update_solarchargers()
		elif service_type == 'battery':
			self._battery_services.append(service)
			self._update_solarchargers()
		else:
			# Skip timer code below
			return
		if self._timer is None:
			# Update the solar charger every 10 seconds, because it has to switch to HEX mode each time
			# we write a value to its D-Bus service. Writing too often may block text messages.
			self._timer = gobject.timeout_add(10000, exit_on_error, self._on_timer)

	def device_removed(self, service, instance):
		if service in self._solarchargers:
			self._solarchargers.remove(service)
		elif service in self._vecan_services:
			self._vecan_services.remove(service)
		elif service in self._battery_services:
			self._battery_services.remove(service)
		if len(self._solarchargers) == 0 and len(self._vecan_services) == 0 and self._timer is not None:
			gobject.source_remove(self._timer)
			self._timer = None

	def _on_timer(self):
		self._update_solarchargers()
		return True

	def _update_solarchargers(self):
		voltage_written = 0
		current_written = 0
		max_charge_current = None
		for battery_service in self._battery_services:
			max_charge_current = safeadd(max_charge_current, \
				self._dbusmonitor.get_value(battery_service, '/Info/MaxChargeCurrent'))
		vebus_path = self._get_vebus_path()
		charge_voltage = None if vebus_path is None else \
			self._dbusmonitor.get_value(vebus_path, '/Hub/ChargeVoltage')
		# Network mode:
		# bit 0: Operated in network environment
		# bit 2: Remote Hub-1 control
		# bit 3: Remote BMS control
		network_mode = 1 | (0 if charge_voltage is None else 4) | (0 if max_charge_current is None else 8)
		if network_mode > 1:
			has_vecan_charger = False
			for service in self._solarchargers:
				if self._service_supervisor.is_busy(service):
					logging.debug('Solarcharger being supervised: {}'.format(service))
					continue
				try:
					# We use /Link/NetworkMode to detect Hub-1 support in the solarcharger. Existence of this item
					# implies existence of the other /Link/* fields
					network_mode_item = self._dbusmonitor.get_item(service, '/Link/NetworkMode')
					if network_mode_item.get_value() is not None:
						network_mode_item.set_value(dbus.Int32(network_mode, variant_level=1))
						if charge_voltage is not None:
							charge_voltage_item = self._dbusmonitor.get_item(service, '/Link/ChargeVoltage')
							charge_voltage_item.set_value(dbus.Double(charge_voltage, variant_level=1))
							firmware_version = self._dbusmonitor.get_value(service, '/FirmwareVersion')
							if firmware_version is not None and (firmware_version & 0x0FFF) == 0x0117:
								state = self._dbusmonitor.get_value(vebus_path, '/State')
								if state is not None:
									state_item = self._dbusmonitor.get_item(service, '/State')
									state_item.set_value(dbus.Int32(state, variant_level=1))
						if max_charge_current is not None:
							charge_current_item = self._dbusmonitor.get_item(service, '/Link/ChargeCurrent')
							charge_current_item.set_value(dbus.Double(max_charge_current, variant_level=1))
							current_written = 1
						voltage_written = 1
				except dbus.exceptions.DBusException:
					pass
				has_vecan_charger = has_vecan_charger or (self._dbusmonitor.get_value(service, '/Mgmt/Connection') == 'VE.Can')
			if has_vecan_charger and charge_voltage is not None:
				for service in self._vecan_services:
					try:
						charge_voltage_item = self._dbusmonitor.get_item(service, '/Link/ChargeVoltage')
						# Note: we don't check the value of charge_voltage_item because it may be invalid,
						# for example if the D-Bus path has not been written for more than 60 (?) seconds.
						# In case there is no path at all, the set_value below will raise an DBusException
						# which we will ignore cheerfully.
						charge_voltage_item.set_value(dbus.Double(charge_voltage, variant_level=1))
						voltage_written = 1
					except dbus.exceptions.DBusException:
						pass
		self._dbusservice['/Control/SolarChargeVoltage'] = voltage_written
		self._dbusservice['/Control/SolarChargeCurrent'] = current_written

	def _get_vebus_path(self, newvalues=None):
		if newvalues == None:
			if '/VebusService' not in self._dbusservice:
				return None
			return self._dbusservice['/VebusService']
		return newvalues.get('/VebusService')


class ServiceMapper(SystemCalcDelegate):
	def __init__(self):
		pass

	def device_added(self, service, instance, do_service_change=True):
		path = self._get_service_mapping_path(service, instance)
		if path in self._dbusservice:
			self._dbusservice[path] = service
		else:
			self._dbusservice.add_path(path, service)

	def device_removed(self, service, instance):
		path = self._get_service_mapping_path(service, instance)
		if path in self._dbusservice:
			del self._dbusservice[path]

	def _get_service_mapping_path(self, service, instance):
		sn = sc_utils.service_instance_name(service, instance).replace('.', '_').replace('/', '_')
		return '/ServiceMapping/%s' % sn


class VebusSocWriter(SystemCalcDelegate):
	_hub2_assistant_ids = set([0x0134, 0x0135, 0x0137, 0x0138, 0x013A, 0x141, 0x0146, 0x014D])

	def __init__(self):
		SystemCalcDelegate.__init__(self)
		gobject.idle_add(exit_on_error, lambda: not self._write_vebus_soc())
		gobject.timeout_add(10000, exit_on_error, self._write_vebus_soc)
		self._is_hub2 = None

	def get_input(self):
		return [('com.victronenergy.vebus', ['/Soc', '/ExtraBatteryCurrent', '/Devices/0/Assistants'])]

	def get_output(self):
		return [('/Control/ExtraBatteryCurrent', {'gettext': '%s'})]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/VebusSoc', value=0)

	def update_values(self, newvalues):
		vebus_service = newvalues.get('/VebusService')
		current_written = 0
		if vebus_service != None and not self._must_write_soc(vebus_service):
			# Always write the extra current, even if there is no solarcharge present. We need this because once
			# an SoC is written to the vebus service, the vebus device will stop adjusting its SoC until an
			# extra current is written.
			total_charge_current = newvalues.get('/Dc/Pv/Current', 0)
			try:
				charge_current_item = self._dbusmonitor.get_item(vebus_service, '/ExtraBatteryCurrent')
				if charge_current_item.get_value() != None:
					charge_current_item.set_value(dbus.Double(total_charge_current, variant_level=1))
					current_written = 1
			except dbus.exceptions.DBusException:
				pass
		newvalues['/Control/ExtraBatteryCurrent'] = current_written

	def _write_vebus_soc(self):
		vebus_service = self._dbusservice['/VebusService']
		soc_written = 0
		if vebus_service != None:
			self._update_hub2_presence(vebus_service)
			if self._must_write_soc(vebus_service):
				soc = self._dbusservice['/Dc/Battery/Soc']
				if soc != None:
					logging.debug("writing this soc to vebus: %d", soc)
					try:
						# Vebus service may go offline while we write this SoC
						self._dbusmonitor.get_item(vebus_service, '/Soc').set_value(dbus.Double(soc, variant_level=1))
						soc_written = 1
					except dbus.exceptions.DBusException:
						pass
		self._dbusservice['/Control/VebusSoc'] = soc_written
		return True

	def _must_write_soc(self, vebus_service):
		if self._is_hub2 == None:
			self._update_hub2_presence(vebus_service)
		if self._is_hub2:
			return False
		active_battery_service = self._dbusservice['/ActiveBatteryService']
		if active_battery_service == None or active_battery_service.startswith('com.victronenergy.vebus'):
			return False
		return True

	def _update_hub2_presence(self, vebus_service):
		# Note that /Devices/0/Assistants provides a list of bytes which can be empty. It can also be invalid
		# (empty list of ints). An empty list of bytes is not interpreted as an invalid value. This allows
		# us to distinguish between an empty list and an invalid value.
		value = self._dbusmonitor.get_value(vebus_service, '/Devices/0/Assistants')
		if value == None:
			# List of assistants is not available, so we don't know which assistants are present. Because
			# it is not allowed to write the vebus SoC on a hub-2 system, we assume for now there is a hub-2
			# assistant. The flag will be reset later when the list is published.
			self._is_hub2 = True
			return
		ids = set(i[0] | i[1] * 256 for i in itertools.izip(\
			itertools.islice(value, 0, None, 2), \
			itertools.islice(value, 1, None, 2)))
		self._is_hub2 = len(set(ids).intersection(VebusSocWriter._hub2_assistant_ids)) > 0


class RelayState(SystemCalcDelegate):
	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		relays = sc_utils.gpio_paths('/etc/venus/relays')
		if len(relays) == 0:
			logging.info('No relays found')
			return
		self._relays = {}
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
				w.write('1'  if int(value) == 1 else '0')
			return True
		except (IOError, ValueError):
			traceback.print_exc()
			return False


class BuzzerControl(SystemCalcDelegate):
	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		paths = sc_utils.gpio_paths('/etc/venus/buzzer')
		if len(paths) == 0:
			logging.info('No buzzer found')
			return
		self._path = os.path.join(paths[0], 'value')
		self._buzzer_on = False
		self._timer = None
		self._dbusservice.add_path('/Buzzer/State', value=0, writeable=True,
			onchangecallback=lambda p,v: exit_on_error(self._on_buzzer_state_changed, v))
		logging.info('Buzzer found: {}'.format(self._path))

	def _on_buzzer_state_changed(self, value):
		try:
			if int(value) == 1:
				if self._timer == None:
					self._timer = gobject.timeout_add(500, exit_on_error, self._on_timer)
					self._set_buzzer(True)
			elif self._timer != None:
				gobject.source_remove(self._timer)
				self._timer = None
				self._set_buzzer(False)
		except ValueError:
			traceback.print_exc()
		return True

	def _on_timer(self):
		self._set_buzzer(not self._buzzer_on)
		return True

	def _set_buzzer(self, on):
		try:
			with open(self._path, 'wt') as w:
				w.write('1' if on else '0')
			self._buzzer_on = on
			return True
		except IOError:
			traceback.print_exc()
			return False


class LgCircuitBreakerDetect(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._lg_battery = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Dc/Battery/Alarms/CircuitBreakerTripped', value=None)

	def device_added(self, service, instance, do_service_change=True):
		service_type = service.split('.')[2]
		if service_type == 'battery' and self._dbusmonitor.get_value(service, '/ProductId') == 0xB004:
			logging.info('LG battery service appeared: %s' % service)
			self._lg_battery = service
			self._lg_voltage_buffer = []
			self._dbusservice['/Dc/Battery/Alarms/CircuitBreakerTripped'] = 0

	def device_removed(self, service, instance):
		if service == self._lg_battery:
			logging.info('LG battery service disappeared: %s' % service)
			self._lg_battery = None
			self._lg_voltage_buffer = None
			self._dbusservice['/Dc/Battery/Alarms/CircuitBreakerTripped'] = None

	def update_values(self, newvalues):
		vebus_path = newvalues.get('/VebusService')
		if self._lg_battery is None or vebus_path is None:
			return
		battery_current = self._dbusmonitor.get_value(self._lg_battery, '/Dc/0/Current')
		if battery_current is None or abs(battery_current) > 0.01:
			if len(self._lg_voltage_buffer) > 0:
				logging.debug('LG voltage buffer reset')
				self._lg_voltage_buffer = []
			return
		vebus_voltage = self._dbusmonitor.get_value(vebus_path, '/Dc/0/Voltage')
		if vebus_voltage is None:
			return
		self._lg_voltage_buffer.append(float(vebus_voltage))
		if len(self._lg_voltage_buffer) > 40:
			self._lg_voltage_buffer = self._lg_voltage_buffer[-40:]
		elif len(self._lg_voltage_buffer) < 20:
			return
		min_voltage = min(self._lg_voltage_buffer)
		max_voltage = max(self._lg_voltage_buffer)
		battery_voltage = self._dbusmonitor.get_value(self._lg_battery, '/Dc/0/Voltage')
		logging.debug('LG battery current V=%s I=%s' % (battery_voltage, battery_current))
		if min_voltage < 0.9 * battery_voltage or max_voltage > 1.1 * battery_voltage:
			logging.error('LG shutdown detected V=%s I=%s %s' % (battery_voltage, battery_current, self._lg_voltage_buffer))
			item = self._dbusmonitor.get_item(vebus_path, '/Mode')
			if item is None:
				logging.error('Cannot switch off vebus device')
			else:
				self._dbusservice['/Dc/Battery/Alarms/CircuitBreakerTripped'] = 2
				item.set_value(dbus.Int32(4, variant_level=1))
				self._lg_voltage_buffer = []


class ServiceSupervisor(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._supervised = set()
		self._busy = set()
		gobject.timeout_add(60000, exit_on_error, self._process_supervised)

	def get_input(self):
		return [
			('com.victronenergy.battery', ['/ProductId']),
			('com.victronenergy.solarcharger', ['/ProductId'])]

	def device_added(self, service, instance, do_service_change=True):
		service_type = service.split('.')[2]
		if service_type == 'battery' or service_type == 'solarcharger':
			self._supervised.add(service)

	def device_removed(self, service, instance):
		self._supervised.discard(service)
		self._busy.discard(service)

	def is_busy(self, service):
		return service in self._busy

	def _process_supervised(self):
		for service in self._supervised:
			# Do an async call. If the owner of the service does not answer, we do not want to wait for
			# the timeout here.
			# Do not use lambda function in the async call, because the lambda functions will be executed
			# after completion of the loop, and the service parameter will have the value that was assigned
			# to it in the last iteration. Instead we use functools.partial, which will 'freeze' the current
			# value of service.
			self._busy.add(service)
			self._dbusmonitor.dbusConn.call_async(
				service, '/ProductId', None, 'GetValue', '', [],
				functools.partial(exit_on_error, self._supervise_success, service),
				functools.partial(exit_on_error, self._supervise_failed, service))
		return True

	def _supervise_success(self, service, value):
		self._busy.discard(service)

	def _supervise_failed(self, service, error):
		try:
			self._busy.discard(service)
			if error.get_dbus_name() != 'org.freedesktop.DBus.Error.NoReply':
				logging.info('Ignoring supervise error from %s: %s' % (service, error))
				return
			logging.error('%s is not responding to D-Bus requests' % service)
			pid = self._dbusmonitor.dbusConn.call_blocking('org.freedesktop.DBus', '/', None,
				'GetConnectionUnixProcessID', 's', [service])
			if pid is not None and pid > 1:
				logging.error('killing owner of %s (pid=%s)' % (service, pid))
				os.kill(pid, signal.SIGKILL)
		except (OSError, dbus.exceptions.DBusException):
			traceback.print_exc()
