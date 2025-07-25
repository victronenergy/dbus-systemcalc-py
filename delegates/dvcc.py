from dbus.exceptions import DBusException
from gi.repository import GLib
from math import pi, ceil
from itertools import count, chain
from functools import partial

# Victron packages
from sc_utils import safeadd, copy_dbus_value, ExpiringValue
from ve_utils import exit_on_error

from delegates.base import SystemCalcDelegate
from delegates.batteryservice import BatteryService
from delegates.multi import Multi as MultiService

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
		return (min(55.0, charge_voltage), 40, feedback_allowed, False)
	return (charge_voltage, charge_current, feedback_allowed, False)

def _lg_quirk(dvcc, bms, charge_voltage, charge_current, feedback_allowed):
	""" Quirk for LG batteries. The hard limit is 58V. Above that you risk
	    tripping on high voltage. The batteries publish a charge voltage of 57.7V
	    but we need to make room for an 0.4V overvoltage when feed-in is enabled.
	"""
	# Make room for a potential 0.4V at the top
	return (min(charge_voltage, 57.3), charge_current, feedback_allowed, False)

class _pylontech_quirk(object):
	def __init__(self):
		self._chargevoltage = 52.5

	def __call__(self, dvcc, bms, charge_voltage, charge_current, feedback_allowed):
		""" Quirk for Pylontech. Make a bit of room at the top. Pylontech says that
			at 51.8V the battery is 95% full, and that balancing starts at 90%.
			53.2V is normally considered 100% full, and 54V raises an alarm. By
			running the battery at 52.4V it will be 99%-100% full, balancing should
			be active, and we should avoid high voltage alarms.

			Identify 24-V batteries by the lower charge voltage, and do the same
			thing with an 8-to-15 cell ratio, +-3.48V per cell.
		"""
		# Use 3.48V per cell plus a little, 52.4V for 15 cell 48V batteries.
		# Use 3.46V per cell plus a little, 27.8V for 24V batteries testing shows that's 100% SOC.
		# That leaves 1.6V margin for 48V batteries and 1.0V for 24V.
		# See https://github.com/victronenergy/venus/issues/536
		if charge_voltage > 55:
			# 48V battery (16 cells.) Assume BMS knows what it's doing.
			return (charge_voltage, charge_current, feedback_allowed, False)
		if charge_voltage > 20:
			# 48V battery (15 cells) or 24V battery (8 cells). We want to halve
			# the charge current limit when CCL=0 is sent. Normally the limit is
			# C/2, so limit to C/4, or assume a single module (25Ah/55Ah) if not
			# known.  The more important part is clipping the charge voltage to a
			# lower value. This is to fix the sawtooth voltage issue.
			if charge_voltage < 30:
				# 24V
				capacity = bms.capacity or 55
				# Lower charge voltage more if CCL is zero
				if charge_current < 0.1:
					charge_voltage = min(charge_voltage, 27.6)
				else:
					charge_voltage = min(charge_voltage, 27.8)
				charge_current = max(charge_current, round(capacity/4.0))
			else:
				# 48V
				capacity = bms.capacity or 25.0
				# Aim for 52.5V, but somewhat aggressively penalise the charge
				# voltage if the highest cell goes over 3.485V. Filter this
				# to keep it somewhat stable.
				try:
					charge_voltage = 52.5 - 30 * max(0, bms.maxcellvoltage-3.485)
					charge_voltage = self._chargevoltage = 0.95 * self._chargevoltage + 0.05 * charge_voltage
					charge_voltage = round(charge_voltage, 2)
				except TypeError:
					charge_voltage = min(charge_voltage, 52.4)

			return (charge_voltage, charge_current, feedback_allowed, False)

		# Not known, probably a 12V battery.
		return (charge_voltage, charge_current, feedback_allowed, False)

def _pylontech_pelio_quirk(dvcc, bms, charge_voltage, charge_current, feedback_allowed):
	""" Quirk for Pelio-L batteries. This is a 16-cell battery. 56V is 3.5V per
	    cell which is where this battery registers 100% SOC. Battery sends
	    CCL=0 at 3.55V per cell, to ensure good feed-in of excess DC coupled
	    PV, set the lower limit to 20% of capacity, which is what the battery
	    itself imposes at around 98% SOC.
	"""
	capacity = bms.capacity or 100.0
	charge_current = max(charge_current, round(capacity/5.0))
	if charge_current < 0.1:
		return (min(charge_voltage, 55.2), charge_current, feedback_allowed, False)
	return (min(charge_voltage, 56.0), charge_current, feedback_allowed, False)

def _lynx_smart_bms_quirk(dvcc, bms, charge_voltage, charge_current, feedback_allowed):
	""" When the Lynx Smart BMS sends CCL=0, it wants all chargers to stop. """
	return (charge_voltage, charge_current, feedback_allowed, True)

QUIRKS = {
	0xB004: _lg_quirk,
	0xB009: _pylontech_quirk(),
	0xB00A: _byd_quirk,
	0xB015: _byd_quirk,
	0xB019: _byd_quirk,
	0xB029: _pylontech_pelio_quirk,
	0xA3E4: _lynx_smart_bms_quirk,
	0xA3E5: _lynx_smart_bms_quirk,
	0xA3E6: _lynx_smart_bms_quirk,
	0xA3E7: _lynx_smart_bms_quirk,
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
		for i, mv, av in zip(count(), max_values, current_values):
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

class LowPassFilter(object):
	""" Low pass filter, with a cap. """
	def __init__(self, omega, value):
		self.omega = omega
		self._value = value

	def update(self, newvalue):
		self._value += (newvalue - self._value) * self.omega
		return self._value

	@property
	def value(self):
		return self._value

class BaseCharger(object):
	def __init__(self, monitor, service):
		self.monitor = monitor
		self.service = service
		self.is_vecan = self.connection == 'VE.Can'

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
		return self.monitor.get_value(self.service, '/ProductId') or 0

	@property
	def chargecurrent(self):
		return self._get_path('/Dc/0/Current')

	@property
	def n2k_device_instance(self):
		return self.monitor.get_value(self.service, '/N2kDeviceInstance')

	@property
	def connection(self):
		return self._get_path('/Mgmt/Connection')

	@property
	def active(self):
		return self._get_path('/State') != 0

	@property
	def has_externalcontrol_support(self):
		""" Override this to implement detection of external control support.
		"""
		return False

	@property
	def want_bms(self):
		""" Indicates whether this solar charger was previously
		    controlled by a BMS and therefore expects one to
		    be present. """
		return self._get_path('/Settings/BmsPresent') == 1

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

	def maximize_charge_current(self):
		""" Max out the charge current of this solar charger by setting
		    ChargeCurrent to the configured limit in settings. """
		if self.monitor.seen(self.service, '/Link/ChargeCurrent'):
			copy_dbus_value(self.monitor,
				self.service, '/Settings/ChargeCurrentLimit',
				self.service, '/Link/ChargeCurrent')

	@property
	def smoothed_current(self):
		# For chargers that are not solar-chargers, the generated current
		# should be fairly stable already
		return self.chargecurrent or 0

class Networkable(object):
	""" Mix into BaseCharger to support network paths. """
	@property
	def networkmode(self):
		return self._get_path('/Link/NetworkMode')

	@networkmode.setter
	def networkmode(self, v):
		self._set_path('/Link/NetworkMode', v)

class SolarCharger(BaseCharger, Networkable):
	""" Encapsulates a solar charger on dbus. Exposes dbus paths as convenient
	    attributes. """

	def __init__(self, monitor, service):
		super().__init__(monitor, service)
		self._smoothed_current = LowPassFilter((2 * pi)/20, self.chargecurrent or 0)
		self._has_externalcontrol_support = False

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
	def smoothed_current(self):
		""" Returns the internal low-pass filtered current value. """
		return self._smoothed_current.value

	def update_values(self):
		# This is called periodically from a timer to maintain
		# a smooth current value.
		v = self.monitor.get_value(self.service, '/Dc/0/Current')
		if v is not None:
			self._smoothed_current.update(v)

class Alternator(BaseCharger, Networkable):
	""" This also includes other DC/DC converters. """
	@property
	def has_externalcontrol_support(self):
		# If it has the ChargeCurrent path, we assume it has
		# external control support
		return self.monitor.seen(self.service, '/Link/ChargeCurrent')

class InverterCharger(SolarCharger):
	""" Encapsulates an inverter/charger object, currently the inverter RS,
	    which has a solar input and can charge the battery like a solar
	    charger, but is also an inverter.
	"""
	def __init__(self, monitor, service):
		super(InverterCharger, self).__init__(monitor, service)

	@property
	def has_externalcontrol_support(self):
		# Inverter RS always had support
		return True

	@property
	def maxdischargecurrent(self):
		""" Returns discharge current setting. This does nothing except
		    return the previously set value. """
		return self.monitor.get_value(self.service, '/Link/DischargeCurrent')

	@maxdischargecurrent.setter
	def maxdischargecurrent(self, limit):
		self.monitor.set_value_async(self.service, '/Link/DischargeCurrent', limit)

	def set_maxdischargecurrent(self, limit):
		""" Write the maximum discharge limit across. The firmware
		    already handles a zero by turning off. """
		self.maxdischargecurrent = limit

	@property
	def active(self):
		# The charger part is active, as long as the maximum charging
		# power value is more than zero.
		return (self.monitor.get_value(self.service,
			'/Settings/ChargeCurrentLimit') or 0) > 0

	@property
	def chargevoltagesetpoint(self):
		return self._get_path('/Link/ChargeVoltageSetpoint')

	@property
	def solaroffset(self):
		return self._get_path('/Link/ChargeVoltageSolarOffset')

class DcGenset(BaseCharger):
	""" Encapsulates a DC genset on dbus. Exposes dbus paths as convenient
	    attributes. """
	@property
	def has_externalcontrol_support(self):
		# If it has the ChargeVoltage path, we assume it has
		# external control support
		return self.monitor.seen(self.service, '/Link/ChargeVoltage')

	@property
	def maxchargecurrent(self):
		""" The DC genset is not part of the current distribution """
		return 0

	@maxchargecurrent.setter
	def maxchargecurrent(self, v):
		pass

	@property
	def networkmode(self):
		""" Network mode is not supported """
		return 0

	@networkmode.setter
	def networkmode(self, v):
		pass

class InverterSubsystem(object):
	""" Encapsulate collection of inverters. """
	def __init__(self, monitor):
		self.monitor = monitor
		self._inverters = {}

	def _add_inverter(self, ob):
		self._inverters[ob.service] = ob
		return ob

	def remove_inverter(self, service):
		del self._inverters[service]

	def __iter__(self):
		return iter(self._inverters.values())

	def __len__(self):
		return len(self._inverters)

	def __contains__(self, k):
		return k in self._inverters

	def set_maxdischargecurrent(self, limit):
		# Inverters only care about limit=0, so simply send
		# it to all.
		for inverter in self:
			inverter.set_maxdischargecurrent(limit)

	@property
	def chargevoltagesetpoint(self):
		""" Returns total chargevoltagesetpoint (including solar offset)
		    from the relevant inverter (N2kDeviceInstance==0). """
		setpoints = (safeadd(ob.chargevoltagesetpoint, ob.solaroffset) \
			for ob in self._inverters.values() \
			if ob.n2k_device_instance == 0 and \
			ob.chargevoltagesetpoint is not None)
		try:
			return min(v for v in setpoints if v is not None)
		except ValueError:
			pass

		return None

	@property
	def solaroffset(self):
		""" Returns only the solar offset from the relevant inverter. """
		offsets = (ob.solaroffset for ob in self._inverters.values() if ob.n2k_device_instance == 0)
		try:
			return min(v for v in offsets if v is not None)
		except ValueError:
			pass

		return None

class ChargerSubsystem(object):
	""" Encapsulates a collection of chargers or devices that incorporate a
	    charger, to collectively make up a charging system (sans Multi).
	    Properties related to the whole system or some combination of the
	    individual chargers are exposed here as attributes. """
	def __init__(self, monitor):
		self.monitor = monitor
		self._solarchargers = {}
		self._inverterchargers = {}
		self._otherchargers = {}

	def add_solar_charger(self, service):
		self._solarchargers[service] = charger = SolarCharger(self.monitor, service)
		return charger

	def add_alternator(self, service):
		self._otherchargers[service] = charger = Alternator(self.monitor, service)
		return charger

	def add_dcgenset(self, service):
		self._otherchargers[service] = dcgenset = DcGenset(self.monitor, service)
		return dcgenset

	def add_invertercharger(self, service):
		self._inverterchargers[service] = inverter = InverterCharger(self.monitor, service)
		return inverter

	def remove_charger(self, service):
		for di in (self._solarchargers, self._inverterchargers, self._otherchargers):
			try:
				del di[service]
			except KeyError:
				pass

	def __iter__(self):
		return iter(chain(self._solarchargers.values(), self._inverterchargers.values(), self._otherchargers.values()))

	def __len__(self):
		return len(self._solarchargers) + len(self._otherchargers) + len(self._inverterchargers)

	def __contains__(self, k):
		return k in self._solarchargers or k in self._inverterchargers or k in self._otherchargers

	@property
	def has_externalcontrol_support(self):
		# Only consider solarchargers. This is used for firmware warning
		# above, and we only care about the solar chargers there.
		return all(s.has_externalcontrol_support for s in self._solarchargers.values()) and \
			all(s.has_externalcontrol_support for s in self._inverterchargers.values())

	@property
	def has_vecan_chargers(self):
		""" Returns true if we have any VE.Can chargers in the system. This is
		    used elsewhere to enable broadcasting charge voltages on the relevant
		    can device. """
		return any((s.is_vecan for s in self))

	@property
	def want_bms(self):
		""" Return true if any of our solar chargers expect a BMS to
		    be present. """
		return any((s.want_bms for s in self))

	@property
	def totalcapacity(self):
		""" Total capacity if all chargers are running at full power. """
		return safeadd(*(c.currentlimit for c in self))

	@property
	def smoothed_current(self):
		""" Total smoothed current, calculated by adding the smoothed current
		    of the individual chargers. """
		return safeadd(*(c.smoothed_current for c in self)) or 0

	@property
	def solar_current(self):
		return safeadd(*(c.smoothed_current for c in chain(
			self._solarchargers.values(), self._inverterchargers.values()))) or 0

	def maximize_charge_current(self):
		""" Max out all chargers. """
		for charger in self:
			charger.maximize_charge_current()

	def shutdown_chargers(self):
		""" Shut down all chargers. """
		for charger in self:
			charger.maxchargecurrent = 0

	def set_networked(self, has_bms, bms_charge_voltage, charge_voltage, max_charge_current, feedback_allowed, stop_on_mcc0):
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
		for charger in self:
			charger.networkmode = network_mode
			network_mode_written = True

		# Distribute the voltage setpoint to all solar chargers.
		# Non-solar chargers are controlled elsewhere.
		voltage_written = 0
		if charge_voltage is not None:
			voltage_written = int(len(self)>0)
			for charger in chain(self._solarchargers.values(),
					self._inverterchargers.values()):
				# VE.Can chargers get their voltage from the VE.Can interface
				if not charger.is_vecan:
					charger.chargevoltage = charge_voltage

		# Distribute the original BMS voltage setpoint, if there is one,
		# to the other chargers
		if bms_charge_voltage is not None:
			for charger in self._otherchargers.values():
				charger.chargevoltage = bms_charge_voltage

		# Do not limit max charge current when feedback is allowed. The
		# rationale behind this is that MPPT charge power should match the
		# capabilities of the battery. If the default charge algorithm is used
		# by the MPPTs, the charge current should stay within limits. This
		# avoids a problem that we do not know if extra MPPT power will be fed
		# back to the grid when we decide to increase the MPPT max charge
		# current.
		#
		# Additionally, don't bother with chargers that are disconnected.
		chargers = [x for x in chain(self._solarchargers.values(),
			self._inverterchargers.values()) if x.active and x.maxchargecurrent is not None and x.n2k_device_instance in (0, None)]
		if len(chargers) > 0:
			if stop_on_mcc0 and max_charge_current == 0:
				self.shutdown_chargers()
			elif feedback_allowed:
				self.maximize_charge_current()
			elif max_charge_current is not None:
				if len(chargers) == 1:
					# The simple case: Only one charger. Simply assign the
					# limit to the charger
					sc = chargers[0]
					cc = min(ceil(max_charge_current), sc.currentlimit)
					sc.maxchargecurrent = cc
				elif max_charge_current > self.totalcapacity * 0.95:
					# Another simple case, we're asking for more than our
					# combined capacity (with a 5% margin)
					self.maximize_charge_current()
				else:
					# The hard case, we have more than one CC and we want
					# less than our capacity.
					self._distribute_current(chargers, max_charge_current)

		# Split remainder over other chargers, according to individual
		# capacity. Only consider controllable devices.
		if max_charge_current is not None:
			remainder = max(0.0, max_charge_current - self.solar_current)
			controllable = [c for c in self._otherchargers.values() if c.has_externalcontrol_support]
			capacity = safeadd(*(c.currentlimit for c in controllable)) or 0
			if capacity > 0:
				for c in controllable:
					try:
						c.maxchargecurrent = remainder * c.currentlimit / capacity
					except TypeError:
						# happens if for some reason
						# /Settings/ChargeCurrentLimit is not defined
						pass

		# Return flags of what we did
		return voltage_written, int(network_mode_written and max_charge_current is not None), network_mode

	# The math for the below is as follows. Let c be the total capacity of the
	# charger, l be the current limit, a the actual current it produces, k the
	# total current limit for the two chargers, and m the margin (l - a)
	# between the limit and what is produced.
	#
	# We want m/c to be the same for all our chargers.
	#
	# Expression 1: (l1-a1)/c1 == (l2-a2)/c2
	# Expression 2: l1 + l2 == k
	#
	# Solving that yields the expression below.
	@staticmethod
	def _balance_chargers(charger1, charger2, l1, l2):
		c1, c2 = charger1.currentlimit, charger2.currentlimit
		a1 = min(charger1.smoothed_current, c1)
		a2 = min(charger2.smoothed_current, c2)
		k = l1 + l2

		try:
			l1 = round((c2 * a1 - c1 * a2 + k * c1)/(c1 + c2), 1)
		except ArithmeticError:
			return l1, l2 # unchanged
		else:
			l1 = max(min(l1, c1), 0)
			return l1, k - l1

	@staticmethod
	def _distribute_current(chargers, max_charge_current):
		""" This is called if there are two or more solar chargers. It
		    distributes the charge current over all of them. """

		# Take the difference between the values and spread it over all
		# the chargers. The maxchargecurrents of the chargers should ideally
		# always add up to the whole.
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
			for charger, limit in zip(chargers, limits):
				charger.maxchargecurrent = limit
		else:
			# Balance the limits so they have the same headroom at the top.
			# Each charger is balanced against its neighbour, the one at the
			# end is paired with the one at the start.
			limits = []
			r = chargers[0].maxchargecurrent
			for c1, c2 in zip(chargers, chargers[1:]):
				l, r = ChargerSubsystem._balance_chargers(c1, c2, r, c2.maxchargecurrent)
				limits.append(l)
			l, limits[0] = ChargerSubsystem._balance_chargers(c2, chargers[0], r, limits[0])
			limits.append(l)

			for charger, limit in zip(chargers, limits):
				charger.maxchargecurrent = limit

	def update_values(self):
		# This is called periodically from a timer to update contained
		# solar chargers with values that they track.
		for charger in self:
			try:
				charger.update_values()
			except AttributeError:
				pass

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
	def __init__(self, monitor, service):
		self.monitor = monitor
		self._service = service
		self.bol = BatteryOperationalLimits(self)
		self._dc_current = LowPassFilter((2 * pi)/30, 0)
		self._v = object()

	@property
	def service(self):
		return getattr(MultiService.instance.vebus_service, 'service', None)

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
		return self._dc_current.value

	@property
	def hub_voltage(self):
		return self.monitor.get_value(self.service, '/Hub/ChargeVoltage')

	@property
	def maxchargecurrent(self):
		return self.monitor.get_value(self.service, '/Dc/0/MaxChargeCurrent')

	@maxchargecurrent.setter
	def maxchargecurrent(self, v):
		# If the Multi is not ready, don't write to it just yet
		if self.active and self.maxchargecurrent is not None and v != self._v:
			# The maximum present charge current is 6-parallel 12V 5kva units, 6*220 = 1320A.
			# We will consider 10000A to be impossibly high.
			self.monitor.set_value_async(self.service, '/Dc/0/MaxChargeCurrent', 10000 if v is None else v)
			self._v = v

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

	@property
	def allow_to_charge(self):
		return self.monitor.get_value(self.service, '/Bms/AllowToCharge') != 0

	@property
	def has_vebus_bms(self):
		""" This checks that we have a VE.Bus BMS. """
		return self.monitor.get_value(self.service, '/Bms/BmsType') == 2

	@property
	def has_vebus_bmsv2(self):
		""" Checks that we have v2 of the VE.Bus BMS, but also that we can
		    properly use it, that is we also have an mk3. """
		version = self.monitor.get_value(self.service, '/Devices/Bms/Version')
		atc = self.monitor.get_value(self.service, '/Bms/AllowToCharge')

		# If AllowToCharge is defined, but we have no version, then the Multi
		# is off, but we still have a v2 BMS. V1 goes invalid if the multi
		# is off. Yes, this is kludgy, but it is less kludgy than the
		# fix the other end would require.
		if self.has_vebus_bms and atc is not None and version is None:
			return True

		# Otherwise, if the Multi is on, check the version to see if we should
		# enable v2 functionality.
		return (version or 0) >= 1146100 and \
			self.monitor.get_value(self.service, '/Interfaces/Mk2/ProductName') == 'MK3'

	def update_values(self, limit):
		c = self.monitor.get_value(self.service, '/Dc/0/Current', 0)
		if c is not None:
			# Cap the filter at a limit. If we don't do this, dc currents
			# in excess of our capacity causes a kind of wind-up that delays
			# backing-off when the load drops suddenly.
			if limit is not None:
				c = max(c, -limit)
			self._dc_current.update(c)

class Dvcc(SystemCalcDelegate):
	""" This is the main DVCC delegate object. """
	def __init__(self, sc):
		super(Dvcc, self).__init__()
		self.systemcalc = sc
		self._chargesystem = None
		self._vecan_services = []
		self._timer = None
		self._tickcount = ADJUST
		self._dcsyscurrent = LowPassFilter((2 * pi)/20, 0.0)
		self._internal_mcp = ExpiringValue(3, None) # Max charging power

	def get_input(self):
		return [
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
				'/Bms/AllowToCharge',
				'/Bms/BmsType',
				'/Devices/Bms/Version',
				'/FirmwareFeatures/BolFrame',
				'/Hub4/L1/DoNotFeedInOvervoltage',
				'/Hub4/UseBatteryOvervoltageProtection',
				'/FirmwareVersion',
				'/Interfaces/Mk2/ProductName']),
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
				'/Mgmt/Connection',
				'/Settings/BmsPresent']),
			('com.victronenergy.alternator', [
				'/ProductId',
				'/Dc/0/Voltage',
				'/Dc/0/Current',
				'/Link/NetworkMode',
				'/Link/ChargeVoltage',
				'/Link/ChargeCurrent',
				'/Settings/ChargeCurrentLimit',
				'/State',
				'/FirmwareVersion',
				'/N2kDeviceInstance',
				'/Mgmt/Connection',
				'/Settings/BmsPresent']),
			('com.victronenergy.dcgenset', [
				'/ProductId',
				'/Link/ChargeVoltage',
				'/Link/ChargeCurrent',
				'/Settings/BmsPresent',
				'/Settings/ChargeCurrentLimit']),
			('com.victronenergy.inverter', [
				'/ProductId',
				'/Dc/0/Current',
				'/IsInverterCharger',
				'/Link/NetworkMode',
				'/Link/ChargeVoltage',
				'/Link/ChargeCurrent',
				'/Link/DischargeCurrent',
				'/Settings/ChargeCurrentLimit',
				'/State',
				'/N2kDeviceInstance',
				'/Mgmt/Connection',
				'/Settings/BmsPresent']),
			('com.victronenergy.multi', [
				'/ProductId',
				'/Dc/0/Current',
				'/IsInverterCharger',
				'/Link/ChargeCurrent',
				'/Link/DischargeCurrent',
				'/Link/ChargeVoltageSetpoint',
				'/Link/ChargeVoltageSolarOffset',
				'/Settings/ChargeCurrentLimit',
				'/State',
				'/N2kDeviceInstance',
				'/Mgmt/Connection',
				'/Settings/BmsPresent']),
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
			('bol', '/Settings/Services/Bol', 0, 0, 7),
			('bolsecondary', '/Settings/SystemSetup/DvccControlAllMultis', 0, 0, 1)
		]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._chargesystem = ChargerSubsystem(dbusmonitor)
		self._inverters = InverterSubsystem(dbusmonitor)
		self._multi = Multi(dbusmonitor, dbusservice)

		self._dbusservice.add_path('/Control/SolarChargeVoltage', value=0)
		self._dbusservice.add_path('/Control/SolarChargeCurrent', value=0)
		self._dbusservice.add_path('/Control/EffectiveChargeVoltage', value=None)
		self._dbusservice.add_path('/Dc/Battery/ChargeVoltage', value=None)
		self._dbusservice.add_path('/Control/BmsParameters', value=0)
		self._dbusservice.add_path('/Control/MaxChargeCurrent', value=0)
		self._dbusservice.add_path('/Control/Dvcc', value=self.has_dvcc)
		self._dbusservice.add_path('/Debug/BatteryOperationalLimits/SolarVoltageOffset', value=0, writeable=True)
		self._dbusservice.add_path('/Debug/BatteryOperationalLimits/VebusVoltageOffset', value=0, writeable=True)
		self._dbusservice.add_path('/Debug/BatteryOperationalLimits/CurrentOffset', value=0, writeable=True)
		self._dbusservice.add_path('/Dvcc/Alarms/FirmwareInsufficient', value=0)
		self._dbusservice.add_path('/Dvcc/Alarms/MultipleBatteries', value=0)

	def device_added(self, service, instance, *args, **kwargs):
		service_type = service.split('.')[2]
		if service_type == 'solarcharger':
			self._chargesystem.add_solar_charger(service)
		elif service_type in ('inverter', 'multi'):
			if self._dbusmonitor.get_value(service, '/IsInverterCharger') == 1:
				# Add to both the solarcharger and inverter collections.
				# add_invertercharger returns an object that can be directly
				# added to the inverter collection.
				self._inverters._add_inverter(
					self._chargesystem.add_invertercharger(service))
		elif service_type == 'vecan':
			self._vecan_services.append(service)
		elif service_type == 'alternator':
			self._chargesystem.add_alternator(service)
		elif service_type == 'dcgenset':
			self._chargesystem.add_dcgenset(service)
		elif service_type == 'battery':
			pass # install timer below
		else:
			# Skip timer code below
			return

		if self._timer is None:
			self._timer = GLib.timeout_add(1000, exit_on_error, self._on_timer)

	def device_removed(self, service, instance):
		if service in self._chargesystem:
			self._chargesystem.remove_charger(service)
			# Some solar chargers are inside an inverter
			if service in self._inverters:
				self._inverters.remove_inverter(service)
		elif service in self._vecan_services:
			self._vecan_services.remove(service)
		elif service in self._inverters:
			self._inverters.remove_inverter(service)
		if len(self._chargesystem) == 0 and len(self._vecan_services) == 0 and \
			len(BatteryService.instance.batteries) == 0 and self._timer is not None:
			GLib.source_remove(self._timer)
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
	def internal_maxchargepower(self):
		return self._internal_mcp.get()

	@internal_maxchargepower.setter
	def internal_maxchargepower(self, v):
		self._internal_mcp.set(v)

	@property
	def dcsyscurrent(self):
		""" Return non-zero DC system current, if it is based on
		    a real measurement. If an estimate/calculation, we cannot use it.
		"""
		if self._dbusservice['/Dc/System/MeasurementType'] == 1:
			try:
				v = self._dbusservice['/Dc/Battery/Voltage']
				return self._dcsyscurrent.update(
					float(self._dbusservice['/Dc/System/Power'])/v)
			except (TypeError, ZeroDivisionError):
				pass
		return 0.0

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
		return BatteryService.instance.bms

	@property
	def bms_seen(self):
		return self._chargesystem.want_bms

	def _on_timer(self):
		def update_solarcharger_control_flags(voltage_written, current_written, chargevoltage):
			self._dbusservice['/Control/SolarChargeVoltage'] = voltage_written
			self._dbusservice['/Control/SolarChargeCurrent'] = current_written
			self._dbusservice['/Control/EffectiveChargeVoltage'] = chargevoltage

		bol_support = self.has_dvcc

		self._tickcount -= 1; self._tickcount %= ADJUST

		if not bol_support:
			if self._tickcount > 0: return True

			voltage_written, current_written = self._legacy_update_solarchargers()
			update_solarcharger_control_flags(voltage_written, current_written, None) # Not tracking for non-DVCC case
			self._dbusservice['/Control/BmsParameters'] = 0
			self._dbusservice['/Control/MaxChargeCurrent'] = 0
			self._dbusservice['/Control/Dvcc'] = 0
			self._dbusservice['/Dvcc/Alarms/FirmwareInsufficient'] = 0
			self._dbusservice['/Dvcc/Alarms/MultipleBatteries'] = 0
			self._dbusservice['/Dc/Battery/ChargeVoltage'] = None
			return True


		# BOL/DVCC support below
		self._dbusservice['/Dvcc/Alarms/FirmwareInsufficient'] = int(
			not self._chargesystem.has_externalcontrol_support or (
			self._multi.firmwareversion is not None and self._multi.firmwareversion < VEBUS_FIRMWARE_REQUIRED))
		self._dbusservice['/Dvcc/Alarms/MultipleBatteries'] = int(
			len(BatteryService.instance.bmses) > 1)

		# Update subsystems
		self._chargesystem.update_values()
		self._multi.update_values(self._chargesystem.totalcapacity)

		# Below are things we only do every ADJUST seconds
		if self._tickcount > 0: return True

		# Signal Dvcc support to other processes
		self._dbusservice['/Control/Dvcc'] = 1

		# Check that we have not lost the BMS, if we ever had one.  If the BMS
		# is lost, stop passing information to the solar chargers so that they
		# might time out.
		bms_service = self.bms
		if self.bms_seen and bms_service is None and not self._multi.has_vebus_bmsv2:
			# BMS is lost
			update_solarcharger_control_flags(0, 0, None)
			self._dbusservice['/Dc/Battery/ChargeVoltage'] = None
			return True

		# Get the user current limit, if set
		user_max_charge_current = self._settings['maxchargecurrent']
		if user_max_charge_current < 0: user_max_charge_current = None

		# If there is a BMS, get the charge voltage and current from it
		max_charge_current = None
		charge_voltage = None
		feedback_allowed = self.feedback_allowed
		stop_on_mcc0 = False
		has_bms = bms_service is not None
		if has_bms:
			charge_voltage, max_charge_current, feedback_allowed, stop_on_mcc0 = \
				self._adjust_battery_operational_limits(bms_service, feedback_allowed)

		# Check /Bms/AllowToCharge on the VE.Bus service, and set
		# max_charge_current to zero if charging is not allowed.  Skip this if
		# ESS is involved, then the Multi controls this through the charge
		# voltage. If it is BMS v2, then also set BMS bit so that solarchargers
		# go into #67 if we lose it.
		if self._multi.has_vebus_bms:
			stop_on_mcc0 = True
			has_bms = has_bms or self._multi.has_vebus_bmsv2
			max_charge_current = 10000 if self._multi.allow_to_charge else 0

		# Take the lesser of the BMS and user current limits, wherever they exist
		maximae = [x for x in (user_max_charge_current, max_charge_current) if x is not None]
		max_charge_current = min(maximae) if maximae else None

		# Override the battery charge voltage by taking the lesser of the
		# voltage limits. Only override if the battery supplies one, to prevent
		# a voltage being sent to a Multi in a system without a managed battery.
		# Otherwise the Multi will go into passthru if the user disables this.
		if charge_voltage is not None:
			user_charge_voltage = self._settings['maxchargevoltage']
			if user_charge_voltage > 0:
				charge_voltage = min(charge_voltage, user_charge_voltage)

		# Publish the charge voltage from the BMS (after quirks) to dbus
		self._dbusservice['/Dc/Battery/ChargeVoltage'] = charge_voltage

		# @todo EV What if ESS + OvervoltageFeedIn? In that case there is no
		# charge current control on the MPPTs, but we'll still indicate that
		# the control is active here. Should we?
		self._dbusservice['/Control/MaxChargeCurrent'] = \
			not self._multi.active or self._multi.has_bolframe

		# If there is a measured DC system, the Multi and solarchargers
		# should add extra current for that. Round this to nearest 100mA.
		if max_charge_current is not None and max_charge_current > 0 and not stop_on_mcc0:
			max_charge_current = round(max_charge_current + self.dcsyscurrent, 1)

		# We need to keep a copy of the original value for later. We will be
		# modifying one of them to compensate for vebus current.
		_max_charge_current = max_charge_current

		# If we have vebus current, we have to compensate for it. But if we must
		# stop on MCC=0, then only if the max charge current is above zero.
		# Otherwise leave it unmodified so that the solarchargers are also
		# stopped.
		vebus_dc_current = self._multi.dc_current
		if _max_charge_current is not None and vebus_dc_current is not None and \
				(not stop_on_mcc0 or _max_charge_current > 0) and vebus_dc_current < 0:
			_max_charge_current = ceil(_max_charge_current - vebus_dc_current)

		# Try to push the solar chargers to the vebus-compensated value
		voltage_written, current_written, effective_charge_voltage = \
			self._update_solarchargers_and_vecan(has_bms, charge_voltage,
			_max_charge_current, feedback_allowed, stop_on_mcc0)
		update_solarcharger_control_flags(voltage_written, current_written, effective_charge_voltage)

		# The Multi gets the remainder after subtracting what the solar
		# chargers made. If there is a maximum charge power from another
		# delegate (dynamicess), apply that here.
		if max_charge_current is not None:
			max_charge_current = max(0.0, round(max_charge_current - self._chargesystem.smoothed_current))

		try:
			internal_mcc = self.internal_maxchargepower / self._dbusservice['/Dc/Battery/Voltage']
		except (TypeError, ZeroDivisionError, ValueError):
			pass
		else:
			try:
				max_charge_current = min(x for x in (ceil(internal_mcc), max_charge_current) if x is not None)
			except ValueError:
				pass

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
		stop_on_mcc0 = False
		if quirk is not None:
			# If any quirks are registered for this battery, use that
			# instead.
			cv, mcc, feedback_allowed, stop_on_mcc0 = quirk(self, bms_service, cv, mcc, feedback_allowed)

		# Add debug offsets
		if cv is not None:
			cv = safeadd(cv, self.invertervoltageoffset)
		if mcc is not None:
			mcc = safeadd(mcc, self.currentoffset)
		return cv, mcc, feedback_allowed, stop_on_mcc0

	def _update_battery_operational_limits(self, bms_service, cv, mcc):
		""" This function writes the bms parameters across to the Multi
		    if it exists. Also communicate DCL=0 to inverters. """
		written = 0
		if self._multi.active:
			if cv is not None:
				self._multi.bol.chargevoltage = cv

			if mcc is not None:
				self._multi.bol.maxchargecurrent = mcc
				# Also set the maxchargecurrent, to ensure this is not stuck
				# at some lower value that overrides the intent here.
				try:
					self._multi.maxchargecurrent = max(self._multi.maxchargecurrent, mcc)
				except TypeError:
					pass

			# Copy the rest unmodified
			self._multi.bol.maxdischargecurrent = bms_service.maxdischargecurrent
			self._multi.bol.batterylowvoltage = bms_service.batterylowvoltage
			written = 1

		# Control secondary Multis (systems with more than one) if configured
		if self._settings['bolsecondary']:
			self._update_secondary_multis(cv, bms_service.maxdischargecurrent)

		# Also control inverters if BMS stops discharge
		if len(self._inverters):
			self._inverters.set_maxdischargecurrent(bms_service.maxdischargecurrent)
			written = 1

		return written

	def _update_secondary_multis(self, cv, dcl):
		if cv is not None:
			for m in MultiService.instance.othermultis:
				self._dbusmonitor.set_value_async(m.service,
					'/BatteryOperationalLimits/MaxChargeVoltage', cv)
		if dcl is not None:
			for m in MultiService.instance.othermultis:
				self._dbusmonitor.set_value_async(m.service,
					'/BatteryOperationalLimits/MaxDischargeCurrent', dcl)

	@property
	def feedback_allowed(self):
		# Feedback allowed is defined as 'ESS present and FeedInOvervoltage is
		# enabled'. This ignores other setups which allow feedback: hub-1.
		return self.has_ess_assistant and self._multi.ac_connected and \
			self._dbusmonitor.get_value('com.victronenergy.settings',
				'/Settings/CGwacs/OvervoltageFeedIn') == 1

	def _update_solarchargers_and_vecan(self, has_bms, bms_charge_voltage, max_charge_current, feedback_allowed, stop_on_mcc0):
		""" This function updates the solar chargers and VE.Can connected
		    devices such as Multi-RS. Parameters related to the Multi are
		    handled elsewhere. """

		# If the vebus service does not provide a charge voltage setpoint (so
		# no ESS/Hub-1/Hub-4), we use the max charge voltage provided by the
		# BMS (if any). This will probably prevent feedback, but that is
		# probably not allowed anyway.
		charge_voltage = vecan_voltage = None
		if self._multi.active:
			charge_voltage = vecan_voltage = self._multi.hub_voltage

		# Next try a voltage from an inverter/charger
		if charge_voltage is None:
			if bms_charge_voltage is None:
				charge_voltage = self._inverters.chargevoltagesetpoint
				# vecan_voltage = None
			else:
				# Add solar offset to BMS voltage
				charge_voltage = safeadd(bms_charge_voltage, self._inverters.solaroffset)
				vecan_voltage = bms_charge_voltage # VE.Can chargers adds own offset

		# Use BMS charge voltage as is
		if charge_voltage is None and bms_charge_voltage is not None:
			charge_voltage = vecan_voltage = bms_charge_voltage

		# Add the debug solar offset
		if charge_voltage is not None:
			try:
				charge_voltage += self.solarvoltageoffset
				vecan_voltage += self.solarvoltageoffset
			except (ValueError, TypeError):
				pass

		if charge_voltage is None and max_charge_current is None:
			return 0, 0, None

		voltage_written, current_written, network_mode = self._chargesystem.set_networked(
			has_bms, bms_charge_voltage, charge_voltage,
			max_charge_current, feedback_allowed, stop_on_mcc0)

		# Write the voltage to VE.Can. Also update the networkmode. If there is
		# no voltage to write to VE.Can, then still set the networkmode so that
		# RS devices will send us their Charge Voltage.
		if vecan_voltage is None:
			for service in self._vecan_services:
				try:
					self._dbusmonitor.set_value_async(service, '/Link/NetworkMode',
						1 | (0 if max_charge_current is None else 4))
				except DBusException:
					pass
		else:
			for service in self._vecan_services:
				try:
					# In case there is no path at all, the set_value below will
					# raise an DBusException which we will ignore cheerfully. If we
					# cannot set the NetworkMode there is no point in setting the
					# ChargeVoltage.
					self._dbusmonitor.set_value_async(service, '/Link/NetworkMode', network_mode)
					self._dbusmonitor.set_value_async(service, '/Link/ChargeVoltage', vecan_voltage)
					voltage_written = 1
				except DBusException:
					pass

		return voltage_written, current_written, charge_voltage

	def _legacy_update_solarchargers(self):
		""" This is the old implementation we used before DVCC. It is kept
		    here so we can fall back to it where DVCC is not fully supported,
			and to avoid maintaining two copies of systemcalc. """

		max_charge_current = None
		for battery in BatteryService.instance.batteries:
			max_charge_current = safeadd(max_charge_current, battery.maxchargecurrent)

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
		for charger in self._chargesystem:
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

		# The below is different to the non-legacy case above, where the voltage
		# the com.victronenergy.vecan.* service instead.
		if charge_voltage is not None and self._chargesystem.has_vecan_chargers:
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
