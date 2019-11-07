import logging
import gobject
from datetime import datetime, timedelta, time
from itertools import izip, imap
from functools import partial

# Victron packages
from ve_utils import exit_on_error
from delegates.base import SystemCalcDelegate
from delegates.batterylife import BatteryLife, BLPATH
from delegates.batterylife import State as BatteryLifeState
from delegates.dvcc import Dvcc

HUB4_SERVICE = 'com.victronenergy.hub4'

# Number of scheduled charge slots
NUM_SCHEDULES = 5

def prev_week_day(adate, w):
	""" finds the previous w-day of the week before adate.
	    Sun=0 or 7, Sat=6, that is what unix uses. """
	w %= 7
	return adate - timedelta(days=(adate.weekday()+7-w) % 7 + 1)

def next_week_day(adate, w):
	""" Finds the next w-day after or equal to adate.
	    Sun=0 or 7, Sat=6, that is what unix uses. """
	w %= 7
	return adate + timedelta(days=(w - adate.weekday() - 1) % 7)

def next_schedule_day(adate, w):
		if w < 7:
			# A specific week day
			return next_week_day(adate, w)
		elif w == 7:
			# 7 days a week
			return adate
		elif w == 8:
			# week days
			if adate.weekday() > 4:
				return next_week_day(adate, 1) # Monday
			return adate

		# w >=9, weekend days
		if adate.weekday() < 5:
			return next_week_day(adate, 6) # Saturday
		return adate

def prev_schedule_day(adate, w):
		if w < 7:
			# A specific week day
			return prev_week_day(adate, w)
		elif w == 7:
			# 7 days a week
			return adate - timedelta(days=1)
		elif w == 8:
			# week days
			if adate.weekday() in (0, 6):
				return prev_week_day(adate, 5) # Mon,Sun preceded by Friday
			return adate - timedelta(days=1)

		# w >= 9, weekend days
		if 0 < adate.weekday() < 5:
			return prev_week_day(adate, 0) # Sunday
		return adate - timedelta(days=1)

class ScheduledWindow(object):
	def __init__(self, starttime, duration):
		self.start = starttime
		self.stop = self.start + timedelta(seconds=duration)

	def __contains__(self, t):
		return self.start <= t < self.stop

	def __eq__(self, other):
		return self.start == other.start and self.stop == other.stop

	def __repr__(self):
		return "Start: {}, Stop: {}".format(self.start, self.stop)

class ScheduledChargeWindow(ScheduledWindow):
	def __init__(self, starttime, duration, soc):
		super(ScheduledChargeWindow, self).__init__(starttime, duration)
		self.soc = soc

	def soc_reached(self, s):
		return not self.soc >= 100 and s >= self.soc

	def __repr__(self):
		return "Start charge: {}, Stop: {}, Soc: {}".format(
			self.start, self.stop, self.soc)

class ScheduledCharging(SystemCalcDelegate):
	""" Let the system do other things based on time schedule. """

	_get_time = datetime.now

	def __init__(self):
		super(ScheduledCharging, self).__init__()
		self.soc = None
		self.pvpower = 0
		self.active = False
		self.hysteresis = True
		self._timer = gobject.timeout_add(5000, exit_on_error, self._on_timer)

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/ScheduledCharge', value=0)

	def get_input(self):
		return [
			(HUB4_SERVICE, ['/Overrides/ForceCharge', '/Overrides/MaxDischargePower'])
		]

	def settings_changed(self, setting, oldvalue, newvalue):
		if setting.startswith("schedule_soc_"):
			# target SOC was modified. Disable the hysteresis on the next
			# run.
			self.hysteresis = False

	def get_settings(self):
		settings = []

		# Paths for scheduled charging. We'll allow 5 for now.
		for i in range(NUM_SCHEDULES):
			settings.append(("schedule_day_{}".format(i),
				BLPATH + "/Schedule/Charge/{}/Day".format(i), -7, -10, 9))
			settings.append(("schedule_start_{}".format(i),
				BLPATH + "/Schedule/Charge/{}/Start".format(i), 0, 0, 0))
			settings.append(("schedule_duration_{}".format(i),
				BLPATH + "/Schedule/Charge/{}/Duration".format(i), 0, 0, 0))
			settings.append(("schedule_soc_{}".format(i),
				BLPATH + "/Schedule/Charge/{}/Soc".format(i), 100, 0, 100))

		return settings

	@classmethod
	def _charge_windows(klass, today, days, starttimes, durations, stopsocs):
		starttimes = (time(x/3600, x/60 % 60, x % 60) for x in starttimes)

		for d, starttime, duration, soc in izip(days, starttimes, durations, stopsocs):
			if d >= 0:
				d0 = prev_schedule_day(today, d)
				d1 = next_schedule_day(today, d)
				yield ScheduledChargeWindow(
					datetime.combine(d0, starttime), duration, soc)
				yield ScheduledChargeWindow(
					datetime.combine(d1, starttime), duration, soc)

	def charge_windows(self, today):
		days = (self._settings['schedule_day_{}'.format(i)] for i in range(NUM_SCHEDULES))
		starttimes = (self._settings['schedule_start_{}'.format(i)] for i in range(NUM_SCHEDULES))
		durations = (self._settings['schedule_duration_{}'.format(i)] for i in range(NUM_SCHEDULES))
		stopsocs = (self._settings['schedule_soc_{}'.format(i)] for i in range(NUM_SCHEDULES))
		return self._charge_windows(today, days, starttimes, durations, stopsocs)

	@property
	def forcecharge(self):
		return self._dbusmonitor.get_value(HUB4_SERVICE, '/Overrides/ForceCharge')

	@forcecharge.setter
	def forcecharge(self, v):
		return self._dbusmonitor.set_value_async(HUB4_SERVICE,
			'/Overrides/ForceCharge', 1 if v else 0)

	@property
	def maxdischargepower(self):
		return self._dbusmonitor.get_value(HUB4_SERVICE, '/Overrides/MaxDischargePower')

	@maxdischargepower.setter
	def maxdischargepower(self, v):
		return self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', v)

	def _on_timer(self):
		if self.soc is None:
			return True

		if not Dvcc.instance.has_ess_assistant:
			return True

		if BatteryLife.instance.state == BatteryLifeState.KeepCharged:
			self._dbusservice['/Control/ScheduledCharge'] = 0
			return True

		now = self._get_time()
		today = now.date()

		for w in self.charge_windows(today):
			if now in w:
				if w.soc_reached(self.soc):
					self.forcecharge = False
				elif self.hysteresis and w.soc_reached(self.soc + 5):
					# If we are within 5%, keep it the same, but write it to
					# avoid a timeout.
					self.forcecharge = self.forcecharge
				else:
					# SoC not reached yet
					# Note: soc_reached always returns False for a target of
					# 100%, so this is the only branch that is ever excuted
					# in those cases.
					self.forcecharge = True

				# Signal that scheduled charging is active
				self.active = True

				# The discharge is limited to 1W or whatever is available
				# from PV. 1W essentially disables discharge without
				# disabling feed-in, so Power-Assist and feeding in
				# the excess continues to work. Setting this to the pv-power
				# causes it to directly consume the PV for loads and charge
				# only with the excess. Scale it between 80% and 93%
				# of PV-power depending on the SOC.
				scale = 0.8 + min(max(0, self.soc - w.soc), 1.3)*0.1
				self.maxdischargepower = max(1, round(self.pvpower*scale))
				break
		else:
			self.forcecharge = False
			self.maxdischargepower = -1
			self.active = False

		self._dbusservice['/Control/ScheduledCharge'] = int(self.active)
		self.hysteresis = True
		return True

	def update_values(self, newvalues):
		self.soc = newvalues.get('/Dc/Battery/Soc')
		self.pvpower = max(newvalues.get('/Dc/Pv/Power'), 0)
