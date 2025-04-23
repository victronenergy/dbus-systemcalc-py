from datetime import datetime, timedelta
import random
from gi.repository import GLib # type: ignore
from delegates.base import SystemCalcDelegate
from delegates.batterysoc import BatterySoc
from delegates.schedule import ScheduledWindow
from delegates.dvcc import Dvcc
from delegates.batterylife import BatteryLife
from delegates.batterylife import State as BatteryLifeState
from enum import Enum
from time import time
import json
import logging
import dbus
from typing import Dict
logger = logging.getLogger(__name__)

HUB4_SERVICE = "com.victronenergy.hub4"
S2_IFACE = "com.victronenergy.S2"

class Modes(int, Enum):
	Off = 0
	On = 1

class SystemType(int, Enum):
	Unknown = 0
	GridConnected1Phase = 1
	GridConnected2PhaseSaldating = 2
	GridConnected3PhaseSaldating = 3
	GridConnected2PhaseIndividual = 4
	GridConnected3PhaseIndividual = 5
	ZeroFeedin1Phase = 6
	ZeroFeedin2Phase = 7
	ZeroFeedin3Phase = 8
	OffGrid1Phase = 9
	OffGrid2Phase = 10
	OffGrid3Phase = 11

class S2RMDelegate():
	def __init__(self, service, instance, rmno):
		self.service = service
		self.instance = instance
		self.rmno = rmno

	@property
	def unique_identifier(self):
		return "{}_RM{}".format(self.service, self.rmno)

class HEMS(SystemCalcDelegate):
	control_priority = 0
	_get_time = datetime.now

	def __init__(self):
		super(HEMS, self).__init__()
		self.system_type = SystemType.Unknown
		self.managed_rms: Dict[str, S2RMDelegate] = {}

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(HEMS, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/HEMS/Active', value=0, gettextcallback=lambda p, v: Modes(v))
		self._dbusservice.add_path('/HEMS/BatteryReservation', value=0)
		self._dbusservice.add_path('/HEMS/BatteryReservationState', value=None)
		self._dbusservice.add_path('/HEMS/SystemType', value=0, gettextcallback=lambda p, v: SystemType(v))

		self.system_type = self._determineSystemType()

		if self.mode == 1:
			self._enable()
		else:
			self._disable()

	def get_settings(self):
		# Settings for HEMS
		path = '/Settings/HEMS'

		settings = [
			("hems_mode", path + "/Mode", 0, 0, 1),
			("hems_clinterval", path + "/ControlLoopInterval", 5, 1, 60),
			("hems_balancingthreshold", path + '/BalancingThreshold', 98, 2, 98),
			("hems_batteryreservation", path + '/BatteryReservationEquation', "15000.0 * (100.0-SOC)/100.0", "", "")
		]

		return settings

	def get_input(self):
		#TODO: Adjust for settings we need.
		return [
			('com.victronenergy.settings', [
				'/Settings/CGwacs/Hub4Mode']),
			('com.victronenergy.s2Mock', [])
		]

	def get_output(self):
		return []

	def _check_s2_rm(self, serviceName, objectPath):
		try:
			self._dbusmonitor.dbusConn.call_blocking(serviceName, objectPath, S2_IFACE, 'GetValue', '', [])
			return True
		except dbus.exceptions.DBusException as e:
			return False
		
	def device_added(self, service, instance, *args):
		logger.info("Device added: {}".format(service))
		i = 0
		while True:
			s2_rm_exists = self._check_s2_rm(service, "/Devices/{}/S2".format(i))

			if s2_rm_exists:
				delegate = S2RMDelegate(service, instance, i)
				self.managed_rms[delegate.unique_identifier] = delegate
				logger.info(" -> Identified S2 RM {} on {}. Added to managed RMs as {}".format(i, service, delegate.unique_identifier))
				i += 1
			else:
				break

	def device_removed(self, service, instance):
		logger.info("Device removed: {}".format(service))

		#check, if this service provided one or multiple rm, we have been controlling. 
		known_rms = list(self.managed_rms.keys()) 
		for key in known_rms:
			if key.startswith(service):
				logger.info(" -> Removing RM {} from managed RMs.".format(key))
				del self.managed_rms[key]

	def settings_changed(self, setting, oldvalue, newvalue):
		if setting == 'hems_mode':
			if oldvalue == 0 and newvalue == 1:
				self._enable()
			if oldvalue == 1 and newvalue == 0:
				self._disable()

	@property
	def mode(self):
		return self._settings['hems_mode']

	@property
	def control_loop_interval(self):
		return self._settings['hems_clinterval']

	@property
	def soc(self) -> float:
		"""
			current soc 0 - 100
		"""
		return BatterySoc.instance.soc
	
	@property
	def current_battery_reservation(self) -> float:
		"""
			returns the current desired battery reservation based on the user equation in watts.
			0 if error in equation. /HEMS/BatteryReservationState will indicate if there is an error with the equation
		"""
		try:
			reservation = round(eval(self._settings['hems_batteryreservation'].replace("SOC", str(self.soc))))
			self._dbusservice["/HEMS/BatteryReservationState"] = "OK"
		except Exception as ex:
			reservation = 0.0
			self._dbusservice["/HEMS/BatteryReservationState"] = "ERROR"
		
		self._dbusservice["/HEMS/BatteryReservation"] = reservation
		return reservation
	
	def _enable(self):
		self._timer = GLib.timeout_add(self.control_loop_interval * 1000, self._on_timer)
		self._dbusservice["/HEMS/Active"] = 1
		logger.info("HEMS activated with a control loop interval of {}s".format(self.control_loop_interval))

	def _disable(self):
		self._dbusservice["/HEMS/Active"] = 0
		logger.info("HEMS deactivated.")
		pass

	def _determineSystemType(self) -> SystemType:
		#TODO: Determine current system kind. Required to distribute energy properly based on the type of the system.
		system_type = SystemType.Unknown
		try:
			no_phases = self._dbusservice["/Ac/Grid/NumberOfPhases"]
			grid_parallel = self._dbusservice["/Ac/ActiveIn/GridParallel"]
			multiphase_mode = self._dbusmonitor.get_value('com.victronenergy.settings', '/Settings/CGwacs/Hub4Mode')

			if grid_parallel is not None and grid_parallel == 1:
				#grid connected
				if no_phases == 1:
					system_type = SystemType.GridConnected1Phase
				elif no_phases == 2:
					if multiphase_mode == 1:
						system_type = SystemType.GridConnected2PhaseSaldating
					else:
						system_type = SystemType.GridConnected2PhaseIndividual
				elif no_phases == 3:
					if multiphase_mode == 1:
						system_type = SystemType.GridConnected3PhaseSaldating
					else:
						system_type = SystemType.GridConnected3PhaseIndividual

			else:
				#offgrid
				#TODO: Determine number of phases Offgrid, can't use grid information here. 
				pass
		except Exception as ex:
			logger.warning("Unable to determine SystemType by now. Retrying later...")
			#may happen during startup, until all delegates have populated their initial values. 
			pass

		self._dbusservice["/HEMS/SystemType"] = system_type.value
		return system_type
	
	def _on_timer(self):
		# Control loop timer.
		now = self._get_time()
		self.system_type = self._determineSystemType()
		
		#TODO: Work work work
		logger.info("SOC / Reservation: {}% / {}W".format(self.soc, self.current_battery_reservation))

		if (self.mode == 1):
			return True	#keep timer up as long as mode is enabled.
	
		#terminate timer
		return False