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
				'/Overrides/MaxChargePower': -1,
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

	def test_buy(self):
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
				'/Overrides/MaxChargePower': -1,
				'/Overrides/FeedInExcess': 0
		}})

		timer_manager.run(5000)
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1, # Charging is forced
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/FeedInExcess': 1
		}})
		# Charge power should be around 2400W (200Wh in 5 minutes)
		self.assertEqual(2400, round(
			self._monitor.get_value('com.victronenergy.hub4',
			'/Overrides/MaxChargePower'), -2))

		timer_manager.run(300000)
		# slot is over
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/MaxChargePower': -1,
				'/Overrides/FeedInExcess': 0
		}})

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
				'/Overrides/MaxDischargePower': 501, # 5% of 10kWh over 1 hour
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
				'/Overrides/MaxDischargePower': 1001, # 5% of 10kWh over 1 hour, including 500W from PV
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
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': 450,
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
		self.assertEqual(250, round(
			self._monitor.get_value('com.victronenergy.hub4',
			'/Overrides/MaxChargePower'), -1))
