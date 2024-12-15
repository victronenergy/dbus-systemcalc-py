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

class OperatingMode(int, Enum):
	TRADEMODE = 0
	GREENMODE = 1

class Flags(int, Enum):
	NONE = 0
	FASTCHARGE = 1
	EXCESSTOGRID = 2 #send excess to grid, if we have any.
	MISSINGTOGRID = 4 #get missing from grid, if we need any.

class Restrictions(int, Enum):
	NONE = 0
	BAT2GRID = 1
	GRID2BAT = 2

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

	ESS_LOW_SOC = 96						
	SELFCONSUME_UNMAPPED_STATE = 97         
	SELFCONSUME_UNPREDICTED = 98			
	NO_WINDOW = 99							

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
		l = self.monitor.get_value('com.victronenergy.settings',
                '/Settings/CGwacs/MaxFeedInPower')
		return SELLPOWER if l < 0 else max(-l, SELLPOWER)

	@property
	def minsoc(self):
		# The BatteryLife delegate puts the active soc limit here.
		return self.delegate._dbusservice['/Control/ActiveSocLimit']	

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
		batteryimport = not restrictions & int(Restrictions.GRID2BAT)

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
		batteryexport = not restrictions & int(Restrictions.BAT2GRID)

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

		#define the four kind of deterministic states we have. 
		#SCHEDULED_SELFCONSUME is left out, it isn't part of the overall deterministic strategy tree, but a quick escape before entering. 
		self.charge_states = (ReactiveStrategy.SCHEDULED_CHARGE_ALLOW_GRID, ReactiveStrategy.SCHEDULED_CHARGE_ENHANCED, 
					ReactiveStrategy.SCHEDULED_CHARGE_NO_GRID, ReactiveStrategy.SCHEDULED_CHARGE_FEEDIN, 
					ReactiveStrategy.SCHEDULED_CHARGE_SMOOTH_TRANSITION)
		self.selfconsume_states = (ReactiveStrategy.SELFCONSUME_ACCEPT_CHARGE, ReactiveStrategy.SELFCONSUME_ACCEPT_DISCHARGE)
		self.idle_states = (ReactiveStrategy.IDLE_SCHEDULED_FEEDIN, ReactiveStrategy.IDLE_MAINTAIN_SURPLUS, ReactiveStrategy.IDLE_MAINTAIN_TARGETSOC)
		self.discharge_states = (ReactiveStrategy.SCHEDULED_DISCHARGE, ReactiveStrategy.SCHEDULED_MINIMUM_DISCHARGE)

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(DynamicEss, self).set_sources(dbusmonitor, settings, dbusservice)
		# Capabilities, 1 = supports charge/discharge restrictions
		#               2 = supports self-consumption strategy
		#               4 = supports fast-charge strategy
		#               8 = values set on Venus (Battery balancing, capacity, operation mode)
		self._dbusservice.add_path('/DynamicEss/Capabilities', value=15)
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
		self._dbusservice.add_path('/DynamicEss/ReactiveStrategy', value=None)
		self._dbusservice.add_path('/DynamicEss/Flags', value=None)
		self._dbusservice.add_path('/DynamicEss/AvailableOverhead', value=None)
		self._dbusservice.add_path('/DynamicEss/CorDataSet', value=None)
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
			("dess_operatingmode", path + '/OperatingMode', -1, 0, 2),
			("dess_batterychargelimit", path + '/BatteryChargeLimit', -1, 0, 0),
			("dess_batterydischargelimit", path + '/BatteryDischargeLimit', -1, 0, 0),
			("dess_gridimportlimit", path + '/GridImportLimit', -1, 0, 0),
			("dess_gridexportlimit", path + '/GridExportLimit', -1, 0, 0),
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
	
	@property
	def oneway_efficency(self):
		''' When charging from AC, only half of the efficency-losses have to be considered
			So, with an overall system efficency of 0.8, the charging efficency would be 0.9 and so on.
		'''
		#TODO: Method implememented, usage defered. We agreed to first start to see, how the dess_efficiency will look like for various systems, 
		#      before starting to actively use it as source.
		return min(1.0, ((1 - self._settings["dess_efficiency"] / 100.0) / -2.0) + 1)
	
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
	def operating_mode(self) -> OperatingMode:
		return OperatingMode(self._settings["dess_operatingmode"])

	@property
	def restrictions(self):
		return self._settings["dess_restrictions"]

	def update_chargerate(self, now, end, percentage):
		""" now is current time, end is end of slot, percentage is amount of battery
		    we want to dump/charge before then. """

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

		final_strategy = "NO_WINDOW"
		current_window = None
		next_window = None

		#iterate through windows, find the current one. Usually it should be first,
		#but in case of update issues may not. Also grab the next window, to perform
		#some "look aheads" for optimizations.
		for w in windows:
			if self.acquire_control() and now in w:
				self.active = 1 # Auto
				self.errorcode = 0 # No error

				#FIXME: Experimental: Set the OverheadTreatmentFlag, so roadmap will be more strict.
				#       Absence of that flag would mean PREFERBATTERY
				
				# Excess Coping, required for trademode forced discharge, to be set by VRM later.
				if (self.operating_mode == OperatingMode.TRADEMODE):
					w.flags |= int(Flags.EXCESSTOGRID)

				# Missing Coping, required for forced grid charge, to be set by VRM later.
				w.flags |= int(Flags.MISSINGTOGRID)

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

			if (self.operating_mode == OperatingMode.TRADEMODE):
				#FIXME: Experimental: Most recent version of the handle-method should be 100% suitable for trademode, with correct copping flags.
				final_strategy = self._handle_green_mode(current_window, next_window, restrictions, now)

			elif (self.operating_mode == OperatingMode.GREENMODE):
				final_strategy = self._handle_green_mode(current_window, next_window, restrictions, now)

		else:
			# No matching windows
			if self.active or self.errorcode != 3:
				self.deactivate(3)
	
		#write out current override strategy to determine if the local system behaves "out of schedule" on purpose.
		if self._dbusservice["/SystemState/LowSoc"] == 1:
			self._dbusservice['/DynamicEss/ReactiveStrategy'] = ReactiveStrategy.ESS_LOW_SOC.value
		else:
			self._dbusservice['/DynamicEss/ReactiveStrategy'] = final_strategy.value
		
		#Publish the correleated DataSet. This is for making sure remote-tools get data that really correletes.
		self._dbusservice['/DynamicEss/CorDataSet'] = "{{\"ts\":{0}, \"s\":{1}, \"chr\":{2}}}".format(self.targetsoc or "null", self.soc or "null", self.chargerate or "null")

		return True

	def _handle_trade_mode(self, w: DynamicEssWindow, nw: DynamicEssWindow, restrictions, now) -> ReactiveStrategy:
		'''
			Logic to be applied in Trademode. It is strictly soc based. Returns the choosen strategy as string.
		'''
		
		if w.strategy == Strategy.SELFCONSUME:
			self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
			self.targetsoc = None
			self._dbusservice['/DynamicEss/Flags'] = ((w.flags & ~int(Flags.EXCESSTOGRID)) & ~int(Flags.MISSINGTOGRID)) #self consume is implicit All2Bat
			self._device.self_consume(restrictions, w.allow_feedin)
			return ReactiveStrategy.SCHEDULED_SELFCONSUME

		# Below here, strategy is Strategy.TARGETSOC
		# round targetsoc.
		if self.targetsoc  != w.soc:
			self.chargerate = None # For recalculation
		
		self._dbusservice['/DynamicEss/Flags'] = w.flags
		self.targetsoc = w.soc

		# When 100% is requested, don't go into idle mode
		if self.soc + self.charge_hysteresis < w.soc or w.soc >= 100: # Charge
			self.charge_hysteresis = 0
			self.discharge_hysteresis = 1
			self.update_chargerate(now, w.stop, abs(self.soc - w.soc))
			self._dbusservice['/DynamicEss/ChargeRate'] = \
				self._device.charge(w.flags, restrictions,
				self.chargerate, w.allow_feedin)
			return ReactiveStrategy.SCHEDULED_CHARGE_ALLOW_GRID #TODO Own state?
		else: # Discharge or idle
			self.charge_hysteresis = 1
			if self.soc - self.discharge_hysteresis > max(w.soc, self._device.minsoc): # Discharge
				self.discharge_hysteresis = 0
				self.update_chargerate(now, w.stop, abs(self.soc - w.soc))
				self._dbusservice['/DynamicEss/ChargeRate'] = \
					self._device.discharge(w.flags, restrictions,
					self.chargerate, w.allow_feedin)
				return ReactiveStrategy.SCHEDULED_DISCHARGE
			else: # battery idle
				# SOC/target-soc needs to move 1% to move out of idle
				# zone
				self.discharge_hysteresis = 1
				self._dbusservice['/DynamicEss/ChargeRate'] = \
					self._device.idle(w.allow_feedin)
				return ReactiveStrategy.IDLE_MAINTAIN_TARGETSOC
				
	def _handle_green_mode(self, w: DynamicEssWindow, nw: DynamicEssWindow, restrictions, now) -> ReactiveStrategy:
		'''
			Logic to be applied in Greenmode. Micro changes in strategy are applied to optimize solar gain / minimize grid pull. Returns the choosen strategy.
			Strategy has to be determined in a 100% deterministic way. After it has been determined the proper system reaction with different variable sets
			is called to minimize repetition of functional code.
		'''
		# required variables to make some improvement decissions
		available_solar_plus = (self._device.pvpower or 0) + (self._device.acpv or 0) * 0.9 - self._device.consumption
		self._dbusservice["/DynamicEss/AvailableOverhead"] = max(0, available_solar_plus)
		next_window_higher_target_soc = nw is not None and (nw.soc > w.soc) and nw.strategy == Strategy.TARGETSOC

		# When we have a Scheduled-Selfconsume, we can ommit to walk through the decission tree. 
		if w.strategy == Strategy.SELFCONSUME:
			self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
			self.targetsoc = None
			#self consume is implicit All2Bat, no matter what window is eventually requesting.
			self._dbusservice['/DynamicEss/Flags'] = ((int(w.flags) & ~int(Flags.EXCESSTOGRID)) & ~int(Flags.MISSINGTOGRID)) 
			self._device.self_consume(restrictions, w.allow_feedin)
			return ReactiveStrategy.SCHEDULED_SELFCONSUME

		# Below here, strategy is Strategy.TARGETSOC. 
		# Every possible variable combination is leading to a deterministic ReactiveStrategy, allowing to easily identify
		# current parameters based on the choosen strategy. 

		# some preparations
		# round targetsoc due to "minute refresh hack"
		if self.targetsoc != w.soc:
			self.chargerate = None # For recalculation
		
		self.targetsoc = w.soc #+ (datetime.now().minute / 10000.0) #FIXME: Experimental. Add a fraction depending on the current minute to targetsoc
		self._dbusservice['/DynamicEss/Flags'] = w.flags
		
		excess_to_grid = bool((w.flags & Flags.EXCESSTOGRID) > 0)
		missing_to_grid = bool((w.flags & Flags.MISSINGTOGRID) > 0)
		excess_to_bat = not excess_to_grid
		missing_to_bat = not missing_to_grid

		#Needs to be determined
		reactive_strategy = None 

		if self.soc + self.charge_hysteresis < w.soc or w.soc >= 100:
			# we are behind plan. Charging is required.
			self.charge_hysteresis = 0
			self.discharge_hysteresis = 1
			self.update_chargerate(now, w.stop, abs(self.soc - w.soc))
			
			# Based on the coping flags, charging has 4 options
			# Also restrictions may be applied (grid2bat). 
			# 1) There is more solar than expected and we are EXCESSTOBAT -> charge enhanced.
			#    This state also needs to be enforced, when feedin is restricted
			if available_solar_plus > self._dbusservice['/DynamicEss/ChargeRate'] and excess_to_bat or not w.allow_feedin:
				self.chargerate = available_solar_plus
				reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_ENHANCED
			
			# 2) There is more solar than expected and we are EXCESSTOGRID -> charge at calculated charge rate, accept feedin happening.
			#    This state is dissallowed, when feedin is restricted, but then we already entered situation 1.
			elif available_solar_plus > self._dbusservice['/DynamicEss/ChargeRate'] and excess_to_grid: 
				reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_FEEDIN

			# 3) There isn't enough solar and we are flagged MISSINGTOGRID -> use calculated charge rate.
			#    (Wording note: Missing2Grid describes the punishment of missing energy to the grid - so TAKING energy from the grid ;-))
			#    But, this state is dissallowed, if a Grid2Bat Restriction is active.
			elif available_solar_plus <= self._dbusservice['/DynamicEss/ChargeRate'] and missing_to_grid and not (w.restrictions & Restrictions.GRID2BAT): 
				reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_ALLOW_GRID
			
			# 4) There isn't enough solar and we are flagged MISSINGTOBAT -> only use solar power that is availble.
			#    In case there is Grid2Bat restriction, this is our only option, even if the flag would indicate MISSINGTOGRID
			elif available_solar_plus <= self._dbusservice['/DynamicEss/ChargeRate'] and (missing_to_bat or (w.restrictions & Restrictions.GRID2BAT)): 
				self.chargerate = available_solar_plus
				reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_NO_GRID
		
		else:
			# if we are currently in any SCHEDULED_CHARGE_* State and our next window outlines an even higher target soc, 
			# don't switch to idle, but keep current charge rate. (Else chargerate will drop to 0, when reaching target soc early)
			if self._dbusservice["/DynamicEss/ReactiveStrategy"] in self.charge_states and next_window_higher_target_soc:
				#keep up current charge rate until window ends, and we no longer have a soc==target_soc condition.
				#FIXME This is currently working as long as we don't progress 2 soc percent before window end. If we do, self.chargerate will become None and can't be used anymore.
				#      So, would be saver to recalculate a chargerate matching the next windows end already over reusing any stored one.
				reactive_strategy =  ReactiveStrategy.SCHEDULED_CHARGE_SMOOTH_TRANSITION
			else:
				# we are above or equal to target soc, or the charge histeresis has not yet kicked in from a prior state.
				self.charge_hysteresis = 1
				
				if (available_solar_plus > 0 and not excess_to_grid):
					# If surplus is available, always attempt to charge, unless we are flagged EXCESSTOGRID
					self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
					self._device.self_consume(restrictions, w.allow_feedin)
					reactive_strategy = ReactiveStrategy.SELFCONSUME_ACCEPT_CHARGE

				else:
					# so, now we have: (availableSolarPlus <= 0 or solaroverhaed, but excess_to_grid) and (equal or above targetSoc).
					# so, most likely any of the discharge-variants is required (or ultimately idle)
					self.discharge_hysteresis = 0
					self.update_chargerate(now, w.stop, abs(self.soc - w.soc))

					# if we are flagged EXESSTOGRID and MISSINGTOGRID, perform a strict discharge, based on soc difference.
					# Any imprecission shall be handled by the grid
					# not allowed with bat2grid restriction
					if self.soc - self.discharge_hysteresis > max(w.soc, self._device.minsoc) and excess_to_grid and missing_to_grid \
						and not self.restrictions & Restrictions.BAT2GRID:
						reactive_strategy = ReactiveStrategy.SCHEDULED_DISCHARGE

					# if flags are EXCESSTOGRID and MISSINGTOBAT, that means: keep a MINIMUM dischargerate, but allow to discharge more, if consumption is higher.
					# not allowed with bat2grid restriction
					elif self.soc - self.discharge_hysteresis > max(w.soc, self._device.minsoc) and excess_to_grid and missing_to_bat \
						and not self.restrictions & Restrictions.BAT2GRID:
						self.chargerate = max(self.chargerate, self._device.consumption)
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
					elif self.soc - self.discharge_hysteresis > max(w.soc, self._device.minsoc) and not next_window_higher_target_soc:
						reactive_strategy = ReactiveStrategy.SELFCONSUME_ACCEPT_DISCHARGE

					else:
						# Here we are:
						# - Ahead of plan, but the next window indicates a higher soc target.
						# - Spot on target soc, so idling is imminent / bellow targetSoc by charge_hysteresis %.
						# - available solar plus, but intended feedin.
						# All cases should lead to idle, just determine which we have, for debug purpose
						if (self.soc > self.targetsoc and next_window_higher_target_soc):
							# next window has a higher target soc than the current window, so idle to maintin advantage.
							# if a discharge would have been enforced by the schedule, we would already be in SCHEDULED_DISCHARGE case.
							reactive_strategy = ReactiveStrategy.IDLE_MAINTAIN_SURPLUS
						elif available_solar_plus > 0 and excess_to_grid:
							# We have solar surplus, but VRM wants an explicit feedin.
							# since we are above or equal to target soc, we are going idle to achieve that.
							reactive_strategy = ReactiveStrategy.IDLE_SCHEDULED_FEEDIN
						else:
							# else, it's idle due to soc==targetsoc, or soc + charge_hystersis == targetsoc.
							reactive_strategy = ReactiveStrategy.IDLE_MAINTAIN_TARGETSOC
	
		#bellow here, ReactiveStrategy should be determined. As well as chargerate, if required. If it isn't
		#Enter self consume, as conditions may change and situation will resolve. 
		#(This would need to be resolved, there shouldn't be any unpredicted combination of parameters)
		if reactive_strategy is None:
			self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
			self._device.self_consume(restrictions, w.allow_feedin)
			return ReactiveStrategy.SELFCONSUME_UNPREDICTED
		else:
			#depending on the reactive strategy choosen, system behaviour may be the same - just different value set
			#and/or different reasoning.
			if reactive_strategy in self.charge_states:
					self._dbusservice['/DynamicEss/ChargeRate'] = self._device.charge(w.flags, restrictions, self.chargerate, w.allow_feedin)

			elif reactive_strategy in self.selfconsume_states:
				self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
				self._device.self_consume(restrictions, w.allow_feedin)

			elif reactive_strategy in self.idle_states:
				self._dbusservice['/DynamicEss/ChargeRate'] = self._device.idle(w.allow_feedin)

			elif reactive_strategy in self.discharge_states:	
				self._dbusservice['/DynamicEss/ChargeRate'] = self._device.discharge(w.flags, restrictions, self.chargerate, w.allow_feedin)

			else:
				#This should never happen, it means that there is a state that is not mapped to a reaction. 
				#We enter self consume and use a own state for that :P 
				self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
				self._device.self_consume(restrictions, w.allow_feedin)
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