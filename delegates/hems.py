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
from ve_utils import wrap_dbus_value, unwrap_dbus_value

logger = logging.getLogger(__name__)

HUB4_SERVICE = "com.victronenergy.hub4"
S2_IFACE = "com.victronenergy.S2"
KEEP_ALIVE_INTERVAL = 20

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
	def __init__(self, monitor, service, instance, rmno):
		self.initialized = False
		self.service = service
		self.instance = instance
		self.rmno = rmno
		self.s2path = "/Devices/{}/S2".format(rmno)
		self._dbusmonitor = monitor
		self.keep_alive_missed = 0
		self._message_receiver=None
		self._disconnect_receiver=None

	@property
	def unique_identifier(self):
		return "{}_RM{}".format(self.service, self.rmno)
	
	def begin(self):
		"""
			Initializes the RM, establishes connection, handshake, etc. 
		"""
		if self._s2_connect():
			#Set KeepAlive Timer. 
			self._timer = GLib.timeout_add(KEEP_ALIVE_INTERVAL * 1000, self._keep_alive_loop)

		#RM is now ready to be managed.
		self.initialized = True
	
	def end(self):
		"""
			To be called when the RM leaves the dbus. 
		"""
		self.initialized=False

	def _keep_alive_loop(self):
		"""
			Sends the keepalive and monitors for success. 
		"""
		def reply_handler(result): 
			if result:
				self.keep_alive_missed = 0
			else:
				self.keep_alive_missed = self.keep_alive_missed + 1	
		
		def error_handler(result): 
			self.keep_alive_missed = self.keep_alive_missed + 1

		self._dbusmonitor.dbusConn.call_async(self.service, self.s2path, S2_IFACE, method='KeepAlive', signature='s',
										args=[wrap_dbus_value(self.unique_identifier)],
										reply_handler=reply_handler, error_handler=error_handler)
		
		if self.keep_alive_missed < 2:
			logger.info("Keepalive OK for {}".format(self.unique_identifier))
			return True
		else:
			#TODO: We missed keepalives. Handle s2-based timeouts. 
			logger.info("Keepalive MISSED for {} ({})".format(self.unique_identifier, self.keep_alive_missed))
			self.initialized=False
			return False

	def _s2_connect(self) -> bool:
		"""
			Establishes Connection to the RM via S2. 
		"""
		#start to monitor for Signals: Message and Disconnect. 
		self._message_receiver = self._dbusmonitor.dbusConn.add_signal_receiver(self._s2_on_message_handler,
			dbus_interface=S2_IFACE, signal_name='Message', path=self.s2path)

		self._disconnect_receiver = self._dbusmonitor.dbusConn.add_signal_receiver(self._s2_on_disconnect_handler,
			dbus_interface=S2_IFACE, signal_name='Disconnect', path=self.s2path)
		
		if self._dbusmonitor.dbusConn.call_blocking(self.service, self.s2path, S2_IFACE, method='Connect', signature='si', 
										   args=[wrap_dbus_value(self.unique_identifier), wrap_dbus_value(KEEP_ALIVE_INTERVAL)]):
			logger.info("S2-Connection to {} established with Keep-Alive {}".format(self.unique_identifier, KEEP_ALIVE_INTERVAL))

			return True
		else:
			logger.warning("S2-Connection to {} failed.".format(self.unique_identifier))
			return False

	def _s2_on_message_handler(self, client_id, message):
		if self.unique_identifier == client_id:
			logger.info("Received Message from {}: {}".format(self.unique_identifier, message))

	def _s2_on_disconnect_handler(self, client_id, reason):
		if self.unique_identifier == client_id:
			logger.info("Received Disconnect from {}: {}".format(self.unique_identifier, reason))
			  
	def _s2_send_keep_alive(self) -> bool:
		pass

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

		self.system_type = self._determine_system_type()

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
				delegate = S2RMDelegate(self._dbusmonitor, service, instance, i)
				self.managed_rms[delegate.unique_identifier] = delegate
				logger.info("Identified S2 RM {} on {}. Added to managed RMs as {}".format(i, service, delegate.unique_identifier))
				delegate.begin()
				i += 1 #probe next one.
			else:
				break

	def device_removed(self, service, instance):
		logger.info("Device removed: {}".format(service))

		#check, if this service provided one or multiple rm, we have been controlling. 
		known_rms = list(self.managed_rms.keys()) 
		for key in known_rms:
			if key.startswith(service):
				logger.info("Removing RM {} from managed RMs.".format(key))
				self.managed_rms[key].end()
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
		reservation = 0.0
		try:
			reservation = round(eval(self._settings['hems_batteryreservation'].replace("SOC", str(self.soc))))
			#TODO: Check BMS ChargeRate capability, reservation should not be greater than what the BMS is currently capable of, else we are wasting solar.
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

	def _determine_system_type(self) -> SystemType:
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
		self.system_type = self._determine_system_type()
		
		#TODO: Work work work
		logger.info("SOC / Reservation: {}% / {}W".format(self.soc, self.current_battery_reservation))

		if (self.mode == 1):
			return True	#keep timer up as long as mode is enabled.
	
		#terminate timer
		return False