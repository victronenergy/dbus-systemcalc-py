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
				'/Dc/0/Temperature': 24,
				'/DeviceInstance': 0,
				'/Devices/0/Assistants': [0x55, 0x1] + (26 * [0]),  # Hub-4 assistant
				'/Dc/0/MaxChargeCurrent': 999,
				'/ExtraBatteryCurrent': 0,
				'/Soc': 53.2,
				'/State': 3,
				'/BatteryOperationalLimits/MaxChargeVoltage': None,
				'/BatteryOperationalLimits/MaxChargeCurrent': None,
				'/BatteryOperationalLimits/MaxDischargeCurrent': None,
				'/BatteryOperationalLimits/BatteryLowVoltage': None,
				'/BatterySense/Voltage': None,
				'/FirmwareFeatures/BolFrame': 1,
				'/FirmwareFeatures/BolUBatAndTBatSense': 1,
				'/FirmwareVersion': 0x456,
				'/Hub4/L1/DoNotFeedInOvervoltage': 1
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
			'/FirmwareVersion': 0x129},
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
			'/FirmwareVersion': 0x0129},
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
			'/FirmwareVersion': 0x0129},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/FirmwareVersion': 0x0129},
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
			'/Link/ChargeVoltage': None,
			'/Link/NetworkMode': None,
			'/Link/TemperatureSense': None,
			'/Link/VoltageSense': None})
		self._update_values(12000)
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self.assertEqual(5, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/NetworkMode'))
		self.assertEqual(12.25, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/VoltageSense'))
		self.assertEqual(24, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/TemperatureSense'))
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 13.2)
		self._add_device('com.victronenergy.vecan.can1', {
			'/Link/ChargeVoltage': None,
			'/Link/NetworkMode': None,
			'/Link/TemperatureSense': None,
			'/Link/VoltageSense': None})
		self._update_values(9000)
		self.assertEqual(13.2, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self.assertEqual(13.2, self._monitor.get_value('com.victronenergy.vecan.can1', '/Link/ChargeVoltage'))
		self.assertEqual(5, self._monitor.get_value('com.victronenergy.vecan.can1', '/Link/NetworkMode'))
		self.assertEqual(12.25, self._monitor.get_value('com.victronenergy.vecan.can1', '/Link/VoltageSense'))
		self.assertEqual(24, self._monitor.get_value('com.victronenergy.vecan.can1', '/Link/TemperatureSense'))

		self._remove_device('com.victronenergy.vecan.can0')
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 13.1)
		self._update_values(interval=3000)
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
			'/Link/ChargeVoltage': 12.3,
			'/Link/NetworkMode': None,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None})
		self._update_values(3000)
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/FirmwareVersion': 0x0129},
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
			'/FirmwareVersion': 0x0129},
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
				'/Info/MaxChargeCurrent': 45,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=60000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 45 + 8,
				'/Link/ChargeVoltage': 55.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 15,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 999}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/EffectiveChargeVoltage': 55.2,
			'/Control/BmsParameters': 1})

	def test_vedirect_solarcharger_bms_battery_max_charge_current_setting(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 55.2)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 0)
		self._set_setting('/Settings/SystemSetup/MaxChargeCurrent', 40)
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Settings/ChargeCurrentLimit': 100,
			'/Dc/0/Voltage': 58.0,
			'/Dc/0/Current': 30,
			'/FirmwareVersion': 0x0129},
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
				'/Info/MaxChargeCurrent': 45,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=60000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 40 + 8,
				'/Link/ChargeVoltage': 55.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 10,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 999}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/EffectiveChargeVoltage': 55.2, # ESS
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
			'/FirmwareVersion': 0x0129},
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
				'/Info/MaxChargeCurrent': 45,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=60000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 45 + 8,
				'/Link/ChargeVoltage': 58.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 14,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 999}})
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
			'/FirmwareVersion': 0x0129},
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
			'/FirmwareVersion': 0x0129},
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
				'/Link/ChargeVoltage': 58.2},
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeVoltage': 58.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 0,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				# Difference goes to the multi
				'/Dc/0/MaxChargeCurrent': 0 }})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/EffectiveChargeVoltage': 58.2,
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
			'/FirmwareVersion': 0x0129},
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
				'/BatteryOperationalLimits/MaxChargeCurrent': 10,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 999}})
		self._check_values({
			'/SystemType': 'ESS',
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/EffectiveChargeVoltage': 58.3,
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
			'/FirmwareVersion': 0x0129},
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
		self._update_values(interval=60000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 45 + 8,
				'/Link/ChargeVoltage': 58.3},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 14,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 999}})
		self._check_values({
			'/SystemType': 'ESS',
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/EffectiveChargeVoltage': 58.3,
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
				'/Dc/0/MaxChargeCurrent': 999}})
		self._check_values({
			'/Control/SolarChargeCurrent': 0,
			'/Control/SolarChargeVoltage': 0,
			'/Control/EffectiveChargeVoltage': 58.2,
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

		self.assertEqual(multi.firmwareversion, 0x456)

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
			'/Dc/0/Current': 9.3,
			'/FirmwareVersion': 0x129,
		}, connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.socketcan_can0_di0_uc30688', {
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/FirmwareVersion': 0x102ff,
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

		# Check parallel support
		self.assertTrue(system.has_externalcontrol_support)

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
		self.assertEqual(system.bmses, [])

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
		self.assertTrue(system.bmses[0] is battery)
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
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x129},
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
				'/BatteryOperationalLimits/MaxChargeCurrent': 15
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
				'/BatteryOperationalLimits/MaxChargeCurrent': 20,
				'/Dc/0/MaxChargeCurrent': 999
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
			'/FirmwareVersion': 0x0139},
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
		self._check_values({
			'/Control/EffectiveChargeVoltage': None,
			'/Control/Dvcc': 0
		})

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
		self._check_values({
			'/Control/Dvcc': 1,
			'/Control/EffectiveChargeVoltage': 12.6,
		})

	def test_byd_bbox_p_quirks(self):
		""" BYD B-Box-Pro batteries should float at 55V when they send CCL=0. """
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
				'/BatteryOperationalLimits/MaxChargeCurrent': 100,
				'/BatteryOperationalLimits/MaxChargeVoltage': 56.5
			}
		})

		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Info/MaxChargeCurrent', 0)
		self._update_values(interval=3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeCurrent': 40,
				'/BatteryOperationalLimits/MaxChargeVoltage': 55
			}
		})
		self._check_values({ '/Control/EffectiveChargeVoltage': 55 })

	def test_byd_bbox_l_quirks(self):
		""" BYD B-Box-L batteries should float at 55V when they send CCL=0. """
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
				'/ProductId': 0xB015})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeCurrent': 100,
				'/BatteryOperationalLimits/MaxChargeVoltage': 56.5
			}
		})

		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Info/MaxChargeCurrent', 0)
		self._update_values(interval=3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeCurrent': 40,
				'/BatteryOperationalLimits/MaxChargeVoltage': 55
			}
		})
		self._check_values({ '/Control/EffectiveChargeVoltage': 55 })

	def test_lg_quirks(self):
		""" LG Batteries run at 57.7V, when we add an 0.4V offset we sometimes
		    trip the overvoltage protection at 58.1V. So we attempt to avoid that
			when feed-in is active. """
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 55.1,
				'/Dc/0/Current': 3,
				'/Dc/0/Power': 165.3,
				'/Soc': 100,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 94,
				'/Info/MaxChargeVoltage': 57.7,
				'/Info/MaxDischargeCurrent': 100,
				'/ProductId': 0xB004})
		self._update_values(interval=3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeVoltage': 57.3,
				'/BatteryOperationalLimits/MaxChargeCurrent': 94
			}
		})
		self._check_values({ '/Control/EffectiveChargeVoltage': 57.3 })

	def test_pylontech_quirks(self):
		""" Pylontech Batteries run at 53.2V and raise an alarm at 54V.
		    We attempt to avoid this with a lower charge voltage. """
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 51.8,
				'/Dc/0/Current': 3,
				'/Dc/0/Power': 155.4,
				'/Soc': 95,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': None,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 53.2,
				'/Info/MaxDischargeCurrent': 25,
				'/InstalledCapacity': None,
				'/ProductId': 0xB009})
		self._update_values(interval=3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeVoltage': 52.4,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25
			}
		})
		self._check_values({ '/Control/EffectiveChargeVoltage': 52.4 })

		# 24V battery is scaled accordingly
		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Info/MaxChargeVoltage', 28.4)
		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Info/MaxChargeCurrent', 55)
		self._update_values(interval=3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeVoltage': 27.8,
				'/BatteryOperationalLimits/MaxChargeCurrent': 55
			}
		})
		self._check_values({ '/Control/EffectiveChargeVoltage': 27.8 })

		# 24V battery has a CCL=0 quirk, replace with 0.25C charge rate. If charge rate is unknown
		# assume a single module at 55Ah.
		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Info/MaxChargeCurrent', 0)
		self._update_values(interval=3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeCurrent': 14
			}
		})
		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/InstalledCapacity', 222)
		self._update_values(interval=3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeCurrent': 56
			}
		})


	def test_no_bms_max_charge_current_setting(self):
		# Test that with no BMS but a user limit, /Dc/0/MaxChargeCurrent is correctly set.
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 55.2)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 0)
		self._set_setting('/Settings/SystemSetup/MaxChargeCurrent', 40)
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Settings/ChargeCurrentLimit': 100,
			'/Dc/0/Voltage': 58.0,
			'/Dc/0/Current': 30,
			'/FirmwareVersion': 0x0129},
			connection='VE.Direct')
		self._update_values(interval=60000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 5,
				'/Link/ChargeCurrent': 40 + 8,
				'/Link/ChargeVoltage': 55.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': None,
				'/BatteryOperationalLimits/MaxChargeCurrent': None,
				'/BatteryOperationalLimits/MaxChargeVoltage': None,
				'/BatteryOperationalLimits/MaxDischargeCurrent': None,
				'/Dc/0/MaxChargeCurrent': 10}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 0})

	def test_battery_properties(self):
		""" Test the propertes of battery objects. """
		from delegates.dvcc import Dvcc
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 51.8,
				'/Dc/0/Current': 3,
				'/Dc/0/Power': 155.4,
				'/Soc': 95,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': None,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 53.2,
				'/Info/MaxDischargeCurrent': 25,
				'/ProductId': 0xB009})
		self._update_values(interval=3000)

		batteries = list(Dvcc.instance._batterysystem)
		self.assertEqual(batteries[0].device_instance, 2)
		self.assertTrue(batteries[0].is_bms)

	def test_bms_selection(self):
		""" Test that if there is more than one BMS in the system,
		    the active battery service is preferred. """
		from delegates.dvcc import Dvcc

		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.battery/1')
		self._check_values({'/ActiveBatteryService': None})

		self._add_device('com.victronenergy.battery.ttyO1',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 51.8,
				'/Dc/0/Current': 3,
				'/Dc/0/Power': 155.4,
				'/Soc': 95,
				'/DeviceInstance': 0,
				'/Info/BatteryLowVoltage': None,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 53.2,
				'/Info/MaxDischargeCurrent': 25,
				'/ProductId': 0xB009})
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 52.8,
				'/Dc/0/Current': 4,
				'/Dc/0/Power': 152.4,
				'/Soc': 95,
				'/DeviceInstance': 1,
				'/Info/BatteryLowVoltage': None,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 53.2,
				'/Info/MaxDischargeCurrent': 25,
				'/ProductId': 0xB009})
		self._check_values({'/ActiveBatteryService': 'com.victronenergy.battery/1'})
		self.assertEqual(len(Dvcc.instance._batterysystem.bmses), 2)

		# Check that the selected battery is chosen, as both here have BMSes
		self.assertEqual(Dvcc.instance.bms.service, 'com.victronenergy.battery.ttyO2')

	def test_bms_selection_lowest_deviceinstance(self):
		""" Test that if there is more than one BMS in the system,
		    the lowest device instance """
		from delegates.dvcc import Dvcc

		# Select a non-existent battery service to ensure that none is active
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.battery/111')

		for did in (1, 0, 2):
			self._add_device('com.victronenergy.battery.ttyO{}'.format(did),
				product_name='battery',
				values={
					'/Dc/0/Voltage': 51.8,
					'/Dc/0/Current': 3,
					'/Dc/0/Power': 155.4,
					'/Soc': 95,
					'/DeviceInstance': did,
					'/Info/BatteryLowVoltage': None,
					'/Info/MaxChargeCurrent': 25,
					'/Info/MaxChargeVoltage': 53.2,
					'/Info/MaxDischargeCurrent': 25,
					'/ProductId': 0xB009})
		self._check_values({'/ActiveBatteryService': None})
		self.assertEqual(len(Dvcc.instance._batterysystem.bmses), 3)

		# Check that the lowest deviceinstante is chosen, as all here have BMSes
		self.assertEqual(Dvcc.instance.bms.service, 'com.victronenergy.battery.ttyO0')

	def test_bms_selection_no_bms(self):
		""" Test that delegate shows no BMS if none is available. """
		from delegates.dvcc import Dvcc

		self._add_device('com.victronenergy.battery.ttyO1',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 51.8,
				'/Dc/0/Current': 3,
				'/Dc/0/Power': 155.4,
				'/Soc': 95,
				'/DeviceInstance': 0})
		self.assertEqual(Dvcc.instance.bms, None)

	def test_firmware_warning(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x129},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x117},
			connection='VE.Direct')
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/FirmwareInsufficient': 1})

		# Upgrade ttyO2
		self._monitor.add_value('com.victronenergy.solarcharger.ttyO2', '/FirmwareVersion', 0x129)
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/FirmwareInsufficient': 0})

	def test_firmware_warning_2(self):
		# 24-bit version that is too old
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x101ff},
			connection='VE.Direct')
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/FirmwareInsufficient': 1})

		# Upgrade to 1.02
		self._monitor.add_value('com.victronenergy.solarcharger.ttyO2', '/FirmwareVersion', 0x102ff)
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/FirmwareInsufficient': 0})

	def test_firmware_warning_3(self):
		# For DVCC to do anything you need at least a managed battery or solarcharger in the
		# system.
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x129},
			connection='VE.Direct')

		# Downgrade the Multi
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/FirmwareVersion', 0x418)
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/FirmwareInsufficient': 1})

	def test_firmware_warning_4(self):
		# Add an RS MPPT, give it "old" firmware, ensure it does not raise
		# the alarm. The RS MPPT has support despite the low number.
		self._add_device('com.victronenergy.solarcharger.ttyO3', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/ProductId': 0xA102,
			'/FirmwareVersion': 0x100ff},
			connection='VE.Direct')
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/FirmwareInsufficient': 0})

	def test_flapping_firmware(self):
		# 24-bit version is new enough
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x102ff},
			connection='VE.Direct')
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/FirmwareInsufficient': 0})

		# Ignore what looks like a downgrade
		self._monitor.add_value('com.victronenergy.solarcharger.ttyO2', '/FirmwareVersion', 0x0f)
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/FirmwareInsufficient': 0})

		# But if the device is actually downgraded (which would cause a disconnect/reconnect), raise alarm
		self._remove_device('com.victronenergy.solarcharger.ttyO2')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x0f},
			connection='VE.Direct')
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/FirmwareInsufficient': 1})

	def test_multiple_battery_warning(self):
		self._check_values({'/Dvcc/Alarms/MultipleBatteries': 0})
		self._add_device('com.victronenergy.battery.ttyO1',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 58.1,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/MaxChargeVoltage': 55})
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/MultipleBatteries': 0})
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 58.1,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 3})
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/MultipleBatteries': 0})

		self._add_device('com.victronenergy.battery.ttyO3',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 58.1,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 4,
				'/Info/MaxChargeVoltage': 54})
		self._update_values(3000)
		self._check_values({'/Dvcc/Alarms/MultipleBatteries': 1})

	def test_only_forward_charge_current_to_n2k_zero(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 55.2)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 0)
		self._set_setting('/Settings/SystemSetup/MaxChargeCurrent', 10)
		self._add_device('com.victronenergy.solarcharger.socketcan_can0_vi0_B00B135', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Settings/ChargeCurrentLimit': 100,
			'/Dc/0/Voltage': 58.0,
			'/Dc/0/Current': 30,
			'/FirmwareVersion': 0x0129,
			'/N2kDeviceInstance': 0},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.socketcan_can0_vi0_B00B136', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Settings/ChargeCurrentLimit': 100,
			'/Dc/0/Voltage': 58.0,
			'/Dc/0/Current': 30,
			'/FirmwareVersion': 0x0129,
			'/N2kDeviceInstance': 1},
			connection='VE.Direct')
		self._update_values(60000)

		# Check that charge current limit is only forwarded to N2kDeviceInstance == 0
		self._check_external_values({
			'com.victronenergy.solarcharger.socketcan_can0_vi0_B00B135': {
				'/Link/ChargeCurrent': 10 + 8}, # 8A vebus dc current
			'com.victronenergy.solarcharger.socketcan_can0_vi0_B00B136': {
				'/Link/ChargeCurrent': None},
			})

	def test_charge_voltage_override(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 55.2)
		self._set_setting('/Settings/SystemSetup/MaxChargeVoltage', 0.0)
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 45,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=3000)

		# Following the battery
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2}})

		# Following lower of the two
		self._set_setting('/Settings/SystemSetup/MaxChargeVoltage', 59)
		self._update_values(interval=3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2}})

		# Following user limit
		self._set_setting('/Settings/SystemSetup/MaxChargeVoltage', 54.5)
		self._update_values(interval=3000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/MaxChargeVoltage': 54.5}})

	def test_inverter_rs_remote_control(self):
		# No multi in this system
		self._remove_device('com.victronenergy.vebus.ttyO1')

		self._add_device('com.victronenergy.inverter.ttyO1', {
			'/Ac/Out/L1/P': 60,
			'/Ac/Out/L1/V': 234.2,
			'/Dc/0/Voltage': 53.1,
			'/Dc/0/Current': -1.2,
			'/DeviceInstance': 278,
			'/Soc': 53.2,
			'/State': 9,
			'/IsInverterCharger': 1,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Settings/ChargeCurrentLimit': 100},
			product_name='Inverter RS', connection='VE.Direct')

		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 53.2,
				'/Dc/0/Current': -1.3,
				'/Dc/0/Power': 65,
				'/Soc': 43.2,
				'/DeviceInstance': 512,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 45,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=3000)

		self._check_external_values({
			'com.victronenergy.inverter.ttyO1': {
				'/Link/ChargeCurrent': 45,
				'/Link/ChargeVoltage': 58.2,
			}
		})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/EffectiveChargeVoltage': 58.2,
			'/Control/BmsParameters': 1
		})

	def test_inverter_rs_remote_control_2(self):
		# No multi in this system
		self._remove_device('com.victronenergy.vebus.ttyO1')

		# Add inverter
		self._add_device('com.victronenergy.inverter.ttyO1', {
			'/Ac/Out/L1/P': 2000,
			'/Ac/Out/L1/V': 234.2,
			'/Dc/0/Voltage': 53.1,
			'/Dc/0/Current': 40.0,
			'/DeviceInstance': 278,
			'/Soc': 53.2,
			'/State': 9,
			'/IsInverterCharger': 1,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Settings/ChargeCurrentLimit': 100},
			product_name='Inverter RS', connection='VE.Direct')

		# Battery
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 53.2,
				'/Dc/0/Current': 80.0,
				'/Dc/0/Power': 4000,
				'/Soc': 43.2,
				'/DeviceInstance': 512,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 100,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 100})

		# Solar charger
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Settings/ChargeCurrentLimit': 35,
			'/Dc/0/Voltage': 58.0,
			'/Dc/0/Current': 30.0,
			'/FirmwareVersion': 0x0129},
			connection='VE.Direct')

		self._update_values(interval=3000)

		# Check that inverter and solarcharger share charge current limit. Both have a 17.5A margin
		# and the total is 100A.
		self._check_external_values({
			'com.victronenergy.inverter.ttyO1': {
				'/Link/ChargeCurrent': 82.5,
				'/Link/ChargeVoltage': 58.2,
			},
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/ChargeVoltage': 58.2,
				'/Link/ChargeCurrent': 17.5
			}
		})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/EffectiveChargeVoltage': 58.2,
			'/Control/BmsParameters': 1
		})
