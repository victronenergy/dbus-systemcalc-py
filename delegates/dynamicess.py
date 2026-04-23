from datetime import datetime, timedelta, timezone
import json
import uuid
from gi.repository import GLib # type: ignore
from delegates.base import SystemCalcDelegate
from delegates.batterysoc import BatterySoc
from delegates.schedule import ScheduledWindow
from delegates.dvcc import Dvcc
from delegates.batterylife import BatteryLife
from delegates.batterylife import State as BatteryLifeState
from delegates.chargecontrol import ChargeControl
from typing import Dict, cast, Callable
from enum import Enum, IntFlag
from time import time
import math
import logging
from ve_utils import wrap_dbus_value, unwrap_dbus_value

from s2python.common import (
    ReceptionStatusValues,
    ReceptionStatus,
	ResourceManagerDetails,
    Handshake,
    EnergyManagementRole,
    HandshakeResponse,
    SelectControlType,
	ControlType,
	CommodityQuantity,
	PowerMeasurement,
	PowerValue,
	PowerRange,
	Timer
)

from s2python.ombc import (
    OMBCInstruction,
    OMBCOperationMode,
    OMBCStatus,
    OMBCSystemDescription,
    OMBCTimerStatus
)

from s2python.s2_parser import S2Parser
from s2python.version import S2_VERSION
from s2python.s2_control_type import S2ControlType, PEBCControlType, NoControlControlType
from s2python.validate_values_mixin import S2MessageComponent

logger = logging.getLogger(__name__)

NUM_SCHEDULES = 48
INTERVAL = 5
HUB4_SERVICE = 'com.victronenergy.hub4'
S2_IFACE = "com.victronenergy.S2"
S2_KEEP_ALIVE_INTERVAL_S = 10
S2_CONNECTION_RETRY_INTERVAL_MS = 30000
ERROR_TIMEOUT = 60
MAX_FEEDIN_VALUE = 96000
TRANSITION_STATE_THRESHOLD = 90.0

MODES = {
       0: 'Off',
       1: 'Auto',
       2: 'Buy',
       3: 'Sell',
       4: 'Local'
}

ERRORS = {
	0: 'No error',
	1: 'No ESS',
	2: 'ESS mode',
	3: 'No matching schedule',
	4: 'SOC low',
	5: 'Battery capacity unset'
}

class Strategy(int, Enum):
	TARGETSOC = 0		#ME-Coping: grid / grid
	SELFCONSUME = 1     #ME-Coping: bat  / bat
	PROBATTERY = 2      #ME-Coping: grid / bat
	PROGRID = 3         #ME-Coping: bat  / grid

class OperatingMode(int, Enum):
	UNKNOWN = -1
	TRADEMODE = 0
	GREENMODE = 1

class Flags(IntFlag):
	NONE = 0
	FASTCHARGE = 1
	DISABLEPV = 2

class Restrictions(IntFlag):
	NONE = 0
	BAT2GRID = 1
	GRID2BAT = 2

class EvcsGxFlags(IntFlag):
	NONE = 0
	GX_AUTO_AQUIRED = 1
	CONTROLLABLE = 2
	SCHEDULED = 4
	CHARGING = 8
	EMERGENCY_COUNTDOWN = 16
	EMERGENCY_ACTIVE = 32
	CHARGE_NOW_ACTIVE = 64
	EVCS_CONTROL_DISABLED=128

	def stringify(self):
		"""Returns a string representation of set flags, e.g., 'SCHEDULED | EMERGENCY_ACTIVE'"""
		if self.value == 0:
			return "NONE"

		flags = []
		for flag in EvcsGxFlags:
			if flag.value != 0 and (self & flag):
				flags.append(flag.name)

		return " | ".join(flags) if flags else "NONE"

class EvcsVrmFlags(IntFlag):
	NONE = 0
	CHARGE_NOW = 1

class ChangeIndicator(int, Enum):
	NONE = 0
	RISING = 1
	FALLING = 2
	BECAME_TRUE = 3
	BECAME_FALSE = 4
	CHANGED = 5

class EVCSDelegate():
	def __init__(self, service_name, instance, monitor, delegate):
		self.service_name = service_name
		self.s2rmpath = "/S2/0/Rm"
		self.instance = instance
		self.current_setpoint = 0
		self.is_started = False
		self.no_phases = None
		self._dbusmonitor = monitor
		self.emergency_charge = False
		self.s2_parser = S2Parser()
		self._keep_alive_missed = 0
		self.emergency_charge_timer = None
		self.gx_flags:EvcsGxFlags = EvcsGxFlags.NONE
		self.rm_details:ResourceManagerDetails = None
		self._dess_delegate = delegate
		self.service = None

		#we need to find the actual service object from _dbusmonitor.servicesById.
		#it is a disct with the id as key and a service object as value.
		for owner_id, service in self._dbusmonitor.servicesById.items():
			if service.name == self.service_name and service.deviceInstance == self.instance:
				self.service = service
				logger.info("Identified service as {}:{} for {}#{}".format(owner_id, service, self.service_name, self.instance))
				break

		#Generic Handler
		self._message_receiver=None
		self._disconnect_receiver=None
		self._keep_alive_timer=None
		self._retry_timer=None
		self._reply_handler_dict:Dict[uuid.UUID, Callable[[ReceptionStatus], None]]={} #TODO Needs handling, when replies are never received?

		#Generic value holder
		self.rm_details=None
		self.active_control_type:ControlType=None

		#OMBC related stuff.
		self.ombc_system_description = None
		self.ombc_active_instruction = None
		self.ombc_active_operation_mode = None

	@property
	def unique_identifier(self) -> str:
		return "EVCS#{}".format(self.instance)

	@property
	def status(self) -> int:
		return self._dbusmonitor.get_value(self.service_name, "/Status")

	@property
	def mode(self) -> int:
		return self._dbusmonitor.get_value(self.service_name, "/Mode")

	def add_flag(self, flag:EvcsGxFlags):
		'''
			Adds the given flag to the current flag collection of this EVCS.
		'''
		self.gx_flags |= flag

	def remove_flag(self, flag:EvcsGxFlags):
		'''
			Removes the given flag from the current flag collection of this EVCS.
		'''
		self.gx_flags &= ~flag

	def begin(self):
		'''
			Establish the S2Connection with the EVCS and starts heartbeat monitoring.
		'''

		#start to monitor for Signals: Message and Disconnect. Yes, we need to do this, before connection
		#is successfull, else we have a race-condition on catching the first reply, if any.
		if self._message_receiver is None:
			self._message_receiver = self._dbusmonitor.dbusConn.add_signal_receiver(self._s2_on_message_handler,
				dbus_interface=S2_IFACE, signal_name='Message', path=self.s2rmpath, sender_keyword='sender_id')

		if self._disconnect_receiver is None:
			self._disconnect_receiver = self._dbusmonitor.dbusConn.add_signal_receiver(self._s2_on_disconnect_handler,
				dbus_interface=S2_IFACE, signal_name='Disconnect', path=self.s2rmpath, sender_keyword='sender_id')

		#Connect to the EVCS only, if DESS is active.
		if self._dess_delegate.active:
			self._dbusmonitor.dbusConn.call_async(self.service_name, self.s2rmpath, S2_IFACE, method='Connect', signature='si',
				args=[wrap_dbus_value(self._dess_delegate.s2_cem_name), wrap_dbus_value(S2_KEEP_ALIVE_INTERVAL_S)],
				reply_handler=self._s2_connect_callback_ok, error_handler=self._s2_connect_callback_error)

		#establish a retry timer, if not already present.
		if self._retry_timer is None:
			self._retry_timer = GLib.timeout_add(S2_CONNECTION_RETRY_INTERVAL_MS, self._on_timer_retry_connection)

	def end(self, send_disconnect=True):
		'''
			Disconnectes the S2RM (if connected) and resets state tracking for this EVCS instance.
			Should be called upon on intended disconnects and when the EVCS service is detected gone.
		'''
		if send_disconnect:
			self._s2_send_disconnect()

		if self._message_receiver is not None:
			self._dbusmonitor.dbusConn.remove_signal_receiver(self._s2_on_message_handler, path=self.s2rmpath,
													 signal_name="Message", dbus_interface=S2_IFACE)
			self._message_receiver = None

		if self._disconnect_receiver is not None:
			self._dbusmonitor.dbusConn.remove_signal_receiver(self._s2_on_disconnect_handler, path=self.s2rmpath,
													 signal_name="Disconnect", dbus_interface=S2_IFACE)
			self._disconnect_receiver = None

		if self._keep_alive_timer is not None:
			GLib.source_remove(self._keep_alive_timer)
			self._keep_alive_timer = None

		self.gx_flags = EvcsGxFlags.NONE
		logger.info("{} | RMDelegate is now uninitialized.".format(self.unique_identifier))

	def loop(self, window:ScheduledWindow, now:datetime, dess_delegate:'DynamicEss'):
		'''
			Should be called every loop to maintain the state of this EVCS and react on changes.
		'''
		#validate the evcs is set to auto, else drop the s2 connection, if established
		if self.mode != 1:
			if self.gx_flags & EvcsGxFlags.GX_AUTO_AQUIRED:
				logger.info("{} | EVCS #{} is no longer in Auto mode. Dropping S2 Connection.".format(self.unique_identifier, self.instance))
				self.end()
			return

		#check if control was explicit denied.
		if self._dess_delegate._settings['dess_evcscontroldisabled'] == 1:
			if not self.gx_flags & EvcsGxFlags.EVCS_CONTROL_DISABLED:
				logger.info("{} | EVCS Control is explicit disabled via setting. Dropping S2 Connection and marking as control disabled.".format(self.unique_identifier))
				self.end()
			self.gx_flags = EvcsGxFlags.EVCS_CONTROL_DISABLED #mark as control disabled.
		else:
			#just gently remove the Control Disabled flag, this will re-initiate connection if possible.
			self.remove_flag(EvcsGxFlags.EVCS_CONTROL_DISABLED)

		#check if this evcs is controllable.
		if EvcsGxFlags.CONTROLLABLE not in self.gx_flags:
			return;

		#Are we charging? See if we can identify the phase count.
		l1 = self._dbusmonitor.get_value(self.service_name, "/Ac/L1/Power") or 0
		l3 = self._dbusmonitor.get_value(self.service_name, "/Ac/L3/Power") or 0

		if l3 > 0 and l1 > 0 and self.no_phases != 3:
			self.no_phases = 3
			logger.info("{} | Detected EVCS as 3 phased.".format(self.unique_identifier))
		elif l1 > 0 and l3 == 0 and self.no_phases != 1:
			self.no_phases = 1
			logger.info("{} | Detected EVCS as 1 phased.".format(self.unique_identifier))

		#Do we have a schedule and need to react?
		if str(self.instance) in window.to_ev.keys():
			#yes, we are at least scheduled now!
			self.add_flag(EvcsGxFlags.SCHEDULED)

			desired_charge_rate = window.to_ev[str(self.instance)] * 4000 #convert kWh/15min to W
			avg_ac = dess_delegate.average_ac_voltage
			desired_current_setpoint = max(6, round((desired_charge_rate) / avg_ac / (self.no_phases or 1), 0))

			if desired_charge_rate == 0:
				#stop regular charging?
				if self.gx_flags & EvcsGxFlags.CHARGING and not self.gx_flags & EvcsGxFlags.EMERGENCY_ACTIVE:
					self.remove_flag(EvcsGxFlags.CHARGING)
					self.current_setpoint = 0
					logger.info("{} | Stopping Charging due to 0 instruction.".format(self.unique_identifier))

				#stop active emergency charging?
				if self.gx_flags & EvcsGxFlags.CHARGING and self.gx_flags & EvcsGxFlags.EMERGENCY_ACTIVE:
					self.remove_flag(EvcsGxFlags.EMERGENCY_ACTIVE)
					self.remove_flag(EvcsGxFlags.CHARGING)
					self.current_setpoint = 0
					logger.info("{} | Stopping Emergency Charging due to 0 instruction.".format(self.unique_identifier))
			else:
				#chargevolume > 0, pass over the setpoint so S2-Control can adjust.
				#if we are not yet charging, log and change status.
				if not self.gx_flags & EvcsGxFlags.CHARGING:
					self.add_flag(EvcsGxFlags.CHARGING)
					logger.info("{} | Starting to charge with {}W according to schedule.".format(self.unique_identifier, desired_charge_rate))
					self.current_setpoint = desired_current_setpoint

				#if we know number of phases, we can hop on directly.
				if self.no_phases is not None:
					#we may directly switch from emergency to charging.
					if self.gx_flags & EvcsGxFlags.EMERGENCY_ACTIVE:
						self.remove_flag(EvcsGxFlags.EMERGENCY_ACTIVE)
						self.current_setpoint = desired_current_setpoint
						logger.info("{} | Switching from Emergency Charging to regular charging with {}W due to valid instruction.".format(self.unique_identifier, desired_charge_rate))

					#do we need to adjust?
					if self.current_setpoint != desired_current_setpoint:
						self.current_setpoint = desired_current_setpoint
						logger.info("{} | Adjusting setpoint to {}W according to schedule.".format(self.unique_identifier, desired_charge_rate))

				else:
					#Phases unknown, we start with 6A and look if we can identify it.
					self.add_flag(EvcsGxFlags.CHARGING)
					self.current_setpoint = 6
					logger.info("{} | Starting to charge with 6A to probe phase count.".format(self.unique_identifier))

		#See, if we have to start an emergency countdown
		#This is the case if we are not charging, scheduled or already in countdown.
		if self.gx_flags & (EvcsGxFlags.SCHEDULED | EvcsGxFlags.CHARGING | EvcsGxFlags.EMERGENCY_COUNTDOWN) == 0:
			self.emergency_charge_timer = now
			logger.info("{} | Starting emergency charge timer ({}s).".format(self.unique_identifier, dess_delegate._settings['dess_evemergencystart']))
			self.add_flag(EvcsGxFlags.EMERGENCY_COUNTDOWN)

		#Are we in an emergency countdown and the timer has expired?
		if self.gx_flags & EvcsGxFlags.EMERGENCY_COUNTDOWN:
			elapsed = (now - self.emergency_charge_timer).total_seconds()
			if elapsed >= dess_delegate._settings['dess_evemergencystart']:
				self.remove_flag(EvcsGxFlags.EMERGENCY_COUNTDOWN)
				self.add_flag(EvcsGxFlags.EMERGENCY_ACTIVE)
				self.add_flag(EvcsGxFlags.CHARGING)
				self.current_setpoint = dess_delegate._settings['dess_evemergencycurrent']
				logger.info("{} | Starting emergency charge after {}s.".format(self.unique_identifier, dess_delegate._settings['dess_evemergencystart']))

		#finally, this EVCS eventually needs to approach a setpoint?
		self._approach_setpoint(dess_delegate)

	def _approach_setpoint(self, dess_delegate:'DynamicEss'):
		"""
			Makes the state machine traverse the S2 Control Model until a suitable state
			is found.
		"""
		if self.ombc_active_operation_mode is None or self.ombc_system_description is None:
			return

		#get all transitions (and their connected states) we have as an option from where we are.
		eligible_operationmodes:dict[str, OMBCOperationMode] = {}
		for transition in self.ombc_system_description.transitions:
			if transition.from_ == self.ombc_active_operation_mode.id:
				#we have a transition from where we are. Check, if this is the one we need to approach our setpoint.
				eligible_operationmodes[transition.to] = None

		#find the operation modes.
		for op_mode_id, _ in eligible_operationmodes.items():
			for op_mode in self.ombc_system_description.operation_modes:
				if op_mode.id == op_mode_id:
					eligible_operationmodes[op_mode_id] = op_mode
					break

		#Now, we should have all the op-modes. current is valid as well.
		eligible_operationmodes[self.ombc_active_operation_mode.id] = self.ombc_active_operation_mode

		#check which mode matches best.
		next_mode = None
		next_mode_delta = 99999
		power_setpoint = dess_delegate.average_ac_voltage * self.current_setpoint * (self.no_phases or 1)
		for op_mode_id, op_mode in eligible_operationmodes.items():
			if op_mode is not None:
				p_total = sum([p.end_of_range for p in op_mode.power_ranges])
				delta = abs(p_total - power_setpoint)
				if delta < next_mode_delta:
					#if we don't know the phase count, we may need to prevent Standby beeing selected.
					if self.no_phases is None and p_total == 0 and self.current_setpoint > 0:
						logger.info("{} | Phase count unknown, surpressing standby mode selection.".format(self.unique_identifier))
						continue

					next_mode_delta = delta
					next_mode = op_mode

		if next_mode is None:
			logger.warning("{} | Unable to find a operation-mode close to {}A / {}W".format(self.unique_identifier, self.current_setpoint, power_setpoint))
		else:
			if next_mode.id != self.ombc_active_operation_mode.id:
				logger.info("{} | Moving to operation mode: {} ({}A / {}W)".format(self.unique_identifier, next_mode.diagnostic_label, self.current_setpoint, power_setpoint))

				#no handler needed, we deal with ombc status confirmations instead.
				self._s2_send_message(
					OMBCInstruction(
						message_id = uuid.uuid4(),
						id = uuid.uuid4(),
						execution_time = datetime.now(timezone.utc),
						operation_mode_id = next_mode.id,
						operation_mode_factor = 1.0,
						abnormal_condition=False
					)
				)

	def _on_timer_retry_connection(self):
		'''
			Retries connection to the evcs, if DESS is active.
		'''
		try:
			if self._dess_delegate.active:
				if not EvcsGxFlags.GX_AUTO_AQUIRED in self.gx_flags and self.mode == 1 and not EvcsGxFlags.EVCS_CONTROL_DISABLED in self.gx_flags:
					logger.info("{} | Retrying connection".format(self.unique_identifier))
					self.begin()
		except Exception as ex:
			logger.error("Exception while retrying connection. Skipping attempt.", exc_info=ex)

		return True

	def _s2_send_disconnect(self):
		"""
			Sends a disconnect message to the RM. Will use fire and forget, as we don't
			care about if the message is receiving the rm, nor what he has to say about it.
		"""
		try:
			logger.warning("{} | Sending disconnect.".format(self.unique_identifier))
			self._dbusmonitor.dbusConn.call_async(self.service_name, self.s2rmpath, S2_IFACE, method='Disconnect', signature='s',
					args=[wrap_dbus_value(self._dess_delegate.s2_cem_name)],
					reply_handler=None, error_handler=None)
		except Exception as ex:
			logger.error("{} | Error sending a S2 Message.".format(self.unique_identifier), exc_info=ex)

	def _s2_on_disconnect_handler(self, client_id, reason, sender_id:str):
		if sender_id == self.service.id and client_id == self._dess_delegate.s2_cem_name:
			logger.info("{} | Received Disconnect: {}".format(self.unique_identifier, reason))
			self.end()

	def _s2_send_reception_message(self, rsv:ReceptionStatusValues, src:S2MessageComponent, info:str=None):
		if isinstance(src, S2MessageComponent):
			message_id = str(src.to_dict()["message_id"])
		else:
			message_id = src

		resp = ReceptionStatus(
			status=rsv,
			subject_message_id = message_id,
			diagnostic_label=info
		)
		self._s2_send_message(resp)

	def _s2_send_message(self, message:S2MessageComponent, reply_handler: Callable[[ReceptionStatus], None] = None):
		'''
			Sends a s2 message. If a reply_handler is passed, this method will track for the response arriving
			and invoke the handler with the ReceptionStatus object as parameter.
		'''
		if reply_handler is not None:
			self._reply_handler_dict[message.model_dump()["message_id"]] = reply_handler

		try:
			self._dbusmonitor.dbusConn.call_async(self.service_name, self.s2rmpath, S2_IFACE, method='Message', signature='ss',
					args=[wrap_dbus_value(self._dess_delegate.s2_cem_name), wrap_dbus_value(message.to_json())],
					reply_handler=None, error_handler=None)
		except Exception as ex:
			logger.error("{} | Error sending a S2 Message.".format(self.unique_identifier), exc_info=ex)
			logger.error("{} | Message was: {}".format(self.unique_identifier, message.model_dump()))
			del self._reply_handler_dict[message.model_dump()["message_id"]]

	def _s2_on_handhsake_message(self, message:Handshake):
		#RM wants to handshake. Do that :)
		if S2_VERSION in message.supported_protocol_versions:
			self._s2_send_reception_message(ReceptionStatusValues.OK, message)
			#Supported Version, Accept.
			resp = HandshakeResponse(
				message_id=uuid.uuid4(),
				selected_protocol_version=S2_VERSION
			)

			self._s2_send_message(resp)
		else:
			logger.warning("{} | Outdated version: {}; expected: {}".format(self.unique_identifier, message.supported_protocol_versions, S2_VERSION))
			#wrong version. Reject.
			self._s2_send_reception_message(ReceptionStatusValues.INVALID_CONTENT, message)

	def _s2_connect_callback_ok(self, result):
		logger.info("{} | S2-Connection established with Keep-Alive {}".format(self.unique_identifier, S2_KEEP_ALIVE_INTERVAL_S))

		#Set KeepAlive Timer.
		self._keep_alive_timer = GLib.timeout_add(S2_KEEP_ALIVE_INTERVAL_S * 1000, self._keep_alive_loop)

		#RM is now ready to be managed.
		self.add_flag(EvcsGxFlags.GX_AUTO_AQUIRED)

	def _s2_connect_callback_error(self, result):
		logger.warning("{} | S2-Connection failed. Operation will be retried in {}s: {}".format(self.unique_identifier, S2_CONNECTION_RETRY_INTERVAL_MS, result))
		self.end(False) #clean handlers and stuff.

	def _keep_alive_loop(self):
		"""
			Sends the keepalive and monitors for success.
		"""
		def reply_handler(result):
			result = unwrap_dbus_value(result)
			if result:
				self._keep_alive_missed = 0
			else:
				self._keep_alive_missed = self._keep_alive_missed + 1

		def error_handler(result):
			self._keep_alive_missed = self._keep_alive_missed + 1

		self._dbusmonitor.dbusConn.call_async(self.service_name, self.s2rmpath, S2_IFACE, method='KeepAlive', signature='s',
										args=[wrap_dbus_value(self._dess_delegate.s2_cem_name)],
										reply_handler=reply_handler, error_handler=error_handler)

		if self._keep_alive_missed < 2:
			return True
		else:
			logger.warning("{} | Keepalive MISSED ({})".format(self.unique_identifier, self._keep_alive_missed))
			self.end()
			return False

	def _s2_on_rm_details(self, message:ResourceManagerDetails):
		# Detail update. Store to keep information present.
		self.rm_details = message

		if len(message.available_control_types) == 0:
			self._s2_send_reception_message(ReceptionStatusValues.TEMPORARY_ERROR, message,"No ControlType provided.")
			self.remove_flag(EvcsGxFlags.CONTROLLABLE) #make sure, that we are not marked as controllable, if no control type is offered.
			return

		self._s2_send_reception_message(ReceptionStatusValues.OK, message)

		if len(message.available_control_types) == 1 and ControlType.NOT_CONTROLABLE in message.available_control_types:
			def noctrl_reply_handler(reply:ReceptionStatus):
				if reply.status == ReceptionStatusValues.OK:
					self.active_control_type = ControlType.NOT_CONTROLABLE
					self.no_phases = None #reset
					self.gx_flags = EvcsGxFlags.GX_AUTO_AQUIRED #reset all flags, as we are not controllable. This is a safe way to ensure, that we don't have any leftovers from previous control sessions, that might cause issues.

			logger.warning("{} | Only offered NOCTRL, accepting.".format(self.unique_identifier))

			self._s2_send_message(
				SelectControlType(
					message_id=uuid.uuid4(),
					control_type=ControlType.NOT_CONTROLABLE
				),noctrl_reply_handler
			)

		else:
			#Check if OMBC is available, that is our prefered mode as of now.
			def ombc_reply_handler(reply:ReceptionStatus):
				if reply.status == ReceptionStatusValues.OK:
					self.active_control_type = ControlType.OPERATION_MODE_BASED_CONTROL
					self.add_flag(EvcsGxFlags.CONTROLLABLE) #mark controllable, as we support a compatible control type, that is not NOCTRL.

			logger.info("{} | Offered OMBC, accepting.".format(self.unique_identifier))

			if ControlType.OPERATION_MODE_BASED_CONTROL in message.available_control_types:
				self._s2_send_message(
					SelectControlType(
						message_id=uuid.uuid4(),
						control_type=ControlType.OPERATION_MODE_BASED_CONTROL
					), ombc_reply_handler
				)

			else:
				logger.error("{} | Offered no compatible ControlType. Rejecting request.".format(self.unique_identifier))
				self._s2_send_reception_message(ReceptionStatusValues.PERMANENT_ERROR, "No supported ControlType offered.")
				self.gx_flags = EvcsGxFlags.NONE #make sure, that we are not marked as controllable, if no compatible control type is offered.
				self.end()

	def _s2_on_ombc_system_description(self, message:OMBCSystemDescription):
		#sort opmodes based on their powerranges. most expensive topmost.
		logger.info("{} | New system description received. Reseting state tracking.".format(self.unique_identifier))
		def sum_key(i:OMBCOperationMode):
			sum = 0
			for r in i.power_ranges:
				sum += r.end_of_range
			return sum

		message.operation_modes.sort(key=sum_key, reverse=True)
		self.ombc_system_description = message
		#reset active state, so transitioning doesn't cause issues. There might be no transition between different system descriptions.
		self.ombc_active_instruction = None
		self.ombc_active_operation_mode = None
		self._s2_send_reception_message(ReceptionStatusValues.OK, message)

	def _s2_on_ombc_status(self, message:OMBCStatus):
		try:
			for opm in self.ombc_system_description.operation_modes:
				#FIXME: Theres an error with message.active_operation_mode_id in s2-pyhton. fix this, once it was fixed.
				#       Until then, compare root with id.
				if "{}".format(opm.id) == "{}".format(message.active_operation_mode_id.root):
					self.ombc_active_operation_mode = opm
					logger.info(f"Reported Operation Mode: {opm.diagnostic_label}")
					self._s2_send_reception_message(ReceptionStatusValues.OK, message)
					return

			#Operationmode is not known. This may be a temporary error.
			logger.error("Unknown operationmode-id reported: {}, expecting any of: {}".format(
				message.active_operation_mode_id,
				["{}=>{}".format(mode.id, mode.diagnostic_label) for mode in self.ombc_system_description.operation_modes]
			))
			self._s2_send_reception_message(ReceptionStatusValues.TEMPORARY_ERROR, message, "Unknown operationmode-id: {}".format(message.active_operation_mode_id))
		except Exception as ex:
			logger.error("Exception during status reception. This may be temporary", exc_info=ex)

	def _s2_on_power_measurement(self, message:PowerMeasurement):
		#we don't care
		self._s2_send_reception_message(ReceptionStatusValues.OK, message)

	def _s2_on_message_handler(self, client_id:str, msg:str, sender_id:str):
		"""
			Handle incoming S2 Messages from this delegate.
		"""
		if sender_id == self.service.id and client_id == self._dess_delegate.s2_cem_name:
			jmsg = json.loads(msg)

			if "message_type" in jmsg:
				#if client is not initialized, deny all messages, except Handshake.
				if jmsg["message_type"] == "Handshake" or (EvcsGxFlags.GX_AUTO_AQUIRED in self.gx_flags):
					if jmsg["message_type"] == "Handshake":
						self._s2_on_handhsake_message(self.s2_parser.parse_as_message(msg, Handshake))
					elif jmsg["message_type"] == "ResourceManagerDetails":
						self._s2_on_rm_details(self.s2_parser.parse_as_message(msg, ResourceManagerDetails))
					elif jmsg["message_type"] == "OMBC.SystemDescription":
						self._s2_on_ombc_system_description(self.s2_parser.parse_as_message(msg, OMBCSystemDescription))
					elif jmsg["message_type"] == "OMBC.Status":
						self._s2_on_ombc_status(self.s2_parser.parse_as_message(msg, OMBCStatus))
					elif jmsg["message_type"] == "PowerMeasurement":
						self._s2_on_power_measurement(self.s2_parser.parse_as_message(msg, PowerMeasurement))
					elif jmsg["message_type"] == "ReceptionStatus":
						p = self.s2_parser.parse_as_message(msg, ReceptionStatus)
						if p.subject_message_id in self._reply_handler_dict:
							self._reply_handler_dict[p.subject_message_id](p)
							del self._reply_handler_dict[p.subject_message_id]
					else:
						#Not yet implemented!
						logger.warning("{} | Received an unknown Message: {} ".format(self.unique_identifier, jmsg["message_type"]))
						self._s2_send_reception_message(ReceptionStatusValues.PERMANENT_ERROR, jmsg["message_id"], "MessageType not yet implemented in EMS.")
				else:
					#Received another message than Handshake without beeing connected. Reject.
					logger.warning("{} | Received a Message: {} while RM is not actively connected".format(self.unique_identifier, jmsg["message_type"]))

					if jmsg["message_type"] != "ReceptionStatus":
						self._s2_send_reception_message(ReceptionStatusValues.TEMPORARY_ERROR, jmsg["message_id"], "Connection not yet established.")

class ReactiveStrategy(int, Enum):
	#do not re-number, external applications rely on this mapping.
	SCHEDULED_SELFCONSUME = 1
	SCHEDULED_CHARGE_ALLOW_GRID = 2
	SCHEDULED_CHARGE_ENHANCED = 3
	SELFCONSUME_ACCEPT_CHARGE = 4
	IDLE_SCHEDULED_FEEDIN = 5
	SCHEDULED_DISCHARGE = 6
	SELFCONSUME_ACCEPT_DISCHARGE = 7
	IDLE_MAINTAIN_SURPLUS = 8
	IDLE_MAINTAIN_TARGETSOC = 9
	SCHEDULED_CHARGE_SMOOTH_TRANSITION = 10
	SCHEDULED_CHARGE_FEEDIN = 11
	SCHEDULED_CHARGE_NO_GRID = 12
	SCHEDULED_MINIMUM_DISCHARGE = 13
	SELFCONSUME_NO_GRID = 14
	IDLE_NO_OPPORTUNITY = 15
	UNSCHEDULED_CHARGE_CATCHUP_TARGETSOC = 16
	SELFCONSUME_INCREASED_DISCHARGE = 17
	KEEP_BATTERY_CHARGED = 18
	SCHEDULED_DISCHARGE_SMOOTH_TRANSITION = 19
	SELFCONSUME_ACCEPT_BELOW_TSOC = 20
	IDLE_NO_DISCHARGE_OPPORTUNITY = 21
	CONTROLLED_DISCHARGE_EVCS = 22

	SELFCONSUME_INVALID_TARGETSOC = 91
	DESS_DISABLED = 92
	SELFCONSUME_UNEXPECTED_EXCEPTION = 93
	SELFCONSUME_FAULTY_CHARGERATE = 94
	UNKNOWN_OPERATING_MODE = 95
	ESS_LOW_SOC = 96
	SELFCONSUME_UNMAPPED_STATE = 97
	SELFCONSUME_UNPREDICTED = 98
	NO_WINDOW = 99

class IterationChangeTracker(object):
	'''
		The iteration change tracker analyzes changes occuring between iterations, if the actual strategy may depend on the triggering factor.
	'''
	def __init__(self, delegate):
		self._current_soc = None
		self._current_target_soc = None
		self._current_nw_tsoc_higher = None
		self._current_nw_tsoc_lower = None
		self._delegate = delegate

		self._previous_reactive_strategy = None
		self._previous_soc = None
		self._previous_target_soc = None
		self._previous_nw_tsoc_higher = None
		self._previous_nw_tsoc_lower = None

	def _check_soc_precision(self, soc):
		"""
			Determines the soc precision of the current soc value.
		"""
		p = 0
		x = round(soc, 2)
		for _ in range(2):
			p += 1
			x *= 10
			if x % 10 < 1e-2:
				return p - 1
		return 2

	def input(self, soc, soc_raw, target_soc, nw_tsoc_higher, nw_tsoc_lower):
		self._current_soc = soc
		self._current_target_soc = target_soc
		self._current_nw_tsoc_higher = nw_tsoc_higher
		self._current_nw_tsoc_lower = nw_tsoc_lower

		#determine if soc precision is higher than currently used. Round to 8 to avoid
		#issues like 1.1 would become 1.1000000000000001 and therefore an unreal precision.
		if self._delegate.soc_precision < 2:
			prec = self._check_soc_precision(soc_raw)
			if (prec > self._delegate.soc_precision):
				self._delegate.soc_precision = min(prec,2)

		#log changes as well.
		tme = datetime.today().strftime('%H:%M:%S')
		if self.soc_change() != ChangeIndicator.NONE:
			logger.log(logging.DEBUG, "detected soc change from {} to {}, identified as: {}".format(
				self._previous_soc if self._previous_soc is not None else "None",
				self._current_soc,
				self.soc_change().name
			))

		if self.target_soc_change() != ChangeIndicator.NONE:
			logger.log(logging.DEBUG, "detected target soc change from {} to {}, identified as: {}".format(
				self._previous_target_soc if self._previous_target_soc is not None else "None",
				self._current_target_soc if self._current_target_soc is not None else "None",
				self.target_soc_change().name
			))

		if self.nw_tsoc_higher_change() != ChangeIndicator.NONE:
			logger.log(logging.DEBUG, "detected nw higher tsoc change from {} to {}, identified as: {}".format(
				self._previous_nw_tsoc_higher if self._previous_nw_tsoc_higher is not None else "None",
				self._current_nw_tsoc_higher,
				self.nw_tsoc_higher_change().name
			))

		if self.nw_tsoc_lower_change() != ChangeIndicator.NONE:
			logger.log(logging.DEBUG, "detected nw lower tsoc change from {} to {}, identified as: {}".format(
				self._previous_nw_tsoc_lower if self._previous_nw_tsoc_lower is not None else "None",
				self._current_nw_tsoc_lower,
				self.nw_tsoc_lower_change().name
			))

	def soc_change(self) -> ChangeIndicator:
		if self._current_soc is None or self._current_soc == self._previous_soc:
			return ChangeIndicator.NONE
		if self._previous_soc is None or self._current_soc > self._previous_soc:
			return ChangeIndicator.RISING
		elif self._current_soc < self._previous_soc:
			return ChangeIndicator.FALLING

	def target_soc_change(self) -> ChangeIndicator:
		#handle None as 0 for indication
		ps = self._previous_target_soc or 0
		cs = self._current_target_soc or 0

		if ps < cs:
			return ChangeIndicator.RISING
		elif ps > cs:
			return ChangeIndicator.FALLING

		return ChangeIndicator.NONE

	def nw_tsoc_higher_change(self) -> ChangeIndicator:
		if self._current_nw_tsoc_higher is None or self._current_nw_tsoc_higher == self._previous_nw_tsoc_higher:
			return ChangeIndicator.NONE

		if self._current_nw_tsoc_higher and (self._previous_nw_tsoc_higher is None or not self._previous_nw_tsoc_higher):
			return ChangeIndicator.BECAME_TRUE
		elif not self._current_nw_tsoc_higher and (self._previous_nw_tsoc_higher is None or self._previous_nw_tsoc_higher):
			return ChangeIndicator.BECAME_FALSE

	def nw_tsoc_lower_change(self) -> ChangeIndicator:
		if self._current_nw_tsoc_lower is None or self._current_nw_tsoc_lower == self._previous_nw_tsoc_lower:
			return ChangeIndicator.NONE

		if self._current_nw_tsoc_lower and (self._previous_nw_tsoc_lower is None or not self._previous_nw_tsoc_lower):
			return ChangeIndicator.BECAME_TRUE
		elif not self._current_nw_tsoc_lower and (self._previous_nw_tsoc_lower is None or self._previous_nw_tsoc_lower):
			return ChangeIndicator.BECAME_FALSE

	def done(self, reactive_strategy):
		self._previous_soc = self._current_soc
		self._previous_target_soc = self._current_target_soc
		self._previous_nw_tsoc_higher = self._current_nw_tsoc_higher
		self._previous_nw_tsoc_lower = self._current_nw_tsoc_lower
		self._current_soc = None
		self._current_target_soc = None
		self._current_nw_tsoc_higher = None
		self._current_nw_tsoc_lower = None

		if (self._previous_reactive_strategy != reactive_strategy):
			tme = datetime.today().strftime('%H:%M:%S')
			logger.log(logging.DEBUG, "Strategy switch from {} to {}".format(
				self._previous_reactive_strategy.name if self._previous_reactive_strategy is not None else "None",
				reactive_strategy.name))

		self._previous_reactive_strategy = reactive_strategy

class EssDevice(object):
	def __init__(self, delegate, monitor, service):
		self.delegate:DynamicEss = delegate
		self.monitor = monitor
		self.service = service

	@property
	def connected(self):
		return self.monitor.get_value(self.service, "/Connected") == 1

	@property
	def device_instance(self):
		""" Returns the DeviceInstance of this device. """
		return self.monitor.get_value(self.service, '/DeviceInstance')

	@property
	def available(self):
		return True

	def check_conditions(self):
		""" Check that the conditions are right to use this device. If not,
		    return a non-zero error code. """
		return 0

	@property
	def average_ac_voltage(self) -> float:
		'''
			Calculates the average voltage seen on ac out, so the desired charge current of the
			evcs can be calculated more accurate to match the intendet charge volume.
		'''
		raise NotImplementedError("average_ac_voltage")

	def charge(self, flags, restrictions:Restrictions, rate, allow_feedin):
		raise NotImplementedError("charge")

	def discharge(self, flags, restrictions:Restrictions, rate, allow_feedin):
		raise NotImplementedError("discharge")

	def idle(self, allow_feedin):
		raise NotImplementedError("idle")

	def self_consume(self, restrictions:Restrictions, allow_feedin):
		raise NotImplementedError("self_consume")

	def deactivate(self):
		raise NotImplementedError("deactivate")

	@property
	def acpv(self):
		return (self.delegate._dbusservice['/Ac/PvOnGrid/L1/Power'] or 0) + \
			(self.delegate._dbusservice['/Ac/PvOnGrid/L2/Power'] or 0) + \
			(self.delegate._dbusservice['/Ac/PvOnGrid/L3/Power'] or 0) + \
			(self.delegate._dbusservice['/Ac/PvOnOutput/L1/Power'] or 0) + \
			(self.delegate._dbusservice['/Ac/PvOnOutput/L2/Power'] or 0) + \
			(self.delegate._dbusservice['/Ac/PvOnOutput/L3/Power'] or 0)

	@property
	def pvpower(self):
		return self.delegate._dbusservice['/Dc/Pv/Power'] or 0

	@property
	def external_pvpower(self):
		power = 0
		for service in self.delegate._external_solarcharger_services:
			power += self.delegate._dbusmonitor.get_value(service, '/Yield/Power') or 0
		return power

	@property
	def consumption(self):
		return max(0, (self.delegate._dbusservice['/Ac/Consumption/L1/Power'] or 0) +
			(self.delegate._dbusservice['/Ac/Consumption/L2/Power'] or 0) +
			(self.delegate._dbusservice['/Ac/Consumption/L3/Power'] or 0))

class VebusDevice(EssDevice):
	@property
	def available(self):
		return Dvcc.instance.has_ess_assistant

	@property
	def hub4mode(self):
		return self.monitor.get_value('com.victronenergy.settings',
                '/Settings/CGwacs/Hub4Mode')

	@property
	def maxfeedinpower(self):
		local_feedin_limit = self.monitor.get_value('com.victronenergy.settings',
                '/Settings/CGwacs/MaxFeedInPower')

		dess_feedin_limit = self.delegate.grid_export_limit * 1000.0 if self.delegate.grid_export_limit is not None else -1

		if local_feedin_limit > -1 and dess_feedin_limit == -1:
			return local_feedin_limit * -1

		if dess_feedin_limit > -1 and local_feedin_limit == -1:
			return dess_feedin_limit * -1

		#if both limits are present, the more restricive one takes precedence.
		if dess_feedin_limit > -1 and local_feedin_limit > -1:
			return min(dess_feedin_limit, local_feedin_limit) * -1

		#No limit present
		return -MAX_FEEDIN_VALUE

	@property
	def minsoc(self):
		# The BatteryLife delegate puts the active soc limit here.
		return self.delegate._dbusservice['/Control/ActiveSocLimit']

	@property
	def average_ac_voltage(self) -> float:
		'''
			Calculates the average voltage seen on ac out, so the desired charge current of the
			evcs can be calculated more accurate to match the intendet charge volume.
		'''
		try:
			l1 = self.monitor.get_value(self.service, '/Ac/Out/L1/V')
			l2 = self.monitor.get_value(self.service, '/Ac/Out/L2/V')
			l3 = self.monitor.get_value(self.service, '/Ac/Out/L3/V')

			#only consider non-None values to calculate the average.
			voltages = [v for v in (l1, l2, l3) if v is not None]
			return sum(voltages) / len(voltages)
		except Exception as e:
			#paths not as expected. Default to 230V.
			return 230.0

	def _set_feedin(self, allow_feedin):
		""" None = follow system setup
			True = allow
			False = restrict """

		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 0 if allow_feedin is None else 2 if allow_feedin else 1)

	def _set_charge_power(self, v):
		Dvcc.instance.internal_maxchargepower = None if v is None else max(v, 50)

	def check_conditions(self):
		# Can't do anything unless we have a minsoc, and the ESS assistant
		if not Dvcc.instance.has_ess_assistant:
			return 1 # No ESS

		# In Keep-Charged mode or external control, no point in doing anything
		if BatteryLife.instance.state == BatteryLifeState.KeepCharged or self.hub4mode == 3:
			return 2 # ESS mode is wrong

		# KeepCharged will also set minsoc to none - so this check should come after.
		if self.minsoc is None:
			return 4 # SOC low

		return 0

	def charge(self, flags, restrictions:Restrictions, rate, allow_feedin):
		self._set_feedin(allow_feedin)

		#if the desired rate is lower than dcpv, this would come down to NOT charging from AC,
		#but 100% of dcpv. To really achieve an overall charge-rate of what's requested, we need
		#to enter discharge mode instead. Discharge needs to be called with the desired discharge rate (positive)
		#minus once more dcpv, as the discharge-method will internally add dcpv again.
		# that'll be self.pvpower - rate - self.pvpower, hence comes down to rate * -1
		# or in other words: we leave the portion of rate * -1 from dcpv available for the battery.
		fast_charge_requested = Flags.FASTCHARGE in flags

		#don't forward fastcharge. That means "max power", so no forced discharge.
		if rate < self.pvpower and not fast_charge_requested:
			self.discharge(flags, restrictions, rate * -1, allow_feedin)
			return rate

		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 1)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)

		# Fast charge, or controlled charge?
		fast_charge_clearance = True #Defaults to true, if we have no limit or can't determine technical limits, we just go for it (legacy behaviour).

		if fast_charge_requested and self.delegate.battery_charge_limit is not None and self.delegate.get_charge_power_capability() is not None:
			# limits and technical capabilities are known. So, only apply fast charge, if limit would be implicit obeyed.
			fast_charge_clearance = self.delegate.get_charge_power_capability() <= self.delegate.battery_charge_limit * 1000

		if rate is None or (fast_charge_requested and fast_charge_clearance):
			self._set_charge_power(None)
			return rate #return the original requested rate either way.
		else:
			# if fast charge is requested, but not yet cleared, use the configured battery charge limit as charge rate.
			# this way the limit is obeyed, but the desired "maximum charge" is achieved.
			if (fast_charge_requested and not fast_charge_clearance and self.delegate.battery_charge_limit is not None):
				rate = self.delegate.battery_charge_limit * 1000

			# Upon first call of charge(), the input charge-rate eventually has some DC-AC losses considered.
			# (Originating from ac consumers currently beeing driven with dcsolar, reducing anticipated solar overhead)
			# As soon, as we start charging, there can't be a flow from dc to ac, so these losses will vanish
			# and the updated chargerate will be a little bit higher, if nothing else changes. This is fine and neglectable.
			# this only happens in certain charge-situations, scheduled charging from grid only changes the chargerate on soc change.
			# rate will already be adjusted for obeying batteryimport limitation, so these check can be omited.
			setrate = rate - self.pvpower
			self._set_charge_power(max(0.0, setrate))
			return rate

	def discharge(self, flags, restrictions:Restrictions, rate, allow_feedin):
		batteryexport = not (Restrictions.BAT2GRID in restrictions)

		self._set_feedin(allow_feedin)
		self._set_charge_power(None)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)

		if allow_feedin:
			# Calculate how fast to sell. If exporting the battery to the grid
			# is allowed, then export rate plus whatever DC-coupled PV is
			# making. If exporting the battery is not allowed, then limit that
			# to DC-coupled PV plus local consumption.
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', self.maxfeedinpower)

			if Flags.FASTCHARGE in flags:
				self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1)
				return None
			else:
				srate = max(1.0, (rate or 0) + self.pvpower) # 1.0 to allow selling overvoltage

				if (batteryexport):
					#discharging the battery by rate requires to discharge all available dcpv as well.
					self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', srate)
				else:
					# this may lead to feedin anyway, but it then is "feedin of solar", while battery is only backing loads.
					self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower',
						(min (srate, self.pvpower + self.consumption + 1.0))) # +1.0 to allow selling overvoltage

				return rate

		else:
			# this should never be reached, as discharge won't be entered with restrictions - leaving it here for double safety.
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None) # Normal ESS, no feedin
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1)
			return rate

	def idle(self, allow_feedin):
		self._set_feedin(allow_feedin)
		self._set_charge_power(None)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)

		if allow_feedin:
			# This keeps battery idle by not allowing more power to be taken
			# from the DC bus than what DC-coupled PV provides.
			mdp = max(1.0, self.pvpower) # 1.0 to allow selling overvoltage
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', mdp)
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', self.maxfeedinpower)
		else:
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', 0) # Normal ESS
			self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', max(1.0, self.pvpower))

		return None

	def self_consume(self, restrictions:Restrictions, allow_feedin):
		batteryimport = not (Restrictions.GRID2BAT in restrictions)

		self._set_feedin(allow_feedin)

		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None) # Normal ESS
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)

		# If importing into battery is allowed, then no restriction, let the
		# setpoint determine that. If disallowed, then only AC-coupled PV may
		# be imported into battery.
		self._set_charge_power(None if batteryimport else self.acpv)

		# Don't limit the MaxDischargePower. If a User opts to select a negative setpoint
		# Same behaviour as regular ESS should apply, despite a bat2grid limitation. (possible)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)

	def deactivate(self):
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
		self.monitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 0)
		self._set_charge_power(None)

class MultiRsDevice(EssDevice):
	@property
	def available(self):
		return self.monitor.get_value(self.service, '/Capabilities/HasDynamicEssSupport') == 1

	@property
	def minsoc(self):
		# The minsoc is here on the Multi-RS
		return self.monitor.get_value(self.service, '/Settings/Ess/MinimumSocLimit')

	@property
	def average_ac_voltage(self) -> float:
		'''
			Calculates the average voltage seen on ac out, so the desired charge current of the
			evcs can be calculated more accurate to match the intendet charge volume.
		'''
		try:
			l1 = self.monitor.get_value(self.service, '/Ac/Out/L1/V')
			l2 = self.monitor.get_value(self.service, '/Ac/Out/L2/V')
			l3 = self.monitor.get_value(self.service, '/Ac/Out/L3/V')

			#only consider non-None values to calculate the average.
			voltages = [v for v in (l1, l2, l3) if v is not None]
			return sum(voltages) / len(voltages)
		except Exception as e:
			#paths not as expected. Default to 230V.
			return 230.0

	@property
	def mode(self):
		return self.monitor.get_value(self.service, '/Settings/Ess/Mode')

	def check_conditions(self):
		# Not in optimised mode, no point in doing anything
		if self.mode not in (0, 1):
			return 2 # ESS mode is wrong
		if self.minsoc is None:
			return 4 # SOC low, happens during firmware updates
		return 0

	def charge(self, flags, restrictions:Restrictions, rate, allow_feedin):
		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin) if allow_feedin is not None else 0)
		self.monitor.set_value_async(self.service, '/Ess/DisableDischarge', 0)
		self.monitor.set_value_async(self.service, '/Ess/DisableCharge', 0)

		#if the desired rate is lower than dcpv, this would come down to NOT charging from AC,
		#but 100% of dcpv. To really achieve an overall charge-rate of what's requested, we need
		#to enter discharge mode instead. Discharge needs to be called with the desired discharge rate (positive)
		#minus once more dcpv, as the discharge-method will internally add dcpv again.
		# that'll be self.pvpower - rate - self.pvpower, hence comes down to rate * -1
		# or in other words: we leave the portion of rate * -1 from dcpv available for the battery.
		fast_charge_requested = Flags.FASTCHARGE in flags
		batteryimport = Restrictions.GRID2BAT not in restrictions

		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 0)

		# if fastcharge is requested, use the maximum power allowed as per user definition.
		if fast_charge_requested:
			rate = self.delegate.battery_charge_limit * 1000.0

		#rate shall never exceed user configured limit
		rate = min(rate, self.delegate.battery_charge_limit*1000.0)

		#if we have a grid2bat restriction, the maximum amount we can charge is solar.
		#consumption can be ignored, may be pulled from grid. (this just validates a grid2bat, not a grid2anywhere restriction)
		#only applicable for charge cases. In that case, acpv has a slight penalty.
		if not batteryimport:
			rate = min(rate, (self.pvpower or 0) + (self.acpv or 0) * self.delegate.oneway_efficiency)

		# In an unrestricted case, we just feedin everything, keep consumption - plus, what we actually want to flow TO the battery.
		# DCPV has a slight penalty, when feeding in. When requesting a certain battery rate, we need to request MORE at the setpoint due to efficiency losses.
		setpoint = - (self.acpv or 0) - ((self.pvpower or 0) / self.delegate.oneway_efficiency) + ((self.consumption or 0) + rate / self.delegate.oneway_efficiency)

		#- If Feedin is restricted, setpoint is not allowed to be negative.
		#this needs to be checked for charge cases as well, because a low chargerate may cause feedin.
		if not allow_feedin:
			setpoint = max(0, setpoint)

		#finally, make sure we stay within user configured bounds with our request.
		if setpoint < 0:
			setpoint = max(setpoint, self.delegate.grid_export_limit * -1000.0)
		elif setpoint > 0:
			setpoint = min(setpoint, self.delegate.grid_import_limit * 1000.0)

		#done, request the desired setpoint.
		self.monitor.set_value_async(self.service, '/Ess/AcPowerSetpoint', setpoint)
		return rate

	def discharge(self, flags, restrictions:Restrictions, rate, allow_feedin):
		rate = rate * -1 #commes in positive
		batteryexport = not Restrictions.BAT2GRID in restrictions

		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin) if allow_feedin is not None else 0)
		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 0)
		self.monitor.set_value_async(self.service, '/Ess/DisableDischarge', 0)
		self.monitor.set_value_async(self.service, '/Ess/DisableCharge', 0)

		#If we have a bat2grid restriction, the maximum amount we can send to grid is solar.
		#In that case, we need to limit the fraction of battery discharge to consumption/0.95.
		if not batteryexport:
			rate = max(rate, -(self.consumption or 0) / self.delegate.oneway_efficiency)

		#rate shall never exceed user configured limit
		rate = max(rate, self.delegate.battery_discharge_limit*-1000.0)

		#In an unrestricted case, we just feedin everything, keep consumption - plus, what we actually want to flow FROM the battery.
		# DCPV has a slight penalty, when feeding in. When requesting a certain battery rate, we need to request LESS at the setpoint due to efficiency losses.
		setpoint = - (self.acpv or 0) - (self.pvpower or 0) * self.delegate.oneway_efficiency + (self.consumption or 0) + rate * self.delegate.oneway_efficiency

		#- If Feedin is restricted, setpoint is not allowed to be negative.
		if not allow_feedin:
			setpoint = max(0, setpoint)

		#finally, make sure we stay within user configured bounds with our request.
		if setpoint < 0:
			setpoint = max(setpoint, self.delegate.grid_export_limit * -1000.0)
		elif setpoint > 0:
			setpoint = min(setpoint, self.delegate.grid_import_limit * 1000.0)

		#done, request the desired setpoint.
		self.monitor.set_value_async(self.service, '/Ess/AcPowerSetpoint', setpoint)
		return rate

	def idle(self, allow_feedin):
		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin) if allow_feedin is not None else 0)
		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 0)

		#idling means: Grid needs to deliver consumption - ACPV - DCPV * 0.95.
		#if there is more solar than consumption, we don't have to mind, the feedin-setting will either allow for it or not.
		acps = (self.consumption or 0) - (self.acpv or 0) - (self.pvpower or 0) * self.delegate.oneway_efficiency

		#finally, make sure we stay within user configured bounds with our request.
		if acps < 0:
			acps = max(acps, self.delegate.grid_export_limit * -1000.0)
		elif acps > 0:
			acps = min(acps, self.delegate.grid_import_limit * 1000.0)

		self.monitor.set_value_async(self.service, '/Ess/AcPowerSetpoint', acps)

		#when idling during 0 external mppt power, we can additionally disable discharge to improve setpoint stability.
		if (math.ceil(self.external_pvpower or 0)) == 0:
			self.monitor.set_value_async(self.service, '/Ess/DisableDischarge', 1)
			self.monitor.set_value_async(self.service, '/Ess/DisableCharge', 1)
		else:
			self.monitor.set_value_async(self.service, '/Ess/DisableDischarge', 0)
			self.monitor.set_value_async(self.service, '/Ess/DisableCharge', 0)

	def self_consume(self, restrictions:Restrictions, allow_feedin):
		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', int(not allow_feedin) if allow_feedin is not None else 0)
		self.monitor.set_value_async(self.service, '/Ess/AcPowerSetpoint', 0)
		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 0)
		self.monitor.set_value_async(self.service, '/Ess/DisableDischarge', 0)
		self.monitor.set_value_async(self.service, '/Ess/DisableCharge', 0)

	def deactivate(self):
		self.monitor.set_value_async(self.service, '/Ess/DisableFeedIn', 0)
		self.monitor.set_value_async(self.service, '/Ess/AcPowerSetpoint', 0)
		self.monitor.set_value_async(self.service, '/Ess/UseInverterPowerSetpoint', 0)
		self.monitor.set_value_async(self.service, '/Ess/InverterPowerSetpoint', 0)
		self.monitor.set_value_async(self.service, '/Ess/DisableDischarge', 0)
		self.monitor.set_value_async(self.service, '/Ess/DisableCharge', 0)

class DynamicEssWindow(ScheduledWindow):

	def __init__(self, start, duration, soc, targetsoc, allow_feedin, restrictions, strategy, flags, slot, to_ev):
		super(DynamicEssWindow, self).__init__(start, duration)
		self.soc = targetsoc if (targetsoc is not None and targetsoc > 0) else soc #legacy support: fall back to /Soc, when /Targetsoc is 0 (default value)
		self.allow_feedin = allow_feedin
		self.restrictions:Restrictions = Restrictions(restrictions)
		self.strategy = strategy
		self.flags:Flags = Flags(flags)
		self.slot = slot
		self.duration = duration
		self.to_ev = json.loads(to_ev) if to_ev is not None and to_ev != "" else {}

	def get_window_progress(self, now) -> float:
		""" returns the progress of the window, 0.00 - 100.00. If the window is not or no longer active, this returns none.
			current time shall be passed as now, to ensure same result throughout multiple calls.
		"""

		if (now < self.start or now > self.stop):
			return None
		elif (now == self.start):
			return 0.00
		elif (now == self.stop):
			return 100.0

		passed_seconds = now - self.start
		progress = passed_seconds.seconds / self.duration * 100.0
		return progress

	def __repr__(self):
		return "Start: {}, Stop: {}, Soc: {}".format(
			self.start, self.stop, self.soc)

class DynamicEss(SystemCalcDelegate, ChargeControl):
	control_priority = 0
	_get_time = datetime.now

	def __init__(self):
		super(DynamicEss, self).__init__()
		self.s2_cem_name = "dynamic_ess"
		self.prevsoc_cr_calc = None
		self._external_solarcharger_services = []
		self.chargerate = None # Chargerate based on tsoc. Always to be set to DynamicEss/ChargeRate, even if an override is used.
		self.override_chargerate = None # chargerate if calculation based on tsco is overwritten.
		self._timer = None
		self._devices = {}
		self._device:EssDevice = None
		self._errorcode = 0
		self._errortimer = ERROR_TIMEOUT
		self.iteration_change_tracker = IterationChangeTracker(self)
		self._is_idle = False #Flag indicating if we are currently idling, resulting in a quick-update of the idle-setpoint upon value change.
		self._idle_feedin = None #Cache the feedin-allowance of the window during idle, to quickly update the idle setpoint upon value changes.
		self._evcs_delegates:dict[str, EVCSDelegate] = {}

		#define the four kind of deterministic states we have.
		#SCHEDULED_SELFCONSUME is left out, it isn't part of the overall deterministic strategy tree, but a quick escape before entering.
		self.charge_states = (ReactiveStrategy.SCHEDULED_CHARGE_ALLOW_GRID, ReactiveStrategy.SCHEDULED_CHARGE_ENHANCED,
					ReactiveStrategy.SCHEDULED_CHARGE_NO_GRID, ReactiveStrategy.SCHEDULED_CHARGE_FEEDIN,
					ReactiveStrategy.SCHEDULED_CHARGE_SMOOTH_TRANSITION, ReactiveStrategy.UNSCHEDULED_CHARGE_CATCHUP_TARGETSOC,
					ReactiveStrategy.KEEP_BATTERY_CHARGED)
		self.selfconsume_states = (ReactiveStrategy.SELFCONSUME_ACCEPT_CHARGE, ReactiveStrategy.SELFCONSUME_ACCEPT_DISCHARGE,
							 ReactiveStrategy.SELFCONSUME_NO_GRID, ReactiveStrategy.SELFCONSUME_INCREASED_DISCHARGE, ReactiveStrategy.SELFCONSUME_ACCEPT_BELOW_TSOC)
		self.idle_states = (ReactiveStrategy.IDLE_SCHEDULED_FEEDIN, ReactiveStrategy.IDLE_MAINTAIN_SURPLUS, ReactiveStrategy.IDLE_MAINTAIN_TARGETSOC,
					  ReactiveStrategy.IDLE_NO_OPPORTUNITY, ReactiveStrategy.IDLE_NO_DISCHARGE_OPPORTUNITY)
		self.discharge_states = (ReactiveStrategy.SCHEDULED_DISCHARGE, ReactiveStrategy.SCHEDULED_MINIMUM_DISCHARGE, ReactiveStrategy.SCHEDULED_DISCHARGE_SMOOTH_TRANSITION,
						   ReactiveStrategy.CONTROLLED_DISCHARGE_EVCS)
		self.error_selfconsume_states = (ReactiveStrategy.NO_WINDOW, ReactiveStrategy.UNKNOWN_OPERATING_MODE, ReactiveStrategy.SELFCONSUME_UNPREDICTED,
								    ReactiveStrategy.SELFCONSUME_UNMAPPED_STATE, ReactiveStrategy.SELFCONSUME_FAULTY_CHARGERATE,
									ReactiveStrategy.SELFCONSUME_UNEXPECTED_EXCEPTION, ReactiveStrategy.SELFCONSUME_INVALID_TARGETSOC)

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(DynamicEss, self).set_sources(dbusmonitor, settings, dbusservice)
		# Capabilities, 1 = supports charge/discharge restrictions
		#               2 = supports self-consumption strategy
		#               4 = supports fast-charge flag
		#               8 = values set on Venus (Battery balancing, capacity, operation mode, rate limits)
		#              16 = DESS split coping capability
		#              32 = support decimal target soc values
		#              64 = support evcs control
		#             128 = Disable PV.
		self._dbusservice.add_path('/DynamicEss/Capabilities', value=255)
		self._dbusservice.add_path('/DynamicEss/NumberOfSchedules', value=NUM_SCHEDULES)
		self._dbusservice.add_path('/DynamicEss/Active', value=0, gettextcallback=lambda p, v: MODES.get(v, 'Unknown'))
		self._dbusservice.add_path('/DynamicEss/TargetSoc', value=0.0, gettextcallback=lambda p, v: '{}%'.format(v))
		self._dbusservice.add_path('/DynamicEss/WindowSoc', value=0.0, gettextcallback=lambda p, v: '{}%'.format(v))
		self._dbusservice.add_path('/DynamicEss/MinimumSoc', value=None, gettextcallback=lambda p, v: '{}%'.format(v))
		self._dbusservice.add_path('/DynamicEss/ErrorCode', value=0, gettextcallback=lambda p, v: ERRORS.get(v, 'Unknown'))
		self._dbusservice.add_path('/DynamicEss/LastScheduledStart', value=None, gettextcallback=lambda p, v: '{}'.format(datetime.fromtimestamp(v).strftime('%Y-%m-%d %H:%M:%S')))
		self._dbusservice.add_path('/DynamicEss/LastScheduledEnd', value=None, gettextcallback=lambda p, v: '{}'.format(datetime.fromtimestamp(v).strftime('%Y-%m-%d %H:%M:%S')))
		self._dbusservice.add_path('/DynamicEss/ChargeRate', value=0, gettextcallback=lambda p, v: '{}W'.format(v))
		self._dbusservice.add_path('/DynamicEss/WindowSlot', value=0)
		self._dbusservice.add_path('/DynamicEss/Strategy', value=None, gettextcallback=lambda p, v: Strategy(v).name)
		self._dbusservice.add_path('/DynamicEss/WorkingSocPrecision', value=0)
		self._dbusservice.add_path('/DynamicEss/Restrictions', value=None, gettextcallback=lambda p, v: '{}'.format(Restrictions(v).name))
		self._dbusservice.add_path('/DynamicEss/AllowGridFeedIn', value=None)
		self._dbusservice.add_path('/DynamicEss/Flags', value=None, gettextcallback=lambda p, v: '{}'.format(Flags(v).name))
		self._dbusservice.add_path('/DynamicEss/AvailableOverhead', value=None, gettextcallback=lambda p, v: '{}W'.format(v))
		self._dbusservice.add_path('/DynamicEss/ChargeHysteresis', value=0, gettextcallback=lambda p, v: '{}%'.format(v))
		self._dbusservice.add_path('/DynamicEss/DischargeHysteresis', value=0, gettextcallback=lambda p, v: '{}%'.format(v))
		self._dbusservice.add_path('/DynamicEss/WindowToEVBattery', value=f"{{}}")
		self._dbusservice.add_path('/DynamicEss/EvcsGxFlags', value=f"{{}}") #channel to communicate flags TO vrm. Inbound is a setting.

		if self.mode > 0:
			self._dbusservice.add_path('/DynamicEss/ReactiveStrategy', value=None, gettextcallback=lambda p, v: ReactiveStrategy(v))
			self._timer = GLib.timeout_add_seconds(INTERVAL, self._on_timer)
		else:
			self._dbusservice.add_path('/DynamicEss/ReactiveStrategy', value = ReactiveStrategy.DESS_DISABLED.value, gettextcallback=lambda p, v: ReactiveStrategy(v))

	def get_settings(self):
		# Settings for DynamicEss
		path = '/Settings/DynamicEss'

		settings = [
			("dess_mode", path + "/Mode", 0, 0, 4),
			("dess_capacity", path + "/BatteryCapacity", 0.0, 0.0, 1000.0),
			("dess_efficiency", path + "/SystemEfficiency", 90.0, 50.0, 100.0),
			("dess_fullchargeinterval", path + "/FullChargeInterval", 14, -1, 99),
			("dess_fullchargeduration", path + "/FullChargeDuration", 2, -1, 12),
			("dess_operatingmode", path + '/OperatingMode', -1, -1, 2),
			("dess_batterychargelimit", path + '/BatteryChargeLimit', -1.0, -1.0, 9999.9),
			("dess_batterydischargelimit", path + '/BatteryDischargeLimit', -1.0, -1.0, 9999.9),
			("dess_gridimportlimit", path + '/GridImportLimit', -1.0, -1.0, 9999.9),
			("dess_gridexportlimit", path + '/GridExportLimit', -1.0, -1.0, 9999.9),
			("dess_evemergencystart", path + '/EVEmergencyStart', 60*60, 0, 86400),
			("dess_evemergencycurrent", path + '/EVEmergencyCurrent', 6, 0, 32),
			("dess_evcscontroldisabled", path + '/DisableEvcsControl', 0, 0, 1),
			("dess_evcsvrmflags", path + '/EvcsVrmFlags', "{}", "", ""),
		]

		for i in range(NUM_SCHEDULES):
			settings.append(("dess_start_{}".format(i),
				path + "/Schedule/{}/Start".format(i), 0, 0, 0))
			settings.append(("dess_duration_{}".format(i),
				path + "/Schedule/{}/Duration".format(i), 0, 0, 0))
			settings.append(("dess_targetsoc_{}".format(i),
				path + "/Schedule/{}/TargetSoc".format(i), 0.0, 0.0, 100.0)) #needs to be decimal
			settings.append(("dess_soc_{}".format(i),
				path + "/Schedule/{}/Soc".format(i), 0, 0, 100)) #keep legacy support for a while.
			settings.append(("dess_discharge_{}".format(i),
				path + "/Schedule/{}/AllowGridFeedIn".format(i), 0, 0, 1))
			settings.append(("dess_restrictions_{}".format(i),
				path + "/Schedule/{}/Restrictions".format(i), 0, 0, sum(res.value for res in Restrictions)))
			settings.append(("dess_strategy_{}".format(i),
				path + "/Schedule/{}/Strategy".format(i), 0, 0, 3))
			settings.append(("dess_flags_{}".format(i),
				path + "/Schedule/{}/Flags".format(i), 0, 0, sum(flag.value for flag in Flags))),
			settings.append(("dess_toev_{}".format(i),
				path + "/Schedule/{}/ToEVBattery".format(i), "", "", ""))

		return settings

	def get_input(self):
		return [
			(HUB4_SERVICE, ['/Overrides/ForceCharge',
				'/Overrides/MaxDischargePower', '/Overrides/Setpoint',
				'/Overrides/FeedInExcess']),
			('com.victronenergy.acsystem', [
				 '/Connected',
				 '/DeviceInstance',
				 '/Capabilities/HasDynamicEssSupport',
				 '/Ess/AcPowerSetpoint',
				 '/Ess/InverterPowerSetpoint',
				 '/Ess/UseInverterPowerSetpoint',
				 '/Ess/DisableCharge',
				 '/Ess/DisableDischarge',
				 '/Ess/DisableFeedIn',
				 '/Settings/Ess/Mode',
				 '/Mode',
				 '/Settings/Ess/MinimumSocLimit']),
			('com.victronenergy.settings', [
				'/Settings/CGwacs/Hub4Mode',
				'/Settings/CGwacs/MaxFeedInPower',
				'/Settings/CGwacs/PreventFeedback']),
			('com.victronenergy.solarcharger', [
				'/Yield/Power']),
			('com.victronenergy.evcharger', [
				'/StartStop',
				'/SetCurrent',
				'/Status',
				'/Mode',
				'/Ac/L1/Power',
				'/Ac/L3/Power'
			]),
			('com.victronenergy.vebus', [
				'/Ac/Out/L1/V',
				'/Ac/Out/L2/V',
				'/Ac/Out/L3/V',
			]),
			('com.victronenergy.acsystem', [
				'/Ac/Out/L1/V',
				'/Ac/Out/L2/V',
				'/Ac/Out/L3/V',
			])
		]

	def get_output(self):
		return [('/DynamicEss/Available', {'gettext': '%s'})]

	def _set_device(self, *args, **kwargs):
		# Use device with lowest DeviceInstance. In systems with both
		# Multi-RS and VE.Bus, this will tend to favour the RS. Otherwise
		# it will favour the device on the internal mk2 connection.
		for self._device in sorted(self._devices.values(),
				key=lambda x: (x.device_instance or 0xFF)):
			if self._device.connected:
				break
		else:
			self._device:EssDevice = None

	def get_charge_power_capability(self) -> float:
		'''
		  Determines the systems maximum battery charge capability in Watts.
		  If the ccl and cvl fails to be determined, then None is returned.
		  None is to be distinguished from 0 (which means no charging allowed by the bms)
		'''

		battery = self._dbusservice["/ActiveBmsService"]

		# first, try to obtain values from the bms service.
		if battery is not None and battery != "":
			ccl = self._dbusmonitor.get_value(battery, '/Info/MaxChargeCurrent')
			cvl = self._dbusmonitor.get_value(battery, '/Info/MaxChargeVoltage')

			if (ccl is not None and cvl is not None):
				return ccl * cvl

		return None

	@property
	def oneway_efficiency(self):
		''' When charging from AC, only half of the efficiency-losses have to be considered
			So, with an overall system efficency of 0.8, the charging efficency would be 0.9 and so on.
		'''
		return min(1.0, ((1 - self._settings["dess_efficiency"] / 100.0) / -2.0) + 1.0)

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.vebus.'):
			self._devices[service] = VebusDevice(self, self._dbusmonitor, service)
			self._dbusmonitor.track_value(service, "/Connected", self._set_device)
			GLib.idle_add(self._set_device)
		elif service.startswith('com.victronenergy.acsystem.'):
			self._devices[service] = MultiRsDevice(self, self._dbusmonitor, service)
			GLib.idle_add(self._set_device)
		elif service.startswith('com.victronenergy.solarcharger.'):
			self._external_solarcharger_services.append(service)
		elif service.startswith('com.victronenergy.evcharger.'):
			logger.info("Registering EVCS #{} on {} for charge control. Attempting S2 Connection.".format(instance, service))
			if str(instance) not in self._evcs_delegates.keys():
				delegate = EVCSDelegate(service, instance, self._dbusmonitor, self)
				self._evcs_delegates[str(instance)] = delegate
				self._evcs_delegates[str(instance)].begin()
				self.publish_evcs_flags()

	def device_removed(self, service, instance):
		if service in self._external_solarcharger_services:
			self._external_solarcharger_services.remove(service)
		elif service.startswith('com.victronenergy.evcharger.'):
			if str(instance) in self._evcs_delegates.keys():
				self._evcs_delegates[str(instance)].end(False) #cleanup.
				del self._evcs_delegates[str(instance)]
				logger.info("EVCS #{} on {} removed from charge control.".format(instance, service))
				self.publish_evcs_flags()

		try:
			del self._devices[service]
		except KeyError:
			pass
		else:
			self._set_device()

	def settings_changed(self, setting, oldvalue, newvalue):
		if setting == 'dess_mode':
			if oldvalue == 0 and newvalue > 0:
				self._timer = GLib.timeout_add_seconds(INTERVAL, self._on_timer)
			if newvalue == 0:
				self._dbusservice['/DynamicEss/ReactiveStrategy'] = ReactiveStrategy.DESS_DISABLED.value

	def windows(self):
		starttimes = (self._settings['dess_start_{}'.format(i)] for i in range(NUM_SCHEDULES))
		durations = (self._settings['dess_duration_{}'.format(i)] for i in range(NUM_SCHEDULES))
		socs = (self._settings['dess_soc_{}'.format(i)] for i in range(NUM_SCHEDULES)) #keep legacy support for a while
		targetsocs = (self._settings['dess_targetsoc_{}'.format(i)] for i in range(NUM_SCHEDULES))
		discharges = (self._settings['dess_discharge_{}'.format(i)] for i in range(NUM_SCHEDULES))
		restrictions = (self._settings['dess_restrictions_{}'.format(i)] for i in range(NUM_SCHEDULES))
		strategies = (self._settings['dess_strategy_{}'.format(i)] for i in range(NUM_SCHEDULES))
		wflags = (self._settings['dess_flags_{}'.format(i)] for i in range(NUM_SCHEDULES))
		toevbattery = (self._settings['dess_toev_{}'.format(i)] for i in range(NUM_SCHEDULES))

		for start, duration, soc, targetsoc, discharge, restrict, strategy, flags, slot, toevbattery in zip(starttimes, durations, socs, targetsocs, discharges, restrictions, strategies, wflags, range(NUM_SCHEDULES), toevbattery):
			if start > 0:
				yield DynamicEssWindow(
					datetime.fromtimestamp(start), duration, soc, targetsoc, discharge, restrict, strategy, flags, slot, toevbattery)

	@property
	def mode(self):
		return self._settings['dess_mode']

	@property
	def grid_import_limit(self) -> float:
		''' Grid import limit as configured by the user for DESS. In kW, positive, None if not set'''
		return self._settings['dess_gridimportlimit'] if self._settings['dess_gridimportlimit'] >= 0 else None

	@property
	def grid_export_limit(self)-> float:
		''' Grid export limit as configured by the user for DESS. In kW, positive, None if not set'''
		return self._settings['dess_gridexportlimit'] if self._settings['dess_gridexportlimit'] >= 0 else None

	@property
	def battery_charge_limit(self)-> float:
		''' Battery charge limit as configured by the user for DESS. In kW, positive, None if not set'''
		return self._settings['dess_batterychargelimit'] if self._settings['dess_batterychargelimit'] >= 0 else None

	@property
	def battery_discharge_limit(self)-> float:
		''' Battery discharge limit as configured by the user for DESS. In kW, positive, None if not set'''
		return self._settings['dess_batterydischargelimit'] if self._settings['dess_batterydischargelimit'] >= 0 else None

	@property
	def active(self):
		return self._dbusservice['/DynamicEss/Active']

	@active.setter
	def active(self, v):
		self._dbusservice['/DynamicEss/Active'] = v

	@property
	def average_ac_voltage(self) -> float:
		'''
			Calculates the average voltage seen on ac out, so the desired charge current of the
			evcs can be calculated more accurate to match the intendet charge volume.
		'''
		return self._device.average_ac_voltage

	@property
	def charge_hysteresis(self):
		return self._dbusservice['/DynamicEss/ChargeHysteresis']

	@charge_hysteresis.setter
	def charge_hysteresis(self, v):
		self._dbusservice['/DynamicEss/ChargeHysteresis'] = v

	@property
	def discharge_hysteresis(self):
		return self._dbusservice['/DynamicEss/DischargeHysteresis']

	@discharge_hysteresis.setter
	def discharge_hysteresis(self, v):
		self._dbusservice['/DynamicEss/DischargeHysteresis'] = v

	@property
	def errorcode(self):
		return self._errorcode

	@errorcode.setter
	def errorcode(self, v):
		self._errorcode = v
		if v == 0:
			# Errors clear immediately
			self._dbusservice['/DynamicEss/ErrorCode'] = 0
			self._errortimer = ERROR_TIMEOUT
		elif self._errortimer == 0:
			# Set the error after it has been non-zero for more than
			# ERROR_TIMEOUT
			self._dbusservice['/DynamicEss/ErrorCode'] = v
		else:
			# Count down
			self._errortimer = max(self._errortimer - INTERVAL, 0)

	@property
	def targetsoc(self):
		return self._dbusservice['/DynamicEss/TargetSoc'] if self._dbusservice['/DynamicEss/TargetSoc'] is not None and  self._dbusservice['/DynamicEss/TargetSoc'] > 0 else None

	@targetsoc.setter
	def targetsoc(self, v):
		self._dbusservice['/DynamicEss/TargetSoc'] = v or 0

	@property
	def soc(self):
		"""
			returns the current soc, rounded to the systems working precission. This allows
			us to omit to round every comparision anywhere else.
		"""
		bsoc = BatterySoc.instance.soc
		return round(bsoc, self.soc_precision) if bsoc is not None else None

	@property
	def soc_raw(self):
		"""
			returns the unmodified soc. Required to detect actual precission.
		"""
		return BatterySoc.instance.soc

	@property
	def soc_precision(self) -> int:
		"""
			Detected SoC Precision of the battery.
		"""
		return self._dbusservice['/DynamicEss/WorkingSocPrecision']

	@soc_precision.setter
	def soc_precision(self, v):
		self._dbusservice['/DynamicEss/WorkingSocPrecision'] = v

	@property
	def soc_precision(self) -> int:
		"""
			Detected SoC Precision of the battery.
		"""
		return self._dbusservice['/DynamicEss/WorkingSocPrecision']

	@soc_precision.setter
	def soc_precision(self, v):
		self._dbusservice['/DynamicEss/WorkingSocPrecision'] = v

	@property
	def capacity(self) -> float:
		"""
			DESS configured capacity in kWh
		"""
		return self._settings["dess_capacity"]

	@property
	def operating_mode(self) -> OperatingMode:
		return OperatingMode(self._settings["dess_operatingmode"])

	def update_chargerate(self, now, end, start_soc, end_soc):
		""" now is current time, end is end of slot, start_soc and end_soc determine the amount of intended soc change. Rate is the rate desired DC-Side. """

		# Only update the charge rate if a new soc value has to be considered or chargerate is none
		# round the soc, otherwise comparission fails for decimal socs and rate is calculated every 5 sec.
		# adapting a chargerate with a forced precision of 1 is enough.
		if self.chargerate is None or self.prevsoc_cr_calc is None or round(self.soc, 1) != round(self.prevsoc_cr_calc, 1):
			try:
				# a Watt is a Joule-second, a Wh is 3600 joules.
				# Capacity is kWh, so multiply by 100, percentage needs division by 100, therefore 36000.
				percentage = abs(start_soc - end_soc)
				duration = abs((end - now).total_seconds())
				chargerate = round((percentage * self.capacity * 36000) / duration)

				logger.debug("Charging from {} to {} in {}s requires a {} rate.".format(start_soc, end_soc, duration, chargerate))

				#Discharge and charge has two different limits for calculation. these limits are added in update_chargerate
				#rather than charge/discharge method, so data logging clearly shows the exact computed chargerate.
				if start_soc <= end_soc:
					chargerate = chargerate if self.battery_charge_limit is None else min(chargerate, self.battery_charge_limit * 1000)
				elif start_soc > end_soc:
					chargerate = chargerate if self.battery_discharge_limit is None else min(chargerate, self.battery_discharge_limit * 1000)

				# keeping up prior chargerate is no longer required at this point.
				self.chargerate = chargerate
				self.prevsoc_cr_calc = self.soc

			except ZeroDivisionError:
				logger.log(logging.WARNING, "Caught ZeroDivisionError in update_chargerate() for end='{}', now='{}'".format(end, now))
				self.chargerate = None

		#chargerate should be negative, if discharge-case to fit into maths elsewhere.
		#discharge_method then has to handle accordingly.
		if (end_soc < start_soc and self.chargerate is not None):
			self.chargerate = abs(self.chargerate) * -1

	def _on_timer(self):
		# If DESS was disabled, deactivate and kill timer.
		if self.mode in (0, 2, 3): # Old buy/sell states now also means off
			self.deactivate(0) # No error
			self._dbusservice['/DynamicEss/ReactiveStrategy'] = ReactiveStrategy.DESS_DISABLED.value

			# Reset EVCS Flags to None as well.
			self.publish_evcs_flags()

			return False

		def bail(code):
			self.release_control()
			self.active = 0 # Off
			self.errorcode = code
			self.targetsoc = None
			self._dbusservice['/DynamicEss/MinimumSoc'] = None

		if self.capacity == 0.0:
			bail(5) # Capacity not set
			return True

		if self._device is None:
			bail(1) # No ESS
			return True

		if self.soc is None:
			bail(4) # Low SOC, can happen during firmware updates
			return True

		errorcode = self._device.check_conditions()
		if errorcode != 0:
			bail(errorcode)
			return True

		now = self._get_time()
		start = None
		stop = None
		self._is_idle = False
		self._idle_feedin = None

		#TODO: this always builds all 48 Windows.
		#      can be optimized, we MOSTLY need 0 - 5
		#      and #47 to determine maximal available schedule.
		windows = list(self.windows())

		#Whenever an error occurs that is totally unexpected, the delegate
		#should enter self consume and not die.(try/catch around the control loop logic)
		try:
			for w in windows:
				# Keep track of maximum available schedule
				if start is None or w.start > start:
					start = w.start
					stop = w.stop

			self._dbusservice['/DynamicEss/LastScheduledStart'] = None if start is None else int(datetime.timestamp(start))
			self._dbusservice['/DynamicEss/LastScheduledEnd'] = None if stop is None else int(datetime.timestamp(stop))

			final_strategy = ReactiveStrategy.NO_WINDOW
			current_window = None
			next_window = None

			# This is the ESS minsoc of the selected device
			self._dbusservice['/DynamicEss/MinimumSoc'] = None if self._device is None else self._device.minsoc

			#iterate through windows, find the current one. Usually it should be first,
			#but in case of update issues may not. Also grab the next window, to perform
			#some "look aheads" for optimizations.
			for w in windows:
				if self.acquire_control() and now in w:
					self.active = 1 # Auto
					self.errorcode = 0 # No error

					current_window = w

					self._dbusservice['/DynamicEss/Strategy'] = w.strategy
					self._dbusservice['/DynamicEss/Restrictions'] = w.restrictions
					self._dbusservice['/DynamicEss/AllowGridFeedIn'] = int(w.allow_feedin)
					break # out of for loop

			if current_window is not None:
				#found current window, now we need nextWindow to do some look aheads as well.
				#next window is the one containing current.start + current.duration + 1.
				#finding next window is not required to enter the control loop, can be None.
				next_window_save_start = current_window.stop + timedelta(seconds = 1)
				for w in windows:
					if (next_window_save_start in w):
						next_window = w
						break # out of for loop

				# validate solar-system state
				self._disable_pv(Flags.DISABLEPV in current_window.flags)

				#As of now, one common handler is enough. Hence, we don't need to validate the operation mode
				final_strategy = self._determine_reactive_strategy(current_window, next_window, current_window.restrictions, now)

				#determine final strategy to use.
				final_strategy = self._determine_reactive_strategy(current_window, next_window, current_window.restrictions, now)
				self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate or 0 #Always set the anticipated chargerate on dbus.

				#check EV instructions, if any.
				for evcs in self._evcs_delegates.values():
					evcs.loop(current_window, now, self)

				#Update EVCS Flags on dbus.
				self.publish_evcs_flags()
			else:
				# No matching windows
				if self.active or self.errorcode != 3:
					self.deactivate(3)

			#write out current override strategy to determine if the local system behaves "out of schedule" on purpose.
			if self._dbusservice["/SystemState/LowSoc"] == 1:
				final_strategy= ReactiveStrategy.ESS_LOW_SOC

			#done, reset iteration_change_tracker
			self._dbusservice['/DynamicEss/ReactiveStrategy'] = final_strategy.value
			self.iteration_change_tracker.done(final_strategy)

		except Exception as ex:
			logger.log(logging.FATAL, "Unexpected exception inside Control Loop.", exc_info = ex)
			final_strategy = ReactiveStrategy.SELFCONSUME_UNEXPECTED_EXCEPTION
			self._dbusservice['/DynamicEss/ReactiveStrategy'] = final_strategy.value

		if final_strategy.value in self.error_selfconsume_states:
			#Do at least regular ESS.
			self.chargerate = None #self consume has no chargerate.
			self.charge_hysteresis = self.discharge_hysteresis = 0
			self._dbusservice['/DynamicEss/ChargeRate'] = 0
			self._device.self_consume(Restrictions.NONE, None) #no schedule, no restrictions.

		return True

	@property
	def hysteresis(self) -> float:
		"""
			Determines the hysteresis value to use. We anticipate that the scheduler may never be off more than
			250 Wh. So, we use the equivalant of 250Wh of the battery size, but limit it to be 1%, as this may
			be the biggest soc-drop that could be encountered on a integer-based system during idle.
		"""
		#capacity (kWh) * 10 is 1% in Wh equivalent.
		return round(min(250.0 / (self.capacity * 10), 1.0), self.soc_precision)

	def publish_evcs_flags(self) -> None:
		jo = {}
		jor = {}
		for evcs_delegate in self._evcs_delegates.values():
			jo[evcs_delegate.instance] = evcs_delegate.gx_flags
			jor[evcs_delegate.instance] = evcs_delegate.gx_flags.stringify()

		jos = json.dumps(jo)
		jors = json.dumps(jor)
		if jos != self._dbusservice['/DynamicEss/EvcsGxFlags']:
			logger.info("EvcsGxFlags changed to: {} => {}".format(jos, jors))
			self._dbusservice['/DynamicEss/EvcsGxFlags'] = jos

	def is_ev_charging(self) -> bool:
		"""
			Checks if any EV is currently charging, used to determine a different behaviour for the
			main battery discharge.
		"""
		for evcsid, evcs_state in self._evcs_delegates.items():
			#we only consider the EV charging, if the state is charging AND we have been the invoker
			#of the start. If it is full or not charging, battery usage behaviour shouldn't be affected.
			if evcs_state.is_started and evcs_state.status == 2:
				return True

		return False

	def _determine_reactive_strategy(self, w: DynamicEssWindow, nw: DynamicEssWindow, restrictions:Restrictions, now) -> ReactiveStrategy:
		'''
			Logic to be applied in Greenmode. Micro changes in strategy are applied to optimize solar gain / minimize grid pull. Returns the choosen strategy.
			Strategy has to be determined in a 100% deterministic way. After it has been determined the proper system reaction with different variable sets
			is called to minimize repetition of functional code.
		'''
		# required variables to make some improvement decissions
		# Generally, solar_plus is PV - Consumption
		# It needs to take efficency into account, legacy equation did this by multiplying acpv with 0.9
		# However it will be more precice to only consider the "available ac pv" with 0.9. Direct Consumption will basically
		# lower the available acpv without conversion losses.

		if w.soc is None:
			return ReactiveStrategy.SELFCONSUME_INVALID_TARGETSOC

		available_solar_plus = 0

		direct_acpv_consume = min(self._device.acpv or 0, self._device.consumption)
		remaining_ac_pv = max(0, (self._device.acpv or 0) - direct_acpv_consume)
		if remaining_ac_pv > 0:
			#dc can be used for charging 100%, ac is penalized with 10% conversion losses.
			available_solar_plus = (self._device.pvpower or 0) + remaining_ac_pv * self.oneway_efficiency
		else:
			#not enough ac pv. so, the part flowing from DC to remaining AC loads will lower the budget.
			#ac doesn't have to be considered, it's 100% consumed. Hower, dc consume is penalized by 10% conversion
			direct_dcpv_consume = self._device.consumption - direct_acpv_consume
			available_solar_plus = (self._device.pvpower or 0) - direct_dcpv_consume / self.oneway_efficiency

		available_solar_plus = round(available_solar_plus)

		self._dbusservice["/DynamicEss/AvailableOverhead"] = available_solar_plus
		self._dbusservice["/DynamicEss/WindowSoc"] = round(w.soc, self.soc_precision)
		self._dbusservice["/DynamicEss/WindowSlot"] = w.slot
		self._dbusservice["/DynamicEss/WindowToEVBattery"] = json.dumps(w.to_ev)


		#logger.log(logging.DEBUG, "ACPV / DCPV / Cons / Overhead is: {} / {} / {} / {}".format(self._device.acpv, self._device.pvpower, self._device.consumption, available_solar_plus))

		next_window_higher_target_soc = nw is not None and (nw.soc > w.soc) and nw.strategy != Strategy.SELFCONSUME
		next_window_lower_target_soc = nw is not None and (nw.soc < w.soc) and nw.strategy != Strategy.SELFCONSUME

		#pass new values to iteration change tracker.
		self.iteration_change_tracker.input(self.soc, self.soc_raw, self.targetsoc, next_window_higher_target_soc, next_window_lower_target_soc)
		soc_change = self.iteration_change_tracker.soc_change()
		target_soc_change = self.iteration_change_tracker.target_soc_change()
		window_progress = w.get_window_progress(now) or 0

		# When we have a Scheduled-Selfconsume, we can ommit to walk through the decission tree.
		if w.strategy == Strategy.SELFCONSUME:
			self.chargerate = None #No scheduled chargerate in this case.
			self.targetsoc = None
			self.charge_hysteresis = self.hysteresis
			self.discharge_hysteresis = 0
			self._device.self_consume(restrictions, w.allow_feedin)
			return ReactiveStrategy.SCHEDULED_SELFCONSUME

		# Below here, strategy is any of the target soc dependent strategies
		# some preparations
		self.override_chargerate = None
		new_targetsoc = round(w.soc, self.soc_precision)

		if new_targetsoc <= 0.1:
			#this should never happen. extra safety check to avoid undesired discharges.
			return ReactiveStrategy.SELFCONSUME_INVALID_TARGETSOC

		#detect soc drop during idle.
		if self.targetsoc is not None and round(self.targetsoc, self.soc_precision) != new_targetsoc:
			self.chargerate = None # For recalculation, if target soc changes.

		self.targetsoc = new_targetsoc
		self._dbusservice['/DynamicEss/Flags'] = w.flags

		#extract some flags for easy access.
		excess_to_grid = (w.strategy == Strategy.PROGRID) or (w.strategy == Strategy.TARGETSOC)
		missing_to_grid = (w.strategy == Strategy.TARGETSOC) or (w.strategy == Strategy.PROBATTERY)
		excess_to_bat = not excess_to_grid
		missing_to_bat = not missing_to_grid

		#Needs to be determined
		reactive_strategy = None

		if round(self.soc + self.charge_hysteresis, self.soc_precision) < self.targetsoc or self.targetsoc >= 100:
			# if 100% is reached, keep batteries charged.
			# Mind we need to leave this, if missing2bat copping is selected and the ME-indicator is negative.
			# (To be more precice, as soon as the 250 Watt requested couldnt't be served by solar, fall back to default behaviour)
			if self.targetsoc >= 100 and self.soc >= 100 and (missing_to_grid or (missing_to_bat and available_solar_plus > 250)):
				self.chargerate = 250
				reactive_strategy = ReactiveStrategy.KEEP_BATTERY_CHARGED

			# we are behind plan. Charging is required.
			else:
				self.update_chargerate(now, w.stop, self.soc, self.targetsoc)

				# Based on the coping flags, charging has 4 options
				# Also restrictions may be applied (grid2bat).
				if available_solar_plus > self.chargerate:
					# 1) There is more solar than expected and we are EXCESSTOBAT -> charge enhanced.
					#    This state also needs to be enforced, when feedin is restricted
					if excess_to_bat or not w.allow_feedin:
						self.override_chargerate = available_solar_plus
						reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_ENHANCED

					# 2) There is more solar than expected and we are EXCESSTOGRID -> charge at calculated charge rate, accept feedin happening.
					#    This state is dissallowed, when feedin is restricted, but then we already entered situation 1.
					elif excess_to_grid:
						reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_FEEDIN
				else:
					#available_solar_plus <= self.chargerate

					# 3) There isn't enough solar and we are flagged MISSINGTOGRID -> use calculated charge rate.
					#    (Wording note: Missing2Grid describes the punishment of missing energy to the grid - so TAKING energy from the grid ;-))
					#    But, this state is dissallowed, if a Grid2Bat Restriction is active.
					if missing_to_grid and not (Restrictions.GRID2BAT in w.restrictions):
						reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_ALLOW_GRID

					# 4) There isn't enough solar and we are flagged MISSINGTOBAT -> only use solar power that is availble.
					#    This is self consume, until condition changes.
					#    In case there is Grid2Bat restriction, this is our only option, even if the flag would indicate MISSINGTOGRID
					elif available_solar_plus > 0 and (missing_to_bat or (Restrictions.GRID2BAT in w.restrictions)):
						reactive_strategy = ReactiveStrategy.SELFCONSUME_NO_GRID

					# 5.) No Grid charge possible, no solar. We can't charge.
					#     However, when we have missing_to_bat, we allow to go bellow target soc.
					elif available_solar_plus <= 0 and missing_to_bat:
						reactive_strategy = ReactiveStrategy.SELFCONSUME_ACCEPT_BELOW_TSOC

					# 5.) No Grid charge possible, no solar. We can't charge.
					#     with missing2grid, but grid2bat restriction we can only idle now.
					#     missing2grid with no restriction is already handled in case 3.
					elif available_solar_plus <= 0 and missing_to_grid and (Restrictions.GRID2BAT in w.restrictions):
						reactive_strategy = ReactiveStrategy.IDLE_NO_OPPORTUNITY

		else:
			# if we are currently in any SCHEDULED_CHARGE_* State and our next window outlines an even higher target soc,
			# don't switch to idle, but keep a certain chargerate. As soon as target_soc changes, this state has to be left.
			# but only enter it, when window progress is >= TRANSITION_STATE_THRESHOLD
			if (self.iteration_change_tracker._previous_reactive_strategy in self.charge_states and
	   			next_window_higher_target_soc and window_progress >= TRANSITION_STATE_THRESHOLD) or \
				(self.iteration_change_tracker._previous_reactive_strategy == ReactiveStrategy.SCHEDULED_CHARGE_SMOOTH_TRANSITION and target_soc_change == ChangeIndicator.NONE):
				# keep current charge rate untouched.
				# already targeting the new soc target of "next" window will cause a not smooth transition, if next window in slot 1 is outdated
				# and the next window beeing pushed to slot 0 indicates another target soc.
				reactive_strategy = ReactiveStrategy.SCHEDULED_CHARGE_SMOOTH_TRANSITION
			else:
				# we are above or equal to target soc, or the charge histeresis has not yet kicked in from a prior state.

				if (available_solar_plus > 0 and not excess_to_grid):
					# If surplus is available, always attempt to charge, unless we are flagged EXCESSTOGRID
					reactive_strategy = ReactiveStrategy.SELFCONSUME_ACCEPT_CHARGE

				else:
					# so, now we have: (availableSolarPlus <= 0 or solaroverhaed, but excess_to_grid) and (equal or above targetSoc).
					# so, most likely any of the discharge-variants is required (or ultimately idle)
					# if we are flagged EXESSTOGRID and MISSINGTOGRID, perform a strict discharge, based on soc difference.
					# Any imprecission shall be handled by the grid
					# not allowed with bat2grid restriction
					#       When we have a bat2grid restriction, we should discharge at full consumption, feeding in 100% of solar production.
					if self.soc - self.discharge_hysteresis > max(self.targetsoc, self._device.minsoc) and excess_to_grid and missing_to_grid \
						and not (Restrictions.BAT2GRID in restrictions):
						self.update_chargerate(now, w.stop, self.soc, self.targetsoc)
						reactive_strategy = ReactiveStrategy.SCHEDULED_DISCHARGE

					# if flags are EXCESSTOGRID and MISSINGTOBAT, that means: keep a MINIMUM dischargerate, but allow to discharge more, if consumption-solar is higher.
					# not allowed with bat2grid restriction
					# so, we do some quick maths, if loads would require a higher discharge - then we let self consume handle that, over calculating a "better" discharge rate.
					elif self.soc - self.discharge_hysteresis > max(self.targetsoc, self._device.minsoc) and excess_to_grid and missing_to_bat \
						and not (Restrictions.BAT2GRID in restrictions):
						self.update_chargerate(now, w.stop, self.soc, self.targetsoc)
						me_indicator = available_solar_plus - self.chargerate

						if me_indicator < 0:
							# missing, let self consume handle this over calculating a improved rate.
							reactive_strategy =  ReactiveStrategy.SELFCONSUME_INCREASED_DISCHARGE
						else:
							# excess, ensure the minimum discharge rate required to reach targetsoc as of "now".
							self.override_chargerate = abs(self.chargerate) * -1
							reactive_strategy =  ReactiveStrategy.SCHEDULED_MINIMUM_DISCHARGE

					# left over discharge cases:
					#	FIXME: When we have pro Grid and a battery restriction but Solar > consumption, self-consume states are not suitable - it will charge. Idle Instead.
					#   - bat2grid restricted -> Selfconsume to drive loads, or Idle
					#   - EXCESSTOBAT and MISSINGTOBAT -> self consume
					#   - EXCESSTOBAT and MISSINGTOGRID:
					#     Technically that means, we should have a MAXIMUM dischargerate and punish the energy above that to the grid
					#     However, that may cause some grid2consumption happening in the beginning of the window, but still ending up above target soc.
					#     So that would be gridpull for no reason.
					#     So, the more logical way is to accept ANY discharge, but simple stop when reaching target soc - and punish the remaining
					#     load during that window to the grid. -> also self consume
					# BUT: we are only doing this, If our next window has a smaller, equal or no target soc
					elif self.soc - self.discharge_hysteresis > max(self.targetsoc, self._device.minsoc):
						# we are supposed to drive loads only to achieve the indendet discharge. However, if solar > consumption and a bat2grid restriction,
						# we have no discharge opportunity, Then, we ultimately only can idle to stay close to target soc.
						if available_solar_plus > 0 and (Restrictions.BAT2GRID in restrictions):
							reactive_strategy = ReactiveStrategy.IDLE_NO_DISCHARGE_OPPORTUNITY
						else:
							if (self.is_ev_charging()):
								self.update_chargerate(now, w.stop, self.soc, self.targetsoc)
								reactive_strategy = ReactiveStrategy.CONTROLLED_DISCHARGE_EVCS
							else:
								reactive_strategy = ReactiveStrategy.SELFCONSUME_ACCEPT_DISCHARGE

					else:
						# Here we are:
						# - Ahead of plan, but the next window indicates a higher soc target.
						# - Spot on target soc, so idling is imminent / above targetSoc by discharge_hysteresis %.
						# - available solar plus, but intended feedin.
						if available_solar_plus > 0 and excess_to_grid:
							# We have solar surplus, but VRM wants an explicit feedin.
							# since we are above or equal to target soc, we are going idle to achieve that.
							reactive_strategy = ReactiveStrategy.IDLE_SCHEDULED_FEEDIN
						else:
							if (self.iteration_change_tracker._previous_reactive_strategy in self.discharge_states and
								next_window_lower_target_soc and window_progress >= TRANSITION_STATE_THRESHOLD) or \
								(self.iteration_change_tracker._previous_reactive_strategy == ReactiveStrategy.SCHEDULED_DISCHARGE_SMOOTH_TRANSITION and target_soc_change == ChangeIndicator.NONE):
								# keep current charge rate untouched.
								# but only enter it, when window progress is >= TRANSITION_STATE_THRESHOLD
								reactive_strategy = ReactiveStrategy.SCHEDULED_DISCHARGE_SMOOTH_TRANSITION
							else:
								# else, we have soc==targetsoc, or soc - discharge_hystersis > targetsoc.
								# In Case of MISSING_TO_BAT, we allow to discharge bellow target soc.
								# Forced discharges are already handled, so we simply let self-consume handle the required amount
								# of discharge here.
								if missing_to_bat:
									reactive_strategy = ReactiveStrategy.SELFCONSUME_ACCEPT_BELOW_TSOC
								else:
									# else we ultimately idle.
									reactive_strategy = ReactiveStrategy.IDLE_MAINTAIN_TARGETSOC

		#bellow here, ReactiveStrategy should be determined. As well as chargerate, if required. If it isn't
		#Enter self consume, as conditions may change and situation will resolve.
		#(This would need to be resolved, there shouldn't be any unpredicted combination of parameters)
		if reactive_strategy is None:
			return ReactiveStrategy.SELFCONSUME_UNPREDICTED
		else:
			#depending on the reactive strategy choosen, system behaviour may be the same - just different value set
			#and/or different reasoning.
			final_chargerate = self.override_chargerate if self.override_chargerate is not None else self.chargerate

			if final_chargerate is None and (reactive_strategy in self.charge_states or reactive_strategy in self.discharge_states):
				# failed to calculate a chargerate. This however is required for charge/discharge.
				# Temporary enter self-consume to keep the system moving, changed conditions may allow for successfull recalculation and
				# getting back on track.
				reactive_strategy = ReactiveStrategy.SELFCONSUME_FAULTY_CHARGERATE

			if reactive_strategy in self.charge_states:
				self.charge_hysteresis = 0 #allow to reach tsoc spot on
				self.discharge_hysteresis = self.hysteresis #avoid discharging on overshoot
				self._device.charge(w.flags, restrictions, abs(final_chargerate), w.allow_feedin)

			elif reactive_strategy in self.selfconsume_states:
				self.charge_hysteresis = self.hysteresis #avoid charge of minor tsoc raise
				self.discharge_hysteresis = 0
				self.chargerate = None #self consume has no chargerate.
				self._device.self_consume(restrictions, w.allow_feedin)

			elif reactive_strategy in self.idle_states:
				self.charge_hysteresis = self.hysteresis #avoid charge on idle soc drop
				self.discharge_hysteresis = 0 #allow follow a controlled discharge
				self.chargerate = None #idle has no chargerate.
				self._idle_feedin = w.allow_feedin #keep track of feedin permission during idle, to be able to react on changes during idle.
				self._is_idle = True
				#idle method is called from within a quicker control loop. (in update_values)

			elif reactive_strategy in self.discharge_states:
				self.charge_hysteresis = self.hysteresis #avoid charging on undershoot.
				self.discharge_hysteresis = 0 #allow to reach tsoc spot on
				#chargerate to be send to discharge method has to be always positive.
				self._device.discharge(w.flags, restrictions, abs(final_chargerate), w.allow_feedin)

			elif reactive_strategy in self.error_selfconsume_states:
				#errorstates are handled outside this method.
				return reactive_strategy

			else:
				#This should never happen, it means that there is a state that is not mapped to a reaction.
				#We enter self consume and use a own state for that :P
				#Doing at least self consume will make the system leave this unmapped state sooner or later for sure and not get stuck.
				return ReactiveStrategy.SELFCONSUME_UNMAPPED_STATE

			return reactive_strategy

	def _disable_pv(self, disabled:bool):
		'''
			Checks, if pv should be enabled or disabled and ensures that state.
		'''
		#if pv shall be disabled, we need to recuringly set that path.
		if disabled:
			self._dbusservice["/Pv/Disable"] = 1
		else:
			#only need to set to 0 once.
			if self._dbusservice["/Pv/Disable"] == 1:
				self._dbusservice["/Pv/Disable"] = 0

	def deactivate(self, reason):
		try:
			self._device.deactivate()
		except AttributeError:
			pass
		self.release_control()
		self.active = 0 # Off
		self.errorcode = reason
		self._disable_pv(False) #enable pv, if it was disabled.
		self._is_idle = False
		self.targetsoc = None
		self._is_idle = False
		self._dbusservice['/DynamicEss/ChargeRate'] = self.chargerate = None
		self._dbusservice['/DynamicEss/Strategy'] = None
		self._dbusservice['/DynamicEss/Restrictions'] = None
		self._dbusservice['/DynamicEss/AllowGridFeedIn'] = None
		self._dbusservice['/DynamicEss/MinimumSoc'] = None

		#disconnect all EVCS we have eventually under control.
		for evcs_id, evcs_delegate in self._evcs_delegates.items():
			if EvcsGxFlags.GX_AUTO_AQUIRED in evcs_delegate.gx_flags:
				evcs_delegate.end()
				#FIXME: Stop Charging?

		#republish evcs states.
		self.publish_evcs_flags()

	def update_values(self, newvalues):
		# Indicate whether this system has DESS capability. Presently
		# that means it has ESS capability.
		try:
			newvalues['/DynamicEss/Available'] = int(self._device.available)
		except AttributeError:
			newvalues['/DynamicEss/Available'] = 0

		# during idling, update the setpoint everytime we receive new values.
		if self.active and self._device is not None and self._is_idle:
			self._device.idle(self._idle_feedin)


