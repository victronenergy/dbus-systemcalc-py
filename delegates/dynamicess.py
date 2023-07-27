from datetime import datetime
from gi.repository import GLib
from delegates.base import SystemCalcDelegate
from delegates.batterysoc import BatterySoc
from delegates.schedule import ScheduledWindow
from delegates.dvcc import Dvcc

NUM_SCHEDULES = 4
INTERVAL = 5
SELLPOWER = -32000
HUB4_SERVICE = 'com.victronenergy.hub4'

MODES = {
       0: 'Off',
       1: 'Auto',
       2: 'Buy',
       3: 'Sell'
}

class DynamicEssWindow(ScheduledWindow):
	def __init__(self, start, duration, soc, allow_feedin):
		super(DynamicEssWindow, self).__init__(start, duration)
		self.soc = soc
		self.allow_feedin = allow_feedin

	def __repr__(self):
		return "Start: {}, Stop: {}, Soc: {}".format(
			self.start, self.stop, self.soc)

class DynamicEss(SystemCalcDelegate):
	_get_time = datetime.now

	def __init__(self):
		super(DynamicEss, self).__init__()
		self.hysteresis = 0
		self._timer = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(DynamicEss, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/DynamicEss/Active', value=0,
			gettextcallback=lambda p, v: MODES.get(v, 'Unknown'))
		self._dbusservice.add_path('/DynamicEss/TargetSoc', value=None,
			gettextcallback=lambda p, v: '{}%'.format(v))

		if self.mode > 0:
			self._timer = GLib.timeout_add(INTERVAL * 1000, self._on_timer)

	def get_settings(self):
		# Settings for DynamicEss
		path = '/Settings/DynamicEss'

		settings = [
			("dess_mode", path + "/Mode", 0, 0, 3),
			("dess_minsoc", path + "/MinSoc", 20.0, 0.0, 100.0)
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

		return settings

	def get_input(self):
		return [
			(HUB4_SERVICE, ['/Overrides/ForceCharge',
				'/Overrides/MaxDischargePower', '/Overrides/Setpoint',
				'/Overrides/FeedInExcess'])
		]

	def settings_changed(self, setting, oldvalue, newvalue):
		if setting == 'dess_mode':
			if oldvalue == 0 and newvalue > 0:
				self._timer = GLib.timeout_add(INTERVAL * 1000, self._on_timer)

	def windows(self):
		starttimes = (self._settings['dess_start_{}'.format(i)] for i in range(NUM_SCHEDULES))
		durations = (self._settings['dess_duration_{}'.format(i)] for i in range(NUM_SCHEDULES))
		socs = (self._settings['dess_soc_{}'.format(i)] for i in range(NUM_SCHEDULES))
		discharges = (self._settings['dess_discharge_{}'.format(i)] for i in range(NUM_SCHEDULES))

		for start, duration, soc, discharge in zip(starttimes, durations, socs, discharges):
			yield DynamicEssWindow(
				datetime.fromtimestamp(start), duration, soc, discharge)

	@property
	def mode(self):
		return self._settings['dess_mode']

	@property
	def minsoc(self):
		return self._settings['dess_minsoc']

	@property
	def active(self):
		return self._dbusservice['/DynamicEss/Active']

	@active.setter
	def active(self, v):
		self._dbusservice['/DynamicEss/Active'] = v

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

	def _on_timer(self):
		# Can't do anything unless we have an SOC, and the ESS assistant
		if self.soc is None:
			self.active = 0 # Off
			self.targetsoc = None
			return True
		if not Dvcc.instance.has_ess_assistant:
			self.active = 0 # Off
			self.targetsoc = None
			return True

		# If DESS was disabled, deactivate and kill timer.
		if self.mode == 0:
			self.deactivate()
			return False

		if self.mode == 2: # BUY
			self.active = 2
			self.targetsoc = None
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 1)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 1)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
			return True

		if self.mode == 3: # SELL
			self.active = 3
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 2)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', SELLPOWER)
			self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
			return True

		# self.mode == 1 (Auto) below here
		# If our SOC is too low, stop DESS
		if self.soc <= self.minsoc:
			self.deactivate()
			return True

		now = self._get_time()
		for w in self.windows():
			if now in w:
				self.active = 1 # Auto
				self.targetsoc = w.soc

				# If schedule allows for feed-in, enable that now.
				self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess',
					2 if w.allow_feedin else 1)

				if self.soc + self.hysteresis < w.soc: # Charge
					self.hysteresis = 0
					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 1)
					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
				else: # Discharge or idle
					self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)

					if self.soc - self.hysteresis > w.soc: # Discharge
						self.hysteresis = 0
						self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
					else: # battery idle
						# SOC/target-soc needs to move 1% to move out of idle
						# zone
						self.hysteresis = 1
						# This keeps battery idle by not allowing more power
						# to be taken from the DC bus than what DC-coupled
						# PV provides.
						self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower',
							max(1.0, round(0.9*self.pvpower)))

					# If Feed-in is requested, set a large negative setpoint.
					# The battery limit above will ensure that no more than
					# available PV is fed in.
					if w.allow_feedin:
						self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', SELLPOWER)
					else:
						self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None) # Normal ESS
				break # out of for loop
		else:
			# No matching windows
			if self.active:
				self.deactivate()

		return True

	def deactivate(self):
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 0)
		self.active = 0 # Off
		self.targetsoc = None
