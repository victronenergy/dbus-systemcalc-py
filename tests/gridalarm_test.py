from datetime import datetime, date, time, timedelta

# This adapts sys.path to include all relevant packages
import context

# Testing tools
from mock_gobject import timer_manager

# our own packages
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

class TestGridAlarm(TestSystemCalcBase):
    vebus = 'com.victronenergy.vebus.ttyO1'
    settings = 'com.victronenergy.settings'
    def __init__(self, methodName='runTest'):
        TestSystemCalcBase.__init__(self, methodName)

    def setUp(self):
        TestSystemCalcBase.setUp(self)
        self._add_device(self.vebus, product_name='Multi',
            values={
                '/Ac/ActiveIn/ActiveInput': None,
                '/Devices/0/Assistants': [0x55, 0x1] + (26 * [0]),
                '/Hub4/AssistantId': 5,
                '/VebusMainState': 9,
                '/State': 3,
                '/Soc': 53.2,
                '/ExtraBatteryCurrent': 0})

        self._add_device(self.settings,
            values={
                '/Settings/SystemSetup/AcInput1': 2, # Generator
                '/Settings/SystemSetup/AcInput2': 1, # Grid
            })

        self._update_values()

    def test_grid_alarm_disabled(self):
        self._set_setting('/Settings/Alarm/System/GridLost', 0)
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0xF0)
        self._update_values(interval=11000)
        self._check_values({'/Ac/Alarms/GridLost': None})

    def test_grid_alarm_enabled(self):
        self._set_setting('/Settings/Alarm/System/GridLost', 1)
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 1)
        self._update_values()

        # Grid fails
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0xF0)

        # Alarm doesn't activate immediately
        self._update_values(interval=6000)
        self._check_values({'/Ac/Alarms/GridLost': 0})

        # Alarm activates after timeout
        self._update_values(interval=6000)
        self._check_values({'/Ac/Alarms/GridLost': 2})

        # Alarm resets if the grid come back
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 1)
        self._update_values()
        self._check_values({'/Ac/Alarms/GridLost': 0})

    def test_grid_alarm_cancel(self):
        self._set_setting('/Settings/Alarm/System/GridLost', 1)
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 1)
        self._update_values()

        # Fail, no alarm
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0xF0)
        self._update_values(interval=6000)
        self._check_values({'/Ac/Alarms/GridLost': 0})

        # AC Return before the timeout
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 1)
        self._update_values(interval=6000)
        self._check_values({'/Ac/Alarms/GridLost': 0})

    def test_grid_alarm_on_genertor(self):
        self._set_setting('/Settings/Alarm/System/GridLost', 1)
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 1) # Grid
        self._update_values()

		# Switch to generator
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0)

        # Alarm doesn't activate immediately
        self._update_values(interval=6000)
        self._check_values({'/Ac/Alarms/GridLost': 0})

        # Alarm activates after timeout
        self._update_values(interval=6000)
        self._check_values({'/Ac/Alarms/GridLost': 2})

		# Grid returns
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 1) # Grid
        self._update_values()
        self._check_values({'/Ac/Alarms/GridLost': 0})
