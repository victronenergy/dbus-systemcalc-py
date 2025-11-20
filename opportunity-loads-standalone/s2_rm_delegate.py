import logging
import uuid
import sys
import os
import json
from typing import Dict, Callable
from datetime import datetime, timezone

#Victron packages and dbus
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 'ext', 'velib_python'))
import dbus #type:ignore

from vedbus import VeDbusService
from dbus.mainloop.glib import DBusGMainLoop #type:ignore
from dbusmonitor import AsyncDbusMonitor
from settingsdevice import SettingsDevice
from gi.repository import GLib # type: ignore
from ve_utils import wrap_dbus_value, unwrap_dbus_value

#S2 imports
from s2python.s2_parser import S2Parser
from s2python.version import S2_VERSION
from s2python.s2_control_type import S2ControlType, PEBCControlType, NoControlControlType
from s2python.validate_values_mixin import S2MessageComponent

from s2python.common import (
    ReceptionStatusValues,
    ReceptionStatus,
	ResourceManagerDetails,
    Handshake,
    HandshakeResponse,
    SelectControlType,
	ControlType,
	CommodityQuantity,
	PowerMeasurement,
	Timer
)

from s2python.ombc import (
    OMBCInstruction,
    OMBCOperationMode,
    OMBCStatus,
    OMBCSystemDescription
)

#internals
from phaseawarefloat import PhaseAwareFloat
from helper import ConsumerType
from solar_overhead import SolarOverhead
from globals import(
	KEEP_ALIVE_INTERVAL_S, CONNECTION_RETRY_INTERVAL_MS, USE_FAKE_BMS,
	S2_IFACE,
	C_PRIORITY_MAPPING,
)

logger = logging.getLogger("opportunity-loads")

class S2RMDelegate():
	def __init__(self, monitor, service, instance, rmno, opportunity_loads):
		#General
		self.initialized = False
		self.service = service
		self.instance = instance
		self.rmno = rmno
		self.s2path = "/S2/0"
		self.s2rmpath = "{}/Rm".format(self.s2path)
		self._dbusmonitor = monitor
		self._keep_alive_missed = 0
		self.s2_parser = S2Parser()
		self._commit_count = 0 #to ensure responsibility if consumers don't react.
		self._no_desc_count = 0 #connection will be dropped after 6 updates with no system description.
		self._opportunity_loads=opportunity_loads
		self.current_state_confirmed=True #will be reset, when new instructions are send. 
		self._reported_as_blocked = False
		self.ombc_transition_info = None
		self.unique_identifier = None
		self.technical_identifier = "{}_{}".format(self.service, self.instance)
		
		#Build a static unique_identifier. Has to be unchanged during connection. 
		#If the service has a customname, use that as foundation, else use the service type. 
		custom_name = monitor.get_value(service, "/CustomName", None)

		if custom_name is not None and custom_name != "":
			self.unique_identifier = "{}_{}".format(custom_name.replace(" ", "_") , self.instance)
		else:
			self.unique_identifier = self.technical_identifier

		if USE_FAKE_BMS:
			self._opportunity_loads.available_fake_bms = sorted(self._opportunity_loads.available_fake_bms)
			self._fake_bms_no = self._opportunity_loads.available_fake_bms.pop(0)
			logger.info("{} | Assigned fakebms {} ".format(self.unique_identifier, self._fake_bms_no))

		#power tracking values
		self.power_claim:PhaseAwareFloat=PhaseAwareFloat()
		self.prior_power_claim:PhaseAwareFloat=PhaseAwareFloat()
		self.power_request:PhaseAwareFloat=PhaseAwareFloat()
		self.prior_power_request:PhaseAwareFloat=PhaseAwareFloat()
		self.current_power:PhaseAwareFloat = None

		#Generic Handler
		self._message_receiver=None
		self._disconnect_receiver=None
		self._keep_alive_timer=None
		self._reply_handler_dict:Dict[uuid.UUID, Callable[[ReceptionStatus], None]]={} #TODO Needs handling, when replies are never received?
		
		#Generic value holder
		self.rm_details=None
		self.active_control_type:ControlType=None
		
		#OMBC related stuff.
		self.ombc_system_description = None
		self.ombc_active_instruction = None
		self.ombc_active_operation_mode = None
		self._ombc_next_operation_mode = None

		#TODO: RM can change timer status by sending a OMBC.TimerStatus update. Need a handler for that?
		self.ombc_timers:dict[str, Timer] = {}
		self.ombc_timer_starts:dict[str, datetime] = {}

	@property
	def priority(self) -> float:
		"""
			priority of this consumer
		"""
		if self.unique_identifier in C_PRIORITY_MAPPING.current_value.keys():
			return C_PRIORITY_MAPPING.current_value[self.unique_identifier]
		
		return 100

	@property
	def priority_sort(self) -> float:
		"""
			priority * 1000 of this consumer and secondary sorting by device instance.
		"""
		priority = self.priority
		return (priority * 1000 +  self.instance) if priority is not None else 10000

	@property
	def consumer_type(self) -> ConsumerType:
		"""
			Returns the consumer type. Primary consumers have a higher priority (lower value) than the battery.
		"""
		return ConsumerType.Primary if self.priority < C_PRIORITY_MAPPING.current_value["battery"] else ConsumerType.Secondary

	def publish_fake_bms_values(self):
		"""
			Updates the Fake BMS display option with current values. 
		"""
		try:
			if not self.initialized:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Dc/0/Power", 0)
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Soc", 0)
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "Uninitialized: {}".format(
						self.unique_identifier.replace("com.victronenergy", "")
				))
				return
			
			if self.current_power is not None:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Dc/0/Power", self.current_power.total)
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Soc", 0)
			else:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Dc/0/Power", 0)
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Soc", 0)

			if self.rm_details is not None:
				self._no_desc_count = 0
				# Setting info based on Control Type. 
				if self.active_control_type == ControlType.OPERATION_MODE_BASED_CONTROL:
					if self.ombc_transition_info is None:
						if self.ombc_active_operation_mode is not None:
							self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] @ {}".format(
								self.priority, self.rm_details.name, self.ombc_active_operation_mode.diagnostic_label
							))
					else:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] @ {}".format(
							self.priority, self.rm_details.name, self.ombc_transition_info
						))
				elif self.active_control_type == ControlType.NOT_CONTROLABLE:
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [NOCTRL]".format(
						self.priority, self.rm_details.name
					))
			else:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "NoDesc: {}".format(
						self.unique_identifier.replace("com.victronenergy", "")
				))
				if self._no_desc_count < 6:
					self._no_desc_count += 1
				else:
					logger.warning("{} | Didn't receive a system description by now. Dropping connection.".format(self.unique_identifier))
					self.end()

		except Exception as ex: 
			logger.error("Exception during fake bms publish. This may be temporary", exc_info=ex)

	def begin(self):
		"""
			Initializes the RM, establishes connection, handshake, etc. 
		"""
		self._s2_connect_async()
	
	def end(self):
		"""
			To be called when the RM leaves the dbus or an s2 timeout occurs. 
		"""
		self._s2_send_disconnect()

		if self._message_receiver is not None:
			self._dbusmonitor.dbusConn.remove_signal_receiver(self._s2_on_message_handler, path=self.s2rmpath, signal_name="Message", dbus_interface=S2_IFACE)
			self._message_receiver = None

		if self._disconnect_receiver is not None:
			self._dbusmonitor.dbusConn.remove_signal_receiver(self._s2_on_disconnect_handler, path=self.s2rmpath, signal_name="Disconnect", dbus_interface=S2_IFACE)
			self._disconnect_receiver = None

		if self._keep_alive_timer is not None:
			GLib.source_remove(self._keep_alive_timer)
			self._keep_alive_timer = None

		self.initialized=False
		logger.info("{} | RMDelegate is now uninitialized.".format(self.unique_identifier))

	def _keep_alive_loop(self):
		"""
			Sends the keepalive and monitors for success.
		"""
		def reply_handler(result): 
			if result:
				self._keep_alive_missed = 0
			else:
				self._keep_alive_missed = self._keep_alive_missed + 1	
		
		def error_handler(result):
			self._keep_alive_missed = self._keep_alive_missed + 1

		self._dbusmonitor.dbusConn.call_async(self.service, self.s2rmpath, S2_IFACE, method='KeepAlive', signature='s',
										args=[wrap_dbus_value(self.unique_identifier)],
										reply_handler=reply_handler, error_handler=error_handler)
		
		if self._keep_alive_missed < 2: 
			return True
		else:
			logger.warning("{} | Keepalive MISSED ({})".format(self.unique_identifier, self._keep_alive_missed))
			self.end()
			return False

	def _s2_connect_async(self):
		"""
			Establishes Connection to the RM via S2. 
		"""
		#start to monitor for Signals: Message and Disconnect. Yes, we need to do this, before connection 
		#is successfull, else we have a race-condition on catching the first reply, if any. 
		self._message_receiver = self._dbusmonitor.dbusConn.add_signal_receiver(self._s2_on_message_handler,
			dbus_interface=S2_IFACE, signal_name='Message', path=self.s2rmpath)

		self._disconnect_receiver = self._dbusmonitor.dbusConn.add_signal_receiver(self._s2_on_disconnect_handler,
			dbus_interface=S2_IFACE, signal_name='Disconnect', path=self.s2rmpath)
		
		self._dbusmonitor.dbusConn.call_async(self.service, self.s2rmpath, S2_IFACE, method='Connect', signature='si', 
			args=[wrap_dbus_value(self.unique_identifier), wrap_dbus_value(KEEP_ALIVE_INTERVAL_S)],
			reply_handler=self._s2_connect_callback_ok, error_handler=self._s2_connect_callback_error)

	def _s2_connect_callback_ok(self, result):
		logger.info("{} | S2-Connection established with Keep-Alive {}".format(self.unique_identifier, KEEP_ALIVE_INTERVAL_S))
		
		#Set KeepAlive Timer. 
		self._keep_alive_timer = GLib.timeout_add(KEEP_ALIVE_INTERVAL_S * 1000, self._keep_alive_loop)

		#RM is now ready to be managed.
		self.initialized = True

	def _s2_connect_callback_error(self, result):
		logger.warning("{} | S2-Connection failed. Operation will be retried in {}s: {}".format(self.unique_identifier, CONNECTION_RETRY_INTERVAL_MS, result))
		self.end() #clean handlers and stuff.

	def _s2_on_message_handler(self, client_id, msg:str):
		if self.unique_identifier == client_id:
			#logger.info("Received Message from {}: {}".format(self.unique_identifier, msg))

			jmsg = json.loads(msg)

			#if jmsg["message_type"] != "ReceptionStatus":
			#	logger.debug("Received Message from {}: {}".format(self.unique_identifier, jmsg["message_type"]))

			if "message_type" in jmsg:
				#if client is not initialized, deny all messages, except Handshake.
				if jmsg["message_type"] == "Handshake" or self.initialized:
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
					self._s2_send_reception_message(ReceptionStatusValues.TEMPORARY_ERROR, jmsg["message_id"], "Connection not yet established.")
						
	def _s2_on_power_measurement(self, message:PowerMeasurement):
		self.current_power = PhaseAwareFloat()
		for pv in message.values:
			if pv.commodity_quantity == CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC:
				for c in [CommodityQuantity.ELECTRIC_POWER_L1, CommodityQuantity.ELECTRIC_POWER_L2, CommodityQuantity.ELECTRIC_POWER_L3]:
					self.current_power.by_commodity[c] = pv.value / 3.0
			else:
				self.current_power.by_commodity[pv.commodity_quantity] = pv.value
		
		self._s2_send_reception_message(ReceptionStatusValues.OK, message)

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
		self._ombc_next_operation_mode = None
		self._s2_send_reception_message(ReceptionStatusValues.OK, message)
	
	def _s2_on_ombc_status(self, message:OMBCStatus):
		try:
			for opm in self.ombc_system_description.operation_modes:
				#FIXME: Theres an error with message.active_operation_mode_id in s2-pyhton. fix this, once it was fixed.
				#       Until then, compare root with id.
				if "{}".format(opm.id) == "{}".format(message.active_operation_mode_id.root):
					
					#Confirm, if we have the state confirmed and received a first power report about it. 
					prior_operation_mode = self.ombc_active_operation_mode

					#logger.info("Received OMBC Status Update: {}; Expected next State: {}".format(
					#	opm.diagnostic_label,
					#	self._ombc_next_operation_mode.diagnostic_label if self._ombc_next_operation_mode is not None else "None"
					#))

					if self._ombc_next_operation_mode is not None and opm.id == self._ombc_next_operation_mode.id:
						self.ombc_active_operation_mode = opm
						logger.info("{} | Confirmed next operation mode: '{}'".format(self.unique_identifier, self.ombc_active_operation_mode.diagnostic_label))	
						self.current_state_confirmed=True
						self._commit_count = 0 #reset, we got response. 
					else:
						# status reported without change-request, accept to stay in sync with RM.
						self.ombc_active_operation_mode = opm
						self.current_state_confirmed=True
						self._commit_count = 0 #reset, we got a RM triggered state change.
						self.power_request = PhaseAwareFloat.from_power_ranges(opm.power_ranges)
						self.prior_power_request = PhaseAwareFloat.from_power_ranges(opm.power_ranges)

						logger.info("{} | Reported operation mode: '{}'".format(self.unique_identifier, self.ombc_active_operation_mode.diagnostic_label))

					#Check, if this transition starts any timer. Only required if we leave a well known operation mode. 
					if prior_operation_mode is not None:
						for t in self.ombc_system_description.transitions:
							if t.from_ == prior_operation_mode.id and t.to == self.ombc_active_operation_mode.id:
								#transition found, timer required?
								for tmr in t.start_timers:
									#find the timer we need to start and start it. 
									for tmr_cand in self.ombc_system_description.timers:
										if tmr_cand.id == tmr:
											logger.debug("{} | Transition from '{}' to '{}' causes a timer: '{}'. Timer started.".format(
												self.unique_identifier, prior_operation_mode.diagnostic_label, self.ombc_active_operation_mode.diagnostic_label,
												tmr_cand.diagnostic_label
											))

											self.ombc_timers[tmr] = tmr_cand
											self.ombc_timer_starts[tmr] = datetime.now(timezone.utc)
											break

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

	def _s2_on_rm_details(self, message:ResourceManagerDetails):
		# Detail update. Store to keep information present.
		self.rm_details = message
		if len(message.available_control_types) == 0:
			self._s2_send_reception_message(ReceptionStatusValues.TEMPORARY_ERROR, message,"No ControlType provided.")
			return

		self._s2_send_reception_message(ReceptionStatusValues.OK, message)

		# TODO: Control-Mode-Selection will later depend on the actual System Type. While some ControlTypes offer greater
		#       User convinience, for a offgrid-situation they are not really feasible (long term scheduling)
		# if there is only 1 mode (and that is NOCTRL) we can select that right away. RM doesn't want to be controlled currently. 
		if len(message.available_control_types) == 1 and ControlType.NOT_CONTROLABLE in message.available_control_types:
			def noctrl_reply_handler(reply:ReceptionStatus):
				if reply.status == ReceptionStatusValues.OK:
					self.active_control_type = ControlType.NOT_CONTROLABLE

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

					if USE_FAKE_BMS:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] ".format(
							self.priority, self.rm_details.name
						))

			logger.info("{} | Offered OMBC, accepting.".format(self.unique_identifier))

			if ControlType.OPERATION_MODE_BASED_CONTROL in message.available_control_types:
				self._s2_send_message(
					SelectControlType(
						message_id=uuid.uuid4(),
						control_type=ControlType.OPERATION_MODE_BASED_CONTROL
					), ombc_reply_handler
				)
				
			else:
				#Any Other controltype is currenetly not implemented, we just can reject. 
				logger.error("{} | Offered no compatible ControlType. Rejecting request.".format(self.unique_identifier))
				self._s2_send_reception_message(ReceptionStatusValues.PERMANENT_ERROR, "No supported ControlType offered.")

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

	def _s2_on_disconnect_handler(self, client_id, reason):
		if self.unique_identifier == client_id:
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
			self._dbusmonitor.dbusConn.call_async(self.service, self.s2rmpath, S2_IFACE, method='Message', signature='ss', 
					args=[wrap_dbus_value(self.unique_identifier), wrap_dbus_value(message.to_json())], 
					reply_handler=None, error_handler=None)
		except Exception as ex:
			logger.error("Error sending a S2 Message.", exc_info=ex)
			logger.error("Mesesage was: {}".format(message.model_dump()))
			del self._reply_handler_dict[message.model_dump()["message_id"]]
	
	def _s2_send_disconnect(self):
		"""
			Sends a disconnect message to the RM. Will use fire and forget, as we don't
			care about if the message is receiving the rm, nor what he has to say about it. 
		"""
		try:
			logger.warning("{} | Sending disconnect.".format(self.unique_identifier))
			self._dbusmonitor.dbusConn.call_async(self.service, self.s2rmpath, S2_IFACE, method='Disconnect', signature='s', 
					args=[wrap_dbus_value(self.unique_identifier)], 
					reply_handler=None, error_handler=None)
		except Exception as ex:
			logger.error("Error sending a S2 Message.", exc_info=ex)

	def self_assign_overhead(self, overhead:SolarOverhead) -> SolarOverhead:
		"""
			RM Delegate is claiming power that matches it's requirements.
			RMDelegate is waiting for comit() of EMS, before sending new instructions to RM.
		"""
		try:
			self.prior_power_claim = PhaseAwareFloat.from_phase_aware_float(self.power_claim) if self.power_claim is not None else None

			#The prior power request cannot be derived from the current power request. 
			#The commit may have been aborted. So all that matters is the power request of the "active" state.
			if self.ombc_active_operation_mode is not None:
				self.prior_power_request = PhaseAwareFloat.from_power_ranges(self.ombc_active_operation_mode.power_ranges)
			else:
				self.prior_power_request = None
			self.power_claim = None
			
			#based on control type, this is different.
			if self.active_control_type == ControlType.OPERATION_MODE_BASED_CONTROL:
				return self._ombc_self_assign_overhead(overhead)
								
		except Exception as ex:
			logger.error("Exception during Power assignment. This may be temporary", exc_info=ex)
			overhead.rollback() #restore state before claiming power values. 

		return overhead
	
	@property
	def expected_power_change(self):
		'''
			Compares the current and last power_request and judges, how big the power change of this 
			consumer will be this round. Required to determine order of comiting changes across delegates.
		'''
		if (self.prior_power_request is None and self.power_request is None):
			return 0
		
		if (self.prior_power_request is None and self.power_request is not None):
			return self.power_request.total
		
		if (self.prior_power_request is not None and self.power_request is None):
			return self.prior_power_request.total * -1
		
		return self.power_request.total - self.prior_power_request.total

	def _ombc_self_assign_overhead(self, overhead:SolarOverhead) -> SolarOverhead:
		#reset values that need to be determined freshly.
		self.ombc_transition_info = None

		#check all Operation modes, and if one fits. op modes have been sorted
		#when retrieved, so first one is most expensive and should be selected
		#if possible. 
		if self.ombc_system_description is None:
			logger.warning("{} | No System Description available".format(self.unique_identifier))	
			return overhead
		
		if self.ombc_active_operation_mode is None:
			logger.warning("{} | No active operation mode known".format(self.unique_identifier))	
			return overhead

		#Not every state may be reachable from within the current operation mode. 
		#So, what we will do here is: 
		# 1.) Get all States that are reachable or equal current state. 
		# 2.) They are sorted expensive to cheap, so for self-consumption-optimization, we start probing the most expensive sate. 
		# 3.) If we couldn't find any suitable state in 0 to n-2, we have to force state n-1 as that means: 
		#      - There isn't enough overhead to enter more expensive states. 
		#      - There isn't enough overhead to keep the current state. 
		#      - hence, the last state in the list - cheapest one - is the one we will choose. 
		eligible_operation_modes:list[OMBCOperationMode] = []
		for opm in self.ombc_system_description.operation_modes:
			if self._ombc_can_transition(self.ombc_active_operation_mode, opm):
				eligible_operation_modes.append(opm)

		logger.debug("Eligible States: {}".format([mode.diagnostic_label for mode in eligible_operation_modes]))

		if len(eligible_operation_modes) == 0:
			logger.error("{} | No valid operationmodes to choose from. Active is: {} / Selection is: {}".format(
				self.unique_identifier, 
				"{}=>{}".format(self.ombc_active_operation_mode.diagnostic_label, self.ombc_active_operation_mode.id) if self.ombc_active_operation_mode is not None else "None",
				["{}=>{}".format(mode.diagnostic_label, mode.id) for mode in self.ombc_system_description.operation_modes])
			)
			return overhead
		
		#this is our last resort.
		forced_state = eligible_operation_modes[len(eligible_operation_modes) -1]
		logger.debug("Forced State: {}".format(forced_state.diagnostic_label))

		for opm in eligible_operation_modes:
			#combine all power ranges into a power_request. To determine if we need to consider
			#a state at all, we need the min request a state could have - and see if that could fit.
			#force state needs to be evaluated always.
			power_request_min = PhaseAwareFloat.from_power_ranges(opm.power_ranges, True)
			power_request_max = PhaseAwareFloat.from_power_ranges(opm.power_ranges)
		
			if (power_request_min.total > overhead.power.total and not opm.id == forced_state.id):
				logger.debug("Skipping detailed check on '{}'. {}W vs {}W raw available won't fit for sure.".format(
					opm.diagnostic_label, power_request_min.total, overhead.power.total
				))
				continue
			
			# minimum request is at least smaller than total available. It may fit, depending on conversion losses, it may not.
			overhead.begin()

			#if we have min.total = max.total it's the easy part. all phases request a fixed amount.
			claim_success = False
			if power_request_min.total == power_request_max.total:
				claim_success = overhead.claim(power_request_max, self.consumer_type==ConsumerType.Primary, opm.id == forced_state.id)
			else:
				claim_success = overhead.claim_range(power_request_min, power_request_max, self.consumer_type==ConsumerType.Primary, opm.id == forced_state.id)
			
			if not claim_success:
				#maximum assignment for this powerrange failed for at least one powerrange requested. This OperationMode is currently not eligible. 
				logger.debug("Operation Mode not eligible: '{}'".format(opm.diagnostic_label))
				overhead.rollback()
			
			else:
				#Probe, if we are trapped in a transition timer, then we cannot do it anyway. 
				if self._ombc_check_timer_block(opm) == 0:
					#all good, commit. Deduct from budget, what we claim. 
					new_power_claim = overhead.comit()
					self.power_request = power_request_min #FIXME: What to do here in case of range-requests?
					self.power_claim = new_power_claim
					
					logger.debug("Operation Mode selected: '{}'. (Power-Claim: {})".format(opm.diagnostic_label, new_power_claim))

					#store this operation_mode as beeing the next one to be send. EMS will call comit() on the RM-Delegate, 
					#once it should inform the actual RM and send out a new instruction, if required. RM-Delegate has to 
					#track if a (re-)send is required. 
					self._ombc_next_operation_mode = opm
	
				else:
					# cannot change, trapped in timer. Thus, we need to revert the overhead
					# and lower it by the consumers active claim (if any)
					overhead.rollback()
					self.power_request = PhaseAwareFloat.from_phase_aware_float(self.prior_power_request)

					# FIXME: Three things to think/fix on overhead budget: 
					#        When a device is running and reporting power, the claim should only be lowerd by the actual power required. 
					#        When a device is supposed to turn off, but stuck in an off hysteresis, it's power claim should remain valid,
					#          preventing lower priority consumers from eventually already turning on. 
					#        When a device is supposed to turn on, but stuck in an on hysteresis, it's power claim should already be considered valie,
					#          preventing lower priority consumers from eventually turning on for a split moment. 
					if (self.power_claim is not None):
						overhead.power -= self.power_claim

				return overhead
		
		logger.warning("{} | Checked all operation modes, none is eligible. This should never happen!".format(self.unique_identifier))
		return overhead

	def _ombc_can_transition(self, active_operation_mode:OMBCOperationMode, candidate:OMBCOperationMode)->bool:
		"""
			Checks, if the transition from active_operation_mode to candidate is teoretically possible,
			i.e. if a transition exists. Then this mode can be selected. Before transitioning however,
			blocking timers need to be validated using _ombc_check_timer_block.

			Transitioning from state X to state X is considered always allowed - that means, keep current operation mode. 
		"""
		if active_operation_mode.id == candidate.id:
			return True
			
		for t in self.ombc_system_description.transitions:
			if t.from_ == active_operation_mode.id and t.to == candidate.id:
				return True
		
		return False

	def _ombc_check_timer_block(self, target_operation_mode:OMBCOperationMode) -> float:
		"""
			Checks if there is a blocking timer, if there is, returns the amount of seconds to go. 
		"""
		#if the current mode is unknown, we have no block. 
		#if the next operation mode is unknown, it's no block at all.
		if self.ombc_active_operation_mode is None or target_operation_mode is None:
			self.ombc_transition_info = None
			return 0
		
		#attempting to transist between 2 operation modes. See, if there is a defined transition
		timer_to_invalidate = []
		seconds_remaining = 0
		for t in self.ombc_system_description.transitions:
			if t.from_ == self.ombc_active_operation_mode.id and t.to == target_operation_mode.id:
				#transition found, timer required?
				if len(t.blocking_timers) > 0:
					#yes, at least one blocking timer. Do we have timers running at all?
					if len(self.ombc_timers) > 0:
						#at least one timer is running. Check the blocking timers against the running timers and if they may have expired already.
						for blocking_timer_id in t.blocking_timers:
							for running_timer_id, running_timer in self.ombc_timers.items():
								if blocking_timer_id == running_timer_id:
									#this one is potentially blocking. Check, if it is still active. 
									if self.ombc_timer_starts[blocking_timer_id] + running_timer.duration.to_timedelta() <  datetime.now(timezone.utc):
										#timer is expired. Schedule for removel, it's non blocking anymore.
										timer_to_invalidate.append(blocking_timer_id)
									else:
										seconds_remaining = round(((self.ombc_timer_starts[blocking_timer_id] + running_timer.duration.to_timedelta()) 
									 		- datetime.now(timezone.utc)).total_seconds(),0)
										
										self.ombc_transition_info = "{} -> {} ({}s)".format(
											self.ombc_active_operation_mode.diagnostic_label, 
											target_operation_mode.diagnostic_label, seconds_remaining
										)
										
										logger.debug("{} | Timer '{}' is preventing to transition from '{}' to '{}' currently. ({}s)".format(
											self.unique_identifier, running_timer.diagnostic_label, self.ombc_active_operation_mode.diagnostic_label, 
											target_operation_mode.diagnostic_label, seconds_remaining
										))
		
		#sanitize timers.
		for id in timer_to_invalidate:
			del self.ombc_timers[id]
			del self.ombc_timer_starts[id]

		return seconds_remaining

	def comit(self) -> bool:
		"""
			To be called, when all consumers have claimed their power share. If no new instruction is required 
			for the rm, there will be none. 
		"""
		if self.active_control_type == ControlType.OPERATION_MODE_BASED_CONTROL:
			#Transitioning may be based on timers. So, check if our transition is suspect to be delayed currently. 
			if self._ombc_next_operation_mode is not None and self._ombc_next_operation_mode.id != self.ombc_active_operation_mode.id:
				#send out op mode selection, as operation mode changed. 
				self.current_state_confirmed=False
				self.ombc_active_instruction = OMBCInstruction(
					message_id = uuid.uuid4(),
					id = uuid.uuid4(),
					execution_time= datetime.now(timezone.utc),
					operation_mode_factor=1.0, #TODO: This needs to be adjusted, along with the factor determined by power allocation. 
					operation_mode_id= self._ombc_next_operation_mode.id,
					abnormal_condition=False
				)

				logger.info("{} | Instruction send: OMBC = {} (Power-Claim: {})".format(self.unique_identifier, self._ombc_next_operation_mode.diagnostic_label, self.power_claim))

				self._s2_send_message(self.ombc_active_instruction)

				self._commit_count += 1
				#This has to be confirmed by the resource-manager, not assume it "worked".
				#self.ombc_active_operation_mode = self._ombc_next_operation_mode

				if self._commit_count >= 7:
					#Consumer is not reacting. That is odd. Only escape we have is to drop off and reconnect. 
					logger.warning("{} | RM didn't respond after 6 commits. Assuming stale, disconnecting.".format(self.unique_identifier))
					self.end()
					return False

				return True
			
			else:
				logger.warning("{} | Comit called, but current state equals desired state or next mode is none: {}->{}".format(
					self.unique_identifier, 
					self.ombc_active_operation_mode.diagnostic_label if self.ombc_active_operation_mode is not None else "None",
					self._ombc_next_operation_mode.diagnostic_label if self._ombc_next_operation_mode is not None else "None"
					))

		else:
			logger.warning("{} | No comit logic implemented for Control Type: {}".format(self.unique_identifier, self.active_control_type.name if self.active_control_type is not None else "None"))

		return False
