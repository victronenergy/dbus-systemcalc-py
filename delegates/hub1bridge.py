from dbus.exceptions import DBusException
import gobject
import logging
import math
import traceback

# Victron packages
from sc_utils import safeadd, copy_dbus_value
from ve_utils import exit_on_error

from delegates.base import SystemCalcDelegate

class SolarCharger(object):
	def __init__(self, monitor, service):
		self.monitor = monitor
		self.service = service

	def _get_path(self, path):
		return self.monitor.get_value(self.service, path)

	def _set_path(self, path, v):
		self.monitor.set_value(self.service, path, v)

	@property
	def connection(self):
		return self._get_path('/Mgmt/Connection')

	@property
	def networkmode(self):
		return self._get_path('/Link/NetworkMode')

	@networkmode.setter
	def networkmode(self, v):
		self._set_path('/Link/NetworkMode', v)

	@property
	def chargecurrent(self):
		return self._get_path('/Dc/0/Current')

	@property
	def maxchargecurrent(self):
		return self._get_path('/Link/ChargeCurrent')

	@maxchargecurrent.setter
	def maxchargecurrent(self, v):
		self._set_path('/Link/ChargeCurrent', v)

	@property
	def chargevoltage(self):
		return self._get_path('/Link/ChargeVoltage')

	@chargevoltage.setter
	def chargevoltage(self, v):
		self._set_path('/Link/ChargeVoltage', v)

	@property
	def currentlimit(self):
		return self._get_path('/Settings/ChargeCurrentLimit')

	@property
	def state(self):
		return self._get_path('/State')

	def maximize_charge_current(self):
		copy_dbus_value(self.monitor,
			self.service, '/Settings/ChargeCurrentLimit',
			self.service, '/Link/ChargeCurrent')

class Hub1Bridge(SystemCalcDelegate):
	# if ChargeCurrent > ChangeCurrentLimitedFactor * MaxChargeCurrent we assume that the solar charger is
	# current limited, and yield do more power if we increase the MaxChargeCurrent
	ChargeCurrentLimitedFactor = 0.9
	VebusChargeFactor = 0.8
	StaticScaleFactor = 1.1 / ChargeCurrentLimitedFactor

	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._solarchargers = {}
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
				'/BatteryOperationalLimits/MaxDischargeCurrent',
				'/FirmwareFeatures/BolFrame']),
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
		self._dbusservice.add_path('/Debug/SolarVoltageOffset', value=0, writeable=True)

	def device_added(self, service, instance, do_service_change=True):
		service_type = service.split('.')[2]
		if service_type == 'solarcharger':
			self._solarchargers[service] = SolarCharger(self._dbusmonitor, service)
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
			del self._solarchargers[service]
		elif service in self._vecan_services:
			self._vecan_services.remove(service)
		elif service in self._battery_services:
			self._battery_services.remove(service)
		if len(self._solarchargers) == 0 and len(self._vecan_services) == 0 and \
			len(self._battery_services) == 0 and self._timer is not None:
			gobject.source_remove(self._timer)
			self._timer = None

	def _on_timer(self):
		bms_service = self.find_bms_service()
		bms_parameters_written = self._update_battery_operational_limits(bms_service)
		voltage_written, current_written = self._update_solarchargers(bms_service)
		self._dbusservice['/Control/SolarChargeVoltage'] = voltage_written
		self._dbusservice['/Control/SolarChargeCurrent'] = current_written
		self._dbusservice['/Control/BmsParameters'] = bms_parameters_written
		# @todo EV What if ESS + OvervoltageFeedIn? In that case there is no charge current control on the
		# MPPTs.
		vebus_path = self._dbusservice['/VebusService']
		self._dbusservice['/Control/MaxChargeCurrent'] = \
			vebus_path is None or \
			self._dbusmonitor.get_value(vebus_path, '/FirmwareFeatures/BolFrame') == 1
		return True

	def _update_battery_operational_limits(self, bms_service):
		if bms_service is None:
			return 0
		vebus_path = self._dbusservice['/VebusService']
		if vebus_path is None:
			return 0
		try:
			copy_dbus_value(self._dbusmonitor,
				bms_service, '/Info/MaxChargeVoltage',
				vebus_path, '/BatteryOperationalLimits/MaxChargeVoltage')
			copy_dbus_value(self._dbusmonitor,
				bms_service, '/Info/MaxChargeCurrent',
				vebus_path, '/BatteryOperationalLimits/MaxChargeCurrent')
			copy_dbus_value(self._dbusmonitor,
				bms_service, '/Info/BatteryLowVoltage',
				vebus_path, '/BatteryOperationalLimits/BatteryLowVoltage')
			copy_dbus_value(self._dbusmonitor,
				bms_service, '/Info/MaxDischargeCurrent',
				vebus_path, '/BatteryOperationalLimits/MaxDischargeCurrent')
			return 1
		except DBusException:
			logging.debug(traceback.format_exc())
			return 0

	def _update_solarchargers(self, bms_service):
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

		has_ess_assistant = None
		if vebus_path is not None:
			# We do not analyse the content of /Devices/0/Assistants, because that would require us to keep
			# a list of ESS assistant version numbers (see VebusSocWriter._hub2_assistant_ids). Because that
			# list is expected to change (unlike the list of hub-2 assistants), we use /Hub4/AssistantId to
			# check the presence. It is guaranteed that /Hub4/AssistantId will be published before
			# /Devices/0/Assistants.
			assistants = self._dbusmonitor.get_value(vebus_path, '/Devices/0/Assistants')
			if assistants is not None:
				has_ess_assistant = self._dbusmonitor.get_value(vebus_path, '/Hub4/AssistantId') == 5
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
		if charge_voltage is not None:
			try:
				charge_voltage += float(self._dbusservice['/Debug/SolarVoltageOffset'])
			except ValueError:
				pass
		if charge_voltage is None and max_charge_current is None:
			# @todo EV Reset vebus_max_charge_current here? To what value? We get here if the BMS battery
			# service disappears or the max charge current setting is reset.
			return 0, 0

		# Network mode:
		# bit 0: Operated in network environment
		# bit 2: Remote Hub-1 control (MPPT will accept charge voltage and max charge current)
		# bit 3: Remote BMS control (MPPT enter BMS mode)
		network_mode = 1 | (0 if charge_voltage is None and max_charge_current is None else 4) | (0 if bms_service is None else 8)
		has_vecan_chargers = False
		vedirect_chargers = []
		network_mode_written = False
		for charger in self._solarchargers.values():
			try:
				if charger.connection == 'VE.Can':
					has_vecan_chargers = True
				# We use /Link/NetworkMode to detect Hub support in the solarcharger. Existence of this item
				# implies existence of the other /Link/* fields.
				if charger.networkmode is not None:
					vedirect_chargers.append(charger)
					charger.networkmode = network_mode
					network_mode_written = True
			except DBusException:
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
			self._distribute_max_charge_current(vebus_path, max_charge_current, vedirect_chargers)

		current_written = 1 if network_mode_written and max_charge_current is not None else 0
		return voltage_written, current_written

	def _distribute_voltage_setpoint(self, vebus_path, charge_voltage, vedirect_chargers, has_vecan_chargers):
		if charge_voltage is None:
			return 0

		voltage_written = 0
		for charger in vedirect_chargers:
			try:
				charger.chargevoltage = charge_voltage
				voltage_written = 1
			except DBusException:
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
			except DBusException:
				pass

		return voltage_written

	def _maximize_charge_current(self, vebus_path, bms_max_charge_current, vedirect_chargers):
		if bms_max_charge_current is None:
			return

		try:
			self._dbusmonitor.set_value(vebus_path, '/Dc/0/MaxChargeCurrent', bms_max_charge_current)
		except DBusException:
			logging.debug(traceback.format_exc())

		for charger in vedirect_chargers:
			try:
				charger.maximize_charge_current()
			except DBusException:
				logging.debug(traceback.format_exc())

	def _distribute_max_charge_current(self, vebus_path, bms_max_charge_current, vedirect_chargers):
		if bms_max_charge_current is None:
			return

		solar_charger_current = 0

		# Find out which solarcharger are likely to be able to produce more current when their max charge
		# current is increased.
		upscalable_chargers = []
		static_chargers = []
		all_chargers = []
		for charger in vedirect_chargers:
			charge_current = charger.chargecurrent
			solar_charger_current = safeadd(solar_charger_current, charge_current)
			max_charge_current = charger.maxchargecurrent
			state = charger.state
			if state != 0:  # Off
				all_chargers.append(charger)
				# See if we can increase PV yield by increasing the maximum charge current of this solar
				# charger. It is assumed that a PV yield can be increased if the actual current is close to
				# the maximum.
				if max_charge_current is None or \
					charge_current >= Hub1Bridge.ChargeCurrentLimitedFactor * max_charge_current:
					upscalable_chargers.append(charger)
				else:
					static_chargers.append(charger)

		if len(upscalable_chargers) == 0:
			upscalable_chargers = static_chargers
			static_chargers = []

		# Handle vebus
		vebus_dc_current = 0
		if vebus_path is not None:
			# In that case we cannot change the vebus charge current (it will be managed indirectly by
			# hub4control), and will try to distribute the remaining current over the MPPTs.
			# *** This is a change in behavior compared with the previous release ***.
			vebus_dc_current = self._dbusmonitor.get_value(vebus_path, '/Dc/0/Current') or 0
			# BOL support on vebus implies dynamic max charge current support. Both features were added in the
			# same version (v415). We cannot check the presence of /Dc/0/MaxChargeCurrent, because it already
			# existed in earlier versions, where it was not dynamic (ie. should not be changed too often).
			if self._dbusmonitor.get_value(vebus_path, '/FirmwareFeatures/BolFrame') == 1:
				vebus_max_charge_current = max(0, bms_max_charge_current - solar_charger_current)
				# If there are MPPTs that may be able to produce more power, we reduce the vebus max charge
				# current to give the MPPTs a change.
				if len(upscalable_chargers) > 0:
					vebus_max_charge_current = math.floor(vebus_max_charge_current * Hub1Bridge.VebusChargeFactor)
					vebus_dc_current = min(vebus_dc_current, vebus_max_charge_current)
				try:
					self._dbusmonitor.set_value(vebus_path, '/Dc/0/MaxChargeCurrent', vebus_max_charge_current)
				except DBusException:
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
					charger.maxchargecurrent = solar_charger_factor * charger.chargecurrent if charger.chargecurrent > 0 else 0
				except DBusException:
					logging.debug(traceback.format_exc())

	def _distribute_currents(self, chargers, increment, scale=1.0):
		if increment < 0:
			return increment
		if len(chargers) == 0:
			return increment
		actual_currents = []
		limits = []

		for charger in chargers:
			actual_currents.append(max(0, charger.chargecurrent))
			limits.append(charger.currentlimit)
		max_currents = Hub1Bridge.distribute(actual_currents, limits, increment)
		i = 0
		for charger in chargers:
			try:
				charger.maxchargecurrent = scale * max_currents[i]
				increment += actual_currents[i] - max_currents[i]
				i += 1
			except DBusException:
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
