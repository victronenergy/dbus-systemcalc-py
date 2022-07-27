from __future__ import division
import logging
from gi.repository import GLib
from datetime import datetime, timedelta, time

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
	def __init__(self, starttime, duration, soc, allow_discharge):
		super(ScheduledChargeWindow, self).__init__(starttime, duration)
		self.soc = soc
		self.allow_discharge = allow_discharge

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
		self._timer = GLib.timeout_add(5000, exit_on_error, self._on_timer)

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/ScheduledCharge', value=0)
		self._dbusservice.add_path('/Control/ScheduledSoc', value=None,
			gettextcallback=lambda p, v: '{}%'.format(v))

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
			settings.append(("schedule_discharge_{}".format(i),
				BLPATH + "/Schedule/Charge/{}/AllowDischarge".format(i), 0, 0, 1))

		return settings

	@classmethod
	def _charge_windows(klass, today, days, starttimes, durations, stopsocs, discharges):
		starttimes = (time(x//3600, x//60 % 60, x % 60) for x in starttimes)

		for d, starttime, duration, soc, discharge in zip(days, starttimes, durations, stopsocs, discharges):
			if d >= 0:
				d0 = prev_schedule_day(today, d)
				d1 = next_schedule_day(today, d)
				yield ScheduledChargeWindow(
					datetime.combine(d0, starttime), duration, soc, discharge)
				yield ScheduledChargeWindow(
					datetime.combine(d1, starttime), duration, soc, discharge)

	def charge_windows(self, today):
		days = (self._settings['schedule_day_{}'.format(i)] for i in range(NUM_SCHEDULES))
		starttimes = (self._settings['schedule_start_{}'.format(i)] for i in range(NUM_SCHEDULES))
		durations = (self._settings['schedule_duration_{}'.format(i)] for i in range(NUM_SCHEDULES))
		stopsocs = (self._settings['schedule_soc_{}'.format(i)] for i in range(NUM_SCHEDULES))
		discharges = (self._settings['schedule_discharge_{}'.format(i)] for i in range(NUM_SCHEDULES))
		return self._charge_windows(today, days, starttimes, durations, stopsocs, discharges)

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
			self._dbusservice['/Control/ScheduledSoc'] = None
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
				self._dbusservice['/Control/ScheduledSoc'] = w.soc

				# If we are force-charging, that means in hub4control the mode
				# is set to either MaxoutSetpoint or SetpointIsMaxFeedIn. When
				# it is set to SetpointIsMaxFeedIn, the discharge limit affects
				# the maximum feed-in, and setting this to too low a value (at
				# 100%) will break feeding in of excess PV. Therefore avoid
				# setting a discharge limit if we're currently charging, in
				# other words, if we're below the target soc, or if the target
				# soc is 100%.
				if self.forcecharge:
					self.maxdischargepower = -1
					break # from the for loop, skip the else clause below.

				# If we are here, it means the battery has reached the target
				# soc, and the target was less than 100%. If the SOC is close
				# to the target, we want to keep it there by limiting the
				# discharge power to available PV, so it settles slightly
				# above the requested target. If the SOC is above the target
				# by some margin, we want to allow normal discharge.
				#
				# If the SOC is within 1% of the target, then pass through
				# between 80% and 95% of the PV power depending on how far
				# over we are. If 1% or more over, and discharge is allowed,
				# then do normal discharge.
				#
				# The ESS MinSoc is still obeyed and takes precedence.
				delta = max(0, self.soc - w.soc)
				if delta > 1 and w.allow_discharge:
					self.maxdischargepower = -1
				else:
					scale = 0.8 + min(delta, 1)*0.15
					self.maxdischargepower = max(1, round(self.pvpower*scale))
				break
		else:
			self.forcecharge = False
			self.maxdischargepower = -1
			self.active = False
			self._dbusservice['/Control/ScheduledSoc'] = None

		self._dbusservice['/Control/ScheduledCharge'] = int(self.active)
		self.hysteresis = True
		return True

	def update_values(self, newvalues):
		self.soc = newvalues.get('/Dc/Battery/Soc')
		self.pvpower = newvalues.get('/Dc/Pv/Power') or 0
