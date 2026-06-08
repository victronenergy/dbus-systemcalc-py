from datetime import datetime, date, time, timedelta
import unittest

# This adapts sys.path to include all relevant packages
import context

# Testing tools
from delegates.batterysoc import BatterySoc
import logger
from mock_gobject import timer_manager

# our own packages
import dbus_systemcalc
from delegates import DynamicEss
from delegates.dynamicess import Flags, Restrictions
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches
import logging

# Time travel patch
DynamicEss._get_time = lambda *a: timer_manager.datetime

class TestDynamicEss(TestSystemCalcBase):
	vebus = 'com.victronenergy.vebus.ttyO1'
	settings_service = 'com.victronenergy.settings'
	rs_service = 'com.victronenergy.acsystem.desstest1'

	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		#FIXME: implement
		pass

	def tearDown(self):
		#FIXME: implement
		pass

	def test_aquire_chargecontrol(self):
		#FIXME: implement
		pass

	def test_value_relaying(self):
		#FIXME: implement
		pass

if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s,%(msecs)d %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.DEBUG,
        handlers=[
            logging.StreamHandler()
        ])
    unittest.main()
