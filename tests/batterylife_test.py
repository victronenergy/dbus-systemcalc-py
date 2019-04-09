#!/usr/bin/env python
from itertools import chain
from collections import Counter

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
        """ No ESS or hub4 assistant in the multi, check that state remains untouched. """
        self._monitor.set_value(self.vebus, '/Hub4/AssistantId', None)
        # For any of the 13 possible states (0-12), ensure that if ESS assistant is gone
        # we just leave matters alone.
        for initialstate in xrange(13):
            self._set_setting('/Settings/CGwacs/BatteryLife/State', initialstate)
            self._update_values()
            self._check_settings({
                'state': initialstate,
                'flags': 0
            })

    def test_no_vebus(self):
        """ If there is no vebus, do nothing at all. """
        self._monitor.remove_service(self.vebus)
        # There's 12 possible ESS states, crudely test that it never hops if
        # there is no vebus.
        for initialstate in xrange(13):
            self._set_setting('/Settings/CGwacs/BatteryLife/State', initialstate)
            self._update_values()
            self._check_settings({
                'state': initialstate,
                'flags': 0
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

    def test_auto_recharge(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 20)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 21)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        # Drop charge a little
        self._monitor.set_value(self.vebus, '/Soc', 20)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged, 'soclimit': 25 })

        # A little more
        self._monitor.set_value(self.vebus, '/Soc', 15.1)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged })

        # Recharge!
        self._monitor.set_value(self.vebus, '/Soc', 15.0)
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

    def test_stability(self):
        """ No flapping between states on boundaries. """
        bl = BatteryLife()
        bl.set_sources(self._system_calc._dbusmonitor,
            self._system_calc._settings, self._system_calc._dbusservice)
        bl._tracked_values = {
            'soc': 100,
            'vebus': 'com.victronenergy.vebus.ttyO1'
        }

        def sweep(start, stop, initialstate):
            step = 1 if start < stop else -1
            newstate = initialstate
            transitions = {}
            for soc in chain(
                    (x/10.0 for x in xrange(start*10, stop*10, step)),
                    (stop,)):
                bl._tracked_values['soc'] = soc
                states = Counter()
                for _ in range(10):
                    _newstate = bl._map.get(newstate, lambda s: State.BLDefault)(bl)
                    if _newstate is not None:
                        # Record the transition points
                        if _newstate != newstate:
                            transitions[_newstate] = soc
                        states.update((_newstate,))
                        newstate = _newstate
                if(len(states)):
                    # We check the stability of the state machine by ensuring
                    # it settles on the most common value, and that all other
                    # states occur only once, that is, there are no cycles.
                    mc = states.most_common()
                    self.assertTrue(newstate == mc[0][0],
                        "state machine should settle on most common value")
                    for state, count in mc[1:]:
                        self.assertTrue(count <= 1,
                            "Cycle through states {}".format(states))
            return transitions

        # Discharge sweep
        transitions = sweep(100, 0, State.BLFloat)
        self.assertEqual(transitions, {
            State.BLAbsorption: 91.9,
            State.BLDefault: 81.9,
            State.BLDischarged: 10.0,
            State.BLLowSocCharge: 5.0
        })

        # Charge sweep
        transitions = sweep(0, 100, State.BLLowSocCharge)
        self.assertEqual(transitions, {
            State.BLDischarged: 10.0,
            State.BLDefault: 18.1,
            State.BLAbsorption: 85.0,
            State.BLFloat: 95.1
        })

        # Repeat the tests for SocGuard
        transitions = sweep(100, 0, State.SocGuardDefault)
        self.assertEqual(transitions, {
            State.SocGuardDischarged: 10.0,
            State.SocGuardLowSocCharge: 5.0
        })
        transitions = sweep(0, 100, State.SocGuardLowSocCharge)
        self.assertEqual(transitions, {
            State.SocGuardDischarged: 10.0,
            State.SocGuardDefault: 13.0
        })

        # Really low
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 5)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 5)

        transitions = sweep(10, 0, State.BLDefault)
        self.assertEqual(transitions, {
            State.BLDischarged: 5.0,
            State.BLLowSocCharge: 0
        })
        transitions = sweep(0, 10, State.BLLowSocCharge)
        self.assertEqual(transitions, {
            State.BLDischarged: 5.0,
            State.BLDefault: 8.1
        })

        # Down to zero. We don't go into BLLowSocCharge when MinSoc < 5%.
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 1)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 1)

        transitions = sweep(10, 0, State.BLDefault)
        self.assertEqual(transitions, {
            State.BLDischarged: 1,
        })
        transitions = sweep(0, 10, State.BLDischarged)
        self.assertEqual(transitions, {
            State.BLDefault: 4.1
        })

        # Repeat the tests for SocGuard
        transitions = sweep(10, 0, State.SocGuardDefault)
        self.assertEqual(transitions, {
            State.SocGuardDischarged: 1,
        })
        transitions = sweep(0, 10, State.SocGuardDischarged)
        self.assertEqual(transitions, {
            State.SocGuardDefault: 4.0
        })

    def test_socguard_auto_recharge_at_5(self):
        # If MinSoC>=5%, activate recharge at 0%. If MinSoC < 5%, never
        # activate recharge, the user clearly wants to go to completely empty.
        self._set_setting('/Settings/CGwacs/BatteryLife/State', State.SocGuardDefault)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 5)

        self._monitor.set_value(self.vebus, '/Soc', 1)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })

        self._monitor.set_value(self.vebus, '/Soc', 0)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardLowSocCharge })

        self._set_setting('/Settings/CGwacs/BatteryLife/State', State.SocGuardDefault)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 1)
        self._monitor.set_value(self.vebus, '/Soc', 1.1)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDefault })

        self._monitor.set_value(self.vebus, '/Soc', 0)
        self._update_values()
        self._check_settings({ 'state': State.SocGuardDischarged })

    def test_batterylife_auto_recharge_at_5(self):
        # If MinSoC=5%, activate recharge at 0%. If MinSoC < 5%, never activate
        # recharge. If MinsoC = 0%, never stop.
        self._set_setting('/Settings/CGwacs/BatteryLife/State', State.BLDefault)
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 5)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 5)

        self._monitor.set_value(self.vebus, '/Soc', 1)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged })

        self._monitor.set_value(self.vebus, '/Soc', 0)
        self._update_values()
        self._check_settings({ 'state': State.BLLowSocCharge })

        self._set_setting('/Settings/CGwacs/BatteryLife/State', State.BLDefault)
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 0)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 0)
        self._monitor.set_value(self.vebus, '/Soc', 1)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        self._monitor.set_value(self.vebus, '/Soc', 0)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })


    def test_recharge_moving_goalpost(self):
        self._set_setting('/Settings/CGwacs/BatteryLife/SocLimit', 20)
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 20)
        self._monitor.set_value(self.vebus, '/Soc', 21)
        self._update_values()
        self._check_settings({
            'soclimit': 20,
            'state': State.BLDefault
        })

        # When we hit the lower limit, soclimit is bumped (batterylife) and we
        # to into Discharged mode.
        self._monitor.set_value(self.vebus, '/Soc', 20)
        self._update_values()
        self._check_settings({
            'soclimit': 25,
            'state': State.BLDischarged
        })

        # SoC drops further
        self._monitor.set_value(self.vebus, '/Soc', 15.1)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged })

        # And we go into recharge
        self._monitor.set_value(self.vebus, '/Soc', 15.0)
        self._update_values()
        self._check_settings({ 'state': State.BLLowSocCharge })

        # User decides 15% is okay after all
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 15)
        self._monitor.set_value(self.vebus, '/Soc', 15)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged })

        # or 10% even, but we remain in Discharged because SocLimit is 25.
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 10)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged, 'soclimit': 25 })

        self._monitor.set_value(self.vebus, '/Soc', 28.1)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        # Simulate new day
        self._set_setting('/Settings/CGwacs/BatteryLife/Flags', 0)
        self._monitor.set_value(self.vebus, '/Soc', 24.9)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged, 'soclimit': 30 })

        self._monitor.set_value(self.vebus, '/Soc', 33.1)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        # And another new day, soclimit keeps creeping up 
        self._set_setting('/Settings/CGwacs/BatteryLife/Flags', 0)
        self._monitor.set_value(self.vebus, '/Soc', 29.9)
        self._update_values()
        self._check_settings({ 'state': State.BLDischarged, 'soclimit': 35 })

        self._monitor.set_value(self.vebus, '/Soc', 38.1)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

        # Go into absorption and check that soclimit is dropped again.
        self._monitor.set_value(self.vebus, '/Soc', 85)
        self._update_values()
        self._check_settings({ 'state': State.BLAbsorption, 'soclimit': 30 })


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
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 75)
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
            'state': State.BLDefault,
            'soclimit': 0
        })

        self._monitor.set_value(self.vebus, '/Soc', 3)
        self._update_values()
        self._check_settings({ 'state': State.BLDefault })

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
        self._set_setting('/Settings/CGwacs/BatteryLife/MinimumSocLimit', 20)
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

        self._monitor.set_value(self.vebus, '/Soc', 14.9)
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
