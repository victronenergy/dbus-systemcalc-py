#!/usr/bin/python -u
# -*- coding: utf-8 -*-

import dbus
import fcntl
import gobject
import itertools
import logging
import math
import os
import sc_utils
import sys
import traceback

# Victron packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 'ext', 'velib_python'))
from sc_utils import safeadd
from ve_utils import exit_on_error


class SystemCalcDelegate(object):
	def __init__(self):
		self._dbusmonitor = None
		self._settings = None
		self._dbusservice = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		self._dbusmonitor = dbusmonitor
		self._settings = settings
		self._dbusservice = dbusservice

	def get_input(self):
		"""In derived classes this function should return the list or D-Bus paths used as input. This will be
		used to populate self._dbusmonitor. Paths should be ordered by service name.
		Example:
		def get_input(self):
			return [
				('com.victronenergy.battery', ['/ProductId']),
				('com.victronenergy.solarcharger', ['/ProductId'])]
		"""
		return []

	def get_output(self):
		"""In derived classes this function should return the list or D-Bus paths used as input. This will be
		used to create the D-Bus items in the com.victronenergy.system service. You can include a gettext
		field which will be used to format the result of the GetText reply.
		Example:
		def get_output(self):
			return [('/Hub', {'gettext': '%s'}), ('/Dc/Battery/Current', {'gettext': '%s A'})]
		"""
		return []

	def get_settings(self):
		"""In derived classes this function should return all settings (from com.victronenergy.settings)
		that are used in this class. The return value will be used to populate self._settings.
		Note that if you add a setting here, it will be created (using AddSettings of the D-Bus), if you
		do not want that, add your setting to the list returned by get_input.
		List item format: (<alias>, <path>, <default value>, <min value>, <max value>)
		def get_settings(self):
			return [('writevebussoc', '/Settings/SystemSetup/WriteVebusSoc', 0, 0, 1)]
		"""
		return []

	def update_values(self, newvalues):
		pass

	def device_added(self, service, instance, do_service_change=True):
		pass

	def device_removed(self, service, instance):
		pass


class HubTypeSelect(SystemCalcDelegate):
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
		if hub4_assistant_id is not None:
			hub = 4
			system_type = 'ESS' if hub4_assistant_id == 5 else 'Hub-4'
		elif self._dbusmonitor.get_value(vebus_path, '/Hub/ChargeVoltage') is not None or \
			newvalues.get('/Dc/Pv/Power') is not None:
			hub = 1
			system_type = 'Hub-1'
		elif newvalues.get('/Ac/PvOnOutput/NumberOfPhases') is not None:
			hub = 2
			system_type = 'Hub-2'
		elif newvalues.get('/Ac/PvOnGrid/NumberOfPhases') is not None or \
			newvalues.get('/Ac/PvOnGenset/NumberOfPhases') is not None:
			hub = 3
			system_type = 'Hub-3'
		newvalues['/Hub'] = hub
		newvalues['/SystemType'] = system_type


class Hub1Bridge(SystemCalcDelegate):
	# if ChargeCurrent > ChangeCurrentLimitedFactor * MaxChargeCurrent we assume that the solar charger is
	# current limited, and yield do more power if we increase the MaxChargeCurrent
	ChargeCurrentLimitedFactor = 0.9
	VebusChargeFactor = 0.8
	StaticScaleFactor = 1.1 / ChargeCurrentLimitedFactor

	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._solarchargers = []
		self._vecan_services = []
		self._battery_services = []
		self._timer = None

	def get_input(self):
		return [
			('com.victronenergy.battery', [
				'/Info/BatteryLowVoltage',
				'/Info/MaxChargeCurrent',
				'/Info/MaxChargeVoltage',
				'/Info/MaxDischargeCurrent']),
			('com.victronenergy.vebus', [
				'/Ac/ActiveIn/Connected',
				'/Hub/ChargeVoltage',
				'/Dc/0/Current',
				'/Dc/0/MaxChargeCurrent',
				'/State',
				'/BatteryOperationalLimits/BatteryLowVoltage',
				'/BatteryOperationalLimits/MaxChargeCurrent',
				'/BatteryOperationalLimits/MaxChargeVoltage',
				'/BatteryOperationalLimits/MaxDischargeCurrent']),
			('com.victronenergy.solarcharger', [
				'/Dc/0/Current',
				'/Link/NetworkMode',
				'/Link/ChargeVoltage',
				'/Link/ChargeCurrent',
				'/Settings/ChargeCurrentLimit',
				'/State',
				'/FirmwareVersion',
				'/Mgmt/Connection']),
			('com.victronenergy.vecan',
				['/Link/ChargeVoltage']),
			('com.victronenergy.settings',
				['/Settings/CGwacs/OvervoltageFeedIn'])]

	def get_settings(self):
		return [('maxchargecurrent', '/Settings/SystemSetup/MaxChargeCurrent', -1, -1, 10000)]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/SolarChargeVoltage', value=0)
		self._dbusservice.add_path('/Control/SolarChargeCurrent', value=0)
		self._dbusservice.add_path('/Control/BmsParameters', value=0)
		self._dbusservice.add_path('/Control/MaxChargeCurrent', value=0)

	def device_added(self, service, instance, do_service_change=True):
		service_type = service.split('.')[2]
		if service_type == 'solarcharger':
			self._solarchargers.append(service)
			self._on_timer()
		elif service_type == 'vecan':
			self._vecan_services.append(service)
			self._on_timer()
		elif service_type == 'battery':
			self._battery_services.append(service)
			self._on_timer()
		else:
			# Skip timer code below
			return
		if self._timer is None:
			# Update the solar charger every 3 seconds, because it has to switch to HEX mode each time
			# we write a value to its D-Bus service. Writing too often may block text messages. In MPPT
			# firmware v1.23 and later, all relevant values will be transmitted as asynchronous message,
			# so the update rate could be increased.
			self._timer = gobject.timeout_add(3000, exit_on_error, self._on_timer)

	def device_removed(self, service, instance):
		if service in self._solarchargers:
			self._solarchargers.remove(service)
		elif service in self._vecan_services:
			self._vecan_services.remove(service)
		elif service in self._battery_services:
			self._battery_services.remove(service)
		if len(self._solarchargers) == 0 and len(self._vecan_services) == 0 and \
			len(self._battery_services) == 0 and self._timer is not None:
			gobject.source_remove(self._timer)
			self._timer = None

	def _on_timer(self):
		vebus_path = self._dbusservice['/VebusService']
		has_ess_assistant = None
		adjust_vebus_max_charge_current = None
		if vebus_path is not None:
			# We do not analyse the content of /Devices/0/Assistants, because that would require us to keep
			# a list of ESS assistant version numbers (see VebusSocWriter._hub2_assistant_ids). Because that
			# list is expected to change (unlike the list of hub-2 assistants), we use /Hub4/AssistantId to
			# check the presence. It is guaranteed that /Hub4/AssistantId will be published before
			# /Devices/0/Assistants.
			assistants = self._dbusmonitor.get_value(vebus_path, '/Devices/0/Assistants')
			if assistants is not None:
				has_ess_assistant = self._dbusmonitor.get_value(vebus_path, '/Hub4/AssistantId') == 5
			# BOL support on vebus implies dynamic max charge current support. Both features were added in the
			# same version (v415). We cannot check the presence of /Dc/0/MaxChargeCurrent, because it already
			# existed in earlier version, where it was not dynamic (ie. should not be change too often).
			adjust_vebus_max_charge_current = \
				self._dbusmonitor.exists(vebus_path, '/BatteryOperationalLimits/MaxChargeVoltage')
		bms_service = self.find_bms_service()
		bms_parameters_written = self._update_battery_operational_limits(bms_service, has_ess_assistant)
		voltage_written, current_written = self._update_solarchargers(bms_service, has_ess_assistant,
			adjust_vebus_max_charge_current)
		self._dbusservice['/Control/SolarChargeVoltage'] = voltage_written
		self._dbusservice['/Control/SolarChargeCurrent'] = current_written
		self._dbusservice['/Control/BmsParameters'] = bms_parameters_written
		# @todo EV What if ESS + OvervoltageFeedIn? In that case there is no charge current control on the
		# MPPTs.
		self._dbusservice['/Control/MaxChargeCurrent'] = vebus_path is None or adjust_vebus_max_charge_current
		return True

	def _update_battery_operational_limits(self, bms_service, has_ess_assistant):
		# If has_ess_assistant is None, it is assumed that is it not clear if the assistant is present.
		# (vebus is still initializing).
		if bms_service is None:
			return 0
		vebus_path = self._dbusservice['/VebusService']
		if vebus_path is None:
			return 0
		try:
			# With vebus firmware v415 and the ESS assistant released on 20170616, the voltage setpoint
			# published by the vebus devices may exceed the BOL max charge voltage. This may cause
			# problems with CAN-bus BMS batteries. For now, we do not copy the max charge voltage if there is
			# a ESS assistant, or if it is not clear yet if the assistant is present.
			if has_ess_assistant is not None and not has_ess_assistant:
				sc_utils.copy_dbus_value(self._dbusmonitor,
					bms_service, '/Info/MaxChargeVoltage',
					vebus_path, '/BatteryOperationalLimits/MaxChargeVoltage')
			sc_utils.copy_dbus_value(self._dbusmonitor,
				bms_service, '/Info/MaxChargeCurrent',
				vebus_path, '/BatteryOperationalLimits/MaxChargeCurrent')
			sc_utils.copy_dbus_value(self._dbusmonitor,
				bms_service, '/Info/BatteryLowVoltage',
				vebus_path, '/BatteryOperationalLimits/BatteryLowVoltage')
			sc_utils.copy_dbus_value(self._dbusmonitor,
				bms_service, '/Info/MaxDischargeCurrent',
				vebus_path, '/BatteryOperationalLimits/MaxDischargeCurrent')
			return 1
		except dbus.exceptions.DBusException:
			logging.debug(traceback.format_exc())
			return 0

	def _update_solarchargers(self, bms_service, has_ess_assistant, adjust_vebus_max_charge_current):
		bms_max_charge_current = None if bms_service is None else \
			self._dbusmonitor.get_value(bms_service, '/Info/MaxChargeCurrent')
		max_charge_current = self._settings['maxchargecurrent']
		# @todo EV If the max charge current is set the MPPTs will move to BMS mode. If the setting is reset
		# later on, the MPPTs will error, because the are taken out of BMS mode. This is a good thing if the
		# BMS max charge current is no longer available, but in case of a setting...
		# Another issue: if a max charge current setting is set and a BMS is present, we get no errors
		# whenever the BMS service disappears, because we keep setting the max charge current on the MPPTs.
		if max_charge_current < 0:
			max_charge_current = bms_max_charge_current
		elif bms_max_charge_current is not None:
			max_charge_current = min(max_charge_current, bms_max_charge_current)

		vebus_path = self._dbusservice['/VebusService']

		# Feedback allowed is defined as 'ESS present and FeedInOvervoltage is enabled'. This ignores other
		# setups which allow feedback: hub-1.
		feedback_allowed = \
			has_ess_assistant and \
			self._dbusmonitor.get_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn') == 1 and \
			self._dbusmonitor.get_value(vebus_path, '/Ac/ActiveIn/Connected') == 1

		# If the vebus service does not provide a charge voltage setpoint (so no ESS/Hub-1/Hub-4), we use the
		# max charge voltage provided by the BMS (if any). This will probably prevent feedback, but that is
		# probably not allowed anyway.
		charge_voltage = None
		if vebus_path is not None:
			charge_voltage = self._dbusmonitor.get_value(vebus_path, '/Hub/ChargeVoltage')
		if charge_voltage is None and bms_service is not None:
			charge_voltage = self._dbusmonitor.get_value(bms_service, '/Info/MaxChargeVoltage')
		if charge_voltage is None and max_charge_current is None:
			# @todo EV Reset vebus_max_charge_current here? To what value? We get here if the BMS battery
			# service disappears or the max charge current setting is reset.
			return 0, 0
		# Network mode:
		# bit 0: Operated in network environment
		# bit 2: Remote Hub-1 control (MPPT will accept charge voltage)
		# bit 3: Remote BMS control (MPPT will accept max charge current, and enter BMS mode)
		network_mode = 1 | (0 if charge_voltage is None else 4) | (0 if max_charge_current is None else 8)
		has_vecan_chargers = False
		vedirect_chargers = []
		network_mode_written = False
		for service in self._solarchargers:
			try:
				if self._dbusmonitor.get_value(service, '/Mgmt/Connection') == 'VE.Can':
					has_vecan_chargers = True
				# We use /Link/NetworkMode to detect Hub support in the solarcharger. Existence of this item
				# implies existence of the other /Link/* fields.
				if self._dbusmonitor.get_value(service, '/Link/NetworkMode') is not None:
					vedirect_chargers.append(service)
					self._dbusmonitor.set_value(service, '/Link/NetworkMode', network_mode)
					network_mode_written = True
			except dbus.exceptions.DBusException:
				pass

		voltage_written = self._distribute_voltage_setpoint(vebus_path, charge_voltage, vedirect_chargers,
															has_vecan_chargers)

		# Do not limit max charge current when feedback is allowed. The rationale behind this is that MPPT
		# charge power should match the capabilities of the battery. If the default charge algorithm is used
		# by the MPPTs, the charge current should stay within limits. This avoids a problem that we do not
		# know if extra MPPT power will be fed back to the grid when we decide to increase the MPPT max charge
		# current.
		# If feedback is allowed, we limit the vebus max charge current to bms_max_charge_current, because we
		# have to write a value, and we have no default value (this would be the max charge current setting,
		# which is not available right now).
		if feedback_allowed:
			self._maximize_charge_current(vebus_path, max_charge_current, vedirect_chargers)
		else:
			self._distribute_max_charge_current(vebus_path, max_charge_current, vedirect_chargers,
				adjust_vebus_max_charge_current)

		current_written = 1 if network_mode_written and max_charge_current is not None else 0
		return voltage_written, current_written

	def _distribute_voltage_setpoint(self, vebus_path, charge_voltage, vedirect_chargers, has_vecan_chargers):
		if charge_voltage is None:
			return 0

		voltage_written = 0
		for service in vedirect_chargers:
			try:
				self._dbusmonitor.set_value(service, '/Link/ChargeVoltage', charge_voltage)
				voltage_written = 1
				# solarcharger firmware v1.17 does not support link items. Version v1.17 itself requires
				# the vebus state to be copied to the solarcharger (otherwise the charge voltage would be
				# ignored). v1.18 and later do not have this requirement.
				firmware_version = self._dbusmonitor.get_value(service, '/FirmwareVersion')
				if firmware_version is not None and (firmware_version & 0x0FFF) == 0x0117:
					state = self._dbusmonitor.get_value(vebus_path, '/State')
					if state is not None:
						self._dbusmonitor.set_value(service, '/State', state)
			except dbus.exceptions.DBusException:
				pass

		if not has_vecan_chargers:
			return voltage_written

		# Charge voltage cannot by written directly to the CAN-bus solar chargers, we have to use
		# the com.victronenergy.vecan.* service instead.
		# Writing charge current to CAN-bus solar charger is not supported yet.
		for service in self._vecan_services:
			try:
				# Note: we don't check the value of charge_voltage_item because it may be invalid,
				# for example if the D-Bus path has not been written for more than 60 (?) seconds.
				# In case there is no path at all, the set_value below will raise an DBusException
				# which we will ignore cheerfully.
				self._dbusmonitor.set_value(service, '/Link/ChargeVoltage', charge_voltage)
				voltage_written = 1
			except dbus.exceptions.DBusException:
				pass

		return voltage_written

	def _maximize_charge_current(self, vebus_path, bms_max_charge_current, vedirect_chargers):
		if bms_max_charge_current is None:
			return

		try:
			self._dbusmonitor.set_value(vebus_path, '/Dc/0/MaxChargeCurrent', bms_max_charge_current)
		except dbus.exceptions.DBusException:
			logging.debug(traceback.format_exc())

		for service in vedirect_chargers:
			try:
				sc_utils.copy_dbus_value(self._dbusmonitor,
					service, '/Settings/ChargeCurrentLimit',
					service, '/Link/ChargeCurrent')
			except dbus.exceptions.DBusException:
				logging.debug(traceback.format_exc())

	def _distribute_max_charge_current(self, vebus_path, bms_max_charge_current, vedirect_chargers,
			adjust_vebus_max_charge_current):
		if bms_max_charge_current is None:
			return

		solar_charger_current = 0

		# Find out which solarcharger are likely to be able to produce more current when their max charge
		# current is increased.
		upscalable_chargers = []
		static_chargers = []
		all_chargers = []
		for service in vedirect_chargers:
			charge_current = self._dbusmonitor.get_value(service, '/Dc/0/Current')
			solar_charger_current = safeadd(solar_charger_current, charge_current)
			max_charge_current = self._dbusmonitor.get_value(service, '/Link/ChargeCurrent')
			state = self._dbusmonitor.get_value(service, '/State')
			if state != 0:  # Off
				all_chargers.append(service)
				# See if we can increase PV yield by increasing the maximum charge current of this solar
				# charger. It is assumed that a PV yield can be increased if the actual current is close to
				# the maximum.
				if max_charge_current is None or \
					charge_current >= Hub1Bridge.ChargeCurrentLimitedFactor * max_charge_current:
					upscalable_chargers.append(service)
				else:
					static_chargers.append(service)

		if len(upscalable_chargers) == 0:
			upscalable_chargers = static_chargers
			static_chargers = []

		# Handle vebus
		vebus_dc_current = 0
		if vebus_path is not None:
			# For freshly updated systems: the vebus will not yet support BOL, if there is a BMS
			# present bol_item.exists will return False because the vebus firmware has not been updated yet
			# (to version 415 or later).
			# In that case we cannot change the vebus charge current (it will be managed indirectly by
			# hub4control), and will try to distribute the remaining current over the MPPTs.
			# *** This is a change in behavior compared with the previous release ***.
			vebus_dc_current = self._dbusmonitor.get_value(vebus_path, '/Dc/0/Current') or 0
			if adjust_vebus_max_charge_current:
				vebus_max_charge_current = max(0, bms_max_charge_current - solar_charger_current)
				# If there are MPPTs that may be able to produce more power, we reduce the vebus max charge
				# current to give the MPPTs a change.
				if len(upscalable_chargers) > 0:
					vebus_max_charge_current = math.floor(vebus_max_charge_current * Hub1Bridge.VebusChargeFactor)
					vebus_dc_current = min(vebus_dc_current, vebus_max_charge_current)
				try:
					self._dbusmonitor.set_value(vebus_path, '/Dc/0/MaxChargeCurrent', vebus_max_charge_current)
				except dbus.exceptions.DBusException:
					logging.debug(traceback.format_exc())

		# Handle Ve.Direct solar chargers
		extra_solar_charger_max_current = bms_max_charge_current - vebus_dc_current - solar_charger_current
		if extra_solar_charger_max_current >= 0:
			extra_solar_charger_max_current = \
				self._distribute_currents(upscalable_chargers, extra_solar_charger_max_current)
			# Scale up the max charge current to prevent the MPPT to be categorized as non static the next
			# time.
			self._distribute_currents(static_chargers, extra_solar_charger_max_current,
				scale=Hub1Bridge.StaticScaleFactor)
		else:
			if solar_charger_current <= 0:
				# No solar charger power is produced, but in total we create too much power. All we can do
				# here is reduce solar charger power to zero. The reduced vebus charge current should take
				# case of the rest.
				solar_charger_factor = 0
			else:
				# If we get here, we know that solar_charger_current > 0 and
				# extra_solar_charger_max_current < 0, so solar_charger_factor will always be between 0 and 1.
				solar_charger_factor = max(0.0, 1 + float(extra_solar_charger_max_current) / solar_charger_current)
			for charger in all_chargers:
				try:
					charge_current = self._dbusmonitor.get_value(charger, '/Dc/0/Current')
					max_charge_current = solar_charger_factor * charge_current if charge_current > 0 else 0
					self._dbusmonitor.set_value(charger, '/Link/ChargeCurrent', max_charge_current)
				except dbus.exceptions.DBusException:
					logging.debug(traceback.format_exc())

	def _distribute_currents(self, chargers, increment, scale=1.0):
		if increment < 0:
			return increment
		if len(chargers) == 0:
			return increment
		actual_currents = []
		limits = []

		for charger in chargers:
			actual_currents.append(max(0, self._dbusmonitor.get_value(charger, '/Dc/0/Current')))
			limits.append(self._dbusmonitor.get_value(charger, '/Settings/ChargeCurrentLimit'))
		max_currents = Hub1Bridge.distribute(actual_currents, limits, increment)
		i = 0
		for charger in chargers:
			try:
				self._dbusmonitor.set_value(charger, '/Link/ChargeCurrent', scale * max_currents[i])
				increment += actual_currents[i] - max_currents[i]
				i += 1
			except dbus.exceptions.DBusException:
				logging.debug(traceback.format_exc())
		return increment

	@staticmethod
	def distribute(actual_values, max_values, increment):
		assert increment >= 0
		assert len(actual_values) == len(max_values)
		n = len(actual_values)
		cn = n
		new_values = [-1] * n
		for j in range(0, n):
			for i in range(0, n):
				mv = max_values[i]
				assert mv >= 0
				if new_values[i] == mv:
					continue
				nv = actual_values[i] + float(increment) / cn
				assert nv >= 0
				if nv >= mv:
					increment += actual_values[i] - mv
					cn -= 1
					new_values[i] = mv
					break
				new_values[i] = nv
			else:
				break
			continue
		return new_values

	def find_bms_service(self):
		for battery_service in self._battery_services:
			if self._dbusmonitor.get_value(battery_service, '/Info/MaxChargeVoltage') is not None:
				return battery_service
		return None


class ServiceMapper(SystemCalcDelegate):
	def device_added(self, service, instance, do_service_change=True):
		path = ServiceMapper._get_service_mapping_path(service, instance)
		if path in self._dbusservice:
			self._dbusservice[path] = service
		else:
			self._dbusservice.add_path(path, service)

	def device_removed(self, service, instance):
		path = ServiceMapper._get_service_mapping_path(service, instance)
		if path in self._dbusservice:
			del self._dbusservice[path]

	@staticmethod
	def _get_service_mapping_path(service, instance):
		sn = sc_utils.service_instance_name(service, instance).replace('.', '_').replace('/', '_')
		return '/ServiceMapping/%s' % sn


class VebusSocWriter(SystemCalcDelegate):
	_hub2_assistant_ids = {0x0134, 0x0135, 0x0137, 0x0138, 0x013A, 0x141, 0x0146, 0x014D}

	def __init__(self):
		SystemCalcDelegate.__init__(self)
		gobject.idle_add(exit_on_error, lambda: not self._write_vebus_soc())
		gobject.timeout_add(10000, exit_on_error, self._write_vebus_soc)

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
			total_charge_current = newvalues.get('/Dc/Pv/Current', 0)
			try:
				charge_current = self._dbusmonitor.get_value(vebus_service, '/ExtraBatteryCurrent')
				if charge_current is not None:
					self._dbusmonitor.set_value(vebus_service, '/ExtraBatteryCurrent', total_charge_current)
					current_written = 1
			except dbus.exceptions.DBusException:
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
						self._dbusmonitor.set_value(vebus_service, '/Soc', soc)
						soc_written = 1
					except dbus.exceptions.DBusException:
						pass
		self._dbusservice['/Control/VebusSoc'] = soc_written
		return True

	def _must_write_soc(self, vebus_service):
		active_battery_service = self._dbusservice['/ActiveBatteryService']
		if active_battery_service is None or active_battery_service.startswith('com.victronenergy.vebus'):
			return False
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


class RelayState(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._relays = {}

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		relays = sc_utils.gpio_paths('/etc/venus/relays')
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


class BuzzerControl(SystemCalcDelegate):
	CLOCK_TICK_RATE = 1193180
	KIOCSOUND = 0x4B2F
	TTY_PATH = '/dev/tty0'
	GPIO_BUZZER_PATH = '/etc/venus/buzzer'
	PWM_BUZZER_PATH = '/etc/venus/pwm_buzzer'

	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._buzzer_on = False
		self._timer = None
		self._gpio_path = None
		self._pwm_frequency = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		# Find GPIO buzzer
		gpio_paths = sc_utils.gpio_paths(BuzzerControl.GPIO_BUZZER_PATH)
		if len(gpio_paths) > 0:
			self._gpio_path = os.path.join(gpio_paths[0], 'value')
			logging.info('GPIO buzzer found: {}'.format(self._gpio_path))
		# Find PWM buzzer
		self._pwm_frequency = None
		try:
			pwm_frequency = sc_utils.gpio_paths(BuzzerControl.PWM_BUZZER_PATH)
			if len(pwm_frequency) > 0:
				self._pwm_frequency = int(pwm_frequency[0])
				logging.info('PWM buzzer found @ frequency: {}'.format(self._pwm_frequency))
		except ValueError:
			logging.error('Parsing of PWM buzzer settings at %s failed', BuzzerControl.PWM_BUZZER_PATH)
		if self._gpio_path is None and self._pwm_frequency is None:
			logging.info('No buzzer found')
			return
		self._dbusservice.add_path('/Buzzer/State', value=0, writeable=True,
			onchangecallback=lambda p, v: exit_on_error(self._on_buzzer_state_changed, v))
		# Reset the buzzer so the buzzer state equals the D-Bus value. It will also silence the buzzer after
		# a restart of the service/system.
		self._set_buzzer(False)

	def _on_buzzer_state_changed(self, value):
		try:
			value = 1 if int(value) == 1 else 0
			if value == 1:
				if self._timer is None:
					self._timer = gobject.timeout_add(500, exit_on_error, self._on_timer)
					self._set_buzzer(True)
			elif self._timer is not None:
				gobject.source_remove(self._timer)
				self._timer = None
				self._set_buzzer(False)
			self._dbusservice['/Buzzer/State'] = value
		except (TypeError, ValueError):
			logging.error('Incorrect value received on /Buzzer/State: %s', value)
		return False

	def _on_timer(self):
		self._set_buzzer(not self._buzzer_on)
		return True

	def _set_buzzer(self, on):
		self._set_gpio_buzzer(on)
		self._set_pwm_buzzer(on)
		self._buzzer_on = on

	def _set_gpio_buzzer(self, on):
		if self._gpio_path is None:
			return
		try:
			with open(self._gpio_path, 'wt') as w:
				w.write('1' if on else '0')
		except (IOError, OSError):
			traceback.print_exc()

	def _set_pwm_buzzer(self, on):
		if self._pwm_frequency is None:
			return
		console_fd = None
		interval = BuzzerControl.CLOCK_TICK_RATE // self._pwm_frequency if on else 0
		try:
			# The return value of os.open does not have an __exit__ function, so we cannot use 'with' here.
			console_fd = os.open(BuzzerControl.TTY_PATH, os.O_RDONLY | os.O_NOCTTY)
			fcntl.ioctl(console_fd, BuzzerControl.KIOCSOUND, interval)
		except (IOError, OSError):
			traceback.print_exc()
		finally:
			try:
				if console_fd is not None:
					os.close(console_fd)
			except:
				traceback.print_exc()


class LgCircuitBreakerDetect(SystemCalcDelegate):
	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._lg_voltage_buffer = None
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
			logging.error('LG shutdown detected V=%s I=%s %s' %
				(battery_voltage, battery_current, self._lg_voltage_buffer))
			self._dbusservice['/Dc/Battery/Alarms/CircuitBreakerTripped'] = 2
			self._lg_voltage_buffer = []
			try:
				self._dbusmonitor.set_value(vebus_path, '/Mode', 4)
			except dbus.exceptions.DBusException:
				logging.error('Cannot switch off vebus device')
