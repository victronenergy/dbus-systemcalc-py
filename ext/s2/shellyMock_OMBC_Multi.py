#!/usr/bin/env python
 
# imports
import sys
import os
import logging
import os
import platform
import subprocess
import uuid
import threading
import requests #type:ignore
import asyncio
from datetime import datetime, timedelta, timezone
import json
from builtins import Exception, int, str
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Callable
import signal

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/s2')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/aiovelib')

from aiovelib.service import IntegerItem, TextItem, DoubleItem
from aiovelib.service import Service
import aiohttp

#s2 related stuff
from s2 import S2ResourceManagerItem
from s2python.s2_connection import AssetDetails

from s2python.s2_control_type import (
    S2ControlType, 
    OMBCControlType, 
    NoControlControlType
)

from s2python.common import (
    CommodityQuantity,
    Role,
    RoleType,
    Commodity,
    Duration,
    ReceptionStatusValues,
    ResourceManagerDetails,
    NumberRange,
    PowerRange,
    Transition,
    PowerMeasurement,
    PowerValue,
    Timer
)

from s2python.ombc import (
    OMBCInstruction,
    OMBCOperationMode,
    OMBCStatus,
    OMBCSystemDescription,
    OMBCTimerStatus
)

def fire_and_forget(url):
    loop = asyncio.get_event_loop()
    loop.create_task(async_fire_and_forget(url))

def aio_fire_and_forget(url):
    asyncio.run(async_fire_and_forget(url))

async def async_fire_and_forget(url):
    async with aiohttp.request('GET', url) as response:
        if response.status == 200:
            await response.json()

class OMBCT(OMBCControlType):
    
    def __init__(self, rm:S2ResourceManagerItem):
        self.rm:UnifiedHttpShellyRM = rm
        self.set_defaults()

        super().__init__()

    def set_defaults(self):
        self.system_description = None
        self.active_operation_mode = None
        self.op_mode_on:OMBCOperationMode = None
        self.op_mode_off:OMBCOperationMode = None
        self.transition_to_on:Transition = None
        self.transition_to_off:Transition = None
        self.on_timer:Timer = None
        self.off_timer:Timer = None

    def activate(self, conn):
        self.rm.log_info("OMBC activated")
        self.rm.offer_count = 0 #reset debug-reconnect flag.

        #First, create the system description required. It contains 2 controltypes (On / Off)
        #and the proper transitions accordings to desired On/Off delays.
        self.op_mode_on = OMBCOperationMode(
            id=uuid.uuid4(),
            diagnostic_label="On",
            abnormal_condition_only=False,
            power_ranges=[PowerRange(
                start_of_range=self.rm.power,
                end_of_range=self.rm.power,
                commodity_quantity=self.rm.phase
            )]
        )

        self.op_mode_off = OMBCOperationMode(
            id=uuid.uuid4(),
            diagnostic_label="Off",
            abnormal_condition_only=False,
            power_ranges=[PowerRange(
                start_of_range=0,
                end_of_range=0,
                commodity_quantity=self.rm.phase
            )]
        )

        self.on_timer = Timer(id=uuid.uuid4(), diagnostic_label="On Hysteresis", duration=self.rm.on_hysteresis)
        self.off_timer = Timer(id=uuid.uuid4(), diagnostic_label="Off Hysteresis", duration=self.rm.off_hysteresis)

        self.transition_to_on = Transition(
            id=uuid.uuid4(),
            from_=self.op_mode_off.id,
            to=self.op_mode_on.id,
            start_timers=[self.off_timer.id],
            blocking_timers=[self.on_timer.id],
            transition_duration=Duration.from_milliseconds(5000),
            abnormal_condition_only=False
        )

        self.transition_to_off = Transition(
            id=uuid.uuid4(),
            from_=self.op_mode_on.id,
            to=self.op_mode_off.id,
            start_timers=[self.on_timer.id],
            blocking_timers=[self.off_timer.id],
            transition_duration=Duration.from_milliseconds(5000),
            abnormal_condition_only=False
        )

        self.system_description = OMBCSystemDescription(
            message_id=uuid.uuid4(),
            valid_from=datetime.now(timezone.utc),
            operation_modes=[self.op_mode_on, self.op_mode_off],
            transitions=[self.transition_to_on, self.transition_to_off],
            timers=[self.on_timer, self.off_timer]
        )

        #Send the system description.
        self.rm.send_msg_and_await_reception_status_sync(self.system_description)
        
        #OMBC has just been activated. Check, which state our shelly has, 
        #report the proper state initially. 
        if not self.rm.service.is_neals_gx:
            response = requests.get("http://{}/rpc/Switch.GetStatus?id={}".format(
                self.rm.ip_address, self.rm.shelly_port)
            )
            
            if response.status_code == 200:
                jresponse = json.loads(response.content)
                if jresponse["output"]:
                    #device is on
                    #FIXME: OMBCStatus is having a wrong datatype for active_operation_mode_id. Need to format as string.
                    self.rm.publish_power_report(jresponse["apower"])
                    self.rm.send_msg_and_await_reception_status_sync(
                        OMBCStatus(
                            message_id=uuid.uuid4(),
                            active_operation_mode_id="{}".format(self.op_mode_on.id),
                            operation_mode_factor=1.0, # hmmm? doesn't matter at this point.
                        )
                    )
                else:
                    #device is off.
                    self.rm.send_msg_and_await_reception_status_sync(
                        OMBCStatus(
                            message_id=uuid.uuid4(),
                            active_operation_mode_id="{}".format(self.op_mode_off.id),
                            operation_mode_factor=1.0, # hmmm? doesn't matter at this point.
                        )
                    )
        else:
            #On neals gx, we pretend an initial off state. 
            self.rm.send_msg_and_await_reception_status_sync(
                OMBCStatus(
                    message_id=uuid.uuid4(),
                    active_operation_mode_id="{}".format(self.op_mode_off.id),
                    operation_mode_factor=1.0, # hmmm? doesn't matter at this point.
                )
            )


        #That's it. reaction to switching opmodes by the EMS will happen in handle_instruction.
    
    def deactivate(self, conn):
        self.set_defaults()
        self.rm.log_info("OMBC deactivated. Restored defaults.")
    
    async def handle_instruction(self, conn, msg, send_okay):
        try:
            #save the prior id for the Status Msg.
            #FIXME: OMBCStatus is having a wrong datatype for active_operation_mode_id. Need to format as string.
            prior_id = "{}".format(self.active_operation_mode.id) if self.active_operation_mode is not None else None

            for op_mode in self.system_description.operation_modes:
                if op_mode.id == msg.operation_mode_id:
                    self.rm.log_info("Instruction received: {}".format(op_mode.diagnostic_label))
                    self.active_operation_mode = op_mode
                    break
            
            #we can fire and forget that update.
            await self.rm._send_and_forget(
                OMBCStatus(
                    message_id=uuid.uuid4(),
                    active_operation_mode_id="{}".format(self.active_operation_mode.id),
                    previous_operation_mode_id=prior_id,
                    transition_timestamp=datetime.now(timezone.utc),
                    operation_mode_factor=msg.operation_mode_factor
                )
            )

            if self.active_operation_mode is not None:
                #Here we actually do, what we are supposed to do. Reporting Power is handled by the loop.
                #We just reuse the diagnostic label to determine which operation type was selected by the ems. 
                #on neals GX we don't have to do actual requests. Just acting based on the selected operation mode. 
                if not self.rm.service.is_neals_gx:
                    if self.active_operation_mode.diagnostic_label == "On":
                        await async_fire_and_forget("http://{}/rpc/Switch.Set?on=true&id={}".format(
                            self.rm.ip_address, self.rm.shelly_port))
                        
                    if self.active_operation_mode.diagnostic_label == "Off":
                        await async_fire_and_forget("http://{}/rpc/Switch.Set?on=false&id={}".format(
                            self.rm.ip_address, self.rm.shelly_port))
            
            await send_okay
                    
        except Exception as ex:
            self.rm.log_error("Exception during handle_instruction", ex)

class NOCTRL(NoControlControlType):
    def __init__(self, rm:S2ResourceManagerItem):
        self.rm:UnifiedHttpShellyRM = rm
        super().__init__()

    def activate(self, conn):
       self.rm.log_info("NOCTRL activated")
       self.rm.offer_count = 0 #reset debug-reconnect flag.

       if not self.rm.can_be_controlled():
           #Switched to NOCTRL because Operation Constraints no longer work out. 
           #In that case, we turn off the consumer, in case it was enabled. 
           self.rm.log_info("Cannot be controlled currently-> deactivating consumer.")

           if not self.rm.service.is_neals_gx:
               aio_fire_and_forget("http://{}/rpc/Switch.Set?on=false&id={}".format(
                       self.rm.ip_address, self.rm.shelly_port))
    
    def deactivate(self, conn):
        #self.rm.log_info("NOCTRL deactivated")
        pass
    
class UnifiedHttpShellyRM(S2ResourceManagerItem):        
    def __init__(self, service:Service, rm_no:int, ip_address:str, custom_name:str, shelly_port:int,
                 priority:int, consumer_type:int, phase:CommodityQuantity, power:float, 
                 on_hysteresis:int, off_hysteresis:int):
      
        self.service = service
        self.rm_no = rm_no
        self.ip_address = ip_address
        self.custom_name = custom_name
        self.shelly_port = shelly_port
        self.priority = priority
        self.consumer_type = consumer_type
        self.phase = phase
        self.power = power
        self.on_hysteresis = Duration.from_milliseconds(on_hysteresis * 1000)
        self.off_hysteresis = Duration.from_milliseconds(off_hysteresis * 1000)

        self.last_power_reported = 0
        self.ct_ombc = OMBCT(self)
        self.ct_no_ctrl = NOCTRL(self)

        #debug hack only to issue reconnect, when stuck.
        self.offer_count = 0
        
        self.asset_details = AssetDetails(
            resource_id=uuid.uuid4(),
            provides_forecast=False,
            provides_power_measurements=[self.phase],
            instruction_processing_delay=5000,
            roles=[(Role(role=RoleType.ENERGY_CONSUMER, commodity=Commodity.ELECTRICITY))],
            name=self.custom_name
        )

        #for startup, only NOCTRL is offered. Based on the state of the device
        #and the result of can_be_controlled(), announcement will be updated.
        super().__init__("/Devices/{}/S2".format(self.rm_no), [self.ct_no_ctrl], self.asset_details)

        #populate additional configuration on dbus. These are not covered by the S2 Standard.
        service.add_item(IntegerItem('/Devices/{}/S2/Priority'.format(self.rm_no), self.priority)) #Priority , EMS will read
        service.add_item(IntegerItem('/Devices/{}/S2/ConsumerType'.format(self.rm_no), self.consumer_type)) # 0=primary load, 1=secondary load, EMS will read
        service.add_item(IntegerItem('/Devices/{}/S2/Auto'.format(self.rm_no), 1, True)) # true for startup, writeable to grant external control above controllability without coding. 

        #Current Type is no ctrl.
        self._current_control_type = self.ct_no_ctrl
    
    def can_be_controlled(self) -> bool:
        return bool(self.service['/Devices/{}/S2/Auto'.format(self.rm_no)])
    
    async def loop_power_report(self):
        #report consumption. 
        try:
            if self.service.is_neals_gx and self.ct_ombc is not None:
                #Pretend the device is running, when turned on
                if self.is_connected and self.ct_ombc.active_operation_mode.diagnostic_label == "On":
                    await self.publish_power_report(self.power)
                else:
                    await self.publish_power_report(0)

            else:
                if self.is_connected:
                    async with aiohttp.request('GET', "http://{}/rpc/Switch.GetStatus?id={}".format(
                        self.ip_address, self.shelly_port)) as response:
                        if response.status == 200:
                            jresponse = await response.json()
                            #self.log_info("Power is {}".format(jresponse["apower"]))
                            await self.publish_power_report(jresponse["apower"])
        except:
            pass

    async def publish_power_report(self, v):
        #self.log_info("Publishing Power: {}".format(v))
        self.last_power_reported = v

        await self.send_msg_and_await_reception_status(
            PowerMeasurement(
                message_id=uuid.uuid4(),
                measurement_timestamp=datetime.now(timezone.utc),
                values = [
                    PowerValue(
                        commodity_quantity=self.phase,
                        value=v
                    )
                ]
            )
        )

    async def loop_conditions(self):
        if self.is_connected:
            try:
                #Connected, check if this shelly can currently be controlled.
                #If yes, ensure OMBC is offered, else offer only noctrl.
                #Rest is handled in the ControlType-Implementations.
                if self.can_be_controlled():
                    if self._current_control_type != self.ct_ombc:
                        #Offer OMBC control.
                        self.offer_count += 1
                        self.log_info("Offering OMBC... (Current ControlType is: {})".format(self._current_control_type))
                        #FIXME: Until Fixed by PT, we need to update the internal control type map as well
                        self.control_types = [self.ct_ombc]
                        await self.send_msg_and_await_reception_status(
                            self.asset_details.to_resource_manager_details([self.ct_ombc])
                        )
                else:
                    if self._current_control_type != self.ct_no_ctrl:
                        self.log_info("Offering NOCTRL... (Current ControlType is: {})".format(self._current_control_type))
                        self.offer_count += 1
                        #FIXME: Until Fixed by PT, we need to update the internal control type map as well
                        self.control_types = [self.ct_no_ctrl]
                        await self.send_msg_and_await_reception_status(
                            self.asset_details.to_resource_manager_details([self.ct_no_ctrl])
                        )
                
                if (self.offer_count > 5):
                    self.log_info("Offered Control 5 times to no success :( - Restarting service.")
                    sys.exit(0)

            except Exception as ex:
                self.log_error("Error in loop_conditions", ex)

    def log_info(self, msg):
        logger.info("[{} @ RM{}] in {}: {}".format(self.custom_name, self.rm_no, threading.current_thread().name, msg))

    def log_error(self, msg, exception):
        logger.error("[{} @ RM{}]: {}".format(self.custom_name, self.rm_no, msg), exc_info=exception)
    
    async def _on_s2_message(self, message):
        #FIXME: Current S2 Implementation uses S2Parser, which fails for certain messages. So, we have to 
        #ignore confirmation errors and handle that manually until fixed. 
        try:
            jmsg = json.loads(message)

            #if (jmsg["message_type"] != "ReceptionStatus"):
            #    self.log_info("Received message: {}".format(jmsg))

        except Exception as ex:
            logger.error("Exception in _on_s2_message", exc_info=ex)

        # forward other messages to base.
        return await super()._on_s2_message(message)

class ShellyMockService(Service):
    
    def __init__(self, bus, name, instance):
        self.instance = instance
        self.shelly_ios:list[UnifiedHttpShellyRM] = None
        super().__init__(bus, "{}.m_{}".format(name, instance))

    def setup_shellies(self):
        #check, which development system we are on, to use the correct batch of shellies :) 
        #you can duplicate for YOUR environment
        self.is_daniels_gx = subprocess.run(["ping", "-c", "1", "10.10.20.20"], stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0
        self.is_neals_gx = subprocess.run(["ping", "-c", "1", "10.230.1.62"], stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0

        #populate all the Shelly RMs we want to use.
        if self.is_daniels_gx:
            logger.info("Loading shellies for Daniels Environment.")
            self.shelly_ios:list[UnifiedHttpShellyRM] = [
                UnifiedHttpShellyRM(
                    self, 0, "10.10.20.90", "Waterplay Filter", 0, 5, 0, CommodityQuantity.ELECTRIC_POWER_L3, 70.0, 600, 60
                ),
                UnifiedHttpShellyRM(
                    self, 1, "10.10.20.57", "Heater L1", 1, 40, 1, CommodityQuantity.ELECTRIC_POWER_L1, 1150.0, 60, 30
                ),
                UnifiedHttpShellyRM(
                    self, 2, "10.10.20.58", "Heater L2", 0, 30, 1, CommodityQuantity.ELECTRIC_POWER_L2, 1150.0, 60, 30
                ),
                UnifiedHttpShellyRM(
                    self, 3, "10.10.20.58", "Heater L3", 1, 35, 1, CommodityQuantity.ELECTRIC_POWER_L3, 1150.0, 60, 30
                ),
                UnifiedHttpShellyRM(
                    self, 4, "10.10.20.98", "Pool Filter", 0, 10, 0, CommodityQuantity.ELECTRIC_POWER_L3, 220.0, 60, 60
                ),
                UnifiedHttpShellyRM(
                    self, 5, "10.10.20.66", "Pool Heatpump", 0, 15, 0, CommodityQuantity.ELECTRIC_POWER_L1, 550.0, 60, 300
                ),
                UnifiedHttpShellyRM(
                    self, 6, "10.10.20.66", "Pool E-Heater", 1, 20, 1, CommodityQuantity.ELECTRIC_POWER_L2, 2750.0, 60, 30
                ),
            ]
        if self.is_neals_gx:
            logger.info("Loading shellies for Neals Environment.")

            #ip adresses don't matter here, we just simulate shellies :)
            self.shelly_ios:list[UnifiedHttpShellyRM] = [

                #two consumers to run BEFORE battery reservation
                UnifiedHttpShellyRM(
                    self, 0, "10.230.1.62", "Staircase Light", 0, 5, 0, CommodityQuantity.ELECTRIC_POWER_L1, 70.0, 60, 60
                ),
                UnifiedHttpShellyRM(
                    self, 1, "10.230.1.62", "Fountain", 0, 10, 0, CommodityQuantity.ELECTRIC_POWER_L1, 230.0, 60, 60
                ),

                #other consumers are just energy sinks to run AFTER battery reservation 
                #priority reversed, so system switches to the biggest rod, when available.
                UnifiedHttpShellyRM(
                    self, 2, "10.230.1.62", "Heating rod small", 0, 40, 1, CommodityQuantity.ELECTRIC_POWER_L1, 500, 60, 60
                ),
                UnifiedHttpShellyRM(
                    self, 3, "10.230.1.62", "Heating rod Medium", 0, 35, 1, CommodityQuantity.ELECTRIC_POWER_L1, 1000, 60, 60
                ),
                UnifiedHttpShellyRM(
                    self, 4, "10.230.1.62", "Heating rod Large", 0, 30, 1, CommodityQuantity.ELECTRIC_POWER_L1, 1500, 60, 60
                ),

                #pretend we have a pool and a ac.
                #AC has 10 min hysterisis to avoid the compressor from turning on/off to often.
                #AC has higher priority than heating rods, we don't like sweating.
                UnifiedHttpShellyRM(
                    self, 5, "10.230.1.62", "Pool Heater", 0, 50, 1, CommodityQuantity.ELECTRIC_POWER_L1, 2000, 180, 60
                ),
                UnifiedHttpShellyRM(
                    self, 6, "10.230.1.62", "AC Office", 0, 20, 1, CommodityQuantity.ELECTRIC_POWER_L1, 3000, 600, 600
                ),
            ]
        else:
            logger.warning("Unknown environment. Not loaded any shellies.")

        #add all rms to dbus.
        if self.shelly_ios is not None:
            for rm in self.shelly_ios:
                logger.info("Registering Shelly device on dbus: {} ({})".format(rm.ip_address, rm.custom_name))
                self.add_item(rm)

    async def _loop_power_report(self):
        while True:
            for rm in self.shelly_ios:
                await rm.loop_power_report()
                await asyncio.sleep(0.1) #distribute load a bit
            await asyncio.sleep(2) #yes indention is right, this should process every rm every 2 seconds :)

    async def _loop_check_conditions(self):
        while True:
            for rm in self.shelly_ios:
                asyncio.create_task(rm.loop_conditions())
            await asyncio.sleep(5) 

if __name__ == "__main__":
    try:
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType
    except ImportError:
        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

    def configure_logger():
        from logging.handlers import TimedRotatingFileHandler
        log_dir = "/data/log/S2"    
        if not os.path.exists(log_dir):
            os.mkdir(log_dir)
        
        logging.basicConfig(format='%(asctime)s,%(msecs)d %(levelname)s %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            level=logging.DEBUG,
            handlers=[
            TimedRotatingFileHandler(log_dir + "/" + os.path.basename(__file__) + ".log", when="midnight", interval=1, backupCount=2),
            logging.StreamHandler()
            ])
        
        # Suppress DEBUG output from requests
        logging.getLogger("requests").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        

    async def main():
        configure_logger()

        def restart_service(signum, frame):
            logger.info("Received sigterm, ending service.")
            sys.exit(0)

        signal.signal(signal.SIGTERM, restart_service)

        try:
            instance = 815 #it's 0815 :)
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            service = ShellyMockService(bus, 'com.victronenergy.switch', instance)
            service.setup_shellies()
            service.add_item(TextItem("/ProductName", "ShellyMockService"))
            service.add_item(IntegerItem("/DeviceInstance", instance))
            service.add_item(TextItem("/Mgmt/ProcessVersion", "1.0"))
            service.add_item(TextItem("/Mgmt/Connection", "dbus via aiovelib"))
            service.add_item(IntegerItem("/Connected", 1))
            service.add_item(TextItem('/CustomName', "ShellyMockService {}".format(instance)))

            #finally register our service.
            await service.register()

            asyncio.get_event_loop().create_task(service._loop_power_report())
            asyncio.get_event_loop().create_task(service._loop_check_conditions())

            await service.bus.wait_for_disconnect()
        except Exception as ex:
            logger.error("Exception in main()", exc_info=ex)
    
    asyncio.get_event_loop().run_until_complete(main())