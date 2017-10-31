#!/usr/bin/env python
import math

# This adapts sys.path to include all relevant packages
import context

# our own packages
import dbus_systemcalc
import delegates
import mock_gobject
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
				'/BatteryOperationalLimits/BatteryLowVoltage': None
			})
		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/SystemSetup/AcInput1': 1,
				'/Settings/SystemSetup/AcInput2': 2,
			})

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
		self._update_values()
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': 12.6,
				'/Link/VoltageSense': 12.25,
				'/State': 2
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
		self._update_values()
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': 12.6,
				'/Link/VoltageSense': 12.25,
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
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x0117},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._update_values()
		self.assertEqual(12.5, self._monitor.get_value('com.victronenergy.solarcharger.ttyO1',
			'/Link/ChargeVoltage'))
		self.assertEqual(12.5, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2',
			'/Link/ChargeVoltage'))
		self.assertEqual(2, self._monitor.get_value('com.victronenergy.solarcharger.ttyO1', '/State'))
		self.assertEqual(0, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2', '/State'))
		self._check_values({'/Control/SolarChargeVoltage': 1})

	def test_hub1_control_voltage_ve_can_solarchargers(self):
		# Hub1 control should ignore VE.Can solarchargers
		# self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.5)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
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
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 13.2)
		self._add_device('com.victronenergy.vecan.can1', {
			'/Link/ChargeVoltage': None})
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
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
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
		self._update_values(interval=10000)
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
		self._update_values(interval=10000)
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
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25 + 8,
				'/Link/ChargeVoltage': 58.2,
				'/Link/VoltageSense': 12.25},
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
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO0': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25 - 7 + 8,
				'/Link/ChargeVoltage': 58.2},
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 7 * 1.1 / 0.9,
				'/Link/ChargeVoltage': 58.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': math.floor((25 - 14.3 - 7) * 0.8)}})
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
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
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
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 100,
				'/Link/ChargeVoltage': 58.3},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 25}})
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
		self._update_values(interval=10000)
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
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
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
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
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
				'/Link/ChargeCurrent': None,
				'/Link/VoltageSense': 12.25}})

	def test_hub1_extra_current_no_active_battery(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 23)
		self._set_setting('/Settings/SystemSetup/BatteryService', 'nobattery')
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
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

	def test_hub1_extra_current_write_soc(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 23)
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.battery/2')
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
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

	def test_hub1_extra_current_no_write_soc(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 23)
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.vebus/0')
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
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

	def test_vebus_soc_writer(self):
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._update_values(10000)
		self.assertEqual(15.3, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._check_values({'/Control/ExtraBatteryCurrent': 0})
		self._check_values({'/Control/VebusSoc': 1})

	def test_vebus_soc_writer_hub2(self):
		# Set hub-2 & Input current control
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Devices/0/Assistants',
			[0x46, 0x01, 0x00, 0x00, 0x4D, 0x01] + (24 * [0]))
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._update_values(10000)
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._check_values({'/Control/ExtraBatteryCurrent': 0})
		self._check_values({'/Control/VebusSoc': 0})

	def test_vebus_soc_writer_no_assistant_ids(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Devices/0/Assistants', None)
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._update_values(10000)
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._check_values({'/Control/ExtraBatteryCurrent': 0})
		self._check_values({'/Control/VebusSoc': 0})

	def test_vebus_soc_writer_vebus(self):
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.vebus/0')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._update_values(10000)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Soc', 54)
		self._update_values(10000)
		self.assertEqual(54, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._check_values({'/Control/ExtraBatteryCurrent': 0})
		self._check_values({'/Control/VebusSoc': 0})
