#!/usr/bin/python3 -u
# -*- coding: utf-8 -*-

#core imports
import sys
import json
import os
import argparse
import logging

from datetime import datetime, timezone
from time import time
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, cast, Callable

#Victron packages and dbus
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 'ext', 'velib_python'))
from logger import setup_logging
import dbus #type:ignore
from vedbus import VeDbusService
from dbus.mainloop.glib import DBusGMainLoop #type:ignore
from dbusmonitor import AsyncDbusMonitor
from settingsdevice import SettingsDevice
from gi.repository import GLib # type: ignore
from ve_utils import wrap_dbus_value, unwrap_dbus_value

#s2
from s2python.common import (
	ControlType
)

#internals
from phaseawarefloat import PhaseAwareFloat
from solar_overhead import SolarOverhead
from s2_rm_delegate import S2RMDelegate
from helper import (
	Modes,
	ConsumerType,
	SystemTypeFlag,
	ClaimType,
	Configurable
)
from globals import (
	USE_FAKE_BMS, AC_DC_EFFICIENCY,
	S2_IFACE,INVERTER_LIMIT_MONITOR_INTERVAL_MS, CONNECTION_RETRY_INTERVAL_MS,
	CONFIGURABLES,
	C_BALANCING_THRESHOLD,
	C_MODE, C_PRIORITY_MAPPING,
	C_CONTROL_LOOP_INTERVAL,
	C_CONTINIOUS_INVERTER_POWER,
	C_RESERVATION_DECREMENT, 
	C_RESERVATION_BASE_POWER,
	C_RESERVATION_EQUATION
)

#debug purpose.
log_dir = "/data/log/opportunity-loads"    
if not os.path.exists(log_dir):
	os.mkdir(log_dir)

class OpportunityLoads():

	def __init__(self):
		self.system_type_flags = SystemTypeFlag.None_
		self.managed_rms: Dict[str, S2RMDelegate] = {}
		self.rms_to_drop: list[str] = []
		self.continuous_inverter_power_per_phase = None #calculated after determining the systemtype.

		#consumption counters and momentary power values
		self.power_primary:PhaseAwareFloat = PhaseAwareFloat()
		self.power_secondary:PhaseAwareFloat = PhaseAwareFloat()
		self.counter_primary:PhaseAwareFloat = PhaseAwareFloat()
		self.counter_secondary:PhaseAwareFloat = PhaseAwareFloat()
		self.dcpv_balancing_offset:float = 0

		if USE_FAKE_BMS:
			self.available_fake_bms = [1,2,3,4,5,6,7,8,9]

		#Create Settings device and dbus service
		self._settings = self._init_settings()

		#initialize configurables with eventually stored settings. 
		for c in CONFIGURABLES:
			try:
				v = self._settings[c.settings_key]
				logger.info("Loading setting {}:{}".format(c.settings_key, v))
				if v is not None:
					c.current_value = v
			except Exception as ex:
				logger.error("Ex", exc_info=ex)
				logger.warning("Couldn't load setting for Configurable {}:{}; Fine if not yet persisted something.".format(c.settings_key, c.settings_path))

		self._dbusservice = self._init_dbus_service()

		

		#spin up dbusmonitor for some stuff we need to monitor for operation.
		

		self._dbusmonitor = self._init_dbus_monitor()
		
		

		self.system_type_flags = self._determine_system_type_flags()

		#enable, if setting indicates enabled. 
		if C_MODE.current_value == 1:
			self._enable()
		else:
			self._disable()

	def _init_settings(self):
		supported_settings = {}
		for c in CONFIGURABLES:
			supported_settings[c.settings_key] = [c.settings_path, c.default_value, c.min_value, c.max_value]

		bus = dbus.SessionBus(private=True) if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus(private=True)
		return SettingsDevice(bus, supported_settings, self._on_settings_changed, timeout=10)

	def _init_dbus_monitor(self):
		s2_topic_list = []
		s2_topic_list.append('/CustomName')

		dbus_spec = {
			'com.victronenergy.settings' : [
				'/Settings/CGwacs/Hub4Mode',
				'/Settings/CGwacs/OvervoltageFeedIn'
			],
			'com.victronenergy.system' : [
				'/ActiveBmsService',
				'/Dc/Battery/Soc',
				'/Dc/Battery/Power',
				'/Dc/Pv/Power',
				'/Ac/Grid/NumberOfPhases',
				'/Ac/ConsumptionOnOutput/NumberOfPhases',
				'/Ac/Consumption/L1/Power',
				'/Ac/Consumption/L2/Power',
				'/Ac/Consumption/L3/Power',
				'/Ac/ActiveIn/GridParallel',
				'/Ac/PvOnOutput/L1/Power',
				'/Ac/PvOnOutput/L2/Power',
				'/Ac/PvOnOutput/L3/Power',
				'/Ac/PvOnGrid/L1/Power',
				'/Ac/PvOnGrid/L2/Power',
				'/Ac/PvOnGrid/L3/Power',
				'/DynamicEss/ChargeRate',
				'/DynamicEss/ReactiveStrategy'
			],
			'com.victronenergy.switch' : s2_topic_list,
			'com.victronenergy.acload' : s2_topic_list,
			'com.victronenergy.battery' : [
				'/CustomName',
				'/Info/MaxChargeCurrent',
				'/Info/MaxChargeVoltage'
			]
		}
		dbus_tree = {}
		dummy = {'code': None, 'whenToLog': 'configChange', 'accessLevel': None}
		for service, arr in dbus_spec.items():
			dbus_tree[service] = {}
			for path in arr:
				dbus_tree[service][path] = dummy

		monitor = AsyncDbusMonitor(dbus_tree,
			valueChangedCallback=self._on_dbus_external_value_changed,
			deviceAddedCallback=self._on_device_added,
			deviceRemovedCallback=self._on_device_removed)
		
		return monitor

	def _init_dbus_service(self):
		dbusservice = VeDbusService('com.victronenergy.opportunityloads', register=False)
		dbusservice.add_mandatory_paths(
			processname=__file__,
			processversion="1.0",
			connection='data from other dbus processes',
			deviceinstance=0,
			productid=None,
			productname=None,
			firmwareversion="1.0",
			hardwareversion=None,
			connected=1)
		
		#Output Paths we use. 
		dbusservice.add_path('/Active', value=0, gettextcallback=lambda p, v: Modes(v))
		dbusservice.add_path('/BatteryReservation', value=0)
		dbusservice.add_path('/BatteryReservationState', value=None)
		dbusservice.add_path('/SystemTypeFlags', value=0)
		dbusservice.add_path('/AvailableServices', value="[]", writeable=True, onchangecallback=self._on_dbus_own_value_changed) #empty json array to start with.
		dbusservice.add_path('/PrimaryConsumer/Ac/Power', value=None)
		dbusservice.add_path('/SecondaryConsumer/Ac/Power', value=None)

		for l in [1,2,3]:
			dbusservice.add_path('/PrimaryConsumer/Ac/L{}/Power'.format(l), value=None)
			dbusservice.add_path('/SecondaryConsumer/Ac/L{}/Power'.format(l), value=None)
		
		#Configurables may produce a Output/Input Path as well. Configurables are writable as per definition. 
		for c in CONFIGURABLES:
			if c.system_path is not None:
				dbusservice.add_path(c.system_path, value=c.current_value or c.default_value, writeable=True, onchangecallback=self._on_dbus_own_value_changed)	

		#register service
		dbusservice.register()

		return dbusservice

	def _on_dbus_external_value_changed(self, service, path, dict, changes, instance):
		pass

	def _on_dbus_own_value_changed(self, path, value):
		"""
			Callback, if one of our writeable system paths is changed.
		"""
		try:
			logger.info("dbus-change on {} detected: {}".format(path, value))
			for c in CONFIGURABLES:
				if c.system_path is not None:
					if c.system_path == path:
						if c.current_value != value:
							logger.info("Config change request detected: {} -> {}".format(c.system_path, value))
							#just update settings device. it'll push back and update the Configurable.
							self._settings[c.settings_key] = value
							return True

			#Also, sorting of the priorities may have changed, which is not a configurable item.
			if  path == "/AvailableServices":
				if isinstance(value, str):
					value = json.loads(value)

					logger.info("Available Services resorted")
					logger.info("Raw Value: {}".format(value))

					p=0
					for obj in value:
						uid = obj["uniqueIdentifier"]
						C_PRIORITY_MAPPING.current_value[uid] = p
						p +=1

					#required cause we modify existing value.	
					C_PRIORITY_MAPPING.force_write(self._settings)

					#republish result?!	
					self.publish_available_services()

					#reject acceptance, we got that covered.
					return False
				
				#writeback
				return True
			
		except Exception as e:
			logger.error("Error during setting change. Rejecting.", exc_info=e)
		
		return False

	def _on_device_added(self, service, instance, *args):
		logger.debug("Device added: {}".format(service))
		i = 0
		while True:
			s2_rm_exists = self._check_s2_rm(service, "/S2/0/Rm")

			if s2_rm_exists:
				delegate = S2RMDelegate(self._dbusmonitor, service, instance, i, self)
				self.managed_rms[delegate.technical_identifier] = delegate
				logger.info("{} | Identified S2 RM on {}. Added to managed RMs".format(delegate.unique_identifier, service))
				delegate.begin()
			
			i += 1 #probe next one.
			
			#if we don't find anything within 10 rms, stop scanning. 
			#for now, there will only be 1 rm per service. So, leave the loop, just break for now.
			if (i >= 1):
				break
		
		#let config ui know, if something changed. 
		self.publish_available_services()

	def _on_device_removed(self, service, instance):
		logger.debug("Device removed: {}".format(service))

		#check, if this service provided one or multiple rm, we have been controlling. 
		known_rms = list(self.managed_rms.keys()) 
		for key in known_rms:
			if key.startswith(service):
				if USE_FAKE_BMS:
					if self.managed_rms[key]._fake_bms_no not in self.available_fake_bms:
						no = self.managed_rms[key]._fake_bms_no
						self.available_fake_bms.append(no)
				
				self.managed_rms[key].end()

				#if device is gone, remove it as managed rm. 
				del self.managed_rms [key]
		
		#let config ui know, if something changed. 
		self.publish_available_services()

	def _on_settings_changed(self, setting, oldvalue, newvalue):
		#generic setting handling
		for c in CONFIGURABLES:
			if c.settings_key == setting:
				c.current_value = newvalue

				#write back to system path, if that's not the origin of the change.
				if self._dbusservice[c.system_path] != newvalue:
					self._dbusservice[c.system_path] = newvalue

				break
		
		#some dedicated handling to make sure immediate effect.
		if setting == C_MODE.settings_key:
			if oldvalue == 0 and newvalue == 1:
				self._enable()
			if oldvalue == 1 and newvalue == 0:
				self._disable()
		
		#accept change
		return True

	def _on_timer_retry_connection(self):
		'''
			Retries connection to RMs that are currently in an unitialized state. 
		'''
		try:
			for technical_identifier, delegate in self.managed_rms.items():
				if not delegate.initialized:
					logger.info("{} | Retrying connection".format(delegate.unique_identifier))
					delegate.begin()
			
			return True
		except Exception as ex:
			logger.error("Exception while retrying connection. Skipping attempt.", exc_info=ex)

	def _on_timer_track_power(self):
		try:
			self.power_primary = PhaseAwareFloat()
			self.power_secondary = PhaseAwareFloat()

			for technical_identifier, delegate in self.managed_rms.items():
				if delegate.initialized:
					if delegate.current_power is not None:
						if delegate.consumer_type == ConsumerType.Primary:
							self.power_primary += delegate.current_power

						elif delegate.consumer_type == ConsumerType.Secondary:
							self.power_secondary += delegate.current_power
			
			#dump on dbus
			for l in [1,2,3]:
				self._dbusservice["/PrimaryConsumer/Ac/L{}/Power".format(l)] = self.power_primary.by_phase[l]
				self._dbusservice["/SecondaryConsumer/Ac/L{}/Power".format(l)] = self.power_secondary.by_phase[l]

			self._dbusservice["/PrimaryConsumer/Ac/Power"] = self.power_primary.total
			self._dbusservice["/SecondaryConsumer/Ac/Power"] = self.power_secondary.total

		except Exception as ex:
			logger.error("Exception while publishing power records", exc_info=ex)

		return True

	def _on_timer_check_inverter_limits(self):
		'''
			The regular control loop is taking care to distribute power in a way, that each inverter operates
			at the desired continious power at maximum. Dropping solar production (ACPV) or raising consumption may cause
			this limit to be exceeded anyway. Thus, this loop is rapidly observing the desired limit and ensures
			loads are shedded, when the limit is exceeded. 
		'''
		# Monitor the total Inverting Power of each phase. This shall not exceed the continious inverter power. 
		# If it does, trigger an immediate re-calculation in order to drop some loads. Also monitor for overload alerts, 
		# if that is the case, pro-actively drop loads, ignoring the off_hysteresis to ensure system-stability.
		# TODO: Implement?
		pass

	def _on_timer(self):
		try:
			logger.debug("v------------------- LOOP -------------------v")
			# Control loop timer.
			now = datetime.now(timezone.utc)

			self.system_type_flags = self._determine_system_type_flags()
			logger.debug("System Type Flags are: {}".format(SystemTypeFlag.to_str(self.system_type_flags)))

			if SystemTypeFlag.None_ == self.system_type_flags:
				logger.warning("Unknown SystemTypeFlags. Doing nothing.")
				return True

			available_overhead = self._get_available_overhead()

			logger.debug("SOC={}%, RSRV={}/{}W ({}), L1o={}W, L2o={}W, L3o={}W, dcpvo={}W, totalo={}W".format(
					self.soc,
					available_overhead.battery_rate,
					self.get_current_battery_reservation(),
					self._dbusservice["/BatteryReservationState"],
					available_overhead.power.l1,
					available_overhead.power.l2,
					available_overhead.power.l3,
					available_overhead.power.dc,
					available_overhead.power.total,
				)
			)

			if USE_FAKE_BMS:
				try:
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Dc/0/Voltage", available_overhead.power.total)

					if available_overhead.battery_rate > -1 and available_overhead.battery_rate < 1:
						#0 will make the power value be calculated. avoid that.
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Dc/0/Power", 1)
					else:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Dc/0/Power", available_overhead.battery_rate)
									
					if available_overhead.battery_reservation > 0:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Soc", available_overhead.battery_rate / available_overhead.battery_reservation * 100.0)
					else:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Soc", 0)
				except ex:
					logger.error("E", exc_info=ex)

			#only iterate when we have solar-overhead, OR EMS-caused consumption (then we may need to turn a consumer off.)
			if (available_overhead.power.total > 0 or 
	   			(self._dbusservice["/PrimaryConsumer/Ac/Power"] or 0) > 0 or 
				(self._dbusservice["/SecondaryConsumer/Ac/Power"] or 0) > 0):
				
				#Iterate over all known RMs, check their requirement and assign them a suitable Budget. 
				#The RMDelegate is responsible to communicate with it's rm upon .comit() beeing called. 
				#sort RMs by priority before iterating.
				for technical_identifier, delegate in sorted(self.managed_rms.items(), key=lambda i: i[1].priority_sort):
					logger.debug("=============================================================================================================")  
					if delegate.initialized and delegate.rm_details is not None:
						if delegate.active_control_type is not None and delegate.active_control_type != ControlType.NOT_CONTROLABLE:
							logger.debug("===== RM {} ({}) is controllable: {} =====".format(delegate.unique_identifier, delegate.rm_details.name, delegate.active_control_type))	
							available_overhead = delegate.self_assign_overhead(available_overhead)
							logger.debug("==> Remaining overhead: {};".format(available_overhead))
						else:
							logger.debug("===== RM {} ({}) is uncontrollable: {} =====".format(delegate.unique_identifier, delegate.rm_details.name, delegate.active_control_type))	
					else:
						logger.debug("===== RM {} is not yet initialized. =====".format(delegate.unique_identifier))

				#Debug situation. Let's dump some informations about EACH RM: 
				#logger.info("  ----")
				#for technical_identifier, delegate in sorted(self.managed_rms.items(), key=lambda i: i[1].priority_sort ):
				#	logger.info("  Id({}), Prio({}, Active({}), Next({}), Change({}), Pr({}->{}))".format(
				#		delegate.unique_identifier,
				#		delegate.priority_sort,
				#		delegate.ombc_active_operation_mode.diagnostic_label if delegate.ombc_active_operation_mode is not None else "None",
				#		delegate._ombc_next_operation_mode.diagnostic_label if delegate._ombc_next_operation_mode is not None else "None",
				#		delegate.expected_power_change,
				#		delegate.prior_power_request.total if delegate.prior_power_request is not None else "None",
				#		delegate.power_request.total if delegate.power_request is not None else "None",
				#	))

				# Check, if there is a pending change on one RM. If so, we don't do anything until it's confirmed. 
				# Ask the RM to kindly recomit the change, in case the consumer did miss it. If it will be missed 6 times,
				# The offendinc consumer will be dropped. 
				state_change_pending = False
				for technical_identifier, delegate in self.managed_rms.items():
					if not delegate.current_state_confirmed and delegate.initialized and delegate._ombc_next_operation_mode is not None :
						delegate.comit()
						logger.warning("{} | State change to '{}' pending. Skipping change comits but forcing a re-comit.".format(
							delegate.unique_identifier, 
							delegate._ombc_next_operation_mode.diagnostic_label if delegate._ombc_next_operation_mode is not None else "UNKNOWN"
						))
						state_change_pending = True

				#each consumer may have a change-plan now. Commit only one per iteration to give 
				#the system time to react. If one change is pending, we don't commit anything.
				#to improve expected behaviour on consumer switching, this happens in two iterations: 
				# first, off commands are processed in low to high priority order. 
				# second, on commands are processed in high to low priority order.  
				global_was_change = False
				if not state_change_pending:
					# debug
					for technical_identifier, delegate in sorted(self.managed_rms.items(), key=lambda i: i[1].priority_sort * -1):
						if delegate.initialized and delegate.expected_power_change < 0:
							logger.info("{} | Pending transition to a lower energy state: {}".format(delegate.unique_identifier, delegate.expected_power_change))
					
					for technical_identifier, delegate in sorted(self.managed_rms.items(), key=lambda i: i[1].priority_sort):
							if delegate.initialized and delegate.expected_power_change > 0:
								logger.info("{} | Pending transition to a higher energy state: +{}".format(delegate.unique_identifier, delegate.expected_power_change))

					for technical_identifier, delegate in sorted(self.managed_rms.items(), key=lambda i: i[1].priority_sort * -1):
						if delegate.initialized and delegate.expected_power_change < 0:
							was_change = delegate.comit()

							if (was_change):
								logger.info("{} | Comited change to transition to a lower energy state ({}). Ending comit round.".format(delegate.unique_identifier, delegate.expected_power_change))
								global_was_change = True
								break

					if not global_was_change:
						for technical_identifier, delegate in sorted(self.managed_rms.items(), key=lambda i: i[1].priority_sort):
							if delegate.initialized and delegate.expected_power_change > 0:
								was_change = delegate.comit()

								if (was_change):
									logger.info("{} | Comited change to transition to a higher energy state (+{}). Ending comit round.".format(delegate.unique_identifier, delegate.expected_power_change))
									break

				logger.debug("SOC={}%, RSRV={}/{}W ({}), L1o={}W, L2o={}W, L3o={}W, dcpvo={}W, totalo={}W".format(
						self.soc,
						available_overhead.battery_rate,
						self.get_current_battery_reservation(),
						self._dbusservice["/BatteryReservationState"],
						available_overhead.power.l1,
						available_overhead.power.l2,
						available_overhead.power.l3,
						available_overhead.power.dc,
						available_overhead.power.total,
					)
				)
			else:
				logger.debug("ZzZzZzz...")

			if USE_FAKE_BMS:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Dc/0/Current", available_overhead.power.total)
				
				for technical_identifier, delegate in self.managed_rms.items():
					delegate.publish_fake_bms_values()

			#drop any RM, if we have to.
			for rm_to_drop in self.rms_to_drop:
				if rm_to_drop in self.managed_rms.keys():
					logger.warning("{} | Dropping RM from managed RMs.".format(rm_to_drop))
					
					if USE_FAKE_BMS:
						if self.managed_rms[rm_to_drop]._fake_bms_no not in self.available_fake_bms:
							no = self.managed_rms[rm_to_drop]._fake_bms_no
							self.available_fake_bms.append(no)
			
			#clean drop list, gone by now.
			self.rms_to_drop = []
				
			#reset unused fake BMS any time (for now, debug only)
			if USE_FAKE_BMS:
				for no in self.available_fake_bms:
					#reset that fake BMS to defaults.
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(no), "/Dc/0/Power", 0.0)
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(no), "/Dc/0/Soc", 0)
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(no), "/CustomName", "EMS Fake BMS {}".format(no))

			now2 = datetime.now(timezone.utc)
			duration = (now2 - now).total_seconds() * 1000
			
			logger.debug("^------------------- LOOP -------------------^")

			if (C_MODE.current_value== 1):
				return True	#keep timer up as long as mode is enabled.
		except Exception as ex:
			logger.fatal("Exception during control loop", exc_info=ex)
			return True #keep the loop runing, this may resolve.

		#terminate timer
		return False

	def _enable(self):
		'''
			Enables EMS.
		'''
		self._timer = GLib.timeout_add(C_CONTROL_LOOP_INTERVAL.current_value * 1000, self._on_timer) #regular control loop according to configuration.
		self._limit_timer = GLib.timeout_add(INVERTER_LIMIT_MONITOR_INTERVAL_MS, self._on_timer_check_inverter_limits) #quick monitoring of desired inverter limitations
		self._timer_track_power = GLib.timeout_add(1000, self._on_timer_track_power)
		self._timer_retry_connections = GLib.timeout_add(CONNECTION_RETRY_INTERVAL_MS, self._on_timer_retry_connection) #retry connection to devices periodically.
		self._dbusservice["/Active"] = 1
		logger.info("EMS activated with a control loop interval of {}s".format(C_CONTROL_LOOP_INTERVAL.current_value))

	def _disable(self):
		'''
			Disables EMS.
		'''
		self._dbusservice["/Active"] = 0
		logger.info("EMS deactivated.")

	def _check_s2_rm(self, serviceName, objectPath)->bool:
		"""
			Checks if the provided service offers an S2 Resource Manager.
		"""
		try:
			self._dbusmonitor.dbusConn.call_blocking(serviceName, objectPath, S2_IFACE, 'Discover', '', [])
			return True
		except dbus.exceptions.DBusException:
			return False

	def _determine_system_type_flags(self) -> SystemTypeFlag:
		'''
			Determines relevant system type flags required for operation. 
		'''
		system_type_flags = SystemTypeFlag.None_
		try:
			no_phases_grid = self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/Grid/NumberOfPhases")
			no_phases_output = self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/ConsumptionOnOutput/NumberOfPhases")
			grid_parallel = self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/ActiveIn/GridParallel")
			multiphase_mode = self._dbusmonitor.get_value('com.victronenergy.settings', '/Settings/CGwacs/Hub4Mode')
			overvoltage_feedin = self._dbusmonitor.get_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn')

			# Determine Flags for this system. 
			if grid_parallel is not None and grid_parallel == 1:
				self.continuous_inverter_power_per_phase = C_CONTINIOUS_INVERTER_POWER.current_value / no_phases_grid
				system_type_flags |= SystemTypeFlag.GridConnected
				if no_phases_grid == 1: system_type_flags |= SystemTypeFlag.SinglePhase
				elif no_phases_grid == 2: system_type_flags |= SystemTypeFlag.DualPhase
				elif no_phases_grid == 3: system_type_flags |= SystemTypeFlag.ThreePhase
				
				if not overvoltage_feedin: system_type_flags |= SystemTypeFlag.ZeroFeedin
				elif overvoltage_feedin: system_type_flags |= SystemTypeFlag.FeedinAllowed
				
				if multiphase_mode == 0: system_type_flags |= SystemTypeFlag.Individual
				elif multiphase_mode == 1: system_type_flags |= SystemTypeFlag.Saldating
			else:
				self.continuous_inverter_power_per_phase = C_CONTINIOUS_INVERTER_POWER.current_value / no_phases_output
				system_type_flags |= SystemTypeFlag.OffGrid
				if no_phases_output == 1: system_type_flags |= SystemTypeFlag.SinglePhase
				elif no_phases_output == 2: system_type_flags |= SystemTypeFlag.DualPhase
				elif no_phases_output == 3: system_type_flags |= SystemTypeFlag.ThreePhase

		except Exception as ex:
			logger.warning("Unable to determine SystemTypeFlags by now. Retrying later...")
			#logger.error("Exception:", exc_info=ex)
			pass

		self._dbusservice["/SystemTypeFlags"] = system_type_flags
		return system_type_flags
	
	def _get_available_overhead(self)-> SolarOverhead:
		"""
			Calculates the available solar overhead.
		"""
		batrate = (self._dbusmonitor.get_value("com.victronenergy.system", "/Dc/Battery/Power") or 0)

		l1 = (
			(self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/PvOnGrid/L1/Power") or 0)
			+ (self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/PvOnOutput/L1/Power") or 0)
			- (self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/Consumption/L1/Power") or 0)
		)

		l2 = (
			(self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/PvOnGrid/L2/Power") or 0)
			+ (self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/PvOnOutput/L2/Power") or 0)
			- (self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/Consumption/L2/Power") or 0)
		)

		l3 = (
			(self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/PvOnGrid/L3/Power") or 0)
			+ (self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/PvOnOutput/L3/Power") or 0)
			- (self._dbusmonitor.get_value("com.victronenergy.system", "/Ac/Consumption/L3/Power") or 0)
		)

		#DCPV Overhead is: Actual DC PV Power - every ac consumption that is not baked by ACPV.
		#finally, if there is no solar at all, dcpv overhead should be negative and equal the
		#battery discharge rate.
	
		dcpv = (
			self._dbusmonitor.get_value("com.victronenergy.system", "/Dc/Pv/Power") or 0
		) * AC_DC_EFFICIENCY
		
		# now, we need to ADD power that is already beeing consumed by S2 Devices, because it will also be deducted in the Consumption-Values.
		for technical_identifier, delegate in self.managed_rms.items():
			if delegate.current_power is not None and delegate.initialized:
				#current power needs only be added to ac. It is real consumption and needs to be deducted where the unmanaged consumption is causing a deduction.
				l1 += delegate.current_power.l1
				l2 += delegate.current_power.l2
				l3 += delegate.current_power.l3
		
		if l1 < 0:
			dcpv -= abs(l1)
			l1=0
		
		if l2 < 0:
			dcpv -= abs(l2)
			l2=0

		if l3 < 0:
			dcpv -= abs(l3)
			l3=0
		
		#ZeroFeedin and Offgrid-Systems are suspect to PV beeing throttled when the batteries CCL is going down. 
		#This is undesired, throttled solar could be used for self-consumption-optimization instead.
		#Hence, when we are at balancingSoc + 1, we going to pretend more DCPV than there is, to increase HEMS consumption
		#and ensure solar is remaining unthrottled. When reaching balancingSoc - 1, we restore normal operation mode. 
		if self.system_type_flags & (SystemTypeFlag.ZeroFeedin | SystemTypeFlag.OffGrid):
			if self.soc is not None and self.soc >= C_BALANCING_THRESHOLD.current_value + 1:
				if (batrate > 0 or self.soc == 100) and self.dcpv_balancing_offset < C_CONTINIOUS_INVERTER_POWER.current_value:
					self.dcpv_balancing_offset += 100 #increse 100 Watts per iteration until we reach a negative charge rate
					logger.debug("Increasing dcpv balancing offset to {}W".format(self.dcpv_balancing_offset))
			
			#reset if applicable.
			if self.soc is None or self.soc <= C_BALANCING_THRESHOLD.current_value - 1:
				self.dcpv_balancing_offset = 0
		else:
			#System Type is something else, we don't need an offset. (Leave this here, type can change)
			self.dcpv_balancing_offset = 0

		return SolarOverhead(
			round(l1, 1), 
			round(l2, 1), 
			round(l3, 1), 
			round(dcpv + self.dcpv_balancing_offset, 1),
			self.get_current_battery_reservation(),
			batrate,
			self.continuous_inverter_power_per_phase,
			self.continuous_inverter_power_per_phase,
			self.continuous_inverter_power_per_phase,
			self
		)

	@property
	def soc(self) -> float:
		"""
			current soc 0 - 100
		"""
		return self._dbusmonitor.get_value("com.victronenergy.system", "/Dc/Battery/Soc", 0.0)

	def get_current_battery_reservation(self) -> float:
		"""
			returns the current desired battery reservation based on the user equation in watts.
			0 if error in equation. /BatteryReservationState will indicate if there is an error with the equation,
			or if the reservation is lowered by BMS capabilities.
		"""
		reservation = 0.0
		try:
			# The Default equation is "RBP - SOC * RD"
			# RBP = ReservationBasePower
			# SOC = SOC
			# RD  = ReservationDecrement
			number_equation = C_RESERVATION_EQUATION.current_value.replace("SOC", str(self.soc))
			number_equation = number_equation.replace("RBP", str(C_RESERVATION_BASE_POWER.current_value))
			number_equation = number_equation.replace("RD", str(C_RESERVATION_DECREMENT.current_value))
			reservation = round(eval(number_equation))
			capability = self.get_charge_power_capability()
			dess_charge = self._dbusmonitor.get_value("com.victronenergy.system", "/DynamicEss/ChargeRate")
			dess_rs = self._dbusmonitor.get_value("com.victronenergy.system", "/DynamicEss/ReactiveStrategy")
			reservation_hint = "OK"

			#When we are at BalancingSoc + 1, Reservation can become 0. (ZeroFeedin and Offgrid) to Keep PV Alive 
			if self.system_type_flags & (SystemTypeFlag.OffGrid | SystemTypeFlag.ZeroFeedin):
				if self.soc is not None and self.soc >= C_BALANCING_THRESHOLD.current_value+ 1:
					reservation = 0
					reservation_hint = "PVKA"

			if capability != None:
				if capability < reservation:
					reservation = capability
					reservation_hint = "BMS"

			# for now, only handle the case when DESS is issuing a positive chargerate.
			# having a lower chargerate issued than the calculated reservation otherwise would cause unused feedin.
			if dess_charge is not None and dess_charge > 0:
				if dess_charge != reservation:
					reservation = dess_charge
					reservation_hint = "DESS"
			
			#dess idle? TODO: eventually replace with a delegate.dynamicess.instance.isIdle() call 
			if dess_rs is not None and dess_rs in [5,8,9,15]:
				reservation = 0
				reservation_hint = "DESS"
				
			self._dbusservice["/BatteryReservationState"] = reservation_hint

			if USE_FAKE_BMS:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/CustomName", "{}: Battery Reservation: {}W ({})".format(
					C_PRIORITY_MAPPING.current_value["battery"], reservation, reservation_hint))

		except Exception as ex:
			reservation = 0.0
			self._dbusservice["/BatteryReservationState"] = "ERROR"
			logger.error("e", exc_info=ex)
		
		self._dbusservice["/BatteryReservation"] = reservation
		return reservation

	def publish_available_services(self):
		"""
			Publishes all known delegates on dbus, so the UI can query this information for configuration purpose.
		"""
		delegate_list = []

		#battery is hardcoded placeholder. 
		battery_instance = {
			"serviceType": "com.victronenergy.system",
			"deviceInstance": 0,
			"configModel": "battery",
			"label": "Battery",
			"priority": C_PRIORITY_MAPPING.current_value["battery"],
			"uniqueIdentifier": "battery"
		}

		delegate_list.append(battery_instance)

		for technical_identifier, delegate in self.managed_rms.items():
			delegate_instance = {}

			#TODO: Once there are other acloads (beside shellies) we need to change this to use another identifier to detect configModel.
			if delegate.technical_identifier.startswith("com.victronenergy.acload"):
				delegate_instance = {
					"serviceType": "com.victronenergy.acload",
					"deviceInstance": delegate.instance,
					"configModel": "shelly",
					"priority": delegate.priority,
					"uniqueIdentifier": delegate.unique_identifier
				}
				delegate_list.append(delegate_instance)

			#TODO: EVCharger needs to be added, once the delegate service is clearified. EVCS-Service itself?
			if delegate.technical_identifier.startswith("com.victronenergy.switch"):
				delegate_instance = {
					"serviceType": "com.victronenergy.switch",
					"deviceInstance": delegate.instance,
					"configModel": "evcs",
					"priority": delegate.priority,
					"uniqueIdentifier": delegate.unique_identifier
				}
				delegate_list.append(delegate_instance)
		
		#sort based on priority We cannot do this before creating the array, because the 
		#battery needs to be "mixed in".
		delegate_list = sorted(delegate_list, key=lambda x: x["priority"]*1000 + x["deviceInstance"])

		#remove prio, not needed for ui
		for entry in delegate_list:
			del entry["priority"]

		self._dbusservice["/AvailableServices"] = delegate_list

	def get_charge_power_capability(self) -> float:
		'''
		  Determines the systems maximum battery charge capability in Watts.
		  If the ccl and cvl fails to be determined, then None is returned.
		  None is to be distinguished from 0 (which means no charging allowed by the bms)
		'''
		battery = self._dbusmonitor.get_value("com.victronenergy.system", "/ActiveBmsService")

		# first, try to obtain values from the bms service.
		if battery is not None and battery != "":
			ccl = self._dbusmonitor.get_value(battery, '/Info/MaxChargeCurrent')
			cvl = self._dbusmonitor.get_value(battery, '/Info/MaxChargeVoltage')

			#TODO: Should take the smaller of CVL and actual chargevoltage here.
			#      System will not use the maximum allowed CVL for certain battery types.

			if (ccl is not None and cvl is not None):
				return ccl * cvl

		return None


if __name__ == "__main__":
	parser = argparse.ArgumentParser(
		description='Converts readings from AC-Sensors connected to a VE.Bus device in a pvinverter ' +
					'D-Bus service.'
	)

	parser.add_argument("-d", "--debug", help="set logging level to debug",
					action="store_true")

	args = parser.parse_args()

	DBusGMainLoop(set_as_default=True)
	logger = setup_logging(args.debug, "opportunity-loads") #TODO: debug args from 
	
	log_dir = "/data/log/opportunity-loads/"    
	if not os.path.exists(log_dir):
		os.mkdir(log_dir)
    
	logging.basicConfig(format='%(asctime)s,%(msecs)d %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.DEBUG,
        handlers=[
       		TimedRotatingFileHandler(log_dir + "/current.log", when="midnight", interval=1, backupCount=2),
        	logging.StreamHandler()
        ])
	
	opportunity_loads = OpportunityLoads()

	# Start and run the mainloop
	logger.info("Starting mainloop, responding only on events")
	mainloop = GLib.MainLoop()
	mainloop.run()