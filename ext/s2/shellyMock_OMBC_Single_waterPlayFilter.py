#!/usr/bin/env python
 
# imports
import sys
import os
import logging
import os
import platform
import uuid
import requests 
import asyncio
from datetime import datetime, timedelta, timezone
import json
from builtins import Exception, int, str
from concurrent.futures import ThreadPoolExecutor
from typing import Dict

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/s2')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/aiovelib')

from aiovelib.service import IntegerItem, TextItem, DoubleItem
from aiovelib.service import Service

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

def spam_web_request(url):
    """
        Sometimes web requests on wifi shellies time out.
        This is a dirty helper to repeat such a request until successfull.
    """
    while True:
        try:
            response = requests.get(url)
            if response.status_code == 200:
                break
        
        except Exception as ex:
            logger.warning("Web request failed,retrying:{}".format(url))

class OMBCT(OMBCControlType):
    
    def __init__(self, rm_item:S2ResourceManagerItem):
        self.rm_item = rm_item
        self.system_description = None
        self.active_operation_mode = None

        super().__init__()

    def activate(self, conn):
        logger.info("OMBC activated.")
        
        #reset
        self.active_operation_mode = None

        #Control Type has been selected by CEM. Advertise OperationModes.
        self.on_id = uuid.uuid4()
        self.off_id = uuid.uuid4()
        self.on_off_timer_id = uuid.uuid4()

        self.system_description = OMBCSystemDescription(
            message_id=uuid.uuid4(),
            valid_from=datetime.now(timezone.utc),
            operation_modes=[
                    OMBCOperationMode(
                        id=self.off_id,
                        diagnostic_label="Off",
                        abnormal_condition_only=False,
                        power_ranges=[
                            PowerRange(
                                start_of_range=0,
                                end_of_range=0,
                                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                            )
                        ]
                    ),
                    OMBCOperationMode(
                    id=self.on_id,
                    diagnostic_label="On",
                    abnormal_condition_only=False,
                    power_ranges=[
                        PowerRange(
                            start_of_range=70,
                            end_of_range=70,
                            commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                        )
                    ]
                )
            ],
            transitions=[
                Transition(
                    id=uuid.uuid4(),
                    from_=self.on_id,
                    to=self.off_id,
                    start_timers=[self.on_off_timer_id],
                    blocking_timers=[self.on_off_timer_id],
                    transition_duration=2000,
                    abnormal_condition_only=False,
                    transition_costs=None
                ),
                Transition(
                    id=uuid.uuid4(),
                    from_=self.off_id,
                    to =self.on_id,
                    start_timers=[self.on_off_timer_id],
                    blocking_timers=[self.on_off_timer_id],
                    transition_duration=2000,
                    abnormal_condition_only=False,
                    transition_costs=None
                )
            ],
            timers=[
                Timer(
                    id = self.on_off_timer_id,
                    diagnostic_label="On/Off Hysteresis 60s",
                    duration=60*1000
                )
            ]
        )

        self.rm_item.send_msg_and_await_reception_status_sync(self.system_description)

        #system description send, tell the HEMS in which state we are currently, so it can
        #start to issue transitions. We start with "off".
        spam_web_request("http://shelly1pmminiwaterplayfilter.ad.equinox-solutions.de/relay/0?turn=off")
        self.rm_item.send_msg_and_await_reception_status_sync(
            OMBCStatus(
                message_id=uuid.uuid4(),
                active_operation_mode_id="{}".format(self.off_id),
                operation_mode_factor=1.0, # hmmm? doesn't matter at this point.
            )
        )

        for opm in self.system_description.operation_modes:
            if opm.id == self.off_id:
                self.active_operation_mode = opm

        #that should be it. 
    
    def deactivate(self, conn):
        #TODO: Implement
        logger.info("OMBC deactivated.")
    
    def handle_instruction(self, conn, msg, send_okay):
        try:
            prior_id = "{}".format(self.active_operation_mode.id) if self.active_operation_mode is not None else None

            for op_mode in self.system_description.operation_modes:
                if op_mode.id == msg.operation_mode_id:
                    logger.info("Instruction received: {}".format(op_mode.diagnostic_label))
                    self.active_operation_mode = op_mode
                    break
            
            status = OMBCStatus(
                    message_id=uuid.uuid4(),
                    active_operation_mode_id="{}".format(self.active_operation_mode.id),
                    previous_operation_mode_id=prior_id,
                    transition_timestamp=datetime.now(timezone.utc),
                    operation_mode_factor=msg.operation_mode_factor
                )
            
            logger.info("Answering with {}".format(status))

            #sending block / wait does not work in here.
            self.rm_item._send_and_forget(
                status
            )

            logger.info("Fire and forgot done.")

            if self.active_operation_mode is not None:
                #Here we actually do, what we are supposed to do. Reporting Power is handled by loop.
                #We just reuse the diagnostic label to determine which operation type was selected by the ems. 
                
                    if self.active_operation_mode.diagnostic_label == "On":
                        spam_web_request("http://shelly1pmminiwaterplayfilter.ad.equinox-solutions.de/relay/0?turn=on")
                    if self.active_operation_mode.diagnostic_label == "Off":
                        spam_web_request("http://shelly1pmminiwaterplayfilter.ad.equinox-solutions.de/relay/0?turn=off")
        except Exception as ex:
            logger.error("Exception during handle_instructions", exc_info=ex)

class RM0(S2ResourceManagerItem):        
    def __init__(self, path:str, asset_details:AssetDetails, service:Service):
        self.ct_ombc = OMBCT(self)    
        self.s2_path = path
        self.asset_details = asset_details
        self.service = service
        super().__init__(self.s2_path, [self.ct_ombc], self.asset_details)

    async def _destroy_connection(self):
        await super()._destroy_connection()

        #debug purpose: When we have a disconnect, simply restart the service. 
        #this ensures the services are restarted with an eventually updated file.
        logger.info("Connection destroyed, ending execution to allow service to restart.")
        sys.exit(0)
    
    async def loop(self):
        try:
            if self.is_connected:
                # dumbest consumer ever - it jus wants to run, no constraints. 
                # see shellyMock_OMBC_Single_heaterL1.py for a mock having operational constraints utilizing no_ctrl type as well.
                if self._current_control_type != self.ct_ombc:
                    logger.info("Not in OMBC Mode. Offering...")
                    await self.send_msg_and_await_reception_status(
                        self.asset_details.to_resource_manager_details([self.ct_ombc])
                    )
                
                if self.ct_ombc.active_operation_mode is None:
                    logger.warning("Operation Mode not yet selected.")
                    return 
                
                #Report power device is currently consuming. 
                response = requests.get("http://shelly1pmminiwaterplayfilter.ad.equinox-solutions.de/rpc/Shelly.GetStatus")
                jo_response = json.loads(response.content)
                power = jo_response["switch:0"]["apower"]

                op_label = self.ct_ombc.active_operation_mode.diagnostic_label if self.ct_ombc.active_operation_mode.diagnostic_label is not None else "None"
                logger.info("Selected operation mode: {} @ {}W".format(op_label, power))

                await self.send_msg_and_await_reception_status(
                    PowerMeasurement(
                        message_id=uuid.uuid4(),
                        measurement_timestamp=datetime.now(timezone.utc),
                        values = [
                            PowerValue(
                                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                                value=power
                            )
                        ]
                    )
                )
        except Exception as ex:
            logger.error("Exception during loop", exc_info=ex)
    
    async def _on_s2_message(self, message):
        #FIXME: Current S2 Implementation uses S2Parser, which fails for certain messages. So, we have to 
        #ignore confirmation errors and handle that manually until fixed. 
        try:
            jmsg = json.loads(message)

            if jmsg["message_type"] == "OMBC.Instruction":
                #FIXME: This should have be called by S2 implementation, this is just a hack lacking type-safety and parameters. 
                msg = self.s2_parser.parse_as_message(jmsg, OMBCInstruction)
                self.ct_ombc.handle_instruction(None, msg, None)
        except Exception as ex:
            logger.error("Exception", exc_info=ex)

        #forward to s2 class
        return await super()._on_s2_message(message)

class ShellyS2Mock(Service):
    
    def __init__(self, bus, name, instance):
        self.instance = instance
        super().__init__(bus, "{}.m_{}".format(name, instance))

    def setup_rm0(self):
        self.rm0 = RM0(
            '/Devices/0/S2', 
            AssetDetails(
                uuid.uuid4(),
                False,
                [CommodityQuantity.ELECTRIC_POWER_L1],
                Duration.from_milliseconds(10000),
                [Role(role=RoleType.ENERGY_CONSUMER, commodity=Commodity.ELECTRICITY)],
                None,
                "Waterplay Filter",
                "Shelly",
                "70 Watts Waterfilter",
                "1.0",
                "1337"
            ), self)
        
        self.add_item(self.rm0)

    async def _loop(self):
        while True:
            await asyncio.sleep(10) #validate operation constraints and report power every 10 seconds. 
            await self.rm0.loop()

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

    async def main():
        configure_logger()
        instance = 900
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        service = ShellyS2Mock(bus, 'com.victronenergy.s2Mock', instance)
        
        service.add_item(TextItem("/ProductName", "ShellyMock"))
        service.add_item(IntegerItem("/DeviceInstance", instance))
        service.add_item(TextItem("/Mgmt/ProcessVersion", "1.0"))
        service.add_item(TextItem("/Mgmt/Connection", "dbus via aiovelib"))
        service.add_item(IntegerItem("/Connected", 1))
        service.add_item(TextItem('/CustomName', "ShellyMock {}".format(instance)))

        service.add_item(IntegerItem('/Devices/0/DeviceInstance', None))
        service.add_item(TextItem('/Devices/0/ServiceName', service.name))
        service.add_item(TextItem('/Devices/0/CustomName', None))
        ##service.add_item(TextItem('/Devices/0/IpAddress', None))
        #service.add_item(IntegerItem('/Devices/0/Notification', 0))

        #Some to be discussed items. Suspect to User-Configuration.
        #Actual (generic) rm may use more here, like Power and Phase(s) used by the RSS.
        #(Because a generic RM controlling a shelly / switchable output can't know.)
        service.add_item(IntegerItem('/Devices/0/S2/Priority', 5)) #Priority , EMS will read
        service.add_item(IntegerItem('/Devices/0/S2/ConsumerType', 0)) # 0=primary load, 1=secondary load, EMS will read
        
        service.setup_rm0()

        #finally register our service.
        await service.register()

        asyncio.get_event_loop().create_task(service._loop())
        await service.bus.wait_for_disconnect()
    
    asyncio.get_event_loop().run_until_complete(main())