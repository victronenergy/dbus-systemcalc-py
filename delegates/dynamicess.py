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

class DynamicEssWindow(ScheduledWindow):
	def __init__(self, start, duration, soc, allow_feedin, restrictions, strategy, flags):
		super(DynamicEssWindow, self).__init__(start, duration)
		self.soc = soc
		self.allow_feedin = allow_feedin
		self.restrictions = restrictions
		self.strategy = strategy
		self.flags = flags

	@property
	def batteryexport(self):
		return not self.restrictions & 1 # Disallow battery export

	@property
	def batteryimport(self):
		return not self.restrictions & 2 # Disallow battery import

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

		if self.mode > 0:
			self._timer = GLib.timeout_add(INTERVAL * 1000, self._on_timer)

	def get_settings(self):
		# Settings for DynamicEss
		path = '/Settings/DynamicEss'

		settings = [
			("dess_mode", path + "/Mode", 0, 0, 4),
			("dess_capacity", path + "/BatteryCapacity", 0.0, 0.0, 1000.0),
			("dess_efficiency", path + "/SystemEfficiency", 90.0, 0.0, 100.0),
			# 0=None, 1=disallow export, 2=disallow import
			("dess_restrictions", path + "/Restrictions", 0, 0, 3),
			("dess_fullchargeinterval", path + "/FullChargeInterval", 7, 0, 0),
			("dess_fullchargeduration", path + "/FullChargeDuration", 7, 0, 0),
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
			('com.victronenergy.settings', [
				'/Settings/CGwacs/Hub4Mode',
				'/Settings/CGwacs/MaxFeedInPower'])
		]

	def get_output(self):
		return [('/DynamicEss/Available', {'gettext': '%s'})]

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
	def hub4mode(self):
		return self._dbusmonitor.get_value('com.victronenergy.settings',
                '/Settings/CGwacs/Hub4Mode')

	@property
	def maxfeedinpower(self):
		l = self._dbusmonitor.get_value('com.victronenergy.settings',
                '/Settings/CGwacs/MaxFeedInPower')
		return SELLPOWER if l < 0 else max(-l, SELLPOWER)

	@property
	def mode(self):
		return self._settings['dess_mode']

	@property
	def minsoc(self):
		# The BatteryLife delegate puts the active soc limit here.
		return self._dbusservice['/Control/ActiveSocLimit']

	@property
	def active(self):
		return self._dbusservice['/DynamicEss/Active']

	@active.setter
	def active(self, v):
		self._dbusservice['/DynamicEss/Active'] = v

	@property
	def errorcode(self):
		return self._dbusservice['/DynamicEss/ErrorCode']

	@errorcode.setter
	def errorcode(self, v):
		self._dbusservice['/DynamicEss/ErrorCode'] = v

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
	def pvpower(self):
		return self._dbusservice['/Dc/Pv/Power'] or 0

	@property
	def consumption(self):
		return max(0, (self._dbusservice['/Ac/Consumption/L1/Power'] or 0) +
			(self._dbusservice['/Ac/Consumption/L2/Power'] or 0) +
			(self._dbusservice['/Ac/Consumption/L3/Power'] or 0))

	@property
	def acpv(self):
		return (self._dbusservice['/Ac/PvOnGrid/L1/Power'] or 0) + \
			(self._dbusservice['/Ac/PvOnGrid/L2/Power'] or 0) + \
			(self._dbusservice['/Ac/PvOnGrid/L3/Power'] or 0) + \
			(self._dbusservice['/Ac/PvOnOutput/L1/Power'] or 0) + \
			(self._dbusservice['/Ac/PvOnOutput/L2/Power'] or 0) + \
			(self._dbusservice['/Ac/PvOnOutput/L3/Power'] or 0)

	@property
	def capacity(self):
		return self._settings["dess_capacity"]

	@property
	def batteryexport(self):
		return not self._settings["dess_restrictions"] & 1 # Disallow battery export

	@property
	def batteryimport(self):
		return not self._settings["dess_restrictions"] & 2 # Disallow battery import

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

	def set_charge_power(self, v):
		Dvcc.instance.internal_maxchargepower = None if v is None else max(v, 50)

	def _on_timer(self):
		# If DESS was disabled, deactivate and kill timer.
		if self.mode == 0:
			self.deactivate(0) # No error
			return False

		# Can't do anything unless we have an SOC, and the ESS assistant
		if self.soc is None or self.minsoc is None:
			self.release_control()
			self.active = 0 # Off
			self.errorcode = 4 # SOC low
			self.targetsoc = None
			return True

		if not Dvcc.instance.has_ess_assistant:
			self.release_control()
			self.active = 0 # Off
			self.errorcode = 1 # No ESS
			self.targetsoc = None
			return True

		if self.capacity == 0.0:
			self.release_control()
			self.active = 0 # Off
			self.errorcode = 5 # Capacity not set
			self.targetsoc = None
			return True

		# In Keep-Charged mode or external control, no point in doing anything
		if BatteryLife.instance.state == BatteryLifeState.KeepCharged or self.hub4mode == 3:
			self.release_control()
			self.active = 0 # Off
			self.errorcode = 2 # ESS mode is wrong
			self.targetsoc = None
			return True

		if self.mode == 2 and self.acquire_control(): # BUY
			self.active = 2
			self.errorcode = 0 # No error
			self.targetsoc = None
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 1)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 1)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
			return True

		if self.mode == 3 and self.acquire_control(): # SELL
			self.active = 3
			self.errorcode = 0 # No error
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 2)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', self.maxfeedinpower)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
			return True

		# self.mode == 1 or self.mode == 4 (Auto) below here
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

				# If schedule allows for feed-in, enable that now.
				self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess',
					2 if w.allow_feedin else 1)

				if w.strategy == Strategy.SELFCONSUME:
					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None) # Normal ESS
					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)

					# If importing into battery is allowed, then no restriction, let the
					# setpoint determine that. If disallowed, then only AC-coupled PV may
					# be imported into battery.
					self.set_charge_power(None if (self.batteryimport and w.batteryimport) else self.acpv)

					# If above window SOC, then normal ESS for reaching the
					# ESS grid setpoint. Unless there are export restrictions,
					# then limited to the pvpower and the local consumption. So
					# a negative setpoint cannot cause power to go to the grid.
					# If below SOC, limit to 90% of PV power only.
					dcp = -1.0 if (self.batteryexport and w.batteryexport) \
						else max(self.pvpower + self.consumption, 1.0)

					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', dcp)
					break # Out of FOR loop

				# Below here, strategy is Strategy.TARGETSOC
				if self.targetsoc != w.soc:
					self.chargerate = None # For recalculation
				self.targetsoc = w.soc

				# When 100% is requested, don't go into idle mode
				if self.soc + self.charge_hysteresis < w.soc or w.soc >= 100: # Charge
					self.charge_hysteresis = 0
					self.discharge_hysteresis = 1
					self.errorcode = 0 # No error
					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 1)
					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)

					if w.flags & Flags.FASTCHARGE:
						self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
						self.set_charge_power(None)
					else:
						# Calculate how fast to buy. Multi is given the remainder
						# after subtracting PV power.
						self.update_chargerate(now, w.stop, abs(self.soc - w.soc))
						self.set_charge_power(max(0.0, self.chargerate - self.pvpower) if (self.batteryimport and w.batteryimport) else 0.9 * self.acpv)
				else: # Discharge or idle
					self.charge_hysteresis = 1
					self.set_charge_power(None)
					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)

					self.errorcode = 0 # No error
					if self.soc - self.discharge_hysteresis > max(w.soc, self.minsoc): # Discharge
						self.discharge_hysteresis = 0

						if w.allow_feedin:
							# Calculate how fast to sell. If exporting the battery
							# to the grid is allowed, then export chargerate plus
							# whatever DC-coupled PV is making. If exporting the
							# battery is not allowed, then limit that to DC-coupled
							# PV plus local consumption.
							self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', self.maxfeedinpower)
							if w.flags & Flags.FASTCHARGE:
								self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1)
							else:
								self.update_chargerate(now, w.stop, abs(self.soc - w.soc))
								self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower',
									(self.chargerate + self.pvpower
										if self.chargerate else 1.0) if (self.batteryexport and w.batteryexport) \
									else self.pvpower + self.consumption + 1.0) # 1.0 to allow selling overvoltage
						else:
							# If we are not allowed to sell to the grid, then we
							# effectively do normal ESS here.
							self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
							self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', 0) # Normal ESS, no feedin
							self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1)

					else: # battery idle
						# SOC/target-soc needs to move 1% to move out of idle
						# zone
						self.discharge_hysteresis = 1
						self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None

						if w.allow_feedin:
							# This keeps battery idle by not allowing more power
							# to be taken from the DC bus than what DC-coupled
							# PV provides.
							self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower',
								max(1.0, round(0.9*self.pvpower)))
							self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', self.maxfeedinpower)
						else:
							self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', 0) # Normal ESS
							self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', max(1.0, self.pvpower))

				break # out of for loop
		else:
			# No matching windows
			if self.active:
				self.deactivate(3)

		return True

	def deactivate(self, reason):
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 0)
		self.set_charge_power(None)
		self.release_control()
		self.active = 0 # Off
		self.errorcode = reason
		self.targetsoc = None
		self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None

	def update_values(self, newvalues):
		# Indicate whether this system has DESS capability. Presently
		# that means it has ESS capability.
		newvalues['/DynamicEss/Available'] = int(Dvcc.instance.has_ess_assistant)
