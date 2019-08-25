#!/usr/bin/env python
from datetime import datetime, date, time, timedelta

# This adapts sys.path to include all relevant packages
import context

# Testing tools
from mock_gobject import timer_manager

# our own packages
import dbus_systemcalc
from delegates import ScheduledCharging
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

# Time travel patch
ScheduledCharging._get_time = lambda *a: timer_manager.datetime

class TestSchedule(TestSystemCalcBase):
	vebus = 'com.victronenergy.vebus.ttyO1'
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device(self.vebus, product_name='Multi',
			values={
				'/Devices/0/Assistants': [0x55, 0x1] + (26 * [0]),
				'/Hub4/AssistantId': 5,
				'/VebusMainState': 9,
				'/State': 3,
				'/Soc': 53.2,
				'/ExtraBatteryCurrent': 0})

		self._add_device('com.victronenergy.hub4',
			values={
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower': None
		})

		self._update_values()

	def test_scheduled_charge(self):
		# Determine seconds since midnight on timer right now.
		now = timer_manager.datetime
		midnight = datetime.combine(now.date(), time.min)
		stamp = (now-midnight).seconds

		# Set a schedule to start in 2 minutes and stop in another 1.
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Day', 7)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Start', stamp+120)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Duration', 60)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Soc', 100)

		# Travel 1 minute ahead, state should remain unchanged.
		timer_manager.run(60000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})
		self._check_values({'/Control/ScheduledCharge': 0})

		# Another minute or so, it should pop unto scheduled charge
		timer_manager.run(66000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
		}})

		# SystemState should indicate what happened. state only updates
		# when something changes, and our async update fools the unit tests,
		# so fake a change by moving the SoC.
		self._monitor.set_value(self.vebus, '/Soc', 70)
		timer_manager.run(1000)
		self._check_values({
			'/SystemState/State': 0x103,
			'/Control/ScheduledCharge': 1})

		# Another minute and scheduled charging will end.
		timer_manager.run(65000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})

	def test_scheduled_charge_stop_on_soc(self):
		# Add solar charger
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 252,
			'/Link/NetworkMode': 5,
			'/Link/ChargeVoltage': 26,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 24,
			'/Dc/0/Current': 10,
			'/FirmwareVersion': 0x129}, connection='VE.Direct')

		# Determine seconds since midnight on timer right now.
		now = timer_manager.datetime
		midnight = datetime.combine(now.date(), time.min)
		stamp = (now-midnight).seconds

		# Set a schedule to start in 2 minutes and stop in 4.
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Day', 7)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Start', stamp+120)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Duration', 180)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Soc', 70)

		# Travel 1 minute ahead, state should remain unchanged.
		timer_manager.run(60000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})

		# Another minute or so, it should pop into scheduled charge
		timer_manager.run(66000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
		}})

		# Another minute or so, Soc increases but not enough
		timer_manager.run(33000)
		self._monitor.set_value(self.vebus, '/Soc', 68)
		timer_manager.run(33000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
		}})

		# Another minute or so, Soc increases to right level. Discharge
		# is disabled while we are inside the window
		self._monitor.set_value(self.vebus, '/Soc', 70)
		timer_manager.run(66000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower': 192, # 80% of available PV
		}})

		# When we emerge from the charge window, discharge is allowed again.
		timer_manager.run(66000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower': -1,
		}})


	def test_scheduled_charge_multiple_windows(self):
		# Determine seconds since midnight on timer right now.
		now = timer_manager.datetime
		midnight = datetime.combine(now.date(), time.min)
		stamp = (now-midnight).seconds

		# Set a schedule to start in 1 minutes and stop in 2, then another
		# to start at 3 and stop at 4.
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Day', 7)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Start', stamp+60)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Duration', 60)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Soc', 100)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/1/Day', 7)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/1/Start', stamp+180)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/1/Duration', 60)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/1/Soc', 100)

		# Another minute or so, it should pop unto scheduled charge
		timer_manager.run(65000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
		}})

		timer_manager.run(65000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})

		timer_manager.run(65000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
		}})

		timer_manager.run(65000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})

	def test_scheduled_for_tomorrow(self):
		# Determine seconds since midnight on timer right now.
		now = timer_manager.datetime
		midnight = datetime.combine(now.date(), time.min)
		stamp = (now-midnight).seconds
		today = (now.date().weekday() + 1) % 7
		tomorrow = (today + 1) % 7

		# Set a schedule to start in 2 minutes and stop in another 10.
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Day', today)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Start', stamp+60)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Duration', 600)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Soc', 100)

		# Travel 1 minute ahead
		timer_manager.run(65000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
		}})

		# But if it was set for tomorrow it wouldn't match
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Day', tomorrow)
		timer_manager.run(5000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})

	def test_run_from_yesterday(self):
		# Determine seconds since midnight on timer right now.
		now = timer_manager.datetime
		midnight = datetime.combine(now.date(), time.min)
		stamp = (now-midnight).seconds
		yesterday = now.date().weekday()

		# Set a schedule that started a minute before midnight yesterday
		# and will expire a minute from now.
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Day', yesterday)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Start', 86340)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Duration', stamp + 120)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Soc', 100)

		timer_manager.run(5000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
		}})

		timer_manager.run(60000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})

	def test_prev_weekday(self):
		from delegates.schedule import prev_week_day
		today = date(2018, 6, 6)
		self.assertEqual(prev_week_day(today, 0), date(2018, 6, 3))
		self.assertEqual(prev_week_day(today, 2), date(2018, 6, 5))
		self.assertEqual(prev_week_day(today, 3), date(2018, 5, 30))
		self.assertEqual(prev_week_day(today, 6), date(2018, 6, 2))
		self.assertEqual(prev_week_day(today, 7), date(2018, 6, 3))

	def test_next_weekday(self):
		from delegates.schedule import next_week_day
		today = date(2018, 6, 6)
		self.assertEqual(next_week_day(today, 0), date(2018, 6, 10))
		self.assertEqual(next_week_day(today, 2), date(2018, 6, 12))
		self.assertEqual(next_week_day(today, 6), date(2018, 6, 9))
		self.assertEqual(next_week_day(today, 7), date(2018, 6, 10))

		today = date(2018, 5, 30)
		self.assertEqual(next_week_day(today, 2), date(2018, 6, 5))

	def test_next_schedule_day(self):
		from delegates.schedule import next_schedule_day
		today = date(2018, 6, 6)
		self.assertEqual(next_schedule_day(today, 0), date(2018, 6, 10))
		self.assertEqual(next_schedule_day(today, 1), date(2018, 6, 11))
		self.assertEqual(next_schedule_day(today, 2), date(2018, 6, 12))
		self.assertEqual(next_schedule_day(today, 3), date(2018, 6, 6))
		self.assertEqual(next_schedule_day(today, 4), date(2018, 6, 7))
		self.assertEqual(next_schedule_day(today, 5), date(2018, 6, 8))
		self.assertEqual(next_schedule_day(today, 6), date(2018, 6, 9))
		self.assertEqual(next_schedule_day(today, 7), date(2018, 6, 6))
		self.assertEqual(next_schedule_day(today, 8), date(2018, 6, 6))
		self.assertEqual(next_schedule_day(today, 9), date(2018, 6, 9))

		# Next week-day from a Saturday is Monday
		self.assertEqual(next_schedule_day(date(2018, 6, 9), 8),
			date(2018, 6, 11))

	def test_prev_schedule_day(self):
		from delegates.schedule import prev_schedule_day
		today = date(2018, 6, 6)
		self.assertEqual(prev_schedule_day(today, 0), date(2018, 6, 3))
		self.assertEqual(prev_schedule_day(today, 1), date(2018, 6, 4))
		self.assertEqual(prev_schedule_day(today, 2), date(2018, 6, 5))
		self.assertEqual(prev_schedule_day(today, 3), date(2018, 5, 30))
		self.assertEqual(prev_schedule_day(today, 4), date(2018, 5, 31))
		self.assertEqual(prev_schedule_day(today, 5), date(2018, 6, 1))
		self.assertEqual(prev_schedule_day(today, 6), date(2018, 6, 2))
		self.assertEqual(prev_schedule_day(today, 7), date(2018, 6, 5))
		self.assertEqual(prev_schedule_day(today, 8), date(2018, 6, 5))
		self.assertEqual(prev_schedule_day(today, 9), date(2018, 6, 3))

		# Prev week-day from a Monday is Friday
		self.assertEqual(prev_schedule_day(date(2018, 6, 11), 8),
			date(2018, 6, 8))

	def test_window_calculation(self):
		from delegates.schedule import ScheduledCharging, ScheduledWindow
		windows = ScheduledCharging._charge_windows(
			date(2018, 6, 6), [1, 7, 8, 9], [0, 3600, 7200, 10800],
			[3595]*4, [100]*4)
		windows = list(windows)
		self.assertEqual(len(windows), 8)

		# Previous monday and next Monday
		self.assertEqual(windows[0], ScheduledWindow(datetime(2018, 6, 4, 0, 0, 0), 3595))
		self.assertEqual(windows[1], ScheduledWindow(datetime(2018, 6, 11, 0, 0, 0), 3595))

		# Previous and next day
		self.assertEqual(windows[2], ScheduledWindow(datetime(2018, 6, 5, 1, 0, 0), 3595))
		self.assertEqual(windows[3], ScheduledWindow(datetime(2018, 6, 6, 1, 0, 0), 3595))

		# Previous and next week day
		self.assertEqual(windows[4], ScheduledWindow(datetime(2018, 6, 5, 2, 0, 0), 3595))
		self.assertEqual(windows[5], ScheduledWindow(datetime(2018, 6, 6, 2, 0, 0), 3595))

		# Previous and next weekend day
		self.assertEqual(windows[6], ScheduledWindow(datetime(2018, 6, 3, 3, 0, 0), 3595))
		self.assertEqual(windows[7], ScheduledWindow(datetime(2018, 6, 9, 3, 0, 0), 3595))

		# Retest week day from a weekend
		for d in (date(2018, 6, 9), date(2018, 6, 10)):
			windows = ScheduledCharging._charge_windows(d, [8], [0], [3600],
				[100])
			windows = list(windows)
			self.assertEqual(len(windows), 2)

			self.assertEqual(windows[0], ScheduledWindow(datetime(2018, 6, 8, 0, 0, 0), 3600))
			self.assertEqual(windows[1], ScheduledWindow(datetime(2018, 6, 11, 0, 0, 0), 3600))

	def test_schedule_charge_window(self):
		from delegates.schedule import ScheduledChargeWindow

		# Simple test
		window = ScheduledChargeWindow(datetime(2018, 6, 6, 0, 0, 1), 2, 99)
		self.assertTrue(datetime(2018, 6, 6, 0, 0, 1) in window)
		self.assertTrue(datetime(2018, 6, 6, 0, 0, 2) in window)
		self.assertTrue(datetime(2018, 6, 6, 0, 0, 3) not in window)
		self.assertFalse(window.soc_reached(98))
		self.assertTrue(window.soc_reached(99))
		self.assertTrue(window.soc_reached(100))

		# Never stop on SoC is set to 100%
		window = ScheduledChargeWindow(datetime(2018, 6, 6, 0, 0, 1), 2, 100)
		self.assertFalse(window.soc_reached(100))

		# Wrap around midnight
		window = ScheduledChargeWindow(datetime(2018, 6, 6, 23, 50, 00), 1200, 97)
		self.assertTrue(datetime(2018, 6, 6, 23, 49, 0) not in window)
		self.assertTrue(datetime(2018, 6, 7, 0, 11, 0) not in window)
		self.assertTrue(datetime(2018, 6, 7, 8, 0, 0) not in window)
		self.assertTrue(datetime(2018, 6, 7, 0, 0, 0) in window)
		self.assertTrue(datetime(2018, 6, 7, 0, 9, 59) in window)

		self.assertTrue(not window.soc_reached(96)) # 96 <= 97
		self.assertTrue(window.soc_reached(97))

		# Failed corner case 1
		window = ScheduledChargeWindow(datetime(2018, 6, 6, 8, 0, 0), 57601, 98)
		self.assertTrue(datetime(2018, 6, 6, 17, 0, 0) in window)

	def test_inactive_when_keepcharged(self):
		# Determine seconds since midnight on timer right now.
		now = timer_manager.datetime
		midnight = datetime.combine(now.date(), time.min)
		stamp = (now-midnight).seconds

		# Set a schedule to start in 1 minutes and stop in another 1.
		self._set_setting('/Settings/CGwacs/BatteryLife/State', 9)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Day', 7)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Start', stamp+60)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Duration', 60)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Soc', 100)

		# Travel 1 minute ahead, but it should remain out of charge mode
		# because we're in KeepBatteriesCharged.
		timer_manager.run(66000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})

	def test_inactive_when_no_ess(self):
		# Determine seconds since midnight on timer right now.
		now = timer_manager.datetime
		midnight = datetime.combine(now.date(), time.min)
		stamp = (now-midnight).seconds

		# Set a schedule to start in 1 minutes and stop in another 1.
		self._set_setting('/Settings/CGwacs/BatteryLife/State', 4)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Day', 7)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Start', stamp+60)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Duration', 60)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Soc', 100)

		# No ESS installed
		self._monitor.set_value(self.vebus, '/Hub4/AssistantId', None)

		# Travel 1 minute ahead, Charging should remain inactive because no ESS
		timer_manager.run(66000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})

	def test_hysteresis(self):
		""" Test that battery is allowed to drop 5% before re-acivating
		    recharge, to avoid pulse-charging in a large window if target SOC
		    is reached early. """
		# Determine seconds since midnight on timer right now.
		now = timer_manager.datetime
		midnight = datetime.combine(now.date(), time.min)
		stamp = (now-midnight).seconds

		# Set a schedule to start now and stop in 5 minutes
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Day', 7)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Start', stamp)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Duration', 300)
		self._set_setting('/Settings/CGwacs/BatteryLife/Schedule/Charge/0/Soc', 70)

		# Travel just far enough ahead to trigger the 5-second task in the delegate
		timer_manager.run(5000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
		}})

		# Increase the SOC to match the target, check that charging stopped.
		self._monitor.set_value(self.vebus, '/Soc', 70)
		timer_manager.run(5000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})

		# Reduce the SOC a little and check that hysteresis is applied
		self._monitor.set_value(self.vebus, '/Soc', 65)
		timer_manager.run(5000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})

		# Reduce SOC below the lower limit and check that charging reactivates
		self._monitor.set_value(self.vebus, '/Soc', 64.9)
		timer_manager.run(5000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
		}})

		# Check again that it stops once the target is reached.
		self._monitor.set_value(self.vebus, '/Soc', 69.9)
		timer_manager.run(5000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
		}})

		self._monitor.set_value(self.vebus, '/Soc', 70)
		timer_manager.run(5000)
		self._check_external_values({
				'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
		}})
