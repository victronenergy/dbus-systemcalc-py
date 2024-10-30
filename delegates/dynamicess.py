from datetime import datetime
from gi.repository import GLib
from delegates.base import SystemCalcDelegate
from delegates.batterysoc import BatterySoc
from delegates.schedule import ScheduledWindow
from delegates.dvcc import Dvcc
from delegates.batterylife import BatteryLife
from delegates.batterylife import State as BatteryLifeState
from delegates.chargecontrol import ChargeControl
from enum import Enum

NUM_SCHEDULES = 12
INTERVAL = 5
SELLPOWER = -32000
HUB4_SERVICE = 'com.victronenergy.hub4'
ERROR_TIMEOUT = 60

MODES = {
       0: 'Off',
       1: 'Auto',
       2: 'Buy',
       3: 'Sell',
       4: 'Local'
}

ERRORS = {
	0: 'No error',
	1: 'No ESS',
	2: 'ESS mode',
	3: 'No matching schedule',
	4: 'SOC low',
	5: 'Battery capacity unset'
}

class Strategy(int, Enum):
	TARGETSOC = 0
	SELFCONSUME = 1

class Flags(int, Enum):
	NONE = 0
	FASTCHARGE = 1

class EssDevice(object):
	def __init__(self, delegate, monitor, service):
		self.delegate = delegate
		self.monitor = monitor
		self.service = service

	@property
	def available(self):
		return True

	def check_conditions(self):
		""" Check that the conditions are right to use this device. If not,
		    return a non-zero error code. """
		return 0

	def charge(self, flags, restrictions, rate, allow_feedin):
		raise NotImplementedError("charge")

	def discharge(self, flags, restrictions, rate, allow_feedin):
		raise NotImplementedError("discharge")

	def idle(self, allow_feedin):
		raise NotImplementedError("idle")

	def self_consume(self, restrictions, allow_feedin):
		raise NotImplementedError("self_consume")

	def deactivate(self):
		raise NotImplementedError("deactivate")

	@property
	def acpv(self):
		return (self.delegate._dbusservice['/Ac/PvOnGrid/L1/Power'] or 0) + \
			(self.delegate._dbusservice['/Ac/PvOnGrid/L2/Power'] or 0) + \
			(self.delegate._dbusservice['/Ac/PvOnGrid/L3/Power'] or 0) + \
			(self.delegate._dbusservice['/Ac/PvOnOutput/L1/Power'] or 0) + \
			(self.delegate._dbusservice['/Ac/PvOnOutput/L2/Power'] or 0) + \
			(self.delegate._dbusservice['/Ac/PvOnOutput/L3/Power'] or 0)

	@property
	def pvpower(self):
		return self.delegate._dbusservice['/Dc/Pv/Power'] or 0

class VebusDevice(EssDevice):
	@property
	def available(self):
		return Dvcc.instance.has_ess_assistant

	@property
	def hub4mode(self):
		return self.monitor.get_value('com.victronenergy.settings',
                '/Settings/CGwacs/Hub4Mode')

	@property
	def maxfeedinpower(self):
		l = self.monitor.get_value('com.victronenergy.settings',
                '/Settings/CGwacs/MaxFeedInPower')
		return SELLPOWER if l < 0 else max(-l, SELLPOWER)

	@property
	def minsoc(self):
		# The BatteryLife delegate puts the active soc limit here.
		return self.delegate._dbusservice['/Control/ActiveSocLimit']

	@property
	def consumption(self):
		return max(0, (self.delegate._dbusservice['/Ac/Consumption/L1/Power'] or 0) +
			(self.delegate._dbusservice['/Ac/Consumption/L2/Power'] or 0) +
			(self.delegate._dbusservice['/Ac/Consumption/L3/Power'] or 0))

	def _set_feedin(self, allow_feedin):
		self.monitor.set_value_async(HUB4_SERVICE,
			'/Overrides/FeedInExcess', 2 if allow_feedin else 1)

	def _set_charge_power(self, v):
		Dvcc.instance.internal_maxchargepower = None if v is None else max(v, 50)

	def check_conditions(self):
		# Can't do anything unless we have a minsoc, and the ESS assistant
		if not Dvcc.instance.has_ess_assistant:
			return 1 # No ESS

		if self.minsoc is None:
			return 4 # SOC low

		# In Keep-Charged mode or external control, no point in doing anything
		if BatteryLife.instance.state == BatteryLifeState.KeepCharged or self.hub4mode == 3:
			return 2 # ESS mode is wrong

		return 0

	def charge(self, flags, restrictions, rate, allow_feedin):
		batteryimport = not restrictions & 2

		self._set_feedin(allow_feedin)

		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 1)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)

		if flags & Flags.FASTCHARGE or rate is None:
			self._set_charge_power(None)
			return None
		else:
			# Calculate how fast to buy. Multi is given the remainder
			# after subtracting PV power.
			self._set_charge_power(max(0.0, rate - self.pvpower) if batteryimport else 0.9 * self.acpv)
			return rate

	def discharge(self, flags, restrictions, rate, allow_feedin):
		batteryexport = not restrictions & 1

		self._set_feedin(allow_feedin)
		self._set_charge_power(None)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)

		if allow_feedin:
			# Calculate how fast to sell. If exporting the battery to the grid
			# is allowed, then export rate plus whatever DC-coupled PV is
			# making. If exporting the battery is not allowed, then limit that
			# to DC-coupled PV plus local consumption.
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', self.maxfeedinpower)
			if flags & Flags.FASTCHARGE:
				self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1)
				return None
			else:
				self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower',
					(rate + self.pvpower if rate else 1.0) \
					if batteryexport \
					else self.pvpower + self.consumption + 1.0) # 1.0 to allow selling overvoltage
				return rate
		else:
			# If we are not allowed to sell to the grid, then we effectively do
			# normal ESS here.
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', 0) # Normal ESS, no feedin
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1)
			return rate

	def idle(self, allow_feedin):
		self._set_feedin(allow_feedin)
		self._set_charge_power(None)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)

		if allow_feedin:
			# This keeps battery idle by not allowing more power to be taken
			# from the DC bus than what DC-coupled PV provides.
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower',
				max(1.0, round(0.9*self.pvpower)))
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', self.maxfeedinpower)
		else:
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', 0) # Normal ESS
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', max(1.0, self.pvpower))

		return None

	def self_consume(self, restrictions, allow_feedin):
		batteryexport = not restrictions & 1
		batteryimport = not restrictions & 2

		self._set_feedin(allow_feedin)

		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None) # Normal ESS
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)

		# If importing into battery is allowed, then no restriction, let the
		# setpoint determine that. If disallowed, then only AC-coupled PV may
		# be imported into battery.
		self._set_charge_power(None if batteryimport else self.acpv)

		# If exporting battery to grid is restricted, then limit DC-AC
		# conversion to pvpower plus consumption. Otherwise unrestricted
		# and even a negative ESS grid setpoint will cause power to go to
		# the grid.
		dcp = -1.0 if batteryexport else max(self.pvpower + self.consumption, 1.0)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', dcp)

	def deactivate(self):
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 0)
		self._set_charge_power(None)

class MultiRsDevice(EssDevice):
	@property
	def available(self):
		return self.monitor.get_value(self.service, '/Capabilities/HasDynamicEssSupport') == 1

	@property
	def minsoc(self):
		# The minsoc is here on the Multi-RS
		return self.monitor.get_value(self.service, '/Settings/Ess/MinimumSocLimit')

	@property
	def mode(self):
		return self.monitor.get_value(self.service, '/Settings/Ess/Mode')

	def check_conditions(self):
		# Not in optimised mode, no point in doing anything
		if self.mode not in (0, 1):
			return 2 # ESS mode is wrong
		if self.minsoc is None:
			return 4 # SOC low, happens during firmware updates
		return 0

	def charge(self, flags, restrictions, rate, allow_feedin):
		batteryimport = not restrictions & 2

		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin))
		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 1)
		if batteryimport:
			if rate is None or (flags & Flags.FASTCHARGE):
				self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', 15000)
			else:
				self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', max(0, rate))
		else:
			# No charging from grid, allow only acpv to be converted.
			self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', self.acpv)

		return rate

	def discharge(self, flags, restrictions, rate, allow_feedin):
		batteryexport = not restrictions & 1
		if batteryexport:
			self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin))
			self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 1)
			if rate is None or (flags & Flags.FASTCHARGE):
				self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', -15000)
			else:
				self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', -max(0, rate+self.pvpower))
		else:
			# We can only discharge into loads, therefore simply run
			# self-consumption
			self.self_consume(restrictions, allow_feedin)

		return rate

	def idle(self, allow_feedin):
		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin))
		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 1)
		self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', -max(0, self.pvpower))

	def self_consume(self, restrictions, allow_feedin):
		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin))
		self.monitor.set_value_async(self.service, '/Ess/AcPowerSetpoint', 0)
		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 0)

	def deactivate(self):
		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', 0)
		self.monitor.set_value_async(self.service, '/Ess/AcPowerSetpoint', 0)
		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 0)
		self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', 0)

class DynamicEssWindow(ScheduledWindow):
	def __init__(self, start, duration, soc, allow_feedin, restrictions, strategy, flags):
		super(DynamicEssWindow, self).__init__(start, duration)
		self.soc = soc
		self.allow_feedin = allow_feedin
		self.restrictions = restrictions
		self.strategy = strategy
		self.flags = flags

	def __repr__(self):
		return "Start: {}, Stop: {}, Soc: {}".format(
			self.start, self.stop, self.soc)

class DynamicEss(SystemCalcDelegate, ChargeControl):
	control_priority = 0
	_get_time = datetime.now

	def __init__(self):
		super(DynamicEss, self).__init__()
		self.charge_hysteresis = 0
		self.discharge_hysteresis = 0
		self.prevsoc = None
		self.chargerate = None # How fast to charge/discharge to get to the next target
		self._timer = None
		self._devices = {}
		self._device = None
		self._errorcode = 0
		self._errortimer = ERROR_TIMEOUT


	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(DynamicEss, self).set_sources(dbusmonitor, settings, dbusservice)
		# Capabilities, 1 = supports charge/discharge restrictions
		#               2 = supports self-consumption strategy
		#               4 = supports fast-charge strategy
		self._dbusservice.add_path('/DynamicEss/Capabilities', value=7)
		self._dbusservice.add_path('/DynamicEss/Active', value=0,
			gettextcallback=lambda p, v: MODES.get(v, 'Unknown'))
		self._dbusservice.add_path('/DynamicEss/TargetSoc', value=None,
			gettextcallback=lambda p, v: '{}%'.format(v))
		self._dbusservice.add_path('/DynamicEss/ErrorCode', value=0,
			gettextcallback=lambda p, v: ERRORS.get(v, 'Unknown'))
		self._dbusservice.add_path('/DynamicEss/LastScheduledStart', value=None)
		self._dbusservice.add_path('/DynamicEss/LastScheduledEnd', value=None)
		self._dbusservice.add_path('/DynamicEss/ChargeRate', value=None)
		self._dbusservice.add_path('/DynamicEss/Strategy', value=None)
		self._dbusservice.add_path('/DynamicEss/Restrictions', value=None)
		self._dbusservice.add_path('/DynamicEss/AllowGridFeedIn', value=None)

		if self.mode > 0:
			self._timer = GLib.timeout_add(INTERVAL * 1000, self._on_timer)

	def get_settings(self):
		# Settings for DynamicEss
		path = '/Settings/DynamicEss'

		settings = [
			("dess_mode", path + "/Mode", 0, 0, 4),
			("dess_capacity", path + "/BatteryCapacity", 0.0, 0.0, 1000.0),
			("dess_efficiency", path + "/SystemEfficiency", 90.0, 50.0, 100.0),
			# 0=None, 1=disallow export, 2=disallow import
			("dess_restrictions", path + "/Restrictions", 0, 0, 3),
			("dess_fullchargeinterval", path + "/FullChargeInterval", 14, 0, 0),
			("dess_fullchargeduration", path + "/FullChargeDuration", 2, 0, 0),
		]

		for i in range(NUM_SCHEDULES):
			settings.append(("dess_start_{}".format(i),
				path + "/Schedule/{}/Start".format(i), 0, 0, 0))
			settings.append(("dess_duration_{}".format(i),
				path + "/Schedule/{}/Duration".format(i), 0, 0, 0))
			settings.append(("dess_soc_{}".format(i),
				path + "/Schedule/{}/Soc".format(i), 100, 0, 100))
			settings.append(("dess_discharge_{}".format(i),
				path + "/Schedule/{}/AllowGridFeedIn".format(i), 0, 0, 1))
			settings.append(("dess_restrictions_{}".format(i),
				path + "/Schedule/{}/Restrictions".format(i), 0, 0, 3))
			settings.append(("dess_strategy_{}".format(i),
				path + "/Schedule/{}/Strategy".format(i), 0, 0, 1))
			settings.append(("dess_flags_{}".format(i),
				path + "/Schedule/{}/Flags".format(i), 0, 0, 1))

		return settings

	def get_input(self):
		return [
			(HUB4_SERVICE, ['/Overrides/ForceCharge',
				'/Overrides/MaxDischargePower', '/Overrides/Setpoint',
				'/Overrides/FeedInExcess']),
			('com.victronenergy.acsystem', [
				 '/Capabilities/HasDynamicEssSupport',
				 '/Ess/AcPowerSetpoint',
				 '/Ess/InverterPowerSetpoint',
				 '/Ess/UseInverterPowerSetpoint',
				 '/Ess/DisableFeedIn',
				 '/Settings/Ess/Mode',
				 '/Settings/Ess/MinimumSocLimit']),
			('com.victronenergy.settings', [
				'/Settings/CGwacs/Hub4Mode',
				'/Settings/CGwacs/MaxFeedInPower'])
		]

	def get_output(self):
		return [('/DynamicEss/Available', {'gettext': '%s'})]

	def _set_device(self):
		# Use first device in dict, there should be just one
		for self._device in self._devices.values():
			break
		else:
			self._device = None

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.vebus.'):
			# Only one device, controlled via hub4control
			if not any(isinstance(s, VebusDevice) for s in self._devices.values()):
				self._devices[service] = VebusDevice(self, self._dbusmonitor, service)
				self._set_device()
		elif service.startswith('com.victronenergy.acsystem.'):
			self._devices[service] = MultiRsDevice(self, self._dbusmonitor, service)
			self._set_device()

	def device_removed(self, service, instance):
		try:
			del self._devices[service]
		except KeyError:
			pass
		else:
			self._set_device()


	def settings_changed(self, setting, oldvalue, newvalue):
		if setting == 'dess_mode':
			if oldvalue == 0 and newvalue > 0:
				self._timer = GLib.timeout_add(INTERVAL * 1000, self._on_timer)

	def windows(self):
		starttimes = (self._settings['dess_start_{}'.format(i)] for i in range(NUM_SCHEDULES))
		durations = (self._settings['dess_duration_{}'.format(i)] for i in range(NUM_SCHEDULES))
		socs = (self._settings['dess_soc_{}'.format(i)] for i in range(NUM_SCHEDULES))
		discharges = (self._settings['dess_discharge_{}'.format(i)] for i in range(NUM_SCHEDULES))
		restrictions = (self._settings['dess_restrictions_{}'.format(i)] for i in range(NUM_SCHEDULES))
		strategies = (self._settings['dess_strategy_{}'.format(i)] for i in range(NUM_SCHEDULES))
		wflags = (self._settings['dess_flags_{}'.format(i)] for i in range(NUM_SCHEDULES))

		for start, duration, soc, discharge, restrict, strategy, flags in zip(starttimes, durations, socs, discharges, restrictions, strategies, wflags):
			if start > 0:
				yield DynamicEssWindow(
					datetime.fromtimestamp(start), duration, soc, discharge, restrict, strategy, flags)

	@property
	def mode(self):
		return self._settings['dess_mode']

	@property
	def active(self):
		return self._dbusservice['/DynamicEss/Active']

	@active.setter
	def active(self, v):
		self._dbusservice['/DynamicEss/Active'] = v

	@property
	def errorcode(self):
		return self._errorcode

	@errorcode.setter
	def errorcode(self, v):
		self._errorcode = v
		if v == 0:
			# Errors clear immediately
			self._dbusservice['/DynamicEss/ErrorCode'] = 0
			self._errortimer = ERROR_TIMEOUT
		elif self._errortimer == 0:
			# Set the error after it has been non-zero for more than
			# ERROR_TIMEOUT
			self._dbusservice['/DynamicEss/ErrorCode'] = v
		else:
			# Count down
			self._errortimer = max(self._errortimer - INTERVAL, 0)

	@property
	def targetsoc(self):
		return self._dbusservice['/DynamicEss/TargetSoc']

	@targetsoc.setter
	def targetsoc(self, v):
		self._dbusservice['/DynamicEss/TargetSoc'] = v

	@property
	def soc(self):
		return BatterySoc.instance.soc

	@property
	def capacity(self):
		return self._settings["dess_capacity"]

	@property
	def restrictions(self):
		return self._settings["dess_restrictions"]

	def update_chargerate(self, now, end, percentage):
		""" now is current time, end is end of slot, percentage is amount of battery
		    we want to dump before then. """

		# Only update the charge rate if a new soc value has to be considered
		if self.chargerate is None or self.soc != self.prevsoc:
			try:
				# a Watt is a Joule-second, a Wh is 3600 joules.
				# Capacity is kWh, so multiply by 100, percentage needs division by 100, therefore 36000.
				chargerate = round(1.1 * (percentage * self.capacity * 36000) / abs((end - now).total_seconds()))
				self.chargerate = chargerate if self.chargerate is None else max(self.chargerate, chargerate)
				self.prevsoc = self.soc
			except ZeroDivisionError:
				self.chargerate = None

		self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate

	def _on_timer(self):
		# If DESS was disabled, deactivate and kill timer.
		if self.mode in (0, 2, 3): # Old buy/sell states now also means off
			self.deactivate(0) # No error
			return False

		def bail(code):
			self.release_control()
			self.active = 0 # Off
			self.errorcode = code
			self.targetsoc = None

		if self.capacity == 0.0:
			bail(5) # Capacity not set
			return True

		if self._device is None:
			bail(1) # No ESS
			return True

		if self.soc is None:
			bail(4) # Low SOC, can happen during firmware updates
			return True

		errorcode = self._device.check_conditions()
		if errorcode != 0:
			bail(errorcode)
			return True

		now = self._get_time()
		start = None
		stop = None
		windows = list(self.windows())

		for w in windows:
			# Keep track of maximum available schedule
			if start is None or w.start > start:
				start = w.start
				stop = w.stop

		self._dbusservice['/DynamicEss/LastScheduledStart'] = None if start is None else int(datetime.timestamp(start))
		self._dbusservice['/DynamicEss/LastScheduledEnd'] = None if stop is None else int(datetime.timestamp(stop))

		for w in windows:
			if now in w and self.acquire_control():
				self.active = 1 # Auto
				self.errorcode = 0 # No error

				# Set some paths on dbus for easier debugging
				restrictions = w.restrictions | self.restrictions
				self._dbusservice['/DynamicEss/Strategy'] = w.strategy
				self._dbusservice['/DynamicEss/Restrictions'] = restrictions
				self._dbusservice['/DynamicEss/AllowGridFeedIn'] = int(w.allow_feedin)

				if w.strategy == Strategy.SELFCONSUME:
					self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
					self.targetsoc = None
					self._device.self_consume(restrictions, w.allow_feedin)
					break # Out of FOR loop

				# Below here, strategy is Strategy.TARGETSOC
				if self.targetsoc != w.soc:
					self.chargerate = None # For recalculation
				self.targetsoc = w.soc

				# When 100% is requested, don't go into idle mode
				if self.soc + self.charge_hysteresis < w.soc or w.soc >= 100: # Charge
					self.charge_hysteresis = 0
					self.discharge_hysteresis = 1
					self.update_chargerate(now, w.stop, abs(self.soc - w.soc))
					self._dbusservice['/DynamicEss/ChargeRate'] = \
						self._device.charge(w.flags, restrictions,
						self.chargerate, w.allow_feedin)
				else: # Discharge or idle
					self.charge_hysteresis = 1
					if self.soc - self.discharge_hysteresis > max(w.soc, self._device.minsoc): # Discharge
						self.discharge_hysteresis = 0
						self.update_chargerate(now, w.stop, abs(self.soc - w.soc))
						self._dbusservice['/DynamicEss/ChargeRate'] = \
							self._device.discharge(w.flags, restrictions,
							self.chargerate, w.allow_feedin)
					else: # battery idle
						# SOC/target-soc needs to move 1% to move out of idle
						# zone
						self.discharge_hysteresis = 1
						self._dbusservice['/DynamicEss/ChargeRate'] = \
							self._device.idle(w.allow_feedin)

				break # out of for loop
		else:
			# No matching windows
			if self.active or self.errorcode != 3:
				self.deactivate(3)

		return True

	def deactivate(self, reason):
		try:
			self._device.deactivate()
		except AttributeError:
			pass
		self.release_control()
		self.active = 0 # Off
		self.errorcode = reason
		self.targetsoc = None
		self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
		self._dbusservice['/DynamicEss/Strategy'] = None
		self._dbusservice['/DynamicEss/Restrictions'] = None
		self._dbusservice['/DynamicEss/AllowGridFeedIn'] = None

	def update_values(self, newvalues):
		# Indicate whether this system has DESS capability. Presently
		# that means it has ESS capability.
		try:
			newvalues['/DynamicEss/Available'] = int(self._device.available)
		except AttributeError:
			newvalues['/DynamicEss/Available'] = 0
