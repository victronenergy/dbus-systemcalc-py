#!/usr/bin/python -u
# -*- coding: utf-8 -*-

from delegates.base import SystemCalcDelegate

# All delegates
from delegates.hubtype import HubTypeSelect
from delegates.dvcc import Dvcc
from delegates.servicemapper import ServiceMapper
from delegates.vebussocwriter import VebusSocWriter
from delegates.relaystate import RelayState
from delegates.buzzercontrol import BuzzerControl
from delegates.lgbattery import LgCircuitBreakerDetect
from delegates.systemstate import SystemState
from delegates.batterysense import BatterySense
from delegates.batterylife import BatteryLife
from delegates.schedule import ScheduledCharging
from delegates.batterydata import BatteryData
from delegates.sourcetimers import SourceTimers
from delegates.bydbattery import BydCurrentSense
from delegates.batterysettings import BatterySettings
from delegates.gps import Gps
