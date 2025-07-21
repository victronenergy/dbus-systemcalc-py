from datetime import datetime, timedelta
import random
from gi.repository import GLib # type: ignore
from delegates.base import SystemCalcDelegate
from delegates.batterysoc import BatterySoc
from delegates.schedule import ScheduledWindow
from delegates.dvcc import Dvcc
from delegates.batterylife import BatteryLife
from delegates.batterylife import State as BatteryLifeState
from delegates.chargecontrol import ChargeControl
from enum import Enum
from time import time
import json
import logging
logger = logging.getLogger(__name__)

NUM_SCHEDULES = 48
INTERVAL = 5
HUB4_SERVICE = 'com.victronenergy.hub4'
ERROR_TIMEOUT = 60
MAX_FEEDIN_VALUE = 96000
TRANSITION_STATE_THRESHOLD = 90.0

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
	TARGETSOC = 0		#ME-Coping: grid / grid
	SELFCONSUME = 1     #ME-Coping: bat  / bat
	PROBATTERY = 2      #ME-Coping: grid / bat
	PROGRID = 3         #ME-Coping: bat  / grid

class OperatingMode(int, Enum):
	UNKNOWN = -1
	TRADEMODE = 0
	GREENMODE = 1

class Flags(int, Enum):
	NONE = 0
	FASTCHARGE = 1

class Restrictions(int, Enum):
	NONE = 0
	BAT2GRID = 1
	GRID2BAT = 2

class ChangeIndicator(int, Enum):
	NONE = 0
	RISING = 1
	FALLING = 2
	BECAME_TRUE = 3
	BECAME_FALSE = 4
	CHANGED = 5

class ReactiveStrategy(int, Enum):
	#do not re-number, external applications rely on this mapping.
	SCHEDULED_SELFCONSUME = 1
	SCHEDULED_CHARGE_ALLOW_GRID = 2
	SCHEDULED_CHARGE_ENHANCED = 3
	SELFCONSUME_ACCEPT_CHARGE = 4
	IDLE_SCHEDULED_FEEDIN = 5
	SCHEDULED_DISCHARGE = 6
	SELFCONSUME_ACCEPT_DISCHARGE = 7
	IDLE_MAINTAIN_SURPLUS = 8
	IDLE_MAINTAIN_TARGETSOC = 9
	SCHEDULED_CHARGE_SMOOTH_TRANSITION = 10
	SCHEDULED_CHARGE_FEEDIN = 11
	SCHEDULED_CHARGE_NO_GRID = 12
	SCHEDULED_MINIMUM_DISCHARGE = 13
	SELFCONSUME_NO_GRID = 14
	IDLE_NO_OPPORTUNITY = 15
	UNSCHEDULED_CHARGE_CATCHUP_TARGETSOC = 16
	SELFCONSUME_INCREASED_DISCHARGE = 17
	KEEP_BATTERY_CHARGED = 18
	SCHEDULED_DISCHARGE_SMOOTH_TRANSITION = 19

	DESS_DISABLED = 92
	SELFCONSUME_UNEXPECTED_EXCEPTION = 93
	SELFCONSUME_FAULTY_CHARGERATE = 94
	UNKNOWN_OPERATING_MODE = 95
	ESS_LOW_SOC = 96
	SELFCONSUME_UNMAPPED_STATE = 97
	SELFCONSUME_UNPREDICTED = 98
	NO_WINDOW = 99

class IterationChangeTracker(object):
	'''
		The iteration change tracker analyzes changes occuring between iterations, if the actual strategy may depend on the triggering factor.
	'''
	def __init__(self):
		self._current_soc = None
		self._current_target_soc = None
		self._current_nw_tsoc_higher = None
		self._current_nw_tsoc_lower = None

		self._previous_reactive_strategy = None
		self._previous_soc = None
		self._previous_target_soc = None
		self._previous_nw_tsoc_higher = None
		self._previous_nw_tsoc_lower = None

	def input(self, soc, target_soc, nw_tsoc_higher, nw_tsoc_lower):
		self._current_soc = soc
		self._current_target_soc = target_soc
		self._current_nw_tsoc_higher = nw_tsoc_higher
		self._current_nw_tsoc_lower = nw_tsoc_lower

		#log changes as well.
		tme = datetime.today().strftime('%H:%M:%S')
		if self.soc_change() != ChangeIndicator.NONE:
			logger.log(logging.DEBUG, "{0}: detected soc change from {1} to {2}, identified as: {3}".format(
				tme,
				self._previous_soc if self._previous_soc is not None else "None",
				self._current_soc,
				self.soc_change().name
			))

		if self.target_soc_change() != ChangeIndicator.NONE:
			logger.log(logging.DEBUG, "{0}: detected target soc change from {1} to {2}, identified as: {3}".format(
				tme,
				self._previous_target_soc if self._previous_target_soc is not None else "None",
				self._current_target_soc if self._current_target_soc is not None else "None",
				self.target_soc_change().name
			))

		if self.nw_tsoc_higher_change() != ChangeIndicator.NONE:
			logger.log(logging.DEBUG, "{0}: detected nw higher tsoc change from {1} to {2}, identified as: {3}".format(
				tme,
				self._previous_nw_tsoc_higher if self._previous_nw_tsoc_higher is not None else "None",
				self._current_nw_tsoc_higher,
				self.nw_tsoc_higher_change().name
			))

		if self.nw_tsoc_lower_change() != ChangeIndicator.NONE:
			logger.log(logging.DEBUG, "{0}: detected nw lower tsoc change from {1} to {2}, identified as: {3}".format(
				tme,
				self._previous_nw_tsoc_lower if self._previous_nw_tsoc_lower is not None else "None",
				self._current_nw_tsoc_lower,
				self.nw_tsoc_lower_change().name
			))

	def soc_change(self) -> ChangeIndicator:
		if self._current_soc is None or self._current_soc == self._previous_soc:
			return ChangeIndicator.NONE

		if self._previous_soc is None or self._current_soc > self._previous_soc:
			return ChangeIndicator.RISING
		elif self._current_soc < self._previous_soc:
			return ChangeIndicator.FALLING

	def target_soc_change(self) -> ChangeIndicator:
		#handle None as 0 for indication
		ps = self._previous_target_soc or 0
		cs = self._current_target_soc or 0

		if ps < cs:
			return ChangeIndicator.RISING
		elif ps > cs:
			return ChangeIndicator.FALLING

		return ChangeIndicator.NONE

	def nw_tsoc_higher_change(self) -> ChangeIndicator:
		if self._current_nw_tsoc_higher is None or self._current_nw_tsoc_higher == self._previous_nw_tsoc_higher:
			return ChangeIndicator.NONE

		if self._current_nw_tsoc_higher and (self._previous_nw_tsoc_higher is None or not self._previous_nw_tsoc_higher):
			return ChangeIndicator.BECAME_TRUE
		elif not self._current_nw_tsoc_higher and (self._previous_nw_tsoc_higher is None or self._previous_nw_tsoc_higher):
			return ChangeIndicator.BECAME_FALSE

	def nw_tsoc_lower_change(self) -> ChangeIndicator:
		if self._current_nw_tsoc_lower is None or self._current_nw_tsoc_lower == self._previous_nw_tsoc_lower:
			return ChangeIndicator.NONE

		if self._current_nw_tsoc_lower and (self._previous_nw_tsoc_lower is None or not self._previous_nw_tsoc_lower):
			return ChangeIndicator.BECAME_TRUE
		elif not self._current_nw_tsoc_lower and (self._previous_nw_tsoc_lower is None or self._previous_nw_tsoc_lower):
			return ChangeIndicator.BECAME_FALSE

	def done(self, reactive_strategy):
		self._previous_soc = self._current_soc
		self._previous_target_soc = self._current_target_soc
		self._previous_nw_tsoc_higher = self._current_nw_tsoc_higher
		self._previous_nw_tsoc_lower = self._current_nw_tsoc_lower
		self._current_soc = None
		self._current_target_soc = None
		self._current_nw_tsoc_higher = None
		self._current_nw_tsoc_lower = None

		if (self._previous_reactive_strategy != reactive_strategy):
			tme = datetime.today().strftime('%H:%M:%S')
			logger.log(logging.DEBUG, "{0}: Strategy switch from {1} to {2}".format(
				tme,
				self._previous_reactive_strategy.name if self._previous_reactive_strategy is not None else "None",
				reactive_strategy.name))

		self._previous_reactive_strategy = reactive_strategy


class EssDevice(object):
	def __init__(self, delegate, monitor, service):
		self.delegate = delegate
		self.monitor = monitor
		self.service = service

	@property
	def connected(self):
		return self.monitor.get_value(self.service, "/Connected") == 1

	@property
	def device_instance(self):
		""" Returns the DeviceInstance of this device. """
		return self.monitor.get_value(self.service, '/DeviceInstance')

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

	@property
	def consumption(self):
		return max(0, (self.delegate._dbusservice['/Ac/Consumption/L1/Power'] or 0) +
			(self.delegate._dbusservice['/Ac/Consumption/L2/Power'] or 0) +
			(self.delegate._dbusservice['/Ac/Consumption/L3/Power'] or 0))

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
		local_feedin_limit = self.monitor.get_value('com.victronenergy.settings',
                '/Settings/CGwacs/MaxFeedInPower')

		dess_feedin_limit = self.delegate.grid_export_limit * 1000.0 if self.delegate.grid_export_limit is not None else -1

		if local_feedin_limit > -1 and dess_feedin_limit == -1:
			return local_feedin_limit * -1

		if dess_feedin_limit > -1 and local_feedin_limit == -1:
			return dess_feedin_limit * -1

		#if both limits are present, the more restricive one takes precedence.
		if dess_feedin_limit > -1 and local_feedin_limit > -1:
			return min(dess_feedin_limit, local_feedin_limit) * -1

		#No limit present
		return -MAX_FEEDIN_VALUE

	@property
	def minsoc(self):
		# The BatteryLife delegate puts the active soc limit here.
		return self.delegate._dbusservice['/Control/ActiveSocLimit']

	def _set_feedin(self, allow_feedin):
		""" None = follow system setup
			True = allow
			False = restrict """

		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 0 if allow_feedin is None else 2 if allow_feedin else 1)

	def _set_charge_power(self, v):
		Dvcc.instance.internal_maxchargepower = None if v is None else max(v, 50)

	def check_conditions(self):
		# Can't do anything unless we have a minsoc, and the ESS assistant
		if not Dvcc.instance.has_ess_assistant:
			return 1 # No ESS

		# In Keep-Charged mode or external control, no point in doing anything
		if BatteryLife.instance.state == BatteryLifeState.KeepCharged or self.hub4mode == 3:
			return 2 # ESS mode is wrong

		# KeepCharged will also set minsoc to none - so this check should come after.
		if self.minsoc is None:
			return 4 # SOC low

		return 0

	def charge(self, flags, restrictions, rate, allow_feedin):
		self._set_feedin(allow_feedin)

		#if the desired rate is lower than dcpv, this would come down to NOT charging from AC,
		#but 100% of dcpv. To really achieve an overall charge-rate of what's requested, we need
		#to enter discharge mode instead. Discharge needs to be called with the desired discharge rate (positive)
		#minus once more dcpv, as the discharge-method will internally add dcpv again.
		# that'll be self.pvpower - rate - self.pvpower, hence comes down to rate * -1
		# or in other words: we leave the portion of rate * -1 from dcpv available for the battery.
		fast_charge_requested = flags & int(Flags.FASTCHARGE)

		#don't forward fastcharge. That means "max power", so no forced discharge. 
		if rate < self.pvpower and not fast_charge_requested:
			self.discharge(flags, restrictions, rate * -1, allow_feedin)
			return rate

		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 1)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)

		# Fast charge, or controlled charge? 
		fast_charge_clearance = True #Defaults to true, if we have no limit or can't determine technical limits, we just go for it (legacy behaviour). 

		if fast_charge_requested and self.delegate.battery_charge_limit is not None and self.delegate.get_charge_power_capability() is not None:
			# limits and technical capabilities are known. So, only apply fast charge, if limit would be implicit obeyed.
			fast_charge_clearance = self.delegate.get_charge_power_capability() <= self.delegate.battery_charge_limit * 1000

		if rate is None or (fast_charge_requested and fast_charge_clearance):
			self._set_charge_power(None)
			return rate #return the original requested rate either way. 
		else:
			# if fast charge is requested, but not yet cleared, use the configured battery charge limit as charge rate. 
			# this way the limit is obeyed, but the desired "maximum charge" is achieved. 
			if (fast_charge_requested and not fast_charge_clearance and self.delegate.battery_charge_limit is not None):
				rate = self.delegate.battery_charge_limit * 1000

			# Upon first call of charge(), the input charge-rate eventually has some DC-AC losses considered. 
			# (Originating from ac consumers currently beeing driven with dcsolar, reducing anticipated solar overhead)
			# As soon, as we start charging, there can't be a flow from dc to ac, so these losses will vanish
			# and the updated chargerate will be a little bit higher, if nothing else changes. This is fine and neglectable. 
			# this only happens in certain charge-situations, scheduled charging from grid only changes the chargerate on soc change.
			# rate will already be adjusted for obeying batteryimport limitation, so these check can be omited.
			self._set_charge_power(max(0.0, rate - self.pvpower))
			return rate

	def discharge(self, flags, restrictions, rate, allow_feedin):
		batteryexport = not (restrictions & int(Restrictions.BAT2GRID))

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
				srate = max(1.0, (rate or 0) + self.pvpower) # 1.0 to allow selling overvoltage

				if (batteryexport):
					#discharging the battery by rate requires to discharge all available dcpv as well.
					self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', srate)
				else:
					# this may lead to feedin anyway, but it then is "feedin of solar", while battery is only backing loads. 
					self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower',
						(min (srate, self.pvpower + self.consumption + 1.0))) # +1.0 to allow selling overvoltage

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
		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin) if allow_feedin is not None else 0)

		#if the desired rate is lower than dcpv, this would come down to NOT charging from AC,
		#but 100% of dcpv. To really achieve an overall charge-rate of what's requested, we need
		#to enter discharge mode instead. Discharge needs to be called with the desired discharge rate (positive)
		#minus once more dcpv, as the discharge-method will internally add dcpv again.
		# that'll be self.pvpower - rate - self.pvpower, hence comes down to rate * -1
		# or in other words: we leave the portion of rate * -1 from dcpv available for the battery.
		fast_charge_requested = flags & int(Flags.FASTCHARGE)

		#don't forward fastcharge. That means "max power", so no forced discharge. 
		if rate < self.pvpower and not fast_charge_requested:
			self.discharge(flags, restrictions, rate * -1, allow_feedin)
			return rate

		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 1)

		# Fast charge, or controlled charge? 
		fast_charge_clearance = True #Defaults to true, if we have no limit or can't determine technical limits, we just go for it (legacy behaviour). 

		if fast_charge_requested and self.delegate.battery_charge_limit is not None and self.delegate.get_charge_power_capability() is not None:
			# limits and technical capabilities are known. So, only apply fast charge, if limit would be implicit obeyed.
			fast_charge_clearance = self.delegate.get_charge_power_capability() <= self.delegate.battery_charge_limit * 1000

		if rate is None or (fast_charge_requested and fast_charge_clearance):
			self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', 15000)
		else:
			# if fast charge is requested, but not yet cleared, use the configured battery charge limit as charge rate. 
			# this way the limit is obeyed, but the desired "maximum charge" is achieved. 
			if (fast_charge_requested and not fast_charge_clearance and self.delegate.battery_charge_limit is not None):
				rate = self.delegate.battery_charge_limit * 1000

			self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', max(0.0, rate - self.pvpower))

		return rate

	def discharge(self, flags, restrictions, rate, allow_feedin):
		batteryexport = not (restrictions & int(Restrictions.BAT2GRID))

		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin) if allow_feedin is not None else 0)
		if allow_feedin:
			# Calculate how fast to sell. If exporting the battery to the grid
			# is allowed, then export rate plus whatever DC-coupled PV is
			# making. If exporting the battery is not allowed, then limit that
			# to DC-coupled PV plus local consumption.
			self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 1)
			if flags & Flags.FASTCHARGE:
				self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', -15000)
				return None
			else:
				srate = max(1.0, (rate or 0) + self.pvpower) # 1.0 to allow selling overvoltage

				if (batteryexport):
					#discharging the battery by rate requires to discharge all available dcpv as well.
					self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', -srate)
				else:
					# this may lead to feedin anyway, but it then is "feedin of solar", while battery is only backing loads. 
					self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', 
						(-min (srate, self.pvpower + self.consumption + 1.0))) # +1.0 to allow selling overvoltage

				return rate

		else:
			# We can only discharge into loads, therefore simply run
			# self-consumption
			self.self_consume(restrictions, allow_feedin)
			return rate

	def idle(self, allow_feedin):
		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin) if allow_feedin is not None else 0)
		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 1)
		self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', -max(0, self.pvpower))

	def self_consume(self, restrictions, allow_feedin):
		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin) if allow_feedin is not None else 0)
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
		self.duration = duration

	def get_window_progress(self, now) -> float:
		""" returns the progress of the window, 0.00 - 100.00. If the window is not or no longer active, this returns none.
			current time shall be passed as now, to ensure same result throughout multiple calls.
		"""

		if (now < self.start or now > self.stop):
			return None
		elif (now == self.start):
			return 0.00
		elif (now == self.stop):
			return 100.0

		passed_seconds = now - self.start
		progress = passed_seconds.seconds / self.duration * 100.0
		#logger.log(logging.INFO, "Start / Now / End / Duration / Passed / Progress: {} / {} / {} / {}s / {}s / {}%".format(self.start, now, self.stop, self.duration, passed_seconds.seconds, round(progress, 2)))
		return progress

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
		self.chargerate = None # Chargerate based on tsoc. Always to be set to DynamicEss/ChargeRate, even if an override is used. 
		self.override_chargerate = None # chargerate if calculation based on tsco is overwritten.
		self._timer = None
		self._devices = {}
		self._device = None
		self._errorcode = 0
		self._errortimer = ERROR_TIMEOUT
		self.iteration_change_tracker = IterationChangeTracker()

		#define the four kind of deterministic states we have. 
		#SCHEDULED_SELFCONSUME is left out, it isn't part of the overall deterministic strategy tree, but a quick escape before entering. 
		self.charge_states = (ReactiveStrategy.SCHEDULED_CHARGE_ALLOW_GRID, ReactiveStrategy.SCHEDULED_CHARGE_ENHANCED, 
					ReactiveStrategy.SCHEDULED_CHARGE_NO_GRID, ReactiveStrategy.SCHEDULED_CHARGE_FEEDIN, 
					ReactiveStrategy.SCHEDULED_CHARGE_SMOOTH_TRANSITION, ReactiveStrategy.UNSCHEDULED_CHARGE_CATCHUP_TARGETSOC,
					ReactiveStrategy.KEEP_BATTERY_CHARGED)
		self.selfconsume_states = (ReactiveStrategy.SELFCONSUME_ACCEPT_CHARGE, ReactiveStrategy.SELFCONSUME_ACCEPT_DISCHARGE, 
							 ReactiveStrategy.SELFCONSUME_NO_GRID, ReactiveStrategy.SELFCONSUME_INCREASED_DISCHARGE)
		self.idle_states = (ReactiveStrategy.IDLE_SCHEDULED_FEEDIN, ReactiveStrategy.IDLE_MAINTAIN_SURPLUS, ReactiveStrategy.IDLE_MAINTAIN_TARGETSOC, 
					  ReactiveStrategy.IDLE_NO_OPPORTUNITY)
		self.discharge_states = (ReactiveStrategy.SCHEDULED_DISCHARGE, ReactiveStrategy.SCHEDULED_MINIMUM_DISCHARGE, ReactiveStrategy.SCHEDULED_DISCHARGE_SMOOTH_TRANSITION)
		self.error_selfconsume_states = (ReactiveStrategy.NO_WINDOW, ReactiveStrategy.UNKNOWN_OPERATING_MODE, ReactiveStrategy.SELFCONSUME_UNPREDICTED,
								    ReactiveStrategy.SELFCONSUME_UNMAPPED_STATE, ReactiveStrategy.SELFCONSUME_FAULTY_CHARGERATE, 
									ReactiveStrategy.SELFCONSUME_UNEXPECTED_EXCEPTION)

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(DynamicEss, self).set_sources(dbusmonitor, settings, dbusservice)
		# Capabilities, 1 = supports charge/discharge restrictions
		#               2 = supports self-consumption strategy
		#               4 = supports fast-charge strategy
		#               8 = values set on Venus (Battery balancing, capacity, operation mode)
		#              16 = DESS split coping capability
		self._dbusservice.add_path('/DynamicEss/Capabilities', value=31)
		self._dbusservice.add_path('/DynamicEss/NumberOfSchedules', value=NUM_SCHEDULES)
		self._dbusservice.add_path('/DynamicEss/Active', value=0,
			gettextcallback=lambda p, v: MODES.get(v, 'Unknown'))
		self._dbusservice.add_path('/DynamicEss/TargetSoc', value=0,
			gettextcallback=lambda p, v: '{}%'.format(v))
		self._dbusservice.add_path('/DynamicEss/MinimumSoc', value=None,
			gettextcallback=lambda p, v: '{}%'.format(v))
		self._dbusservice.add_path('/DynamicEss/ErrorCode', value=0,
			gettextcallback=lambda p, v: ERRORS.get(v, 'Unknown'))
		self._dbusservice.add_path('/DynamicEss/LastScheduledStart', value=None)
		self._dbusservice.add_path('/DynamicEss/LastScheduledEnd', value=None)
		self._dbusservice.add_path('/DynamicEss/ChargeRate', value=0)
		self._dbusservice.add_path('/DynamicEss/Strategy', value=None)
		self._dbusservice.add_path('/DynamicEss/Restrictions', value=None)
		self._dbusservice.add_path('/DynamicEss/AllowGridFeedIn', value=None)
		self._dbusservice.add_path('/DynamicEss/Flags', value=None)
		self._dbusservice.add_path('/DynamicEss/AvailableOverhead', value=None)

		if self.mode > 0:
			self._dbusservice.add_path('/DynamicEss/ReactiveStrategy', value=None)
			self._timer = GLib.timeout_add(INTERVAL * 1000, self._on_timer)
		else:
			self._dbusservice.add_path('/DynamicEss/ReactiveStrategy', value = ReactiveStrategy.DESS_DISABLED.value)

	def get_settings(self):
		# Settings for DynamicEss
		path = '/Settings/DynamicEss'

		settings = [
			("dess_mode", path + "/Mode", 0, 0, 4),
			("dess_capacity", path + "/BatteryCapacity", 0.0, 0.0, 1000.0),
			("dess_efficiency", path + "/SystemEfficiency", 90.0, 50.0, 100.0),
			# 0=None, 1=disallow export, 2=disallow import
			("dess_restrictions", path + "/Restrictions", 0, 0, 3),
			("dess_fullchargeinterval", path + "/FullChargeInterval", 14, -1, 99),
			("dess_fullchargeduration", path + "/FullChargeDuration", 2, -1, 12),
			("dess_operatingmode", path + '/OperatingMode', -1, -1, 2),
			("dess_batterychargelimit", path + '/BatteryChargeLimit', -1.0, -1.0, 9999.9),
			("dess_batterydischargelimit", path + '/BatteryDischargeLimit', -1.0, -1.0, 9999.9),
			("dess_gridimportlimit", path + '/GridImportLimit', -1.0, -1.0, 9999.9),
			("dess_gridexportlimit", path + '/GridExportLimit', -1.0, -1.0, 9999.9),
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
				path + "/Schedule/{}/Strategy".format(i), 0, 0, 3))
			settings.append(("dess_flags_{}".format(i),
				path + "/Schedule/{}/Flags".format(i), 0, 0, 1))

		return settings

	def get_input(self):
		return [
			(HUB4_SERVICE, ['/Overrides/ForceCharge',
				'/Overrides/MaxDischargePower', '/Overrides/Setpoint',
				'/Overrides/FeedInExcess']),
			('com.victronenergy.acsystem', [
				 '/Connected',
				 '/DeviceInstance',
				 '/Capabilities/HasDynamicEssSupport',
				 '/Ess/AcPowerSetpoint',
				 '/Ess/InverterPowerSetpoint',
				 '/Ess/UseInverterPowerSetpoint',
				 '/Ess/DisableFeedIn',
				 '/Settings/Ess/Mode',
				 '/Settings/Ess/MinimumSocLimit']),
			('com.victronenergy.settings', [
				'/Settings/CGwacs/Hub4Mode',
				'/Settings/CGwacs/MaxFeedInPower',
				'/Settings/CGwacs/PreventFeedback'])
		]

	def get_output(self):
		return [('/DynamicEss/Available', {'gettext': '%s'})]

	def _set_device(self, *args, **kwargs):
		# Use device with lowest DeviceInstance. In systems with both
		# Multi-RS and VE.Bus, this will tend to favour the RS. Otherwise
		# it will favour the device on the internal mk2 connection.
		for self._device in sorted(self._devices.values(),
				key=lambda x: (x.device_instance or 0xFF)):
			if self._device.connected:
				break
		else:
			self._device = None

	def get_charge_power_capability(self) -> float:
		'''
		  Determines the systems maximum battery charge capability in Watts.
		  If the ccl and cvl fails to be determined, then None is returned.
		  None is to be distinguished from 0 (which means no charging allowed by the bms)
		'''

		battery = self._dbusservice["/ActiveBmsService"]

		# first, try to obtain values from the bms service.
		if battery is not None and battery != "":
			ccl = self._dbusmonitor.get_value(battery, '/Info/MaxChargeCurrent')
			cvl = self._dbusmonitor.get_value(battery, '/Info/MaxChargeVoltage')

			if (ccl is not None and cvl is not None):
				return ccl * cvl

		return None

	@property
	def oneway_efficency(self):
		''' When charging from AC, only half of the efficency-losses have to be considered
			So, with an overall system efficency of 0.8, the charging efficency would be 0.9 and so on.
		'''
		return min(1.0, ((1 - self._settings["dess_efficiency"] / 100.0) / -2.0) + 1.0)

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.vebus.'):
			self._devices[service] = VebusDevice(self, self._dbusmonitor, service)
			self._dbusmonitor.track_value(service, "/Connected", self._set_device)
			GLib.idle_add(self._set_device)
		elif service.startswith('com.victronenergy.acsystem.'):
			self._devices[service] = MultiRsDevice(self, self._dbusmonitor, service)
			GLib.idle_add(self._set_device)

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
			if newvalue == 0:
				self._dbusservice['/DynamicEss/ReactiveStrategy'] = ReactiveStrategy.DESS_DISABLED.value

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
	def grid_import_limit(self) -> float:
		''' Grid import limit as configured by the user for DESS. In kW, positive, None if not set'''
		return self._settings['dess_gridimportlimit'] if self._settings['dess_gridimportlimit'] >= 0 else None
    
	@property
	def grid_export_limit(self)-> float:
		''' Grid export limit as configured by the user for DESS. In kW, positive, None if not set'''
		return self._settings['dess_gridexportlimit'] if self._settings['dess_gridexportlimit'] >= 0 else None
    
	@property
	def battery_charge_limit(self)-> float:
		''' Battery charge limit as configured by the user for DESS. In kW, positive, None if not set'''
		return self._settings['dess_batterychargelimit'] if self._settings['dess_batterychargelimit'] >= 0 else None
    
	@property
	def battery_discharge_limit(self)-> float:
		''' Battery discharge limit as configured by the user for DESS. In kW, positive, None if not set'''
		return self._settings['dess_batterydischargelimit'] if self._settings['dess_batterydischargelimit'] >= 0 else None

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
		return self._dbusservice['/DynamicEss/TargetSoc'] if self._dbusservice['/DynamicEss/TargetSoc'] is not None and  self._dbusservice['/DynamicEss/TargetSoc'] > 0 else None

	@targetsoc.setter
	def targetsoc(self, v):
		self._dbusservice['/DynamicEss/TargetSoc'] = v or 0

	@property
	def soc(self):
		return BatterySoc.instance.soc

	@property
	def capacity(self):
		return self._settings["dess_capacity"]

	@property
	def operating_mode(self) -> OperatingMode:
		return OperatingMode(self._settings["dess_operatingmode"])

	@property
	def restrictions(self):
		return self._settings["dess_restrictions"]

	def update_chargerate(self, now, end, start_soc, end_soc):
		""" now is current time, end is end of slot, start_soc and end_soc determine the amount of intended soc change """

		# Only update the charge rate if a new soc value has to be considered or chargerate is none
		if self.chargerate is None or self.soc != self.prevsoc:
			try:
				# a Watt is a Joule-second, a Wh is 3600 joules.
				# Capacity is kWh, so multiply by 100, percentage needs division by 100, therefore 36000.
				percentage = abs(start_soc - end_soc)
				chargerate = round(1.1 * (percentage * self.capacity * 36000) / abs((end - now).total_seconds()))

				#Discharge and charge has two different limits for calculation. these limits are added in update_chargerate
				#rather than charge/discharge method, so data logging clearly shows the exact computed chargerate.
				if start_soc <= end_soc:
					chargerate = chargerate if self.battery_charge_limit is None else min(chargerate, self.battery_charge_limit * 1000)
				elif start_soc > end_soc:
					chargerate = chargerate if self.battery_discharge_limit is None else min(chargerate, self.battery_discharge_limit * 1000)

				# keeping up prior chargerate is no longer required at this point.
				self.chargerate = chargerate
				#self.chargerate = chargerate if self.chargerate is None else max(abs(self.chargerate), chargerate)
				self.prevsoc = self.soc

			except ZeroDivisionError:
				logger.log(logging.WARNING, "Caught ZeroDivisionError in update_chargerate() for end='{}', now='{}'".format(end, now))
				self.chargerate = None

		#chargerate should be negative, if discharge-case to fit into maths elsewhere.
		#discharge_method then has to handle accordingly.
		if (end_soc < start_soc and self.chargerate is not None):
			self.chargerate = abs(self.chargerate) * -1

	def _on_timer(self):
		# If DESS was disabled, deactivate and kill timer.
		if self.mode in (0, 2, 3): # Old buy/sell states now also means off
			self.deactivate(0) # No error
			self._dbusservice['/DynamicEss/ReactiveStrategy'] = ReactiveStrategy.DESS_DISABLED.value
			return False

		def bail(code):
			self.release_control()
			self.active = 0 # Off
			self.errorcode = code
			self.targetsoc = None
			self._dbusservice['/DynamicEss/MinimumSoc'] = None

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
		restrictions = self.restrictions

		#Whenever an error occurs that is totally unexpected, the delegate
		#should enter self consume and not die.(try/catch around the control loop logic)
		try:
			for w in windows:
				# Keep track of maximum available schedule
				if start is None or w.start > start:
					start = w.start
					stop = w.stop

			self._dbusservice['/DynamicEss/LastScheduledStart'] = None if start is None else int(datetime.timestamp(start))
			self._dbusservice['/DynamicEss/LastScheduledEnd'] = None if stop is None else int(datetime.timestamp(stop))

			final_strategy = ReactiveStrategy.NO_WINDOW
			current_window = None
			next_window = None

			# This is the ESS minsoc of the selected device
			self._dbusservice['/DynamicEss/MinimumSoc'] = None if self._device is None else self._device.minsoc

			#iterate through windows, find the current one. Usually it should be first,
			#but in case of update issues may not. Also grab the next window, to perform
			#some "look aheads" for optimizations.
			for w in windows:
				if self.acquire_control() and now in w:
					self.active = 1 # Auto
					self.errorcode = 0 # No error

					current_window = w
					restrictions = w.restrictions | self.restrictions

					self._dbusservice['/DynamicEss/Strategy'] = w.strategy
					self._dbusservice['/DynamicEss/Restrictions'] = restrictions
					self._dbusservice['/DynamicEss/AllowGridFeedIn'] = int(w.allow_feedin)
					break # out of for loop

			if current_window is not None:
				#found current window, now we need nextWindow to do some look aheads as well. 
				#next window is the one containing current.start + current.duration + 1.
				#finding next window is not required to enter the control loop, can be None.
				next_window_save_start = current_window.stop + timedelta(seconds = 1)
				for w in windows:
					if (next_window_save_start in w):
						next_window = w
						break # out of for loop

				#As of now, one common handler is enough. Hence, we don't need to validate the operation mode 
				final_strategy = self._determine_reactive_strategy(current_window, next_window, restrictions, now)

				if (self.chargerate or 0) != self._dbusservice['/DynamicEss/ChargeRate']:
					logger.log(logging.DEBUG, "Anticipated chargerate is now: {}".format(self.chargerate or 0))

				self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate or 0 #Always set the anticipated chargerate on dbus.
			else:
				# No matching windows
				if self.active or self.errorcode != 3:
					self.deactivate(3)

			#write out current override strategy to determine if the local system behaves "out of schedule" on purpose.
			if self._dbusservice["/SystemState/LowSoc"] == 1:
				final_strategy= ReactiveStrategy.ESS_LOW_SOC

			#done, reset iteration_change_tracker
			self._dbusservice['/DynamicEss/ReactiveStrategy'] = final_strategy.value
			self.iteration_change_tracker.done(final_strategy)

		except Exception as ex:
			logger.log(logging.FATAL, "Unexpected exception inside Control Loop.", exc_info = ex)
			final_strategy = ReactiveStrategy.SELFCONSUME_UNEXPECTED_EXCEPTION
			self._dbusservice['/DynamicEss/ReactiveStrategy'] = final_strategy.value

		if final_strategy.value in self.error_selfconsume_states:
			#Do at least regular ESS. 
			self.chargerate = None
			self._dbusservice['/DynamicEss/ChargeRate'] = 0
			self._device.self_consume(restrictions, None)

		return True

	def _determine_reactive_strategy(self, w: DynamicEssWindow, nw: DynamicEssWindow, restrictions, now) -> ReactiveStrategy:
		'''
			Logic to be applied in Greenmode. Micro changes in strategy are applied to optimize solar gain / minimize grid pull. Returns the choosen strategy.
			Strategy has to be determined in a 100% deterministic way. After it has been determined the proper system reaction with different variable sets
			is called to minimize repetition of functional code.
		'''
		# required variables to make some improvement decissions
		# Generally, solar_plus is PV - Consumption
		# It needs to take efficency into account, legacy equation did this by multiplying acpv with 0.9
		# However it will be more precice to only consider the "available ac pv" with 0.9. Direct Consumption will basically
		# lower the available acpv without conversion losses.

		available_solar_plus = 0

		direct_acpv_consume = min(self._device.acpv or 0, self._device.consumption)
		remaining_ac_pv = max(0, (self._device.acpv or 0) - direct_acpv_consume)
		if remaining_ac_pv > 0:
			#dc can be used for charging 100%, ac is penalized with 10% conversion losses.
			available_solar_plus = (self._device.pvpower or 0) + remaining_ac_pv * self.oneway_efficency
		else:
			#not enough ac pv. so, the part flowing from DC to remaining AC loads will lower the budget.
			#ac doesn't have to be considered, it's 100% consumed. Hower, dc consume is penalized by 10% conversion
			direct_dcpv_consume = self._device.consumption - direct_acpv_consume
			available_solar_plus = (self._device.pvpower or 0) - direct_dcpv_consume / self.oneway_efficency

		self._dbusservice["/DynamicEss/AvailableOverhead"] = available_solar_plus
		logger.log(logging.DEBUG, "ACPV / DCPV / Cons / Overhead is: {} / {} / {} / {}".format(self._device.acpv, self._device.pvpower, self._device.consumption, available_solar_plus))

		next_window_higher_target_soc = nw is not None and (nw.soc > w.soc) and nw.strategy != Strategy.SELFCONSUME
		next_window_lower_target_soc = nw is not None and (nw.soc < w.soc) and nw.strategy != Strategy.SELFCONSUME

		#pass new values to iteration change tracker. 
		self.iteration_change_tracker.input(self.soc, self.targetsoc, next_window_higher_target_soc, next_window_lower_target_soc)
		soc_change = self.iteration_change_tracker.soc_change()
		target_soc_change = self.iteration_change_tracker.target_soc_change()
		window_progress = w.get_window_progress(now) or 0

		# When we have a Scheduled-Selfconsume, we can ommit to walk through the decission tree. 
		if w.strategy == Strategy.SELFCONSUME:
			self.chargerate = None #No scheduled chargerate in this case.
			self.targetsoc = None
			self._device.self_consume(restrictions, w.allow_feedin)
			return ReactiveStrategy.SCHEDULED_SELFCONSUME

		# Below here, strategy is any of the target soc dependent strategies
		# some preparations
		self.override_chargerate = None #if a override to chargerate can be found, it is set here.
		if self.targetsoc != w.soc:
			self.chargerate = None # For recalculation, if target soc changes.

		self.targetsoc = w.soc 
		self._dbusservice['/DynamicEss/Flags'] = w.flags

		excess_to_grid = (w.strategy == Strategy.PROGRID) or (w.strategy == Strategy.TARGETSOC)
		missing_to_grid = (w.strategy == Strategy.TARGETSOC) or (w.strategy == Strategy.PROBATTERY)
		excess_to_bat = not excess_to_grid
		missing_to_bat = not missing_to_grid

		#Needs to be determined
		reactive_strategy = None 

		if self.soc + self.charge_hysteresis < w.soc or w.soc >= 100:
			# if 100% is reached, keep batteries charged. 
			# Mind we need to leave this, if missing2bat copping is selected and the ME-indicator is negative. 
			# (To be more precice, as soon as the 250 Watt requested couldnt't be served by solar, fall back to default behaviour)
			if w.soc >= 100 and self.soc >= 100 and (missing_to_grid or (missing_to_bat and available_solar_plus > 250)):
				self.chargerate = 250
				reactive_strategy = ReactiveStrategy.KEEP_BATTERY_CHARGED

			# we are behind plan. Charging is required. 
			else:
				self.update_chargerate(now, w.stop, self.soc, w.soc)

				# Based on the coping flags, charging has 4 options
				# Also restrictions may be applied (grid2bat). 
				if available_solar_plus > self.chargerate:
					# 1) There is more solar than expected and we are EXCESSTOBAT -> charge enhanced.
					#    This state also needs to be enforced, when feedin is restricted
					if excess_to_bat or not w.allow_feedin: 
						self.override_chargerate = available_solar_plus 
						reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_ENHANCED

					# 2) There is more solar than expected and we are EXCESSTOGRID -> charge at calculated charge rate, accept feedin happening.
					#    This state is dissallowed, when feedin is restricted, but then we already entered situation 1.
					elif excess_to_grid: 
						reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_FEEDIN
				else:
					#available_solar_plus <= self.chargerate
					# 3) There isn't enough solar and we are flagged MISSINGTOGRID -> use calculated charge rate.
					#    (Wording note: Missing2Grid describes the punishment of missing energy to the grid - so TAKING energy from the grid ;-))
					#    But, this state is dissallowed, if a Grid2Bat Restriction is active.
					if missing_to_grid and not (w.restrictions & Restrictions.GRID2BAT): 
						reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_ALLOW_GRID

					# 4) There isn't enough solar and we are flagged MISSINGTOBAT -> only use solar power that is availble.
					#    This is self consume, until condition changes.
					#    In case there is Grid2Bat restriction, this is our only option, even if the flag would indicate MISSINGTOGRID
					elif available_solar_plus > 0 and (missing_to_bat or (w.restrictions & Restrictions.GRID2BAT)): 
						reactive_strategy = ReactiveStrategy.SELFCONSUME_NO_GRID

					# 5.) Ultimate case: No Grid charge possible, no solar. We can't charge. Therefore, the strategy best is to go idle. 
					elif available_solar_plus <= 0 and (missing_to_bat or (w.restrictions & Restrictions.GRID2BAT)):
						reactive_strategy = ReactiveStrategy.IDLE_NO_OPPORTUNITY

		else:
			# if we are currently in any SCHEDULED_CHARGE_* State and our next window outlines an even higher target soc, 
			# don't switch to idle, but keep a certain chargerate. As soon as target_soc changes, this state has to be left.
			# but only enter it, when window progress is >= TRANSITION_STATE_THRESHOLD
			if (self.iteration_change_tracker._previous_reactive_strategy in self.charge_states and 
	   			next_window_higher_target_soc and window_progress >= TRANSITION_STATE_THRESHOLD) or \
				(self.iteration_change_tracker._previous_reactive_strategy == ReactiveStrategy.SCHEDULED_CHARGE_SMOOTH_TRANSITION and target_soc_change == ChangeIndicator.NONE):
				# keep current charge rate untouched.
				# already targeting the new soc target of "next" window will cause a not smooth transition, if next window in slot 1 is outdated
				# and the next window beeing pushed to slot 0 indicates another target soc.
				reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_SMOOTH_TRANSITION
			else:
				# we are above or equal to target soc, or the charge histeresis has not yet kicked in from a prior state.

				if (available_solar_plus > 0 and not excess_to_grid):
					# If surplus is available, always attempt to charge, unless we are flagged EXCESSTOGRID
					reactive_strategy = ReactiveStrategy.SELFCONSUME_ACCEPT_CHARGE

				else:
					# so, now we have: (availableSolarPlus <= 0 or solaroverhaed, but excess_to_grid) and (equal or above targetSoc).
					# so, most likely any of the discharge-variants is required (or ultimately idle)
					# if we are flagged EXESSTOGRID and MISSINGTOGRID, perform a strict discharge, based on soc difference.
					# Any imprecission shall be handled by the grid
					# not allowed with bat2grid restriction
					#       When we have a bat2grid restriction, we should discharge at full consumption, feeding in 100% of solar production. 
					if self.soc - self.discharge_hysteresis > max(w.soc, self._device.minsoc) and excess_to_grid and missing_to_grid \
						and not (int(restrictions) & int(Restrictions.BAT2GRID)):
						self.update_chargerate(now, w.stop, self.soc, w.soc)
						reactive_strategy = ReactiveStrategy.SCHEDULED_DISCHARGE

					# if flags are EXCESSTOGRID and MISSINGTOBAT, that means: keep a MINIMUM dischargerate, but allow to discharge more, if consumption-solar is higher.
					# not allowed with bat2grid restriction
					# so, we do some quick maths, if loads would require a higher discharge - then we let self consume handle that, over calculating a "better" discharge rate. 
					elif self.soc - self.discharge_hysteresis > max(w.soc, self._device.minsoc) and excess_to_grid and missing_to_bat \
						and not (int(restrictions) & int(Restrictions.BAT2GRID)):
						self.update_chargerate(now, w.stop, self.soc, w.soc)
						me_indicator = available_solar_plus - self.chargerate

						if me_indicator < 0:
							# missing, let self consume handle this over calculating a improved rate.
							reactive_strategy =  ReactiveStrategy.SELFCONSUME_INCREASED_DISCHARGE
						else:
							# excess, ensure the minimum discharge rate required to reach targetsoc as of "now". 
							self.override_chargerate = abs(self.chargerate) * -1
							reactive_strategy =  ReactiveStrategy.SCHEDULED_MINIMUM_DISCHARGE

					# left over discharge cases:
					#   - bat2grid restricted -> self consume
					#   - EXCESSTOBAT and MISSINGTOBAT -> self consume
					#   - EXCESSTOBAT and MISSINGTOGRID:
					#     Technically that means, we should have a MAXIMUM dischargerate and punish the energy above that to the grid
					#     However, that may cause some grid2consumption happening in the beginning of the window, but still ending up above target soc.
					#     So that would be gridpull for no reason. 
					#     So, the more logical way is to accept ANY discharge, but simple stop when reaching target soc - and punish the remaining
					#     load during that window to the grid. -> also self consume
					# BUT: we are only doing this, If our next window has a smaller, equal or no target soc
					elif self.soc - self.discharge_hysteresis > max(w.soc, self._device.minsoc):
						reactive_strategy = ReactiveStrategy.SELFCONSUME_ACCEPT_DISCHARGE

					else:
						# Here we are:
						# - Ahead of plan, but the next window indicates a higher soc target.
						# - Spot on target soc, so idling is imminent / bellow targetSoc by charge_hysteresis %.
						# - available solar plus, but intended feedin.
						# All cases should lead to idle, just determine which we have, for debug purpose
						#if (self.soc > self.targetsoc and next_window_higher_target_soc):
							# next window has a higher target soc than the current window, so idle to maintin advantage.
							# if a discharge would have been enforced by the schedule, we would already be in SCHEDULED_DISCHARGE case.
							#reactive_strategy = ReactiveStrategy.IDLE_MAINTAIN_SURPLUS
						if available_solar_plus > 0 and excess_to_grid:
							# We have solar surplus, but VRM wants an explicit feedin.
							# since we are above or equal to target soc, we are going idle to achieve that.
							reactive_strategy = ReactiveStrategy.IDLE_SCHEDULED_FEEDIN
						else:
							if (self.iteration_change_tracker._previous_reactive_strategy in self.discharge_states and 
								next_window_lower_target_soc and window_progress >= TRANSITION_STATE_THRESHOLD) or \
								(self.iteration_change_tracker._previous_reactive_strategy == ReactiveStrategy.SCHEDULED_DISCHARGE_SMOOTH_TRANSITION and target_soc_change == ChangeIndicator.NONE):
								# keep current charge rate untouched.
								# already targeting the new soc target of "next" window will cause a not smooth transition, if next window in slot 1 is outdated
								# and the next window beeing pushed to slot 0 indicates another target soc.
								# but only enter it, when window progress is >= TRANSITION_STATE_THRESHOLD
								reactive_strategy = ReactiveStrategy.SCHEDULED_DISCHARGE_SMOOTH_TRANSITION
							else:
								# else, it's idle due to soc==targetsoc, or soc + charge_hystersis == targetsoc.
								reactive_strategy = ReactiveStrategy.IDLE_MAINTAIN_TARGETSOC

		#bellow here, ReactiveStrategy should be determined. As well as chargerate, if required. If it isn't
		#Enter self consume, as conditions may change and situation will resolve. 
		#(This would need to be resolved, there shouldn't be any unpredicted combination of parameters)
		if reactive_strategy is None:
			return ReactiveStrategy.SELFCONSUME_UNPREDICTED
		else:
			#depending on the reactive strategy choosen, system behaviour may be the same - just different value set
			#and/or different reasoning.
			final_chargerate = self.override_chargerate if self.override_chargerate is not None else self.chargerate

			if final_chargerate is None and (reactive_strategy in self.charge_states or reactive_strategy in self.discharge_states):
				# failed to calculate a chargerate. This however is required for charge/discharge.
				# Temporary enter self-consume to keep the system moving, changed conditions may allow for successfull recalculation and
				# getting back on track.
				reactive_strategy = ReactiveStrategy.SELFCONSUME_FAULTY_CHARGERATE

			if reactive_strategy in self.charge_states:
				self._device.charge(w.flags, restrictions, abs(final_chargerate), w.allow_feedin)
				self.charge_hysteresis = 0 #allow to reach targetsoc precicesly.
				self.discharge_hysteresis = 1 #avoid discharging, when overshooting targetsoc.

			elif reactive_strategy in self.selfconsume_states:
				self._device.self_consume(restrictions, w.allow_feedin)
				self.charge_hysteresis = 0 #no hysteresis.
				self.discharge_hysteresis = 0 #no hysteresis.

			elif reactive_strategy in self.idle_states:
				self._device.idle(w.allow_feedin)
				self.charge_hysteresis = 1 #avoid rapid changes.
				self.discharge_hysteresis = 1 #avoid rapid changes.

			elif reactive_strategy in self.discharge_states:
				#chargerate to be send to discharge method has to be always positive.
				self._device.discharge(w.flags, restrictions, abs(final_chargerate), w.allow_feedin)
				self.charge_hysteresis = 1 #avoid charging, when undershooting targetsoc.
				self.discharge_hysteresis = 0 # allow to reach tsoc precicesly.

			elif reactive_strategy in self.error_selfconsume_states:
				#errorstates are handled outside this method. Seperate return to avoid else-case.
				self.charge_hysteresis = 0 #no hysteresis.
				self.discharge_hysteresis = 0 #no hysteresis.
				return reactive_strategy

			else:
				#This should never happen, it means that there is a state that is not mapped to a reaction. 
				#We enter self consume and use a own state for that :P 
				#Doing at least self consume will make the system leave this unmapped state sooner or later for sure and not get stuck.
				return ReactiveStrategy.SELFCONSUME_UNMAPPED_STATE

			return reactive_strategy


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
		self._dbusservice['/DynamicEss/MinimumSoc'] = None

	def update_values(self, newvalues):
		# Indicate whether this system has DESS capability. Presently
		# that means it has ESS capability.
		try:
			newvalues['/DynamicEss/Available'] = int(self._device.available)
		except AttributeError:
			newvalues['/DynamicEss/Available'] = 0

#Helper methods
def xor(a, b):
	''' 
		equivalent of a ^ b.
		Does a "bitwise exclusive or". Each bit of the output is the same as the corresponding bit in x if that bit in y is 0, and it's the complement of the bit in x if that bit in y is 1.
	'''
	return (a and not b) or (not a and b)
