import logging
import gobject
from datetime import datetime, timedelta

# Victron packages
from ve_utils import exit_on_error
from delegates.base import SystemCalcDelegate

# Path constants
BLPATH = "/Settings/CGwacs/BatteryLife";
STATE_PATH = BLPATH + "/State";
FLAGS_PATH = BLPATH + "/Flags";
SOC_LIMIT_PATH = BLPATH + "/SocLimit";
MIN_SOC_LIMIT_PATH = BLPATH + "/MinimumSocLimit";
DISCHARGED_TIME_PATH = BLPATH + "/DischargedTime";
DISCHARGED_SOC_PATH = BLPATH + "/DischargedSoc"

class State(object):
	BLDisabled = 0
	BLRestart = 1
	BLDefault = 2
	BLAbsorption = 3
	BLFloat = 4
	BLDischarged = 5
	BLForceCharge = 6
	BLSustain = 7
	BLLowSocCharge = 8
	KeepCharged = 9
	SocGuardDefault = 10
	SocGuardDischarged = 11
	SocGuardLowSocCharge = 12

class Flags(object):
	Float = 0x01
	Absorption = 0x02
	Discharged = 0x04

class Constants(object):
	SocSwitchOffset = 3.0
	SocSwitchIncrement = 5.0
	SocSwitchDefaultMin = 10.0
	LowSocChargeOffset = 5.0
	AbsorptionLevel = 85.0
	FloatLevel = 95.0
	SocSwitchMax = AbsorptionLevel - SocSwitchIncrement
	ForceChargeCurrent = 5.0
	ForceChargeInterval = 24 * 60 * 60 # 24 Hours

def bound(low, v, high):
	return max(low, min(v, high))

def dt_to_stamp(dt):
	""" Return UTC timestamp for datetime dt. """
	return (dt - datetime(1970, 1, 1)).total_seconds()

class BatteryLife(SystemCalcDelegate):
	""" Calculates the ESS CGwacs state. """

	# Items we want to track from systemcalc
	_tracked_attrs = {'soc': '/Dc/Battery/Soc', 'vebus': '/VebusService'}

	def __init__(self):
		super(BatteryLife, self).__init__()
		self._tracked_values = {}
		self._timer = gobject.timeout_add(900000, exit_on_error, self._on_timer)

	def get_input(self):
		# We need to check the assistantid to know if we should even be active.
		# We also need to check the sustain flag.
		return [
			('com.victronenergy.vebus', [
				'/Hub4/AssistantId',
				'/Hub4/Sustain']),
			('com.victronenergy.settings', [
				STATE_PATH, FLAGS_PATH, DISCHARGED_TIME_PATH,
				DISCHARGED_SOC_PATH, SOC_LIMIT_PATH, MIN_SOC_LIMIT_PATH])
		]

	def get_output(self):
		return []

	def get_settings(self):
		return [
			('state', STATE_PATH, 1, 0, 0, 1),
			('flags', FLAGS_PATH, 0, 0, 0, 1),
			('dischargedtime', DISCHARGED_TIME_PATH, 0, 0, 0, 1),
			('soclimit', SOC_LIMIT_PATH, 10.0, 0, 100, 1),
			('minsoclimit', MIN_SOC_LIMIT_PATH, 10.0, 0, 100),
		]

	_get_time = datetime.now

	@property
	def state(self):
		return self._settings['state']

	@state.setter
	def state(self, v):
		v = int(v)
		if self._settings['state'] != v:
			self._settings['state'] = v

	@property
	def flags(self):
		return self._settings['flags']

	@flags.setter
	def flags(self, v):
		self._settings['flags'] = v

	def _disabled(self):
		if self._dbusmonitor.get_value(self.vebus, '/Hub4/AssistantId') is not None:
			return State.BLRestart

	def _restart(self):
		# Do the same as in the default case
		return self._default(False)

	@property
	def is_active_soc_low(self):
		limit = self.active_soclimit
		return self.sustain or (limit > 0 and self.soc <= limit and self.soc < 100)

	def _default(self, adjust=True):
		if self.is_active_soc_low:
			return self.on_discharged(adjust)
		elif self.soc >= Constants.FloatLevel:
			return self.on_float(adjust)
		elif self.soc >= Constants.AbsorptionLevel:
			return self.on_absorption(adjust)

		# Remain in default state
		return State.BLDefault

	def _discharged(self):
		if not self.sustain and (self.soc > self.switch_on_soc or self.soc >= 100):
			return State.BLDefault
		elif self.soc <= self.minsoclimit - Constants.LowSocChargeOffset:
			return State.BLLowSocCharge

	def _lowsoccharge(self):
		# We stop charging when we get back to the SoC we had when we entered
		# the discharged state. If we switched into discharged state at 0%,
		# we will enter LowSocCharge, so we should not switch out until
		# we picked up at least to 3% (SocSwitchOffset).
		if self.soc >= min(100, max(self.minsoclimit, Constants.SocSwitchOffset)):
			return State.BLDischarged

	def _forcecharge(self):
		if not self.sustain and (self.soc > self.active_soclimit or self.soc >= 100):
			self.dischargedtime = dt_to_stamp(self._get_time())
			return State.BLDischarged

	def _absorption(self):
		if self.is_active_soc_low:
			return self.on_discharged(True)
		elif self.soc > Constants.FloatLevel:
			return self.on_float(True)
		elif self.soc < Constants.AbsorptionLevel - Constants.SocSwitchOffset:
			return State.BLDefault

	def _float(self):
		if self.is_active_soc_low:
			return self.on_discharged(True)
		elif self.soc < Constants.FloatLevel - Constants.SocSwitchOffset:
			return State.BLAbsorption

	def _socguard_default(self):
		if self.soc < 100 and self.minsoclimit > 0 and self.soc <= self.minsoclimit:
			return State.SocGuardDischarged

	def _socguard_discharged(self):
		if self.soc >= min(100, self.minsoclimit + Constants.SocSwitchOffset):
			return State.SocGuardDefault
		elif self.soc <= self.minsoclimit - Constants.LowSocChargeOffset:
			return State.SocGuardLowSocCharge

	def _socguard_lowsoccharge(self):
		if self.soc >= min(100, self.minsoclimit):
			return State.SocGuardDischarged

	def adjust_soc_limit(self, delta):
		limit = max(self._settings['minsoclimit'],
			self._settings['soclimit']) + delta
		self._settings['soclimit'] = bound(0.0, limit, Constants.SocSwitchMax)

	def on_discharged(self, adjust):
		if adjust:
			if not self.flags & Flags.Discharged:
				self.flags |= Flags.Discharged
				self.adjust_soc_limit(Constants.SocSwitchIncrement)
			self.dischargedtime = dt_to_stamp(self._get_time())
		return State.BLSustain if self.sustain else State.BLDischarged

	def on_absorption(self, adjust):
		if adjust and not self.flags & Flags.Absorption:
			self.flags |= Flags.Absorption
			self.adjust_soc_limit(-Constants.SocSwitchIncrement)
		return State.BLAbsorption

	def on_float(self, adjust):
		offset = 0
		flags = self.flags
		if adjust:
			if not (flags & Flags.Absorption):
				offset -= Constants.SocSwitchIncrement
				flags |= Flags.Absorption
			if not (flags & Flags.Float):
				offset -= Constants.SocSwitchIncrement
				flags |= Flags.Float
			self.flags = flags
			self.adjust_soc_limit(offset)
		return State.BLFloat

	_map = {
		State.BLDisabled: _disabled,
		State.BLRestart: _restart,
		State.BLDefault: _default,
		State.BLAbsorption: _absorption,
		State.BLFloat: _float,
		State.BLDischarged: _discharged,
		State.BLForceCharge: _forcecharge,
		State.BLSustain: _discharged,
		State.BLLowSocCharge: _lowsoccharge,
		State.KeepCharged: lambda s: State.KeepCharged,
		State.SocGuardDefault: _socguard_default,
		State.SocGuardDischarged: _socguard_discharged,
		State.SocGuardLowSocCharge: _socguard_lowsoccharge,
	}

	@property
	def sustain(self):
		return self._dbusmonitor.get_value(self.vebus, '/Hub4/Sustain')

	@property
	def soclimit(self):
		return self._settings['soclimit']

	@property
	def minsoclimit(self):
		return self._settings['minsoclimit']

	@property
	def active_soclimit(self):
		m = self._settings['minsoclimit']
		l = self._settings['soclimit']
		if m > Constants.SocSwitchMax:
			return m
		return bound(0, max(m, l), Constants.SocSwitchMax)

	@property
	def switch_on_soc(self):
		""" This property determines when we go from Discharged state to
		    Default state. """
		return self.active_soclimit + Constants.SocSwitchOffset

	@property
	def dischargedtime(self):
		return self._settings['dischargedtime']

	@dischargedtime.setter
	def dischargedtime(self, v):
		self._settings['dischargedtime'] = int(v)

	def __getattr__(self, k):
		""" Make our tracked values available as attributes, makes the
			code look neater. """
		try:
			return self._tracked_values[k]
		except KeyError:
			raise AttributeError(k)

	def update_values(self, newvalues):
		# Update tracked attributes
		for k, v in self._tracked_attrs.iteritems():
			self._tracked_values[k] = newvalues.get(v)

		# Cannot start without a multi or an soc
		if self.vebus is None or self.soc is None:
			logging.debug("[BatteryLife] No vebus or no valid SoC")
			return

		# Cannot start without ESS available
		if self._dbusmonitor.get_value(self.vebus, '/Hub4/AssistantId') is None:
			logging.debug("[BatteryLife] No ESS Assistant found")
			return

		# The values we received might transition our state machine through
		# more than one state. For example,
		# 1. At startup
		#    BLRestart -> BLDefault
		# 2. multi detected with very low soc:
		#    BLDisabled -> BLRestart -> BLDefault -> BLDischarged -> BLLowSocCharge
		# 3. Sudden drop in SoC
		#    BLDefault -> BLDischarged -> BLLowSocCharge
		newstate = self.state
		for _ in range(5):
			_newstate = self._map.get(newstate, lambda s: State.BLDefault)(self)
			if _newstate is None or _newstate == newstate: break
			newstate = _newstate
		self.state = newstate

	def _on_timer(self):
		now = self._get_time()

		# Test for the first 15-minute window of the day, and clear the flags
		if now.hour == 0 and now.minute < 15:
			self.flags = 0

		if self.state in (State.BLDischarged, State.BLSustain):
			# load dischargedtime, it's a unix timestamp, ie UTC
			if self.dischargedtime:
				dt = datetime.fromtimestamp(self.dischargedtime)
				if now - dt > timedelta(seconds=Constants.ForceChargeInterval):
					self.adjust_soc_limit(Constants.SocSwitchIncrement)
					self.state = State.BLForceCharge
			else:
				self.dischargedtime = dt_to_stamp(now)

		return True
