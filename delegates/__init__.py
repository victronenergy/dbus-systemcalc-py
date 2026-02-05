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
from delegates.batterysoc import BatterySoc
from delegates.schedule import ScheduledCharging
from delegates.batterydata import BatteryData
from delegates.sourcetimers import SourceTimers
from delegates.batterysettings import BatterySettings
from delegates.gps import Gps
from delegates.acinput import AcInputs
from delegates.multi import Multi
from delegates.genset import GensetStartStop
from delegates.socsync import SocSync
from delegates.pvinverter import PvInverters
from delegates.batteryservice import BatteryService
from delegates.canbatterysense import CanBatterySense
from delegates.invertercharger import InverterCharger
from delegates.dynamicess import DynamicEss
from delegates.loadshedding import LoadShedding
from delegates.motordrive import MotorDrive
from delegates.motordriveconsumption import MotorDriveConsumption
