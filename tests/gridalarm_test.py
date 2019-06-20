from datetime import datetime, date, time, timedelta

# This adapts sys.path to include all relevant packages
import context

# Testing tools
from mock_gobject import timer_manager

# our own packages
from delegates import GridAlarm
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

# Shorten timeout
GridAlarm.ALARM_TIMEOUT = 5000

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
        self._set_setting('/Settings/Alarm/System/AcLost', 0)
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0xF0)
        self._update_values(interval=6000)
        self._check_values({'/Ac/Alarms/AcLost': 0})

    def test_grid_alarm_enabled(self):
        self._set_setting('/Settings/Alarm/System/AcLost', 1)
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0)
        self._monitor.set_value(self.settings, '/Settings/SystemSetup/AcInput1', 0) # Not available
        self._update_values()

        # Alarm not armed because AC explicitly marked as unavailable
        self.assertTrue(not GridAlarm.instance.armed)

        self._monitor.set_value(self.settings, '/Settings/SystemSetup/AcInput1', 2) # Available
        self._update_values()
        # Alarm armed because AC available
        self.assertTrue(GridAlarm.instance.armed)

        # Grid fails
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0xF0)

        # Alarm doesn't activate immediately
        self._update_values(interval=3000)
        self._check_values({'/Ac/Alarms/AcLost': 0})

        # Alarm activates after timeout
        self._update_values(interval=3000)
        self._check_values({'/Ac/Alarms/AcLost': 2})

        # Alarm resets if the grid come back
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0)
        self._update_values()
        self._check_values({'/Ac/Alarms/AcLost': 0})

    def test_grid_alarm_cancel(self):
        self._set_setting('/Settings/Alarm/System/AcLost', 1)
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0)
        self._update_values()
        self.assertTrue(GridAlarm.instance.armed) # Armed

        # Fail, no alarm
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0xF0)
        self._update_values(interval=3000)
        self._check_values({'/Ac/Alarms/AcLost': 0})

        # AC Return before the timeout
        self._monitor.set_value(self.vebus, '/Ac/ActiveIn/ActiveInput', 0)
        self._update_values(interval=3000)
        self._check_values({'/Ac/Alarms/AcLost': 0})
