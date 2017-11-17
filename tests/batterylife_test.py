#!/usr/bin/env python

# This adapts sys.path to include all relevant packages
import context

# Testing tools
from mock_gobject import timer_manager

# our own packages
import dbus_systemcalc
from delegates import BatteryLife
from delegates.batterylife import State, Flags
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

# Time travel patch
BatteryLife._get_time = lambda *a: timer_manager.datetime

class TestBatteryLife(TestSystemCalcBase):
    vebus = 'com.victronenergy.vebus.ttyO1'
    settings = 'com.victronenergy.settings'
    def __init__(self, methodName='runTest'):
        TestSystemCalcBase.__init__(self, methodName)

    def setUp(self):
        TestSystemCalcBase.setUp(self)
        self._add_device(self.vebus, product_name='Multi',
            values={
                '/State': 3,
                '/Hub4/AssistantId': 5,
                '/Hub4/Sustain': 0,
                '/Soc': 53.2})

        # Settings related to BatteryLife
        self._add_device(self.settings, values={
                '/Settings/CGwacs/BatteryLife/State': 2,
                '/Settings/CGwacs/BatteryLife/Flags': 0,
                '/Settings/CGwacs/BatteryLife/SocLimit': 10,
                '/Settings/CGwacs/BatteryLife/MinimumSocLimit': 10,
                '/Settings/CGwacs/BatteryLife/DischargedTime': 0})

        self._update_values()

    def test_no_ess(self):
        """ No ESS or hub4 assistant in the multi, go to the disabled state. """
        self._monitor.set_value(self.vebus, '/Hub4/AssistantId', None)
        self._update_values()
        self._check_settings({
            'state': State.BLDisabled, # Disabled
            'flags': 0, # No flags
        })

    def test_default(self):
        """ If assistant installed and SOC is above the minumum, go to default
            state. """
        self._update_values()
        self._check_settings({
            'state': State.BLDefault, # Default
            'flags': 0, # No flags
        })

    def test_lowsoc(self):
        # Set SocLimit
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 55)
        self._update_values()

        self._check_settings({
            'state': State.BLDischarged, # Discharged
            'flags': Flags.Discharged,
        })

        # Bring the Soc Up to the limit, it should only switch after hysteresis
        # value
        self._monitor.set_value(self.vebus, '/Soc', 55)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged, # Discharged
            'flags': Flags.Discharged,
        })

        # Push Soc past hysteresis
        self._monitor.set_value(self.vebus, '/Soc', 63.01)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'flags': Flags.Discharged,
        })

    def test_flag_accumulation(self):
        self._update_values()

        # Set Discharged flag
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 55)
        self._update_values()

        # Back to default
        self._monitor.set_value(self.vebus, '/Soc', 65)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'flags': Flags.Discharged,
        })

        # Absorption flag
        self._monitor.set_value(self.vebus, '/Soc', 86)
        self._update_values()
        self._check_settings({
            'state': State.BLAbsorption,
            'flags': Flags.Discharged | Flags.Absorption,
        })

        # Float flag
        self._monitor.set_value(self.vebus, '/Soc', 96)
        self._update_values()

        self._check_settings({
            'state': State.BLFloat,
            'flags': Flags.Discharged | Flags.Float | Flags.Absorption,
        })

    def test_flag_reset(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/Flags',
            Flags.Discharged | Flags.Float | Flags.Absorption)
        self._update_values()
        self._check_settings({
            'flags': Flags.Discharged | Flags.Float | Flags.Absorption
        })

        # It was day and it was night, the second day.
        timer_manager.run(86400000)

        self._check_settings({
            'flags': 0,
        })

    def test_slow_charge(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 55)
        self._set_setting('/Settings/CGwacs/BatteryLife/State', State.BLSustain) # Sustain
        self._set_setting('/Settings/CGwacs/BatteryLife/Flags', Flags.Discharged) # Discharged
        self._set_setting('/Settings/CGwacs/BatteryLife/DischargedTime', 1)

        timer_manager.run(900000)
        self._check_settings({
            'state': State.BLForceCharge, # Slow charge
            'flags': Flags.Discharged,
        })

        self._monitor.set_value(self.vebus, '/Soc', 60.01)
        self._update_values()

        self._check_settings({
            'state': State.BLDischarged, # Back into Discharged state
            'flags': Flags.Discharged,
        })

    def test_record_discharged_soc(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 50)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        # Send into discharged state, check that soc is recorded
        self._monitor.set_value(self.vebus, '/Soc', 18.5)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'dischargedsoc': 20
        })

        # Subsequent changes to the limit does not modify the recorded value
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 19)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'dischargedsoc': 20
        })

    def test_auto_recharge(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 21)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        # Drop charge a little
        self._monitor.set_value(self.vebus, '/Soc', 20)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged })

        # A little more
        self._monitor.set_value(self.vebus, '/Soc', 15)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged })

        # Recharge!
        self._monitor.set_value(self.vebus, '/Soc', 14.9)
        self._update_values()
        self._check_settings({ 'state': State.BLLowSocCharge })

        # Charging
        self._monitor.set_value(self.vebus, '/Soc', 19.9)
        self._update_values()
        self._check_settings({ 'state': State.BLLowSocCharge })

        # Stop charging
        self._monitor.set_value(self.vebus, '/Soc', 20)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged })

    def test_socguard_auto_recharge(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/State', State.SocGuardDefault)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 21)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDefault })

        # Drop charge a little
        self._monitor.set_value(self.vebus, '/Soc', 20)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })

        # A little more
        self._monitor.set_value(self.vebus, '/Soc', 17)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })

        # Recharge!
        self._monitor.set_value(self.vebus, '/Soc', 14.9)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardLowSocCharge })

        # Charging
        self._monitor.set_value(self.vebus, '/Soc', 19.9)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardLowSocCharge })

        # Stop charging
        self._monitor.set_value(self.vebus, '/Soc', 20)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })

    # Older tests migrated and adapted from hub4control
    def test_chargeToAbsorption(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 50)
        self._update_values()
        self._check_settings({
            'soclimit': 20,
            'state': State.BLDefault
        })
        self._check_values({
			'/Dc/Battery/Soc': 50
        })

        self._monitor.set_value(self.vebus, '/Soc', 85)
        self._update_values()
        self._check_settings({
            'soclimit': 15,
            'state': State.BLAbsorption
        })

        self._monitor.set_value(self.vebus, '/Soc', 75)
        self._update_values()
        self._check_settings({
            'soclimit': 15,
            'state': State.BLDefault
        })

        self._monitor.set_value(self.vebus, '/Soc', 14)
        self._update_values()
        self._check_settings({
            'soclimit': 20,
            'state': State.BLDischarged
        })

        self._monitor.set_value(self.vebus, '/Soc', 16)
        self._update_values()
        self._check_settings({
            'soclimit': 20,
            'state': State.BLDischarged
        })

        self._monitor.set_value(self.vebus, '/Soc', 26)
        self._update_values()
        self._check_settings({
            'soclimit': 20,
            'state': State.BLDefault
        })

    def test_chargeToAbsorption80Pct(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 80)
        self._monitor.set_value(self.vebus, '/Soc', 50)
        self._update_values()
        self._check_settings({ 'state': State.BLLowSocCharge })

        self._monitor.set_value(self.vebus, '/Soc', 85.5)
        self._update_values()
        self._check_settings({ 'state': State.BLAbsorption })

        self._monitor.set_value(self.vebus, '/Soc', 86)
        self._update_values()
        self._check_settings({
            'soclimit': 75,
            'state': State.BLAbsorption
        })

        self._monitor.set_value(self.vebus, '/Soc', 69.9)
        self._update_values()
        self._check_settings({ 'state': State.BLLowSocCharge })

        self._monitor.set_value(self.vebus, '/Soc', 74)
        self._update_values()
        self._check_settings({ 'state': State.BLLowSocCharge })

        self._monitor.set_value(self.vebus, '/Soc', 75)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged })

        self._monitor.set_value(self.vebus, '/Soc', 85)
        self._update_values()
        self._check_settings({ 'state': State.BLAbsorption })

        self._monitor.set_value(self.vebus, '/Soc', 85.5)
        self._update_values()
        self._check_settings({
            'state': State.BLAbsorption,
            'soclimit': 75
        })

        self._monitor.set_value(self.vebus, '/Soc', 100)
        self._update_values()
        self._check_settings({
            'state': State.BLFloat,
            'soclimit': 70
        })

    def test_chargeToAbsorption0Pct(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 0)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 0)
        self._monitor.set_value(self.vebus, '/Soc', 0)
        self._update_values()
        self._check_settings({
            'state': State.BLLowSocCharge,
            'soclimit': 5
        })

        self._monitor.set_value(self.vebus, '/Soc', 3)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged })

        self._monitor.set_value(self.vebus, '/Soc', 9)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        self._monitor.set_value(self.vebus, '/Soc', 86)
        self._update_values()
        self._check_settings({
            'state': State.BLAbsorption,
            'soclimit': 0
        })

    def test_chargeToAbsorption5Pct(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 0)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 5)
        self._monitor.set_value(self.vebus, '/Soc', 10)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        self._monitor.set_value(self.vebus, '/Soc', 5)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'soclimit': 10
        })

        self._monitor.set_value(self.vebus, '/Soc', 10)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged })

        self._monitor.set_value(self.vebus, '/Soc', 14)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        self._monitor.set_value(self.vebus, '/Soc', 86)
        self._update_values()
        self._check_settings({
            'state': State.BLAbsorption,
            'soclimit': 5
        })

    def test_chargeToAbsorption95Pct(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 95)
        self._monitor.set_value(self.vebus, '/Soc', 50)
        self._update_values()
        self._check_settings({ 'state': State.BLLowSocCharge })

        self._monitor.set_value(self.vebus, '/Soc', 98.1)
        self._update_values()
        self._check_settings({ 'state': State.BLFloat })

        self._monitor.set_value(self.vebus, '/Soc', 86)
        self._update_values()
        self._check_settings({
            'state': State.BLLowSocCharge,
            'soclimit': 80
        })

        self._monitor.set_value(self.vebus, '/Soc', 98.1)
        self._update_values()
        self._check_settings({ 'state': State.BLFloat })

        self._monitor.set_value(self.vebus, '/Soc', 85.5)
        self._update_values()
        self._check_settings({
            'state': State.BLLowSocCharge,
            'soclimit': 80
        })

        self._monitor.set_value(self.vebus, '/Soc', 100)
        self._update_values()
        self._check_settings({
            'state': State.BLFloat,
            'soclimit': 80
        })


    def test_chargeToAbsorption100Pct(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 100)
        self._monitor.set_value(self.vebus, '/Soc', 50)
        self._update_values()
        self._check_settings({ 'state': State.BLLowSocCharge })

        self._monitor.set_value(self.vebus, '/Soc', 100)
        self._update_values()
        self._check_settings({ 'state': State.BLFloat })

        self._monitor.set_value(self.vebus, '/Soc', 99.5)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'soclimit': 80
        })

        self._monitor.set_value(self.vebus, '/Soc', 100)
        self._update_values()
        self._check_settings({ 'state': State.BLFloat })

        self._monitor.set_value(self.vebus, '/Soc', 85.5)
        self._update_values()
        self._check_settings({
            'state': State.BLLowSocCharge,
            'soclimit': 80
        })

        self._monitor.set_value(self.vebus, '/Soc', 100)
        self._update_values()
        self._check_settings({
            'state': State.BLFloat,
            'soclimit': 80
        })

    def test_chargeToFloat(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 50)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 20
        })

        self._monitor.set_value(self.vebus, '/Soc', 90)
        self._update_values()
        self._check_settings({
            'state': State.BLAbsorption,
            'soclimit': 15,
            'flags': Flags.Absorption
        })

        self._monitor.set_value(self.vebus, '/Soc', 98)
        self._update_values()
        self._check_settings({
            'state': State.BLFloat,
            'soclimit': 10,
            'flags': Flags.Absorption | Flags.Float
        })

        self._monitor.set_value(self.vebus, '/Soc', 70)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        self._monitor.set_value(self.vebus, '/Soc', 65)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 10
        })

        self._monitor.set_value(self.vebus, '/Soc', 9)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'soclimit': 15
        })

        self._monitor.set_value(self.vebus, '/Soc', 14)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'soclimit': 15
        })

        self._monitor.set_value(self.vebus, '/Soc', 21)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 15
        })

    def test_socJumpToFloat(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 50)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 20
        })

        self._monitor.set_value(self.vebus, '/Soc', 100)
        self._update_values()
        self._check_settings({
            'state': State.BLFloat,
            'soclimit': 10,
            'flags': Flags.Absorption | Flags.Float
        })

        self._monitor.set_value(self.vebus, '/Soc', 80)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 10,
            'flags': Flags.Absorption | Flags.Float
        })

    def test_socJumpToFloat2(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 50)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 20
        })

        self._monitor.set_value(self.vebus, '/Soc', 90)
        self._update_values()
        self._check_settings({
            'state': State.BLAbsorption,
            'soclimit': 15,
            'flags': Flags.Absorption
        })

        self._monitor.set_value(self.vebus, '/Soc', 80)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 15,
            'flags': Flags.Absorption
        })

        self._monitor.set_value(self.vebus, '/Soc', 100)
        self._update_values()
        self._check_settings({
            'state': State.BLFloat,
            'soclimit': 10,
            'flags': Flags.Absorption | Flags.Float
        })

        self._monitor.set_value(self.vebus, '/Soc', 80)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 10,
            'flags': Flags.Absorption | Flags.Float
        })

    def test_batteryLifeLowSocCharge(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 21)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 20
        })

        self._monitor.set_value(self.vebus, '/Soc', 20)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'soclimit': 25
        })

        self._monitor.set_value(self.vebus, '/Soc', 19)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'soclimit': 25
        })

        self._monitor.set_value(self.vebus, '/Soc', 22)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'soclimit': 25
        })

        self._monitor.set_value(self.vebus, '/Soc', 29)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 25
        })

        self._monitor.set_value(self.vebus, '/Soc', 20)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'soclimit': 25
        })

        self._monitor.set_value(self.vebus, '/Soc', 19)
        self._update_values()
        self._check_settings({
            'state': State.BLLowSocCharge,
            'soclimit': 25
        })

        self._monitor.set_value(self.vebus, '/Soc', 27)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'soclimit': 25
        })

        self._monitor.set_value(self.vebus, '/Soc', 29)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 25
        })

        # Check if we get out if Discharged state without entering LowSocCharge
        self._monitor.set_value(self.vebus, '/Soc', 25)
        self._update_values()
        self._check_settings({
            'state': State.BLDischarged,
            'soclimit': 25
        })

        self._monitor.set_value(self.vebus, '/Soc', 29)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 25
        })

    def test_minSocLimitTest(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/State', State.SocGuardDefault)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 50)
        self._update_values()
        self._check_settings({
            'state': State.SocGuardDefault,
            'minsoclimit': 20
        })

        self._monitor.set_value(self.vebus, '/Soc', 20)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })
        self._monitor.set_value(self.vebus, '/Soc', 19)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })
        self._monitor.set_value(self.vebus, '/Soc', 21)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })
        self._monitor.set_value(self.vebus, '/Soc', 23)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDefault })
        self._monitor.set_value(self.vebus, '/Soc', 20)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })
        self._monitor.set_value(self.vebus, '/Soc', 21)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })
        self._monitor.set_value(self.vebus, '/Soc', 23)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDefault })

    def test_minSocLimit100Test(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/State', State.SocGuardDefault)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 100)
        self._monitor.set_value(self.vebus, '/Soc', 100)
        self._update_values()
        self._check_settings({
            'state': State.SocGuardDefault,
            'minsoclimit': 100
        })
        self._monitor.set_value(self.vebus, '/Soc', 99)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })
        self._monitor.set_value(self.vebus, '/Soc', 94.9)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardLowSocCharge })
        self._monitor.set_value(self.vebus, '/Soc', 99)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardLowSocCharge })
        self._monitor.set_value(self.vebus, '/Soc', 100)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDefault })
        self._monitor.set_value(self.vebus, '/Soc', 99)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })
        self._monitor.set_value(self.vebus, '/Soc', 100)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDefault })
        self._monitor.set_value(self.vebus, '/Soc', 99)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })

    def test_minSocLimit80Test(self):
        self._monitor.set_value(self.vebus, '/Soc', 82)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 80)
        self._set_setting('/Settings/CGwacs/BatteryLife/State', State.BLDefault)
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 75)
        self._set_setting('/Settings/CGwacs/BatteryLife/Flags', Flags.Discharged)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 75
        })

        for soc in (78.6, 78.5, 78.4):
            self._monitor.set_value(self.vebus, '/Soc', soc)
            self._update_values()
            self._check_settings({
                'state': State.BLDischarged,
                'soclimit': 75
            })

        self._monitor.set_value(self.vebus, '/Soc', 84)
        self._update_values()
        self._check_settings({
            'state': State.BLDefault,
            'soclimit': 75
        })
