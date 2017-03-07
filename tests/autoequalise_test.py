import time
from datetime import datetime, timedelta

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches


class TestAutoEqualise(TestSystemCalcBase):
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device('com.victronenergy.vebus.ttyO1',
			product_name='Multi',
			values={
				'/Ac/ActiveIn/L1/P': 123,
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/ActiveIn/Connected': 1,
				'/Ac/Out/L1/P': 100,
				'/Dc/0/Voltage': 12.25,
				'/Dc/0/Current': -8,
				'/DeviceInstance': 0,
				'/Devices/0/Assistants': [0x55, 0x1] + (26 * [0]),  # Hub-4 assistant
				'/Dc/0/MaxChargeCurrent': None,
				'/Soc': 53.2,
				'/State': 3,
				'/VebusSubstate': 0,
				'/VebusSetChargeState': 0,
				'/BatteryOperationalLimits/MaxChargeVoltage': None,
				'/BatteryOperationalLimits/MaxChargeCurrent': None,
				'/BatteryOperationalLimits/MaxDischargeCurrent': None,
				'/BatteryOperationalLimits/BatteryLowVoltage': None
			})

	def test_auto_eq(self):
		today = datetime.today()
		starttime = today.time().strftime('%H:%M')
		interval = 30

		self._set_setting('/Settings/AutoEqualise/Enabled', 0)
		self._set_setting('/Settings/AutoEqualise/StartDate', today.date().isoformat())
		self._set_setting('/Settings/AutoEqualise/Interval', 30)
		self._set_setting('/Settings/AutoEqualise/StartTime', starttime)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/VebusSubstate', 1)  # Bulk

		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 0
		})

		# Enable and set start date to 30 days ago, equalisation mechanism must start
		self._set_setting('/Settings/AutoEqualise/Enabled', 1)
		self._set_setting('/Settings/AutoEqualise/StartDate',
		                  (today.date() - timedelta(days=interval)).isoformat())

		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 1
		})

		# Set multi to absorption
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/VebusSubstate', 2)
		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 1
		})

		# Set multi to equalise
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/VebusSubstate', 7)
		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 2
		})

		# Set multi to float, this means that equalisation already finished
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/VebusSubstate', 3)
		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 0
		})

		# Next auto EQ will be in 'interval' days
		self._update_values(5000)
		nextdate = (today + timedelta(days=interval)).replace(hour=today.time().hour,
		                                                      minute=today.time().minute, second=0)
		self._check_values({
			'/AutoEqualise/State': 0,
			'/AutoEqualise/NextEqualisation': time.mktime(nextdate.timetuple())
		})

	def test_auto_eq_enabled_already_in_eq(self):
		today = datetime.today()
		starttime = today.time().strftime('%H:%M')
		interval = 30
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/VebusSubstate', 7)
		self._set_setting('/Settings/AutoEqualise/Enabled', 1)
		self._set_setting('/Settings/AutoEqualise/Interval', 30)
		self._set_setting('/Settings/AutoEqualise/StartTime', starttime)

		self._set_setting('/Settings/AutoEqualise/StartDate',
		                  (today.date() - timedelta(days=interval)).isoformat())
		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 2
		})

	def test_auto_eq_manual(self):
		today = datetime.today()
		starttime = today.time().strftime('%H:%M')
		interval = 30
		self._set_setting('/Settings/AutoEqualise/Enabled', 0)
		self._service['/AutoEqualise/ManualEqualisation'] = 1

		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 1
		})
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/VebusSubstate', 7)
		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 2
		})

	def test_auto_eq_timed_out(self):
		today = datetime.today()
		starttime = today.time().strftime('%H:%M')
		interval = 30
		self._set_setting('/Settings/AutoEqualise/Enabled', 1)
		self._set_setting('/Settings/AutoEqualise/StartDate', today.date().isoformat())
		self._set_setting('/Settings/AutoEqualise/Interval', 30)
		self._set_setting('/Settings/AutoEqualise/StartTime', starttime)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/VebusSubstate', 1)

		self._set_setting('/Settings/AutoEqualise/StartDate',
		                  (today.date() - timedelta(days=interval)).isoformat())
		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 1
		})
		self._set_setting('/Settings/AutoEqualise/LastStarted',
		                  time.mktime((today - timedelta(hours=9)).timetuple()))
		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 1
		})
		self._set_setting('/Settings/AutoEqualise/LastStarted',
		                  time.mktime((today - timedelta(hours=10)).timetuple()))
		self._update_values(5000)
		self._check_values({
			'/AutoEqualise/State': 0
		})
