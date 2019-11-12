#!/usr/bin/env python

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

class TestEssStates(TestSystemCalcBase):
    vebus = 'com.victronenergy.vebus.ttyO1'
    battery = 'com.victronenergy.battery.ttyO2'
    settings = 'com.victronenergy.settings'
    def __init__(self, methodName='runTest'):
        TestSystemCalcBase.__init__(self, methodName)

    def setUp(self):
        TestSystemCalcBase.setUp(self)
        self._add_device(self.vebus, product_name='Multi',
            values={
                '/Ac/ActiveIn/L1/P': 123,
                '/Ac/ActiveIn/ActiveInput': 0,
                '/Ac/ActiveIn/Connected': 1,
                '/Ac/Out/L1/P': 100,
                '/Dc/0/Voltage': 12.25,
                '/Dc/0/Current': 8,
                '/DeviceInstance': 0,
                '/Hub4/AssistantId': 5,
                '/Hub4/Sustain': 0,
                '/Dc/0/MaxChargeCurrent': None,
                '/Soc': 53.2,
                '/State': 3, # Bulk
                '/VebusMainState': 9, # Charging
                '/BatteryOperationalLimits/MaxChargeVoltage': None,
                '/BatteryOperationalLimits/MaxChargeCurrent': None,
                '/BatteryOperationalLimits/MaxDischargeCurrent': None,
                '/BatteryOperationalLimits/BatteryLowVoltage': None,
                '/Bms/AllowToCharge': None,
                '/Bms/AllowToDischarge': None})

        self._add_device(self.battery, product_name='battery', values={
                '/Dc/0/Voltage': 12.4,
                '/Dc/0/Current': 5.6,
                '/Dc/0/Power': 69.4,
                '/Soc': 53.2})

        # Self-Consumption, BatteryLife
        self._add_device(self.settings, values={
                '/Settings/CGwacs/BatteryLife/State': 2,
                '/Settings/SystemSetup/MaxChargeCurrent': None,
                '/Settings/CGwacs/MaxDischargePower': None})

    def test_no_ess(self):
        self._monitor.set_value(self.vebus, '/Hub4/AssistantId', None)
        self._update_values()

        # SystemState should mirror State
        self._check_values({'/SystemState/State': 3})

    def test_with_ess(self):
        self._update_values()

        # State should pass through unchanged while charging
        self._check_values({'/SystemState/State': 3})

    def test_discharge_indicator(self):
        # Battery is discharging
        self._monitor.set_value(self.battery, '/Dc/0/Current', -5.6)
        self._monitor.set_value(self.battery, '/Dc/0/Power', -69.4)
        self._update_values()
        self._check_values({'/SystemState/State': 0x100}) # Discharging

    def test_sustain_state(self):
        self._monitor.set_value(self.vebus, '/Hub4/Sustain', 1)
        self._update_values()
        self._check_values({'/SystemState/State': 0x101}) # Sustain


    def test_no_flags(self):
        # Check that all flags are cleared when in good state
        self._check_values({
            '/SystemState/LowSoc': 0,
            '/SystemState/BatteryLife': 0,
            '/SystemState/ChargeDisabled': 0,
            '/SystemState/DischargeDisabled': 0,
            '/SystemState/SlowCharge': 0,
            '/SystemState/UserChargeLimited': 0,
            '/SystemState/UserDischargeLimited': 0})

    def test_low_soc(self):
        self._monitor.set_value(self.settings,
            '/Settings/CGwacs/BatteryLife/State', 5)
        self._update_values()
        self._check_values({
            '/SystemState/LowSoc': 1,
            '/SystemState/BatteryLife': 1,
            '/SystemState/ChargeDisabled': 0,
            '/SystemState/DischargeDisabled': 0,
            '/SystemState/SlowCharge': 0,
            '/SystemState/UserChargeLimited': 0,
            '/SystemState/UserDischargeLimited': 0})

    def test_vebus_bms(self):
        def _check_vebus_bms(x, y):
            self._monitor.set_value(self.vebus, '/Bms/AllowToCharge', x)
            self._monitor.set_value(self.vebus, '/Bms/AllowToDischarge', y)
            self._update_values()
            self._check_values({
                '/SystemState/LowSoc': 0,
                '/SystemState/BatteryLife': 0,
                '/SystemState/ChargeDisabled': int(not x),
                '/SystemState/DischargeDisabled': int(not y),
                '/SystemState/SlowCharge': 0,
                '/SystemState/UserChargeLimited': 0,
                '/SystemState/UserDischargeLimited': 0})
        _check_vebus_bms(0, 0)
        _check_vebus_bms(0, 1)
        _check_vebus_bms(1, 0)
        _check_vebus_bms(1, 1)

    def test_can_bms(self):
        def _check_can_bms(x, y):
            self._monitor.set_value(self.vebus,
                '/BatteryOperationalLimits/MaxChargeCurrent', x*600)
            self._monitor.set_value(self.vebus,
                '/BatteryOperationalLimits/MaxDischargeCurrent', y*600)
            self._update_values()
            self._check_values({
                '/SystemState/LowSoc': 0,
                '/SystemState/BatteryLife': 0,
                '/SystemState/ChargeDisabled': int(not x),
                '/SystemState/DischargeDisabled': int(not y),
                '/SystemState/SlowCharge': 0,
                '/SystemState/UserChargeLimited': 0,
                '/SystemState/UserDischargeLimited': 0})
        _check_can_bms(0, 0)
        _check_can_bms(0, 1)
        _check_can_bms(1, 0)
        _check_can_bms(1, 1)

    def test_slow_charge(self):
        self._monitor.set_value(self.settings,
            '/Settings/CGwacs/BatteryLife/State', 6)
        self._update_values()
        self._check_values({
            '/SystemState/LowSoc': 0,
            '/SystemState/BatteryLife': 0,
            '/SystemState/ChargeDisabled': 0,
            '/SystemState/DischargeDisabled': 0,
            '/SystemState/SlowCharge': 1,
            '/SystemState/UserChargeLimited': 0,
            '/SystemState/UserDischargeLimited': 0})

    def test_user_discharge_limited(self):
        self._monitor.set_value(self.settings,
            '/Settings/CGwacs/MaxDischargePower', 0)
        self._update_values()
        self._check_values({
            '/SystemState/LowSoc': 0,
            '/SystemState/BatteryLife': 0,
            '/SystemState/ChargeDisabled': 0,
            '/SystemState/DischargeDisabled': 0,
            '/SystemState/SlowCharge': 0,
            '/SystemState/UserChargeLimited': 0,
            '/SystemState/UserDischargeLimited': 1})

    def test_user_charge_limited(self):
        self._monitor.set_value(self.settings,
            '/Settings/SystemSetup/MaxChargeCurrent', 0)
        self._update_values()
        self._check_values({
            '/SystemState/LowSoc': 0,
            '/SystemState/BatteryLife': 0,
            '/SystemState/ChargeDisabled': 0,
            '/SystemState/DischargeDisabled': 0,
            '/SystemState/SlowCharge': 0,
            '/SystemState/UserChargeLimited': 1,
            '/SystemState/UserDischargeLimited': 0})

    def test_user_discharge_limited_keepcharged(self):
        self._monitor.set_value(self.settings,
            '/Settings/CGwacs/BatteryLife/State', 9)
        self._monitor.set_value(self.settings,
            '/Settings/CGwacs/MaxDischargePower', 0)
        self._update_values()
        self._check_values({
            '/SystemState/LowSoc': 0,
            '/SystemState/BatteryLife': 0,
            '/SystemState/ChargeDisabled': 0,
            '/SystemState/DischargeDisabled': 0,
            '/SystemState/SlowCharge': 0,
            '/SystemState/UserChargeLimited': 0,
            '/SystemState/UserDischargeLimited': 0})

    def test_vedirect_inverter(self):
        """ Check that a VE.Direct inverter's state is also returned. """
        self._add_device('com.victronenergy.inverter.ttyO2',
            product_name='inverter', values={
                '/Ac/Out/L1/I': 0.7,
                '/Ac/Out/L1/V': 230,
                '/Dc/0/Voltage': 12.4,
                '/State': 9 }) # Inverting
        self._update_values()
        self._check_values({ '/SystemState/State': 3 }) # The VE.Bus inverter takes precedence

        self._remove_device(self.vebus)
        self._update_values()
        self._check_values({ '/SystemState/State': 9 }) # State from VE.Direct inverter
