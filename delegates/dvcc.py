from dbus.exceptions import DBusException
import gobject
import logging
from math import pi, floor, ceil
import traceback
from itertools import izip, count
from functools import partial, wraps
from collections import namedtuple

# Victron packages
from sc_utils import safeadd, copy_dbus_value, reify
from ve_utils import exit_on_error

from delegates.base import SystemCalcDelegate

# Adjust things this often (in seconds)
# solar chargers has to switch to HEX mode each time we write a value to its
# D-Bus service. Writing too often may block text messages. In MPPT firmware
# v1.23 and later, all relevant values will be transmitted as asynchronous
# message, so the update rate could be increased. For now, keep this at 3 and
# above.
ADJUST = 3

VEBUS_FIRMWARE_REQUIRED = 0x422
VEDIRECT_FIRMWARE_REQUIRED = 0x129
VECAN_FIRMWARE_REQUIRED = 0x10200 # 1.02, 24-bit version

# This is a place to account for some BMS quirks where we may have to ignore
# the BMS value and substitute our own.

def _byd_quirk(dvcc, bms, charge_voltage, charge_current, feedback_allowed):
	""" Quirk for the BYD batteries. When the battery sends CCL=0, float it at
	   55V. """
	if charge_current == 0:
		return (55, 40, feedback_allowed)
	return (charge_voltage, charge_current, feedback_allowed)

def _lg_quirk(dvcc, bms, charge_voltage, charge_current, feedback_allowed):
	""" Quirk for LG batteries. The hard limit is 58V. Above that you risk
	    tripping on high voltage. The batteries publish a charge voltage of 57.7V
	    but we need to make room for an 0.4V overvoltage when feed-in is enabled.
	"""
	# Make room for a potential 0.4V at the top
	return (min(charge_voltage, 57.3), charge_current, feedback_allowed)

def _pylontech_quirk(dvcc, bms, charge_voltage, charge_current, feedback_allowed):
	""" Quirk for Pylontech. Make a bit of room at the top. Pylontech says that
	    at 51.8V the battery is 95% full, and that balancing starts at 90%.
	    53.2V is normally considered 100% full, and 54V raises an alarm. By
		running the battery at 52.4V it will be 99%-100% full, balancing should
		be active, and we should avoid high voltage alarms.

	    Identify 24-V batteries by the lower charge voltage, and do the same
	    thing with an 8-to-15 cell ratio, +-3.48V per cell.
	"""
	# Use 3.48V per cell plus a little, 52.4V for 48V batteries.
	# Use 3.46V per cell plus a little, 27.8V for 24V batteries testing shows that's 100% SOC.
	# That leaves 1.6V margin for 48V batteries and 1.0V for 24V.
	# See https://github.com/victronenergy/venus/issues/536
	if charge_voltage > 30:
		# 48V battery (15 cells)
		return (min(charge_voltage, 52.4), charge_current, feedback_allowed)
	else:
		# 24V battery (8 cells). 24V batteries send CCL=0 when they are full,
		# whereas the 48V batteries reduce CCL by 50% when the battery is full.
		# Do the same for 24V batteries. The normal limit is C/2, so put the
		# limit to C/4. Note that this is just a nicety, the important part is
		# to clip the charge voltage to 27.8 volts. That fixes the sawtooth
		# issue.
		capacity = bms.capacity or 55
		return (min(charge_voltage, 27.8), max(charge_current, round(capacity/4.0)), feedback_allowed)

# Quirk = namedtuple('Quirk', ['product_id', 'floatvoltage', 'floatcurrent'])
QUIRKS = {
	0xB004: _lg_quirk,
	0xB009: _pylontech_quirk,
	0xB00A: _byd_quirk,
	0xB015: _byd_quirk,
	0xB019: _byd_quirk,
}

def distribute(current_values, max_values, increment):
	""" current_values and max_values are lists of equal size containing the
	    current limits, and the maximum they can be increased to. increment
	    contains the amount by which we want to increase the total, ie the sum
	    of the values in current_values, while staying below max_values.

	    This is done simply by first attempting to spread the increment
	    equally. If a value exceeds the max in that process, the remainder is
	    thrown back into the pot and distributed equally among the rest.

	    Negative values are also handled, and zero is assumed to be the
	    implicit lower limit. """
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
	""" Encapsulates a solar charger on dbus. Exposes dbus paths as convenient
	    attributes. """

	# Used for the low-pass filter that determines smoothed_current below.
	OMEGA = (2 * pi)/20

	def __init__(self, monitor, service):
		self.monitor = monitor
		self.service = service
		self._smoothed_current = self.chargecurrent or 0
		self._has_externalcontrol_support = False

	def _get_path(self, path):
		return self.monitor.get_value(self.service, path)

	def _set_path(self, path, v):
		if self.monitor.seen(self.service, path):
			self.monitor.set_value_async(self.service, path, v)

	@property
	def firmwareversion(self):
		return self.monitor.get_value(self.service, '/FirmwareVersion')

	@property
	def product_id(self):
		return self.monitor.get_value(self.service, '/ProductId')

	@property
	def n2k_device_instance(self):
		return self.monitor.get_value(self.service, '/N2kDeviceInstance')

	@property
	def has_externalcontrol_support(self):
		# If we have previously determined that there is support, re-use that.
		# If the firmware is ever to be downgraded, the solarcharger must necessarily
		# disconnect and reconnect, so this is completely safe.
		if self._has_externalcontrol_support:
			return True

		# These products are known to have support, but may have older firmware
		# See https://github.com/victronenergy/venus/issues/655
		if 0xA102 <= self.product_id <= 0xA10E:
			self._has_externalcontrol_support = True
			return True

		v = self.firmwareversion

		# If the firmware version is not known, don't raise a false
		# warning.
		if v is None:
			return True

		# New VE.Can controllers have 24-bit version strings. One would
		# hope that any future VE.Direct controllers with 24-bit firmware
		# versions will 1) have a version larger than 1.02 and 2) support
		# external control.
		if v & 0xFF0000:
			self._has_externalcontrol_support = (v >= VECAN_FIRMWARE_REQUIRED)
		else:
			self._has_externalcontrol_support = (v >= VEDIRECT_FIRMWARE_REQUIRED)
		return self._has_externalcontrol_support

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
		""" Returns the internal low-pass filtered current value. """
		return self._smoothed_current

	def maximize_charge_current(self):
		""" Max out the charge current of this solar charger by setting
		    ChargeCurrent to the configured limit in settings. """
		if self.monitor.seen(self.service, '/Link/ChargeCurrent'):
			copy_dbus_value(self.monitor,
				self.service, '/Settings/ChargeCurrentLimit',
				self.service, '/Link/ChargeCurrent')

	def update_values(self):
		# This is called periodically from a timer to maintain
		# a smooth current value.
		v = self.monitor.get_value(self.service, '/Dc/0/Current')
		if v is not None:
			self._smoothed_current += (v - self._smoothed_current) * self.OMEGA

class Inverter(SolarCharger):
	""" Encapsulates an inverter object, currently the inverter RS, which has a solar
	    input and can charge the battery like a solar charger. """

	@property
	def has_externalcontrol_support(self):
		# Inverter RS always had support
		return True

class SolarChargerSubsystem(object):
	""" Encapsulates a collection of solar chargers that collectively make up
	    a charging system (sans Multi). Properties related to the whole
	    system or some combination of the individual chargers are exposed
		here as attributes. """
	def __init__(self, monitor):
		self.monitor = monitor
		self._solarchargers = {}

	def add_charger(self, service):
		self._solarchargers[service] = charger = SolarCharger(self.monitor, service)
		return charger

	def add_inverter(self, service):
		self._solarchargers[service] = inverter = Inverter(self.monitor, service)
		return inverter

	def remove_charger(self, service):
		del self._solarchargers[service]

	def __iter__(self):
		return self._solarchargers.itervalues()

	def __len__(self):
		return len(self._solarchargers)

	def __contains__(self, k):
		return k in self._solarchargers

	@property
	def has_externalcontrol_support(self):
		return all(s.has_externalcontrol_support for s in self._solarchargers.values())

	@property
	def has_vecan_chargers(self):
		""" Returns true if we have any VE.Can chargers in the system. This is
		    used elsewhere to enable broadcasting charge voltages on the relevant
		    can device. """
		return any((s.connection == 'VE.Can' for s in self._solarchargers.values()))

	@property
	def capacity(self):
		""" Total capacity if all chargers are running at full power. """
		return safeadd(*(c.currentlimit for c in self._solarchargers.values()))

	@property
	def smoothed_current(self):
		""" Total smoothed current, calculated by adding the smoothed current
		    of the individual chargers. """
		return safeadd(*(c.smoothed_current for c in self._solarchargers.values())) or 0

	def maximize_charge_current(self):
		""" Max out all chargers. """
		for charger in self._solarchargers.values():
			charger.maximize_charge_current()

	def set_networked(self, has_bms, charge_voltage, max_charge_current, feedback_allowed):
		""" This is the main entry-point into the solar charger subsystem. This
		    sets all chargers to the same charge_voltage, and distributes
		    max_charge_current between the chargers. If feedback_allowed, then
		    we simply max out the chargers. We also don't bother with
		    distribution if there's only one charger in the system or if
		    it exceeds our total capacity.
		"""
		# Network mode:
		# bit 0: Operated in network environment
		# bit 2: Remote Hub-1 control (MPPT will accept charge voltage and max charge current)
		# bit 3: Remote BMS control (MPPT enter BMS mode)
		network_mode = 1 | (0 if charge_voltage is None and max_charge_current is None else 4) | (8 if has_bms else 0)
		network_mode_written = False
		for charger in self._solarchargers.values():
			charger.networkmode = network_mode
			network_mode_written = True

		# Update vecan only if there is one..
		vecan = self.monitor.get_service_list('com.victronenergy.vecan')
		if len(vecan):
			for _ in vecan.iterkeys():
				self.monitor.set_value_async(_, '/Link/NetworkMode', network_mode)
			network_mode_written = True

		# Distribute the voltage setpoint. Simply write it to all of them.
		voltage_written = 0
		if charge_voltage is not None:
			voltage_written = int(len(self._solarchargers)>0)
			for charger in self._solarchargers.values():
				charger.chargevoltage = charge_voltage

		# Do not limit max charge current when feedback is allowed. The
		# rationale behind this is that MPPT charge power should match the
		# capabilities of the battery. If the default charge algorithm is used
		# by the MPPTs, the charge current should stay within limits. This
		# avoids a problem that we do not know if extra MPPT power will be fed
		# back to the grid when we decide to increase the MPPT max charge
		# current.
		#
		# Additionally, don't bother with chargers that are disconnected.
		chargers = filter(lambda x: x.state !=0 and x.n2k_device_instance in (0, None),
			self._solarchargers.values())
		if len(chargers) > 0:
			if feedback_allowed:
				self.maximize_charge_current()
			elif max_charge_current is not None:
				if len(chargers) == 1:
					# The simple case: Only one charger. Simply assign the
					# limit to the charger
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

		# We cannot have a max_charge_current higher than the sum of the
		# ceilings.
		max_charge_current = min(sum(ceilings), max_charge_current)


		# Check how far we have to move our adjustment. If it doesn't have to
		# move much (or at all), then just balance the charge limits. Our
		# threshold for doing an additional distribution of charge is relative
		# to the number of chargers, as it makes no sense to attempt a
		# distribution if there is too little to be gained. The chosen value
		# here is 100mA per charger.
		delta = max_charge_current - sum(limits)
		if abs(delta) > 0.1 * len(chargers):
			limits = distribute(limits, ceilings, delta)
		else:
			# Balance the limits so they have the same headroom at the top.
			# This works well for the most part. A previous version of this
			# algorithm attempted to balance the headroom percentage according
			# to the capacity of the charger. This tended to break at the edges
			# of the spectrum. The current algorithm may load a
			# disproportionately smaller charger a bit harder, but in practice
			# it seems to work well enough.
			#
			# We also round the figure a little for discrete distribution and
			# stability.
			margins = [max(0, l - a) for a, l in izip(actual, limits)]
			avgmargin = sum(margins)/len(margins)
			deltas = [round(avgmargin - x, 1) for x in margins]
			for i, a, d in izip(count(), actual, deltas):
				limits[i] += d

		# Finally set the limits. Do this every time, otherwise the chargers
		# go back to their default algorithm.
		for charger, limit in izip(chargers, limits):
			charger.maxchargecurrent = limit

	def update_values(self):
		# This is called periodically from a timer to update contained
		# solar chargers with values that they track.
		for charger in self._solarchargers.values():
			charger.update_values()

class Battery(object):
	""" Class that encapsulates the battery and/or BMS. """
	def __init__(self, monitor, service):
		self.monitor = monitor
		self.service = service

	@property
	def is_bms(self):
		""" Indicates if this battery has a BMS that can communicate the
		    preferred charge parameters. """
		return self.monitor.get_value(self.service, '/Info/MaxChargeVoltage') is not None

	@reify
	def device_instance(self):
		""" Returns the DeviceInstance of this device. """
		return self.monitor.get_value(self.service, '/DeviceInstance')

	@property
	def maxchargecurrent(self):
		""" Returns maxumum charge current published by the BMS. """
		return self.monitor.get_value(self.service, '/Info/MaxChargeCurrent')

	@property
	def chargevoltage(self):
		""" Returns charge voltage published by the BMS. """
		return self.monitor.get_value(self.service, '/Info/MaxChargeVoltage')

	@property
	def batterylowvoltage(self):
		""" Returns battery low voltage published by the BMS. """
		return self.monitor.get_value(self.service, '/Info/BatteryLowVoltage')

	@property
	def maxdischargecurrent(self):
		""" Returns max discharge current published by the BMS. """
		return self.monitor.get_value(self.service, '/Info/MaxDischargeCurrent')

	@property
	def voltage(self):
		""" Returns current voltage of battery. """
		return self.monitor.get_value(self.service, '/Dc/0/Voltage')

	@reify
	def product_id(self):
		""" Returns Product ID of battery. """
		return self.monitor.get_value(self.service, '/ProductId')

	@property
	def capacity(self):
		""" Capacity of battery, if defined. """
		return self.monitor.get_value(self.service, '/InstalledCapacity')


class BatterySubsystem(object):
	""" Encapsulates multiple battery services. We may have both a BMV and a
	    BMS. """
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
	def bmses(self):
		""" Returns the battery services with a BMS. """
		return filter(lambda b: b.is_bms,
			self._battery_services.itervalues())

class BatteryOperationalLimits(object):
	""" Only used to encapsulate this part of the Multi's functionality.
	"""
	def __init__(self, multi):
		self._multi = multi

	def _property(path, self):
		# Due to the use of partial, path and self is reversed.
		return self._multi.monitor.get_value(self._multi.service, path)

	def _set_property(path, self, v):
		# None of these values can be negative
		if v is not None:
			v = max(0, v)
		self._multi.monitor.set_value_async(self._multi.service, path, v)

	chargevoltage = property(
		partial(_property, '/BatteryOperationalLimits/MaxChargeVoltage'),
		partial(_set_property, '/BatteryOperationalLimits/MaxChargeVoltage'))
	maxchargecurrent = property(
		partial(_property, '/BatteryOperationalLimits/MaxChargeCurrent'),
		partial(_set_property, '/BatteryOperationalLimits/MaxChargeCurrent'))
	maxdischargecurrent = property(
		partial(_property, '/BatteryOperationalLimits/MaxDischargeCurrent'),
		partial(_set_property, '/BatteryOperationalLimits/MaxDischargeCurrent'))
	batterylowvoltage = property(
		partial(_property, '/BatteryOperationalLimits/BatteryLowVoltage'),
		partial(_set_property, '/BatteryOperationalLimits/BatteryLowVoltage'))


class Multi(object):
	""" Encapsulates the multi. Makes access to dbus paths a bit neater by
	    exposing them as attributes. """
	# Used for low-pass filter
	OMEGA = (2 * pi)/30

	def __init__(self, monitor, service):
		self.monitor = monitor
		self._service = service
		self.bol = BatteryOperationalLimits(self)
		self._dc_current = 0

	@property
	def service(self):
		try:
			return self._service['/VebusService']
		except KeyError:
			return None

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
		# If the Multi is not ready, don't write to it just yet
		if self.active and self.maxchargecurrent is not None:
			# The maximum present charge current is 6-parallel 12V 5kva units, 6*220 = 1320A.
			# We will consider 10000A to be impossibly high.
			self.monitor.set_value_async(self.service, '/Dc/0/MaxChargeCurrent', 10000 if v is None else v)

	@property
	def state(self):
		return self.monitor.get_value(self.service, '/State')

	@property
	def feedin_enabled(self):
		return self.monitor.get_value(self.service,
			'/Hub4/L1/DoNotFeedInOvervoltage') == 0

	@property
	def firmwareversion(self):
		return self.monitor.get_value(self.service, '/FirmwareVersion')

	def update_values(self, limit):
		c = self.monitor.get_value(self.service, '/Dc/0/Current', 0)
		if c is not None:
			# Cap the filter at a limit. If we don't do this, dc currents
			# in excess of our capacity causes a kind of wind-up that delays
			# backing-off when the load drops suddenly.
			if limit is not None:
				c = max(c, -limit)
			self._dc_current += (c - self._dc_current) * self.OMEGA

class Dvcc(SystemCalcDelegate):
	""" This is the main DVCC delegate object. """
	def __init__(self, sc):
		super(Dvcc, self).__init__()
		self.systemcalc = sc
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
				'/Info/MaxDischargeCurrent',
				'/InstalledCapacity']),
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
				'/FirmwareFeatures/BolFrame',
				'/Hub4/L1/DoNotFeedInOvervoltage',
				'/FirmwareVersion']),
			('com.victronenergy.solarcharger', [
				'/ProductId',
				'/Dc/0/Current',
				'/Link/NetworkMode',
				'/Link/ChargeVoltage',
				'/Link/ChargeCurrent',
				'/Settings/ChargeCurrentLimit',
				'/State',
				'/FirmwareVersion',
				'/N2kDeviceInstance',
				'/Mgmt/Connection']),
			('com.victronenergy.inverter', [
				'/ProductId',
				'/Dc/0/Current',
				'/IsInverterCharger',
				'/Link/NetworkMode',
				'/Link/ChargeVoltage',
				'/Link/ChargeCurrent',
				'/Settings/ChargeCurrentLimit',
				'/State',
				'/N2kDeviceInstance',
				'/Mgmt/Connection']),
			('com.victronenergy.vecan',	[
				'/Link/ChargeVoltage',
				'/Link/NetworkMode']),
			('com.victronenergy.settings', [
				 '/Settings/CGwacs/OvervoltageFeedIn',
				 '/Settings/Services/Bol'])]

	def get_settings(self):
		return [
			('maxchargecurrent', '/Settings/SystemSetup/MaxChargeCurrent', -1, -1, 10000),
			('maxchargevoltage', '/Settings/SystemSetup/MaxChargeVoltage', 0.0, 0.0, 80.0),
			('bol', '/Settings/Services/Bol', 0, 0, 7)
		]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._batterysystem = BatterySubsystem(dbusmonitor)
		self._solarsystem = SolarChargerSubsystem(dbusmonitor)
		self._multi = Multi(dbusmonitor, dbusservice)

		self._dbusservice.add_path('/Control/SolarChargeVoltage', value=0)
		self._dbusservice.add_path('/Control/SolarChargeCurrent', value=0)
		self._dbusservice.add_path('/Control/EffectiveChargeVoltage', value=None)
		self._dbusservice.add_path('/Control/BmsParameters', value=0)
		self._dbusservice.add_path('/Control/MaxChargeCurrent', value=0)
		self._dbusservice.add_path('/Control/Dvcc', value=1)
		self._dbusservice.add_path('/Debug/BatteryOperationalLimits/SolarVoltageOffset', value=0, writeable=True)
		self._dbusservice.add_path('/Debug/BatteryOperationalLimits/VebusVoltageOffset', value=0, writeable=True)
		self._dbusservice.add_path('/Debug/BatteryOperationalLimits/CurrentOffset', value=0, writeable=True)
		self._dbusservice.add_path('/Dvcc/Alarms/FirmwareInsufficient', value=0)
		self._dbusservice.add_path('/Dvcc/Alarms/MultipleBatteries', value=0)

	def device_added(self, service, instance, do_service_change=True):
		service_type = service.split('.')[2]
		if service_type == 'solarcharger':
			self._solarsystem.add_charger(service)
		elif service_type == 'inverter' and self._dbusmonitor.get_value(service, '/IsInverterCharger') == 1:
			self._solarsystem.add_inverter(service)
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

	def _property(path, self):
		# Due to the use of partial, path and self is reversed.
		try:
			return float(self._dbusservice[path])
		except ValueError:
			return None

	solarvoltageoffset = property(partial(_property, '/Debug/BatteryOperationalLimits/SolarVoltageOffset'))
	invertervoltageoffset = property(partial(_property, '/Debug/BatteryOperationalLimits/VebusVoltageOffset'))
	currentoffset = property(partial(_property, '/Debug/BatteryOperationalLimits/CurrentOffset'))

	@property
	def has_ess_assistant(self):
		return self._multi.active and self._multi.has_ess_assistant

	@property
	def has_dvcc(self):
		# 0b00  = Off
		# 0b01  = On
		# 0b10  = Forced off
		# 0b11  = Forced on
		v = self._settings['bol']
		return bool(v & 1)

	@property
	def bms(self):
		bmses = sorted(self._batterysystem.bmses,
			key=lambda b: (b.service != self.systemcalc._batteryservice, b.device_instance))
		try:
			return bmses[0]
		except IndexError:
			pass
		return None

	def _on_timer(self):
		bol_support = self.has_dvcc

		self._tickcount -= 1; self._tickcount %= ADJUST

		if not bol_support:
			if self._tickcount > 0: return True

			voltage_written, current_written = self._legacy_update_solarchargers()
			self._dbusservice['/Control/SolarChargeVoltage'] = voltage_written
			self._dbusservice['/Control/SolarChargeCurrent'] = current_written
			self._dbusservice['/Control/EffectiveChargeVoltage'] = None # Not tracking for non-DVCC cases
			self._dbusservice['/Control/BmsParameters'] = 0
			self._dbusservice['/Control/MaxChargeCurrent'] = 0
			self._dbusservice['/Control/Dvcc'] = 0
			self._dbusservice['/Dvcc/Alarms/FirmwareInsufficient'] = 0
			self._dbusservice['/Dvcc/Alarms/MultipleBatteries'] = 0
			return True


		# BOL/DVCC support below
		self._dbusservice['/Dvcc/Alarms/FirmwareInsufficient'] = int(
			not self._solarsystem.has_externalcontrol_support or (
			self._multi.firmwareversion is not None and self._multi.firmwareversion < VEBUS_FIRMWARE_REQUIRED))
		self._dbusservice['/Dvcc/Alarms/MultipleBatteries'] = int(
			len(self._batterysystem.bmses) > 1)

		# Update subsystems
		self._solarsystem.update_values()
		self._multi.update_values(self._solarsystem.capacity)

		# Below are things we only do every ADJUST seconds
		if self._tickcount > 0: return True

		# Signal Dvcc support to other processes
		self._dbusservice['/Control/Dvcc'] = 1

		# Get the user current limit, if set
		user_max_charge_current = self._settings['maxchargecurrent']
		if user_max_charge_current < 0: user_max_charge_current = None

		# If there is a BMS, get the charge voltage and current from it
		bms_service = self.bms
		max_charge_current = None
		charge_voltage = None
		feedback_allowed = self.feedback_allowed
		if bms_service is not None:
			charge_voltage, max_charge_current, feedback_allowed = \
				self._adjust_battery_operational_limits(bms_service, feedback_allowed)

		# Take the lesser of the BMS and user current limits, wherever they exist
		maximae = filter(lambda x: x is not None,
			(user_max_charge_current, max_charge_current))
		max_charge_current = min(maximae) if maximae else None

		# Override the battery charge voltage by taking the lesser of the
		# voltage limits. Only override if the battery supplies one, to prevent
		# a voltage being sent to a Multi in a system without a managed battery.
		# Otherwise the Multi will go into passthru if the user disables this.
		if charge_voltage is not None:
			user_charge_voltage = self._settings['maxchargevoltage']
			if user_charge_voltage > 0:
				charge_voltage = min(charge_voltage, user_charge_voltage)

		# @todo EV What if ESS + OvervoltageFeedIn? In that case there is no
		# charge current control on the MPPTs, but we'll still indicate that
		# the control is active here. Should we?
		self._dbusservice['/Control/MaxChargeCurrent'] = \
			not self._multi.active or self._multi.has_bolframe

		# We need to keep a copy of the original value for later. We will be
		# modifying one of them to compensate for vebus current.
		_max_charge_current = max_charge_current

		# If we have vebus current, we have to compensate for it
		vebus_dc_current = self._multi.dc_current
		if _max_charge_current is not None and vebus_dc_current is not None and \
				vebus_dc_current < 0:
			_max_charge_current = ceil(_max_charge_current - vebus_dc_current)

		# Try to push the solar chargers to the vebus-compensated value
		voltage_written, current_written, effective_charge_voltage = \
			self._update_solarchargers(bms_service is not None, charge_voltage,
			_max_charge_current, feedback_allowed)
		self._dbusservice['/Control/SolarChargeVoltage'] = voltage_written
		self._dbusservice['/Control/SolarChargeCurrent'] = current_written
		self._dbusservice['/Control/EffectiveChargeVoltage'] = effective_charge_voltage

		# The Multi gets the remainder after subtracting what the solar chargers made
		if max_charge_current is not None:
			max_charge_current = max(0.0, round(max_charge_current - self._solarsystem.smoothed_current))

		# Write the remainder to the Multi.
		# There are two ways to limit the charge current of a VE.Bus system. If we have a BMS,
		# the BOL parameter is used.
		# If not, then the BOL parameters are not available, and the /Dc/0/MaxChargeCurrent path is
		# used instead. This path relates to the MaxChargeCurrent setting as also available in
		# VEConfigure, except that writing to it only changes the value in RAM in the Multi.
		# Unlike VEConfigure it's not necessary to take the number of units in a system into account.
		#
		# Venus OS v2.30 fixes in mk2-dbus related to /Dc/0/MaxChargeCurrent:
		# 1) Fix charge current too high in systems with multiple units per phase. mk2-bus was dividing
		#    the received current only by the number of phases in the system instead of dividing by the
		#    number of units in the system.
		# 2) Fix setted charge current still active after disabling the "Limit charge current" setting.
		#    It used to be necessary to set a high current; and only then disable the setting or reset
		#    the VE.Bus system to re-initialise from the stored setting as per VEConfigure.
		bms_parameters_written = 0
		if bms_service is None:
			if max_charge_current is None:
				self._multi.maxchargecurrent = None
			else:
				# Don't bother setting a charge current at 1A or less
				self._multi.maxchargecurrent = max_charge_current if max_charge_current > 1 else 0
		else:
			bms_parameters_written = self._update_battery_operational_limits(bms_service, charge_voltage, max_charge_current)
		self._dbusservice['/Control/BmsParameters'] = int(bms_parameters_written or (bms_service is not None and voltage_written))

		return True

	def _adjust_battery_operational_limits(self, bms_service, feedback_allowed):
		""" Take the charge voltage and maximum charge current from the BMS
		    and adjust it as necessary. For now we only implement quirks
		    for batteries known to have them.
		"""
		cv = bms_service.chargevoltage
		mcc = bms_service.maxchargecurrent

		quirk = QUIRKS.get(bms_service.product_id)
		if quirk is not None:
			# If any quirks are registered for this battery, use that
			# instead.
			cv, mcc, feedback_allowed = quirk(self, bms_service, cv, mcc, feedback_allowed)

		# Add debug offsets
		if cv is not None:
			cv = safeadd(cv, self.invertervoltageoffset)
		if mcc is not None:
			mcc = safeadd(mcc, self.currentoffset)
		return cv, mcc, feedback_allowed

	def _update_battery_operational_limits(self, bms_service, cv, mcc):
		""" This function writes the bms parameters across to the Multi
		    if it exists. The parameters may be modified before being
		    copied across. The modified current value is returned to be
		    used elsewhere. """
		if self._multi.active:
			if cv is not None:
				self._multi.bol.chargevoltage = cv

			if mcc is not None:
				self._multi.bol.maxchargecurrent = mcc

			# Copy the rest unmodified
			self._multi.bol.maxdischargecurrent = bms_service.maxdischargecurrent
			self._multi.bol.batterylowvoltage = bms_service.batterylowvoltage
			return 1

		return 0

	@property
	def feedback_allowed(self):
		# Feedback allowed is defined as 'ESS present and FeedInOvervoltage is
		# enabled'. This ignores other setups which allow feedback: hub-1.
		return self.has_ess_assistant and self._multi.ac_connected and \
			self._dbusmonitor.get_value('com.victronenergy.settings',
				'/Settings/CGwacs/OvervoltageFeedIn') == 1

	def _update_solarchargers(self, has_bms, bms_charge_voltage, max_charge_current, feedback_allowed):
		""" This function updates the solar chargers only. Parameters
		    related to the Multi are handled elsewhere. """

		# If the vebus service does not provide a charge voltage setpoint (so
		# no ESS/Hub-1/Hub-4), we use the max charge voltage provided by the
		# BMS (if any). This will probably prevent feedback, but that is
		# probably not allowed anyway.
		charge_voltage = None
		if self._multi.active:
			charge_voltage = self._multi.hub_voltage
		if charge_voltage is None and bms_charge_voltage is not None:
			charge_voltage = bms_charge_voltage
		if charge_voltage is not None:
			try:
				charge_voltage += self.solarvoltageoffset
			except (ValueError, TypeError):
				pass

		if charge_voltage is None and max_charge_current is None:
			return 0, 0, None

		voltage_written, current_written = self._solarsystem.set_networked(
			has_bms, charge_voltage, max_charge_current, feedback_allowed)

		# Charge voltage cannot by written directly to the CAN-bus solar chargers, we have to use
		# the com.victronenergy.vecan.* service instead.
		if charge_voltage is not None and self._solarsystem.has_vecan_chargers:
			for service in self._vecan_services:
				try:
					# Note: we don't check the value of charge_voltage_item
					# because it may be invalid, for example if the D-Bus path
					# has not been written for more than 60 (?) seconds.  In
					# case there is no path at all, the set_value below will
					# raise an DBusException which we will ignore cheerfully.
					self._dbusmonitor.set_value_async(service, '/Link/ChargeVoltage', charge_voltage)
					voltage_written = 1
				except DBusException:
					pass

		return voltage_written, current_written, charge_voltage

	def _legacy_update_solarchargers(self):
		""" This is the old implementation we used before DVCC. It is kept
		    here so we can fall back to it where DVCC is not fully supported,
			and to avoid maintaining two copies of systemcalc. """

		max_charge_current = None
		for battery in self._batterysystem:
			max_charge_current = safeadd(max_charge_current, \
				self._dbusmonitor.get_value(battery.service, '/Info/MaxChargeCurrent'))

		# Workaround: copying the max charge current from BMS batteries to the solarcharger leads to problems:
		# excess PV power is not fed back to the grid any more, and loads on AC-out are not fed with PV power.
		# PV power is used for charging the batteries only.
		# So we removed this feature, until we have a complete solution for solar charger support. Until then
		# we set a 'high' max charge current to avoid 'BMS connection lost' alarms from the solarcharger.
		if max_charge_current is not None:
			max_charge_current = 1000

		vebus_path = self._multi.service if self._multi.active else None
		charge_voltage = None if vebus_path is None else \
			self._dbusmonitor.get_value(vebus_path, '/Hub/ChargeVoltage')

		if charge_voltage is None and max_charge_current is None:
			return (0, 0)

		# Network mode:
		# bit 0: Operated in network environment
		# bit 2: Remote Hub-1 control
		# bit 3: Remote BMS control
		network_mode = 1 | (0 if charge_voltage is None else 4) | (0 if max_charge_current is None else 8)
		voltage_written = 0
		current_written = 0
		for charger in self._solarsystem:
			try:
				# We use /Link/NetworkMode to detect Hub support in the solarcharger. Existence of this item
				# implies existence of the other /Link/* fields.
				if charger.networkmode is None:
					continue
				charger.networkmode = network_mode

				if charge_voltage is not None:
					charger.chargevoltage = charge_voltage
					voltage_written = 1

				if max_charge_current is not None:
					charger.maxchargecurrent = max_charge_current
					current_written = 1
			except DBusException:
				# If the charger for whatever reason doesn't have the /Link
				# path, ignore it. This is the legacy implementation and
				# better to keep it for the moment.
				pass

		if charge_voltage is not None and self._solarsystem.has_vecan_chargers:
			# Charge voltage cannot by written directly to the CAN-bus solar chargers, we have to use
			# the com.victronenergy.vecan.* service instead.
			# Writing charge current to CAN-bus solar charger is not supported yet.
			for service in self._vecan_services:
				try:
					# Note: we don't check the value of charge_voltage_item because it may be invalid,
					# for example if the D-Bus path has not been written for more than 60 (?) seconds.
					# In case there is no path at all, the set_value below will raise an DBusException
					# which we will ignore cheerfully.
					self._dbusmonitor.set_value_async(service, '/Link/ChargeVoltage', charge_voltage)
					voltage_written = 1
				except DBusException:
					pass

		return (voltage_written, current_written)
