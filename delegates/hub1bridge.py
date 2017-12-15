from dbus.exceptions import DBusException
import gobject
import logging
from math import pi, floor, ceil
import traceback
from itertools import izip, count

# Victron packages
from sc_utils import safeadd, copy_dbus_value
from ve_utils import exit_on_error

from delegates.base import SystemCalcDelegate

# Adjust things this often (in seconds)
# solar chargers has to switch to HEX mode each time we write a value to its
# D-Bus service. Writing too often may block text messages. In MPPT firmware
# v1.23 and later, all relevant values will be transmitted as asynchronous
# message, so the update rate could be increased. For now, keep this at 3 and
# above.
ADJUST = 3

def _distribute_currents(chargers, increment, scale=1.0):
	if increment < 0:
		return increment
	if len(chargers) == 0:
		return increment
	actual_currents = []
	limits = []

	for charger in chargers:
		actual_currents.append(max(0, charger.chargecurrent))
		limits.append(charger.currentlimit)
	max_currents = distribute(actual_currents, limits, increment)
	i = 0
	for charger in chargers:
		try:
			charger.maxchargecurrent = scale * max_currents[i]
			increment += actual_currents[i] - max_currents[i]
			i += 1
		except DBusException:
			logging.debug(traceback.format_exc())
	return increment

def distribute(current_values, max_values, increment):
	n = cn = len(current_values)
	new_values = [-1] * n
	for j in range(0, n):
		for i, mv, av in izip(count(), max_values, current_values):
			assert mv >= 0
			if new_values[i] == mv or new_values[i] == 0:
				continue
			nv = av + float(increment) / cn

			if nv >= mv:
				increment += av - mv
				cn -= 1
				new_values[i] = mv
				break
			elif nv < 0:
				increment += av
				cn -= 1
				new_values[i] = 0
				break

			new_values[i] = nv
		else:
			break
		continue
	return new_values

class SolarCharger(object):
	# Used for low-pass filter
	OMEGA = (2 * pi)/20

	def __init__(self, monitor, service):
		self.monitor = monitor
		self.service = service
		self._smoothed_current = self.chargecurrent or 0

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
		v = self._get_path('/Link/ChargeCurrent')
		return v if v is not None else self.currentlimit

	@maxchargecurrent.setter
	def maxchargecurrent(self, v):
		v = max(0, min(v, self.currentlimit))
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

	@property
	def smoothed_current(self):
		return self._smoothed_current

	def maximize_charge_current(self):
		copy_dbus_value(self.monitor,
			self.service, '/Settings/ChargeCurrentLimit',
			self.service, '/Link/ChargeCurrent')

	def update_values(self):
		v = self.monitor.get_value(self.service, '/Dc/0/Current')
		if v is not None:
			self._smoothed_current += (v - self._smoothed_current) * self.OMEGA

class SolarChargerSubsystem(object):
	def __init__(self, monitor):
		self.monitor = monitor
		self._solarchargers = {}

	def add_charger(self, service):
		self._solarchargers[service] = charger = SolarCharger(self.monitor, service)
		return charger

	def remove_charger(self, service):
		del self._solarchargers[service]

	def __iter__(self):
		return self._solarchargers.itervalues()

	def __len__(self):
		return len(self._solarchargers)

	def __contains__(self, k):
		return k in self._solarchargers

	@property
	def has_vecan_chargers(self):
		return any((s.connection == 'VE.Can' for s in self._solarchargers.values()))

	@property
	def capacity(self):
		""" Total capacity if all chargers are running at full power. """
		return safeadd(*(c.currentlimit for c in self._solarchargers.values()))

	@property
	def chargecurrent(self):
		""" Total current generated this instant. """
		return safeadd(*(c.chargecurrent for c in self._solarchargers.values()))

	@property
	def maxchargecurrent(self):
		""" Total current we're limited to now. """
		return safeadd(*(c.maxchargecurrent for c in self._solarchargers.values()))

	@property
	def smoothed_current(self):
		""" Total smoothed current. """
		return safeadd(*(c.smoothed_current for c in self._solarchargers.values())) or 0

	def maximize_charge_current(self):
		for charger in self._solarchargers.values():
			if charger.connection == 'VE.Can': continue
			try:
				charger.maximize_charge_current()
			except DBusException:
				logging.debug(traceback.format_exc())

	def set_networked(self, has_bms, charge_voltage, max_charge_current, feedback_allowed):
		# Network mode:
		# bit 0: Operated in network environment
		# bit 2: Remote Hub-1 control (MPPT will accept charge voltage and max charge current)
		# bit 3: Remote BMS control (MPPT enter BMS mode)
		network_mode = 1 | (0 if charge_voltage is None and max_charge_current is None else 4) | (8 if has_bms else 0)
		vedirect_chargers = []
		network_mode_written = False
		for charger in self._solarchargers.values():
			try:
				# We use /Link/NetworkMode to detect Hub support in the
				# solarcharger. Existence of this item implies existence of the
				# other /Link/* fields.
				if charger.networkmode is not None:
					vedirect_chargers.append(charger)
					charger.networkmode = network_mode
					network_mode_written = True
			except DBusException:
				pass

		# Distribute the voltage setpoint
		voltage_written = 0
		if charge_voltage is not None:
			voltage_written = int(len(vedirect_chargers)>0)
			for charger in vedirect_chargers:
				try:
					charger.chargevoltage = charge_voltage
				except DBusException:
					pass

		# Do not limit max charge current when feedback is allowed. The
		# rationale behind this is that MPPT charge power should match the
		# capabilities of the battery. If the default charge algorithm is used
		# by the MPPTs, the charge current should stay within limits. This
		# avoids a problem that we do not know if extra MPPT power will be fed
		# back to the grid when we decide to increase the MPPT max charge
		# current.
		chargers = filter(lambda x: x.state !=0 and x.connection != 'VE.Can', self._solarchargers.values())
		if len(chargers) > 0:
			if feedback_allowed:
				self.maximize_charge_current()
			elif max_charge_current is not None:
				if len(chargers) == 1:
					# The simple case, simply assign the limit to the charger
					sc = chargers[0]
					cc = min(ceil(max_charge_current), sc.currentlimit)
					sc.maxchargecurrent = cc
				elif max_charge_current > self.capacity * 0.95:
					# Another simple case, we're asking for more than our
					# combined capacity (with a 5% margin)
					self.maximize_charge_current()
				else:
					# The hard case, we have more than one CC and we want
					# less than our capacity
					self._distribute_current(chargers, max_charge_current)

		# Return flags of what we did
		return voltage_written, int(network_mode_written and max_charge_current is not None)

	def _distribute_current(self, chargers, max_charge_current):
		""" This is called if there are two or more solar chargers. It
		    distributes the charge current over all of them. """

		# Take the difference between the values and spread it over all
		# the chargers. The maxchargecurrents of the chargers should ideally
		# always add up to the whole.
		actual = [c.smoothed_current for c in chargers]
		limits = [c.maxchargecurrent for c in chargers]
		ceilings = [c.currentlimit for c in chargers]
		max_charge_current = min(sum(ceilings), max_charge_current)
		delta = max_charge_current - sum(limits)
		if abs(delta) > 0.1 * len(chargers):
			# No point in disturbing chargers if the increment is too low.
			limits = distribute(limits, ceilings, delta)
		else:
			# Balance the limits so they have the same headroom at the top.
			# Round the figure a little for discrete distribution and
			# stability
			margins = [max(0, l - a) for a, l in izip(actual, limits)]
			avgmargin = sum(margins)/len(margins)
			deltas = [round(avgmargin - x, 1) for x in margins]
			for i, a, d in izip(count(), actual, deltas):
				limits[i] += d

		# Set the limits
		for charger, limit in izip(chargers, limits):
			charger.maxchargecurrent = limit

	def update_values(self):
		for charger in self._solarchargers.values():
			charger.update_values()

class Battery(object):
	def __init__(self, monitor, service):
		self.monitor = monitor
		self.service = service

	@property
	def is_bms(self):
		""" Indicates if this battery has a BMS that can communicate the
		    preferred charge parameters. """
		return self.monitor.get_value(self.service, '/Info/MaxChargeVoltage') is not None

	@property
	def maxchargecurrent(self):
		""" Returns maxumum charge current published by the BMS. """
		return self.monitor.get_value(self.service, '/Info/MaxChargeCurrent')

class BatterySubsystem(object):
	def __init__(self, monitor):
		self.monitor = monitor
		self._battery_services = {}

	def __iter__(self):
		return self._battery_services.itervalues()

	def __len__(self):
		return len(self._battery_services)

	def __contains__(self, k):
		return k in self._battery_services

	def add_battery(self, service):
		self._battery_services[service] = battery = Battery(self.monitor, service)
		return battery

	def remove_battery(self, service):
		del self._battery_services[service]

	@property
	def bms(self):
		""" Returns the first battery service with a BMS. """
		for b in self._battery_services.values():
			if b.is_bms: return b
		return None

class Multi(object):
	# Used for low-pass filter
	OMEGA = (2 * pi)/30

	def __init__(self, monitor, service):
		self.monitor = monitor
		self._service = service
		self._dc_current = monitor.get_value(service, '/Dc/0/Current', 0)

	@property
	def service(self):
		return self._service['/VebusService']

	@property
	def active(self):
		return self.service is not None

	@property
	def ac_connected(self):
		return self.monitor.get_value(self.service, '/Ac/ActiveIn/Connected') == 1

	@property
	def has_bolframe(self):
		return self.monitor.get_value(self.service, '/FirmwareFeatures/BolFrame') == 1

	@property
	def has_ess_assistant(self):
		# We do not analyse the content of /Devices/0/Assistants, because that
		# would require us to keep a list of ESS assistant version numbers (see
		# VebusSocWriter._hub2_assistant_ids). Because that list is expected to
		# change (unlike the list of hub-2 assistants), we use
		# /Hub4/AssistantId to check the presence. It is guaranteed that
		# /Hub4/AssistantId will be published before /Devices/0/Assistants.
		assistants = self.monitor.get_value(self.service, '/Devices/0/Assistants')
		return assistants is not None and \
			self.monitor.get_value(self.service, '/Hub4/AssistantId') == 5

	@property
	def dc_current(self):
		""" Return a low-pass smoothed current. """
		return self._dc_current

	@property
	def hub_voltage(self):
		return self.monitor.get_value(self.service, '/Hub/ChargeVoltage')

	@property
	def maxchargecurrent(self):
		return self.monitor.get_value(self.service, '/Dc/0/MaxChargeCurrent')

	@maxchargecurrent.setter
	def maxchargecurrent(self, v):
		self.monitor.set_value(self.service, '/Dc/0/MaxChargeCurrent', v)

	def update_values(self):
		c = self.monitor.get_value(self.service, '/Dc/0/Current', 0)
		self._dc_current += (c - self._dc_current) * self.OMEGA

class Hub1Bridge(SystemCalcDelegate):
	# if ChargeCurrent > ChangeCurrentLimitedFactor * MaxChargeCurrent we assume that the solar charger is
	# current limited, and yield do more power if we increase the MaxChargeCurrent
	ChargeCurrentLimitedFactor = 0.9
	VebusChargeFactor = 0.8
	StaticScaleFactor = 1.1 / ChargeCurrentLimitedFactor

	def __init__(self):
		SystemCalcDelegate.__init__(self)
		self._solarsystem = None
		self._vecan_services = []
		self._timer = None
		self._tickcount = ADJUST

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
		self._batterysystem = BatterySubsystem(dbusmonitor)
		self._solarsystem = SolarChargerSubsystem(dbusmonitor)
		self._multi = Multi(dbusmonitor, dbusservice)

		self._dbusservice.add_path('/Control/SolarChargeVoltage', value=0)
		self._dbusservice.add_path('/Control/SolarChargeCurrent', value=0)
		self._dbusservice.add_path('/Control/BmsParameters', value=0)
		self._dbusservice.add_path('/Control/MaxChargeCurrent', value=0)
		self._dbusservice.add_path('/Debug/SolarVoltageOffset', value=0, writeable=True)

	def device_added(self, service, instance, do_service_change=True):
		service_type = service.split('.')[2]
		if service_type == 'solarcharger':
			self._solarsystem.add_charger(service)
		elif service_type == 'vecan':
			self._vecan_services.append(service)
		elif service_type == 'battery':
			self._batterysystem.add_battery(service)
		else:
			# Skip timer code below
			return
		if self._timer is None:
			self._timer = gobject.timeout_add(1000, exit_on_error, self._on_timer)

	def device_removed(self, service, instance):
		if service in self._solarsystem:
			self._solarsystem.remove_charger(service)
		elif service in self._vecan_services:
			self._vecan_services.remove(service)
		elif service in self._batterysystem:
			self._batterysystem.remove_battery(service)
		if len(self._solarsystem) == 0 and len(self._vecan_services) == 0 and \
			len(self._batterysystem) == 0 and self._timer is not None:
			gobject.source_remove(self._timer)
			self._timer = None

	@property
	def maxchargecurrent(self):
		""" Returns maxumum charge current published by the BMS. """
		bms_service = self._batterysystem.bms
		bms_max_charge_current = None if bms_service is None else bms_service.maxchargecurrent
		max_charge_current = self._settings['maxchargecurrent']

		if max_charge_current < 0:
			return bms_max_charge_current
		elif bms_max_charge_current is not None:
			return min(max_charge_current, bms_max_charge_current)

		return None if max_charge_current < 0 else max_charge_current

	def _on_timer(self):
		self._tickcount -= 1; self._tickcount %= ADJUST

		# Update subsystems
		self._solarsystem.update_values()
		self._multi.update_values()

		# Below are things we only do every ADJUST seconds
		if self._tickcount > 0: return True

		bms_service = self._batterysystem.bms
		bms_parameters_written = self._update_battery_operational_limits(bms_service)
		self._dbusservice['/Control/BmsParameters'] = bms_parameters_written

		# @todo EV What if ESS + OvervoltageFeedIn? In that case there is no charge current control on the
		# MPPTs.
		self._dbusservice['/Control/MaxChargeCurrent'] = \
			not self._multi.active or self._multi.has_bolframe

		max_charge_current = _max_charge_current = self.maxchargecurrent

		# If we have vebus current, we have to compensate for it
		vebus_dc_current = self._multi.dc_current
		if _max_charge_current is not None and vebus_dc_current is not None and \
				vebus_dc_current < 0:
			_max_charge_current = ceil(_max_charge_current - vebus_dc_current)

		# Try to push the solar chargers to this value
		voltage_written, current_written = self._update_solarchargers(
			bms_service, _max_charge_current)

		# Using the original uncompensated max_charge_current, set the multi
		# to make up the difference between the mppts and this value.
		if max_charge_current is not None:
			remainder = floor(max_charge_current - self._solarsystem.smoothed_current)
			try:
				self._multi.maxchargecurrent = remainder if remainder > 1 else 0
			except DBusException:
				logging.debug(traceback.format_exc())

		self._dbusservice['/Control/SolarChargeVoltage'] = voltage_written
		self._dbusservice['/Control/SolarChargeCurrent'] = current_written
		return True

	def _update_battery_operational_limits(self, bms_service):
		if bms_service is None:
			return 0
		if not self._multi.active:
			return 0

		try:
			copy_dbus_value(self._dbusmonitor,
				bms_service.service, '/Info/MaxChargeVoltage',
				self._multi.service, '/BatteryOperationalLimits/MaxChargeVoltage')
			copy_dbus_value(self._dbusmonitor,
				bms_service.service, '/Info/MaxChargeCurrent',
				self._multi.service, '/BatteryOperationalLimits/MaxChargeCurrent')
			copy_dbus_value(self._dbusmonitor,
				bms_service.service, '/Info/BatteryLowVoltage',
				self._multi.service, '/BatteryOperationalLimits/BatteryLowVoltage')
			copy_dbus_value(self._dbusmonitor,
				bms_service.service, '/Info/MaxDischargeCurrent',
				self._multi.service, '/BatteryOperationalLimits/MaxDischargeCurrent')
			return 1
		except DBusException:
			logging.debug(traceback.format_exc())
			return 0

	def _update_solarchargers(self, bms_service, max_charge_current):
		has_ess_assistant = self._multi.active and self._multi.has_ess_assistant

		# Feedback allowed is defined as 'ESS present and FeedInOvervoltage is
		# enabled'. This ignores other setups which allow feedback: hub-1.
		feedback_allowed = has_ess_assistant and self._multi.ac_connected and \
			self._dbusmonitor.get_value('com.victronenergy.settings',
				'/Settings/CGwacs/OvervoltageFeedIn') == 1

		# If the vebus service does not provide a charge voltage setpoint (so
		# no ESS/Hub-1/Hub-4), we use the max charge voltage provided by the
		# BMS (if any). This will probably prevent feedback, but that is
		# probably not allowed anyway.
		charge_voltage = None
		if self._multi.active:
			charge_voltage = self._multi.hub_voltage
		if charge_voltage is None and bms_service is not None:
			charge_voltage = self._dbusmonitor.get_value(bms_service.service, '/Info/MaxChargeVoltage')
		if charge_voltage is not None:
			try:
				charge_voltage += float(self._dbusservice['/Debug/SolarVoltageOffset'])
			except ValueError:
				pass

		if charge_voltage is None and max_charge_current is None:
			# @todo EV Reset vebus_max_charge_current here? To what value? We get here if the BMS battery
			# service disappears or the max charge current setting is reset.
			# TODO IB Writing a large value to the Multi will reset it to max.
			return 0, 0

		voltage_written, current_written = self._solarsystem.set_networked(bms_service is not None,
				charge_voltage, max_charge_current, feedback_allowed)

		# Charge voltage cannot by written directly to the CAN-bus solar chargers, we have to use
		# the com.victronenergy.vecan.* service instead.
		# Writing charge current to CAN-bus solar charger is not supported yet.
		if self._solarsystem.has_vecan_chargers:
			for service in self._vecan_services:
				try:
					# Note: we don't check the value of charge_voltage_item
					# because it may be invalid, for example if the D-Bus path
					# has not been written for more than 60 (?) seconds.  In
					# case there is no path at all, the set_value below will
					# raise an DBusException which we will ignore cheerfully.
					self._dbusmonitor.set_value(service, '/Link/ChargeVoltage', charge_voltage)
					voltage_written = 1
				except DBusException:
					pass

		return voltage_written, current_written
