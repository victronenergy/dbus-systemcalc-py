from __future__ import division
from enum import IntEnum
import logging
from gi.repository import GLib
from datetime import datetime, timedelta, time, date

# Victron packages
from ve_utils import exit_on_error
from delegates.base import SystemCalcDelegate
from delegates.batterylife import BatteryLife, BLPATH
from delegates.batterylife import State as BatteryLifeState
from delegates.dvcc import Dvcc
from delegates.batterysoc import BatterySoc

from delegates.chargecontrol import ChargeControl

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
		elif w == 9:
			# weekend days
			if adate.weekday() < 5:
				return next_week_day(adate, 6) # Saturday
			return adate

		# w >=10, 11 = monthly
		if adate.day == 1:
			return adate
		return (adate.replace(day=1) + timedelta(days=31)).replace(day=1)

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
		elif w == 9:
			# weekend days
			if 0 < adate.weekday() < 5:
				return prev_week_day(adate, 0) # Sunday
			return adate - timedelta(days=1)

		# w >= 10, 11 = monthly
		if adate.day == 1:
			return (adate - timedelta(days=1)).replace(day=1)
		return adate.replace(day=1)

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

class EssDevice(object):
	def __init__(self, delegate, monitor, service):
		self.delegate = delegate
		self.monitor = monitor
		self.service = service

	def check_conditions(self):
		return 0

	def _forcecharge(self):
		raise NotImplementedError("forcecharge")

	def _set_forcecharge(self, v):
		raise NotImplementedError("forcecharge")

	def _maxdischargepower(self):
		raise NotImplementedError("maxdischargepower")

	def _set_maxdischargepower(self, v):
		raise NotImplementedError("maxdischargepower")

	@property
	def forcecharge(self):
		return self._forcecharge()

	@forcecharge.setter
	def forcecharge(self, v):
		return self._set_forcecharge(v)

	@property
	def maxdischargepower(self):
		return self._maxdischargepower()

	@maxdischargepower.setter
	def maxdischargepower(self, v):
		return self._set_maxdischargepower(v)

class VebusDevice(EssDevice):
	def check_conditions(self):
		if not Dvcc.instance.has_ess_assistant:
			return Reasons.NO_ESS
		if BatteryLife.instance.state == BatteryLifeState.KeepCharged:
			return Reasons.ESS_MODE
		return 0

	def _forcecharge(self):
		return self.monitor.get_value(HUB4_SERVICE, '/Overrides/ForceCharge')

	def _set_forcecharge(self, v):
		return self.monitor.set_value_async(HUB4_SERVICE,
			'/Overrides/ForceCharge', 1 if v else 0)

	def _maxdischargepower(self):
		return self.monitor.get_value(HUB4_SERVICE,
			'/Overrides/MaxDischargePower')

	def _set_maxdischargepower(self, v):
		return self.monitor.set_value_async(HUB4_SERVICE,
			'/Overrides/MaxDischargePower', -1 if v is None else v)

class MultiRsDevice(EssDevice):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.charge = False

	@property
	def mode(self):
		return self.monitor.get_value(self.service, '/Settings/Ess/Mode')

	def check_conditions(self):
		if self.mode not in (0, 1):
			return Reasons.ESS_MODE
		return 0

	def _forcecharge(self):
		return self.charge

	def _set_forcecharge(self, v):
		self.charge = bool(v)

	def _maxdischargepower(self):
		return None # We only ever set this

	def _set_maxdischargepower(self, v):
		# Setting the maxdischargepower always happens last, so we can
		# include charging decisions here
		if self.charge:
			self.monitor.set_value_async(self.service,
				'/Ess/UseInverterPowerSetpoint', 1)
			self.monitor.set_value_async(self.service,
				'/Ess/InverterPowerSetpoint', 15000)
		elif v is not None:
			self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 1)
			self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', -v)
		else:
			self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 0)

class Reasons(IntEnum):
	OK = 0
	NO_ESS = 1
	NO_SOC = 2
	ESS_MODE = 3
	BLOCKED = 4

	@classmethod
	def get_text(cls, v):
		try:
			return {v: k for k, v in cls.__members__.items()}[v]
		except KeyError:
			return '--'

class ScheduledCharging(SystemCalcDelegate, ChargeControl):
	""" Let the system do other things based on time schedule. """
	control_priority = 20
	_get_time = datetime.now

	def __init__(self):
		super(ScheduledCharging, self).__init__()
		self.pvpower = 0
		self.active = False
		self.hysteresis = True
		self.devices = []
		self._timer = GLib.timeout_add(5000, exit_on_error, self._on_timer)

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(ScheduledCharging, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/Control/ScheduledCharge', value=0)
		self._dbusservice.add_path('/Control/ScheduledChargeStatus', value=None,
			gettextcallback=lambda p, v: Reasons.get_text(v))
		self._dbusservice.add_path('/Control/ScheduledSoc', value=None,
			gettextcallback=lambda p, v: '{}%'.format(v))

		# Assume a VE.Bus device is present. If not, check_conditions() will
		# return non-zero.
		self.devices.append(VebusDevice(self, dbusmonitor, None))

	def get_input(self):
		return [
			(HUB4_SERVICE, [
				'/Overrides/ForceCharge',
				'/Overrides/MaxDischargePower']),
			('com.victronenergy.acsystem', [
				 '/Ess/InverterPowerSetpoint',
				 '/Ess/UseInverterPowerSetpoint',
				 '/Settings/Ess/Mode']),
		]

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.acsystem.'):
			self.device_removed(service, instance) # Avoid duplicates
			self.devices.append(MultiRsDevice(self, self._dbusmonitor, service))

	def device_removed(self, service, instance):
		# Just rebuild the list and drop the one that went away
		self.devices = [d for d in self.devices if d.service != service]

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
				BLPATH + "/Schedule/Charge/{}/Day".format(i), -7, -11, 11))
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

	def _on_timer(self):
		# Another delegate controls charging
		if not self.can_acquire_control:
			self._dbusservice['/Control/ScheduledChargeStatus'] = Reasons.BLOCKED
			self._dbusservice['/Control/ScheduledCharge'] = 0
			self._dbusservice['/Control/ScheduledSoc'] = None
			return True

		if self.soc is None:
			self._dbusservice['/Control/ScheduledChargeStatus'] = Reasons.NO_SOC
			self._dbusservice['/Control/ScheduledCharge'] = 0
			self._dbusservice['/Control/ScheduledSoc'] = None
			return True

		condition = Reasons.NO_ESS
		for device in self.devices:
			if (condition := device.check_conditions()) == 0:
				break
		else:
			self._dbusservice['/Control/ScheduledChargeStatus'] = condition
			self._dbusservice['/Control/ScheduledCharge'] = 0
			self._dbusservice['/Control/ScheduledSoc'] = None
			return True

		now = self._get_time()
		today = now.date()

		for w in self.charge_windows(today):
			if now in w:
				if w.soc_reached(self.soc):
					device.forcecharge = False
				elif self.hysteresis and w.soc_reached(self.soc + 5):
					# If we are within 5%, keep it the same, but write it to
					# avoid a timeout.
					device.forcecharge = device.forcecharge
				else:
					# SoC not reached yet
					# Note: soc_reached always returns False for a target of
					# 100%, so this is the only branch that is ever excuted
					# in those cases.
					device.forcecharge = True

				# Signal that scheduled charging is active
				self.acquire_control() # Block out other controllers
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
				if device.forcecharge:
					device.maxdischargepower = None
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
					device.maxdischargepower = None
				else:
					scale = 0.8 + min(delta, 1)*0.15
					device.maxdischargepower = max(1, round(self.pvpower*scale))
				break
		else:
			if self.has_control():
				device.forcecharge = False
				device.maxdischargepower = None
			self.active = False
			self.release_control()
			self._dbusservice['/Control/ScheduledSoc'] = None

		self._dbusservice['/Control/ScheduledCharge'] = int(self.active)
		self._dbusservice['/Control/ScheduledChargeStatus'] = Reasons.OK
		self.hysteresis = True
		return True

	@property
	def soc(self):
		return BatterySoc.instance.soc

	def update_values(self, newvalues):
		self.pvpower = newvalues.get('/Dc/Pv/Power') or 0
