import logging
import gobject
from datetime import datetime, timedelta, time
from itertools import izip, imap
from functools import partial

# Victron packages
from ve_utils import exit_on_error
from delegates.base import SystemCalcDelegate

# Path constants
BLPATH = "/Settings/CGwacs/BatteryLife";
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
		return False if self.soc >= 100 else s >= self.soc

	def __repr__(self):
		return "Start charge: {}, Stop: {}, Soc: {}".format(
			self.start, self.stop, self.soc)

class ScheduledCharging(SystemCalcDelegate):
	""" Let the system do other things based on time schedule. """

	_get_time = datetime.now

	def __init__(self):
		super(ScheduledCharging, self).__init__()
		self.soc = None
		self.active = False
		self._timer = gobject.timeout_add(5000, exit_on_error, self._on_timer)

	def get_input(self):
		return [
			(HUB4_SERVICE, ['/Overrides/ForceCharge', '/Overrides/MaxDischargePower'])
		]

	def get_settings(self):
		settings = []

		# Paths for scheduled charging. We'll allow 4 for now.
		for i in range(NUM_SCHEDULES):
			settings.append(("schedule_day_{}".format(i),
				BLPATH + "/Schedule/Charge/{}/Day".format(i), -1, -1, 9))
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

	def _on_timer(self):
		if self.soc is None:
			return True

		now = self._get_time()
		today = now.date()

		for w in self.charge_windows(today):
			if now in w:
				# If the target SoC has been reached, but we are still in
				# the charge window, disable discharge to hold the SoC. There
				# may however be other chargers in the system, so if we
				# inexplicably end up 3% above that, re-enable discharge.
				if w.soc_reached(self.soc):
					self._dbusmonitor.set_value(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
					if w.soc_reached(self.soc-3):
						self._dbusmonitor.set_value(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1)
					else:
						self._dbusmonitor.set_value(HUB4_SERVICE, '/Overrides/MaxDischargePower', 0)
				else:
					self._dbusmonitor.set_value(HUB4_SERVICE, '/Overrides/ForceCharge', 1)
					self._dbusmonitor.set_value(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1)
				self.active = True
				break
		else:
				self._dbusmonitor.set_value(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
				self._dbusmonitor.set_value(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1)
				self.active = False

		return True

	def update_values(self, newvalues):
		self.soc = newvalues.get('/Dc/Battery/Soc')
