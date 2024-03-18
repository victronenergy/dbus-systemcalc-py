from datetime import datetime, date, time, timedelta

# This adapts sys.path to include all relevant packages
import context

# Testing tools
from mock_gobject import timer_manager

# our own packages
import dbus_systemcalc
from delegates import DynamicEss
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

# Time travel patch
DynamicEss._get_time = lambda *a: timer_manager.datetime

class TestDynamicEss(TestSystemCalcBase):
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
				'/Soc': 55.0,
				'/ExtraBatteryCurrent': 0})

		self._add_device('com.victronenergy.hub4',
			values={
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/Setpoint': None,
				'/Overrides/FeedInExcess': 0
		})

		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/CGwacs/MaxFeedInPower': -1,
			})

		self._set_setting('/Settings/DynamicEss/BatteryCapacity', 10.0)

		self._update_values()

	def tearDown(self):
		DynamicEss.instance.release_control()

	def test_buy(self):
		from delegates import Dvcc
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp+6)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 300)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 57)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 0)

		timer_manager.run(5000)
		# Nothing is overriden, slot has not arrived
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/FeedInExcess': 0
		}})
		self.assertEqual(None, Dvcc.instance.internal_maxchargepower)

		timer_manager.run(5000)
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1, # Charging is forced
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/FeedInExcess': 1
		}})
		# Charge power should be around 2400W (200Wh in 5 minutes)
		self.assertEqual(2700, round(Dvcc.instance.internal_maxchargepower, -2))

		timer_manager.run(300000)
		# slot is over
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/FeedInExcess': 0
		}})
		self.assertEqual(None, Dvcc.instance.internal_maxchargepower)

	def test_sell(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp+6)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 50)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 1)

		timer_manager.run(5000)
		# Nothing is overriden, slot has not arrived
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/FeedInExcess': 0
		}})

		timer_manager.run(5000)
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': -32000,
				'/Overrides/MaxDischargePower': 551, # 5% of 10kWh over 1 hour
				'/Overrides/FeedInExcess': 2
		}})

		timer_manager.run(3600000)
		# slot is over
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/FeedInExcess': 0
		}})

	def test_stop_on_soc(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 252,
			'/Link/NetworkMode': 5,
			'/Link/ChargeVoltage': 55.0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 50.0,
			'/Dc/0/Current': 10,
			'/FirmwareVersion': 0x129}, connection='VE.Direct')

		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 50)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 1)

		timer_manager.run(1800000)
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': -32000,
				'/Overrides/MaxDischargePower': 1051, # 5% of 10kWh over 1 hour, including 500W from PV
				'/Overrides/FeedInExcess': 2
		}})

		self._monitor.set_value(self.vebus, '/Soc', 50.0)

		timer_manager.run(5000)
		# SOC is reached
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': -32000,
				'/Overrides/MaxDischargePower': 450.0, # 90% of solar
				'/Overrides/FeedInExcess': 2
		}})

		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 0)
		timer_manager.run(5000)
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': 0,
				'/Overrides/MaxDischargePower': 500, # Available solar
				'/Overrides/FeedInExcess': 1 # Feed-in not allowed
		}})

	def test_buy_account_for_solar(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 252,
			'/Link/NetworkMode': 5,
			'/Link/ChargeVoltage': 55.0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 50.0,
			'/Dc/0/Current': 5,
			'/FirmwareVersion': 0x129}, connection='VE.Direct')

		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp-5)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 60)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 0)

		timer_manager.run(5000)

		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1, # Charging is forced
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/FeedInExcess': 1
		}})
		# Charge power should be around 500W, minus 250W from solar (500Wh in 1 hour)
		from delegates import Dvcc
		self.assertEqual(300, round(Dvcc.instance.internal_maxchargepower, -1))

	def test_hysteresis(self):
		""" Test case for batteries that don't report whole numbers, but
		    jumps between SOC values and don't always hit match target SOC
		    exactly. Use case jitters between 43.8% and 44.4%. """
		from delegates import Dvcc
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 44)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 1)

		self._monitor.set_value(self.vebus, '/Soc', 44.4)
		timer_manager.run(5000)
		self.assertTrue(self._monitor.get_value('com.victronenergy.hub4',
			'/Overrides/MaxDischargePower') > 1.0) # Controlled discharge

		self._monitor.set_value(self.vebus, '/Soc', 43.8)
		timer_manager.run(5000)
		# SOC is reached, idle
		self.assertTrue(self._monitor.get_value('com.victronenergy.hub4',
			'/Overrides/MaxDischargePower') == 1.0)
		self.assertEqual(None, Dvcc.instance.internal_maxchargepower)

		# Returns to 44.4, remain in idle
		self._monitor.set_value(self.vebus, '/Soc', 44.4)
		timer_manager.run(5000)
		self.assertTrue(self._monitor.get_value('com.victronenergy.hub4',
			'/Overrides/MaxDischargePower') == 1.0)
		self.assertEqual(None, Dvcc.instance.internal_maxchargepower)

		# Increases to 45.1%, go back to discharge.
		self._monitor.set_value(self.vebus, '/Soc', 45.1)
		timer_manager.run(5000)
		self.assertTrue(self._monitor.get_value('com.victronenergy.hub4',
			'/Overrides/MaxDischargePower') > 1.0) # Controlled discharge

		# Idle again
		self._monitor.set_value(self.vebus, '/Soc', 43.8)
		timer_manager.run(5000)
		# SOC is reached, idle
		self.assertTrue(self._monitor.get_value('com.victronenergy.hub4',
			'/Overrides/MaxDischargePower') == 1.0)
		self.assertEqual(None, Dvcc.instance.internal_maxchargepower)

		# Back to charge if we go low enough
		self._monitor.set_value(self.vebus, '/Soc', 42.9)
		timer_manager.run(5000)
		self.assertTrue(self._monitor.get_value('com.victronenergy.hub4',
			'/Overrides/MaxDischargePower') == -1.0)
		self.assertTrue(Dvcc.instance.internal_maxchargepower > 0.0)

	def test_self_consume(self):
		from delegates import Dvcc
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 0)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 1) # Self consume

		timer_manager.run(5000)

		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/FeedInExcess': 1
		}})
		self.assertEqual(None, Dvcc.instance.internal_maxchargepower)
