#!/usr/bin/python -u
# -*- coding: utf-8 -*-

from delegates.base import SystemCalcDelegate

# All delegates
from delegates.hubtype import HubTypeSelect
from delegates.hub1bridge import Hub1Bridge
from delegates.servicemapper import ServiceMapper
from delegates.vebussocwriter import VebusSocWriter
from delegates.relaystate import RelayState
from delegates.buzzercontrol import BuzzerControl
from delegates.lgbattery import LgCircuitBreakerDetect
from delegates.systemstate import SystemState
from delegates.voltagesense import VoltageSense
