#!/usr/bin/env python
import math

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

class TestHubSystem(TestSystemCalcBase):
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
				'/BatteryOperationalLimits/MaxChargeVoltage': None,
				'/BatteryOperationalLimits/MaxChargeCurrent': None,
				'/BatteryOperationalLimits/MaxDischargeCurrent': None,
				'/BatteryOperationalLimits/BatteryLowVoltage': None,
				'/BatterySense/Voltage': None,
				'/FirmwareFeatures/BolFrame': 1,
				'/FirmwareFeatures/BolUBatAndTBatSense': 1
			})
		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/SystemSetup/AcInput1': 1,
				'/Settings/SystemSetup/AcInput2': 2,
			})
		self._set_setting('/Settings/Services/Bol', 1)

	def test_hub1_control_voltage_with_state(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.6)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0xE117},
			connection='VE.Direct')
		self._update_values(3000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': 12.6
			}})
		self._check_values({
			'/Control/SolarChargeVoltage': 1,
			'/Control/SolarChargerVoltageSense': 1})

	def test_hub1_control_voltage_without_state(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.6)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x0119},
			connection='VE.Direct')
		self._update_values(3000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': 12.6,
				'/State': 0
			}})
		self._check_values({
			'/Control/SolarChargeVoltage': 1,
			'/Control/SolarChargerVoltageSense': 1})

	def test_hub1_control_voltage_multiple_solarchargers(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.5)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x0117},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._update_values(3000)
		self.assertEqual(12.5, self._monitor.get_value('com.victronenergy.solarcharger.ttyO1',
			'/Link/ChargeVoltage'))
		self.assertEqual(12.5, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2',
			'/Link/ChargeVoltage'))
		self.assertEqual(0, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2', '/State'))
		self._check_values({'/Control/SolarChargeVoltage': 1})

	def test_hub1_control_voltage_ve_can_solarchargers(self):
		# Hub1 control should ignore VE.Can solarchargers
		# self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.5)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7},
			connection='VE.Can')
		self._update_values()
		self.assertEqual(None, self._monitor.get_value('com.victronenergy.solarcharger.ttyO1',
			'/Link/ChargeVoltage'))
		self.assertEqual(0, self._monitor.get_value('com.victronenergy.solarcharger.ttyO1', '/State'))
		self._check_values({'/Control/SolarChargeVoltage': 0})

	def test_hub1_control_ve_can_service(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.63)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.solarcharger.can0', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7},
			connection='VE.Can')
		self._add_device('com.victronenergy.vecan.can0', {
			'/Link/ChargeVoltage': None})
		self._update_values(3000)
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 13.2)
		self._add_device('com.victronenergy.vecan.can1', {
			'/Link/ChargeVoltage': None})
		self._update_values(3000)
		self.assertEqual(13.2, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self.assertEqual(13.2, self._monitor.get_value('com.victronenergy.vecan.can1', '/Link/ChargeVoltage'))
		self._remove_device('com.victronenergy.vecan.can0')
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 13.1)
		self._update_values(interval=10000)
		self.assertEqual(None, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self.assertEqual(13.1, self._monitor.get_value('com.victronenergy.vecan.can1', '/Link/ChargeVoltage'))
		self._check_values({'/Control/SolarChargeVoltage': 1})

	def test_hub1_control_ve_can_service_no_solar_charger(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.63)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.vecan.can0', {
			'/Link/ChargeVoltage': None})
		self.assertEqual(None, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 13.2)
		self._check_values({'/Control/SolarChargeVoltage': 0})

	def test_hub1_control_ve_can_and_solar_charger(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.63)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.solarcharger.can0', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7},
			connection='VE.Can')
		self._add_device('com.victronenergy.vecan.can0', {
			'/Link/ChargeVoltage': 12.3})
		self._update_values(3000)
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._update_values(3000)
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2',
			'/Link/ChargeVoltage'))
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.53)
		self._update_values(interval=10000)
		self.assertEqual(5, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2', '/Link/NetworkMode'))
		self.assertEqual(12.53, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self.assertEqual(12.53, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2',
			'/Link/ChargeVoltage'))
		self._check_values({'/Control/SolarChargeVoltage': 1})

	def test_hub1_control_ve_can_service_no_setpoint(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.65)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.vecan.can0', {}, connection='VE.Can')
		self._update_values()
		self.assertEqual(None, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._check_values({'/Control/SolarChargeCurrent': 0})
		self._check_values({'/Control/SolarChargeVoltage': 0})

	def test_hub1_control_vedirect_solarcharger_bms_battery(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 55.2)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Settings/ChargeCurrentLimit': 100,
			'/Dc/0/Voltage': 58.0,
			'/Dc/0/Current': 30,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 58.1,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=60000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25 + 8,
				'/Link/ChargeVoltage': 55.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 0}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 1})

	def test_vedirect_solarcharger_bms_battery_max_charge_current_setting(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 55.2)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 0)
		self._set_setting('/Settings/SystemSetup/MaxChargeCurrent', 20)
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Settings/ChargeCurrentLimit': 100,
			'/Dc/0/Voltage': 58.0,
			'/Dc/0/Current': 30,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=60000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 20 + 8,
				'/Link/ChargeVoltage': 55.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 0}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 1})

	def test_control_vedirect_solarcharger_bms_battery_no_charge_voltage(self):
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 31,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=60000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25 + 8,
				'/Link/ChargeVoltage': 58.3},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 0}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/SolarChargerVoltageSense': 1,
			'/Control/BmsParameters': 1})

	def test_control_vedirect_solarcharger_charge_distribution(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Dc/0/MaxChargeCurrent', 0)
		self._update_values()
		self._add_device('com.victronenergy.solarcharger.ttyO0', {
			'/State': 3,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': 15,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 14.3,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': 15,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 7,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})

		# Simulate the solar charger moving towards the requested limit
		for _ in range(12):
			self._update_values(interval=1000)

			for c in ('com.victronenergy.solarcharger.ttyO0',
					'com.victronenergy.solarcharger.ttyO2'):
				self._monitor.set_value(c, '/Dc/0/Current', min(100.0,
					self._monitor.get_value(c, '/Link/ChargeCurrent')))

		total = self._monitor.get_value('com.victronenergy.vebus.ttyO1',
				'/Dc/0/Current') + \
			self._monitor.get_value('com.victronenergy.solarcharger.ttyO0',
				'/Dc/0/Current') + \
			self._monitor.get_value('com.victronenergy.solarcharger.ttyO2',
				'/Dc/0/Current')

		# Check that total is within 5%
		self.assertTrue(abs(total - 25) <= 1.25)

		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO0': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeVoltage': 58.3},
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeVoltage': 58.3},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				# Difference goes to the multi
				'/Dc/0/MaxChargeCurrent': 0 }})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 1})

	def test_control_vedirect_solarcharger_bms_ess_feedback(self):
		# When feedback is allowed we do not limit MPPTs
		# Force system type to ESS
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 5)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 58.3)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 1)
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 1,
			'/Settings/ChargeCurrentLimit': 35,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 35,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 58.0,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 45,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 35,
				'/Link/ChargeVoltage': 58.3},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 45,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 10}})
		self._check_values({
			'/SystemType': 'ESS',
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 1})

	def test_control_vedirect_solarcharger_bms_ess_feedback_no_ac_in(self):
		# When feedback is allowed we do not limit MPPTs, but in this case there is no AC-in so feedback is
		# not possible.
		# Force system type to ESS
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 5)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 58.3)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 1)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/Connected', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 1,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': 57.3,
			'/Link/ChargeCurrent': 20,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 31,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 58.0,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=60000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25 + 8,
				'/Link/ChargeVoltage': 58.3},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 0}})
		self._check_values({
			'/SystemType': 'ESS',
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 1})

	def test_hub1_control_vedirect_solarcharger_bms_battery_no_link(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 55.2)
		# Solar chargers with firmware < 1.17 do not publish the /Link section.
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Settings/ChargeCurrentLimit': 100,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 30,
			'/Link/ChargeCurrent': None,
			'/FirmwareVersion': 0x0116},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=60000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 0}})
		self._check_values({
			'/Control/SolarChargeCurrent': 0,
			'/Control/SolarChargeVoltage': 0,
			'/Control/BmsParameters': 1})

	def test_hub1_control_vedirect_solarcharger_bms_battery_no_solarcharger(self):
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 25}})
		self._check_values({
			'/Control/SolarChargeCurrent': 0,
			'/Control/SolarChargeVoltage': 0,
			'/Control/BmsParameters': 1})

	def test_system_mapping(self):
		self._update_values()
		self._check_values({
			'/ServiceMapping/com_victronenergy_vebus_0': 'com.victronenergy.vebus.ttyO1',
			'/ServiceMapping/com_victronenergy_settings_0': 'com.victronenergy.settings'})
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery', values={'/DeviceInstance': 3})
		self._check_values({
			'/ServiceMapping/com_victronenergy_vebus_0': 'com.victronenergy.vebus.ttyO1',
			'/ServiceMapping/com_victronenergy_battery_3': 'com.victronenergy.battery.ttyO2'})
		self._remove_device('com.victronenergy.battery.ttyO2')
		self.assertFalse('/ServiceMapping/com_victronenergy_battery_3' in self._service)

	def test_hub1_extra_current(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3},
			connection='VE.Direct')
		self._update_values()
		self.assertEqual(9.7 + 9.3, self._monitor.get_value('com.victronenergy.vebus.ttyO1',
			'/ExtraBatteryCurrent'))

	def test_hub1_extra_current_no_battery_no_solarcharger(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 1)
		self._update_values()
		self.assertEqual(0, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({
			'/Control/ExtraBatteryCurrent': 1,
			'/Control/SolarChargeVoltage': 0,
			'/Control/SolarChargeCurrent': 0,
			'/Control/SolarChargerVoltageSense': 0})

	def test_hub1_extra_current_hub2_no_battery_monitor(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3},
			connection='VE.Direct')
		self._update_values()
		self.assertEqual(9.7 + 9.3, self._monitor.get_value('com.victronenergy.vebus.ttyO1',
			'/ExtraBatteryCurrent'))
		self._check_values({'/Control/ExtraBatteryCurrent': 1})

	def test_hub1_no_extra_current(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', None)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._update_values()
		self.assertIsNone(self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({'/Control/ExtraBatteryCurrent': 0})

	def test_hub1_with_bmv_extra_current_battery(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		self.assertEqual(9.7, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({'/Control/ExtraBatteryCurrent': 1})
		self._check_values({'/Control/VebusSoc': 0})

	def test_hub2_extra_current_battery(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 0)
		# Set hub-2 & Lynx Ion assistant
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Devices/0/Assistants',
			[0x4D, 0x01, 0x3C, 0x01] + (26 * [0]))
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values(3000)
		self.assertEqual(9.7, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({
			'/Control/ExtraBatteryCurrent': 1,
			'/Control/VebusSoc': 0,
			'/Control/SolarChargerVoltageSense': 1,
			'/Control/SolarChargeVoltage': 0,
			'/Control/SolarChargeCurrent': 0,
			'/Control/BmsParameters': 0})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': None,
				'/Link/ChargeCurrent': None}})

	def test_hub1_extra_current_no_active_battery(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 23)
		self._set_setting('/Settings/SystemSetup/BatteryService', 'nobattery')
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		self.assertEqual(9.7, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({'/Control/ExtraBatteryCurrent': 1})
		self._check_values({'/Control/VebusSoc': 0})

	def test_multi_class(self):
		from delegates.dvcc import Multi
		multi = Multi(self._system_calc._dbusmonitor, self._service)
		self.assertIsNone(multi.bol.chargevoltage)
		self.assertIsNone(multi.bol.maxchargecurrent)

		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/BatteryOperationalLimits/MaxChargeVoltage', 26)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/BatteryOperationalLimits/MaxChargeCurrent', 99)
		self._update_values()
		self.assertEqual(multi.bol.chargevoltage, 26)
		self.assertEqual(multi.bol.maxchargecurrent, 99)

		multi.bol.chargevoltage = 27
		multi.bol.maxchargecurrent = 55

		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeVoltage': 27,
				'/BatteryOperationalLimits/MaxChargeCurrent': 55,
			}})

	def test_multi_nobol(self):
		from dbus.exceptions import DBusException
		from delegates.dvcc import Multi

		self._remove_device('com.victronenergy.vebus.ttyO1')
		self._add_device('com.victronenergy.vebus.ttyB1',
			product_name='Multi',
			values={
				'/State': 3,
			})
		self._update_values()
		multi = Multi(self._system_calc._dbusmonitor, self._service)
		with self.assertRaises(DBusException):
			multi.bol.chargevoltage = 22
		self.assertIsNone(multi.bol.chargevoltage)


	def test_solar_subsys(self):
		from delegates.dvcc import SolarChargerSubsystem
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3
		}, connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3
		}, connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.socketcan_can0_di0_uc30688', {
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3
		}, connection='VE.Can')

		system = SolarChargerSubsystem(self._system_calc._dbusmonitor)
		system.add_charger('com.victronenergy.solarcharger.ttyO1')
		system.add_charger('com.victronenergy.solarcharger.ttyO2')

		# Test __contains__
		self.assertTrue('com.victronenergy.solarcharger.ttyO1' in system)
		self.assertTrue('com.victronenergy.solarcharger.ttyO2' in system)
		self.assertTrue('com.victronenergy.solarcharger.ttyO3' not in system)

		# Test __len__
		self.assertTrue(len(system)==2)

		# test __iter__
		chargers = list(system)
		self.assertTrue(chargers[0].service == 'com.victronenergy.solarcharger.ttyO1')
		self.assertTrue(chargers[1].service == 'com.victronenergy.solarcharger.ttyO2')

		# Add vecan charger
		self.assertFalse(system.has_vecan_chargers)
		system.add_charger('com.victronenergy.solarcharger.socketcan_can0_di0_uc30688')
		self.assertTrue(system.has_vecan_chargers)

	def test_solar_subsys_distribution(self):
		from delegates.dvcc import SolarChargerSubsystem
		self._add_device('com.victronenergy.battery.socketcan_can0_di0_uc30688', {
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/Info/MaxChargeCurrent': 100
		}, connection='VE.Can')
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': 14.5,
			'/Link/ChargeCurrent': 50,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 40,
			'/Settings/ChargeCurrentLimit': 70,
		}, connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': 14.5,
			'/Link/ChargeCurrent': 32,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 30,
			'/Settings/ChargeCurrentLimit': 35,
		}, connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO3', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': 14.5,
			'/Link/ChargeCurrent': 12,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 10,
			'/Settings/ChargeCurrentLimit': 15,
		}, connection='VE.Direct')

		system = SolarChargerSubsystem(self._system_calc._dbusmonitor)
		system.add_charger('com.victronenergy.solarcharger.ttyO1')
		system.add_charger('com.victronenergy.solarcharger.ttyO2')
		system.add_charger('com.victronenergy.solarcharger.ttyO3')

		self.assertTrue(system.capacity == 120)

		self._monitor.set_value('com.victronenergy.battery.socketcan_can0_di0_uc30688', '/Info/MaxChargeCurrent', 100)

	def test_battery_subsys_no_bms(self):
		from delegates.dvcc import BatterySubsystem
		self._add_device('com.victronenergy.battery.socketcan_can0_di0_uc30688', {
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3
		}, connection='VE.Can')

		system = BatterySubsystem(self._system_calc._dbusmonitor)
		system.add_battery('com.victronenergy.battery.socketcan_can0_di0_uc30688')
		self.assertTrue(system.bms is None)

		# Test magic methods
		self.assertTrue('com.victronenergy.battery.socketcan_can0_di0_uc30688' in system)
		self.assertTrue(len(system)==1)
		batteries = list(system)
		self.assertTrue(batteries[0].service == 'com.victronenergy.battery.socketcan_can0_di0_uc30688')

	def test_battery_subsys_bms(self):
		from delegates.dvcc import BatterySubsystem
		self._add_device('com.victronenergy.battery.socketcan_can0_di0_uc30688', {
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/Info/MaxChargeVoltage': 15,
			'/Info/MaxChargeCurrent': 100,
			'/Info/MaxDischargeCurrent': 100
		}, connection='VE.Can')

		system = BatterySubsystem(self._system_calc._dbusmonitor)
		battery = system.add_battery('com.victronenergy.battery.socketcan_can0_di0_uc30688')
		self.assertTrue(system.bms is battery)
		self.assertTrue(battery.maxchargecurrent == 100)
		self.assertTrue(battery.chargevoltage == 15)
		self.assertEqual(battery.voltage, 12.6)

	def test_distribute(self):
		from delegates.dvcc import distribute

		actual = [1, 2, 3, 4, 5] # 15 amps
		limits = [5, 5, 5, 5, 5] # 25 amps

		# add 5 amps
		newlimits = distribute(actual, limits, 5)
		self.assertTrue(sum(newlimits)==20)

		# max it out
		newlimits = distribute(actual, limits, 10)
		self.assertTrue(sum(newlimits)==25)

		# overflow it
		newlimits = distribute(actual, limits, 11)
		self.assertTrue(sum(newlimits)==25)

		# Drop 5 amps
		newlimits = distribute(actual, limits, -5)
		self.assertTrue(sum(newlimits)==10)

		# Drop 10 amps
		newlimits = distribute(actual, limits, -10)
		self.assertTrue(sum(newlimits)==5)

		# All of it
		newlimits = distribute(actual, limits, -15)
		self.assertTrue(sum(newlimits)==0)

		# Attempt to go negative
		newlimits = distribute(actual, limits, -20)
		self.assertTrue(sum(newlimits)==0)

		newlimits = distribute([2, 2], [2, 2], 20)

	def test_hub1bridge_distr_1(self):
		from delegates.dvcc import distribute
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = distribute(actual_values, max_values, 3)
		self.assertEqual(new_values, [2, 3, 4])

	def test_hub1bridge_distr_2(self):
		from delegates.dvcc import distribute
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = distribute(actual_values, max_values, 9.0)
		self.assertEqual(new_values, [6, 5, 4])

	def test_hub1bridge_distr_3(self):
		from delegates.dvcc import distribute
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = distribute(actual_values, max_values, 10.0)
		self.assertEqual(new_values, [6, 5, 4])

	def test_hub1bridge_distr_4(self):
		from delegates.dvcc import distribute
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = distribute(actual_values, max_values, 6.0)
		self.assertEqual(new_values, [3.5, 4.5, 4])

	def test_hub1bridge_distr_5(self):
		from delegates.dvcc import distribute
		actual_values = [3, 2, 1]
		max_values = [4, 5, 6]
		new_values = distribute(actual_values, max_values, 6.0)
		self.assertEqual(new_values, [4, 4.5, 3.5])

	def test_hub1bridge_distr_6(self):
		from delegates.dvcc import distribute
		actual_values = [4, 5, 6]
		max_values = [1, 2, 8]
		new_values = distribute(actual_values, max_values, 0.0)
		self.assertEqual(new_values, [1, 2, 8])

	def test_hub1bridge_distr_7(self):
		from delegates.dvcc import distribute
		actual_values = [1]
		max_values = [5]
		new_values = distribute(actual_values, max_values, 6.0)
		self.assertEqual(new_values, [5])

	def test_debug_chargeoffsets(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.6)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._service.set_value('/Debug/BatteryOperationalLimits/SolarVoltageOffset', 0.4)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2', product_name='battery',
			values={
				'/Dc/0/Voltage': 12.5,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 10,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 12.6,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(3000)

		# Check that debug voltage works for solar chargers
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': 13
			}})

		# Check that we can also offset the Multi's voltage and current
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeVoltage': 12.6
			}})
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeCurrent': 25
			}})
		self._service.set_value('/Debug/BatteryOperationalLimits/VebusVoltageOffset', 0.2)
		self._service.set_value('/Debug/BatteryOperationalLimits/CurrentOffset', 5)
		self._update_values(3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeVoltage': 12.8
			}})
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeCurrent': 30,
				'/Dc/0/MaxChargeCurrent': 20 # because solar provides 9.7.
			}})

	def test_hub1_legacy_voltage_control(self):
		# BOL support is off initialy
		self._set_setting('/Settings/Services/Bol', 0)
		self._update_values()

		# Start without a BMS. No Current sharing should be done, only
		# voltage.
		self._monitor.add_value('com.victronenergy.vebus.ttyO1',
			'/Hub/ChargeVoltage', 12.6)

		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 252,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/Settings/ChargeCurrentLimit': 35,
			'/FirmwareVersion': 0x0119},
			connection='VE.Direct')
		self._update_values(10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': 12.6,
				'/Link/ChargeCurrent': None,
				'/Link/NetworkMode': 5,
			}})
		self._check_values({'/Control/Dvcc': 0})

		# Add a BMS
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.7,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 67,
				'/Soc': 25.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 10,
				'/Info/MaxChargeCurrent': 10,
				'/Info/MaxChargeVoltage': 15,
				'/Info/MaxDischargeCurrent': 10})
		self._update_values(10000)

		# Current should be shared with solar chargers. Voltage
		# reflects the Multi's /Hub/ChargeVoltage
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': 12.6,
				'/Link/ChargeCurrent': 35,
				'/Link/NetworkMode': 13,
			}})
		self._check_values({'/Control/Dvcc': 0})

		# Switch to DVCC
		self._set_setting('/Settings/Services/Bol', 1)
		self._update_values(10000)

		# Now the charge current of the BMS was used.
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': 12.6,
				'/Link/ChargeCurrent': 18, # 10 + 8 for the Multi
				'/Link/NetworkMode': 13,
			}})
		self._check_values({'/Control/Dvcc': 1})

	def test_byd_quirks(self):
		""" BYD batteries should float at 55V when they send CCL=0. """
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 55.1,
				'/Dc/0/Current': 3,
				'/Dc/0/Power': 165.3,
				'/Soc': 100,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 100,
				'/Info/MaxChargeVoltage': 56.5,
				'/Info/MaxDischargeCurrent': 100,
				'/ProductId': 0xB00A})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeCurrent': 100
			}
		})

		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Info/MaxChargeCurrent', 0)
		self._update_values(interval=3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 55
			}
		})
