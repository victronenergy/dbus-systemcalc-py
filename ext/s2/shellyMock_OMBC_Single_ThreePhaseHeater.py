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
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler(sys.stdout))

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
    PowerValue
)

from s2python.ombc import (
    OMBCInstruction,
    OMBCOperationMode,
    OMBCStatus,
    OMBCSystemDescription,
    OMBCTimerStatus
)

class OMBCT(OMBCControlType):
    
    def __init__(self, rm_item:S2ResourceManagerItem):
        self.rm_item = rm_item
        self.system_description = None
        self.active_operation_mode = None

        super().__init__()

    def activate(self, conn):
        logger.info("OMBC activated.")
        
        #Generate the OperationModes and Transitions.
        #After creating each Mode, we create Transitions programmatically.
        operation_modes_x=[
            OMBCOperationMode(
                id=uuid.uuid4(),
                diagnostic_label="Off",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    ),
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2
                    ),
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3
                    )
                ]
            ),
            OMBCOperationMode(
                id=uuid.uuid4(),
                diagnostic_label="On 1-0-0",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    ),
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2
                    ),
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3
                    )
                ]
            ),
            OMBCOperationMode(
                id=uuid.uuid4(),
                diagnostic_label="On 0-1-0",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    ),
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2
                    ),
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3
                    )
                ]
            ),
            OMBCOperationMode(
                id=uuid.uuid4(),
                diagnostic_label="On 0-0-1",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    ),
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2
                    ),
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3
                    )
                ]
            ),
            OMBCOperationMode(
                id=uuid.uuid4(),
                diagnostic_label="On 1-1-0",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    ),
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2
                    ),
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3
                    )
                ]
            ),
            OMBCOperationMode(
                id=uuid.uuid4(),
                diagnostic_label="On 1-0-1",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    ),
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2
                    ),
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3
                    )
                ]
            ),
            OMBCOperationMode(
                id=uuid.uuid4(),
                diagnostic_label="On 0-1-1",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    ),
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2
                    ),
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3
                    )
                ]
            ),
            OMBCOperationMode(
                id=uuid.uuid4(),
                diagnostic_label="On 1-1-1",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    ),
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2
                    ),
                    PowerRange(
                        start_of_range=1200,
                        end_of_range=1200,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3
                    )
                ]
            )
        ]

        #generate transitions. We can transist from any state to any.
        transitions_x = []
        for x in operation_modes_x:
            for y in operation_modes_x:
                if x.id != y.id:
                    transitions_x.append(
                       Transition(
                            id=uuid.uuid4(),
                            from_=x.id,
                            to=y.id,
                            start_timers=[],
                            blocking_timers=[],
                            transition_duration=2000,
                            abnormal_condition_only=False,
                            transition_costs=None
                        ) 
                    )

        #Control Type has been selected by CEM. Advertise OperationModes.
        self.system_description = OMBCSystemDescription(
            message_id=uuid.uuid4(),
            valid_from=datetime.now(timezone.utc),
            operation_modes=operation_modes_x,
            transitions=transitions_x,
            timers=[]
        )

        self.rm_item.send_msg_and_await_reception_status_sync(self.system_description)
    
    def deactivate(self, conn):
        #TODO: Implement
        logger.info("OMBC deactivated.")
    
    def handle_instruction(self, conn, msg, send_okay):
        logger.info("Instruction received: {}".format(msg))

        for op_mode in self.system_description.operation_modes:
            if op_mode.id == msg.operation_mode_id:
                self.active_operation_mode = op_mode
                break

        if self.active_operation_mode is not None:
            #Here we actually do, what we are supposed to do. Reporting Power is handled by loop.
            if self.active_operation_mode.diagnostic_label == "Off":
                requests.get("http://10.10.20.57/relay/1?turn=off")
                requests.get("http://10.10.20.58/relay/0?turn=off")
                requests.get("http://10.10.20.58/relay/1?turn=off")
            
            elif self.active_operation_mode.diagnostic_label == "On 1-0-0":
                requests.get("http://10.10.20.57/relay/1?turn=on")
                requests.get("http://10.10.20.58/relay/0?turn=off")
                requests.get("http://10.10.20.58/relay/1?turn=off")
            
            elif self.active_operation_mode.diagnostic_label == "On 0-1-0":
                requests.get("http://10.10.20.57/relay/1?turn=off")
                requests.get("http://10.10.20.58/relay/0?turn=on")
                requests.get("http://10.10.20.58/relay/1?turn=off")
            
            elif self.active_operation_mode.diagnostic_label == "On 0-0-1":
                requests.get("http://10.10.20.57/relay/1?turn=off")
                requests.get("http://10.10.20.58/relay/0?turn=off")
                requests.get("http://10.10.20.58/relay/1?turn=on")
            
            elif self.active_operation_mode.diagnostic_label == "On 1-1-0":
                requests.get("http://10.10.20.57/relay/1?turn=on")
                requests.get("http://10.10.20.58/relay/0?turn=on")
                requests.get("http://10.10.20.58/relay/1?turn=off")
            
            elif self.active_operation_mode.diagnostic_label == "On 1-0-1":
                requests.get("http://10.10.20.57/relay/1?turn=on")
                requests.get("http://10.10.20.58/relay/0?turn=off")
                requests.get("http://10.10.20.58/relay/1?turn=on")
            
            elif self.active_operation_mode.diagnostic_label == "On 0-1-1":
                requests.get("http://10.10.20.57/relay/1?turn=off")
                requests.get("http://10.10.20.58/relay/0?turn=on")
                requests.get("http://10.10.20.58/relay/1?turn=on")
            
            elif self.active_operation_mode.diagnostic_label == "On 1-1-1":
                requests.get("http://10.10.20.57/relay/1?turn=on")
                requests.get("http://10.10.20.58/relay/0?turn=on")
                requests.get("http://10.10.20.58/relay/1?turn=on")

class CTNOCTRL(NoControlControlType):

    def __init__(self, rm_item:S2ResourceManagerItem):
        self.rm_item = rm_item
        super().__init__()

    def activate(self, conn):
        logger.info("NOCTRL activated.")
        self.system_description=None
        self.on_id=None
        self.off_id=None
        return super().activate(conn)
    
    def deactivate(self, conn):
        logger.info("NOCTRL deactivated.")
        return super().deactivate(conn)

class RM0(S2ResourceManagerItem):        
    def __init__(self, path:str, asset_details:AssetDetails, service:Service):
        self.ct_ombc = OMBCT(self)    
        self.ct_noctrl = CTNOCTRL(self)
        self.s2_path = path
        self.asset_details = asset_details
        self.service = service
        super().__init__(self.s2_path, [self.ct_noctrl], self.asset_details)
        
    async def loop(self):
        #Check, if Heatingrod-Control is set to automatic.
        #This is indicated by port 0 on the first shelly2pm. "On = Manual Control"
        response = requests.get(url="http://10.10.20.57/relay/0")
        jo_response = json.loads(response.content)
        is_manual_override = jo_response["ison"]

        #As a second and third constraint that may cause NOCTRL Mode, we use temperaturesensors
        #these values are taken from my home servers mqtt proxy, generally would be the RMs 
        #task to account for it's enviornment settings and constraints.
        response = requests.get(url="http://aps.ad.equinox-solutions.de/dashboard/hook/mqtt-relay/get/Devices/piGardenControl/Sensors/tempReservoirPVHeater/Value?type=Double")
        jo_response = json.loads(response.content)
        rod_temp = float(jo_response["value"]) #rod temp to prevent overheating.

        response = requests.get(url="http://aps.ad.equinox-solutions.de/dashboard/hook/mqtt-relay/get/Devices/piGardenControl/Sensors/tempReservoirTop/Value?type=Double")
        jo_response = json.loads(response.content)
        water_temp = float(jo_response["value"]) #reservoir temp to determine target temperature.

        logger.info("Manual/RT/WT/ControlType: {}/{}/{}/{}".format(
            is_manual_override, 
            rod_temp, 
            water_temp, 
            self._current_control_type.__class__.__name__)
        )

        if is_manual_override or rod_temp >= 100 or water_temp >= 85:
            #Manual Mode or temperaturelimit exceeded. Only possible control type is now noctrl. 
            # Change the advertisement, if confirmed, we are done.
            if self._current_control_type != self.ct_noctrl:
                self.control_types = [self.ct_noctrl]
                await self.send_msg_and_await_reception_status(
                    self.asset_details.to_resource_manager_details(self.control_types)
                )
             
        else:
            #Automatic Mode and temperatures are good. Only possible control type is now PEBC. 
            # Change the advertisement, if confirmed, we are done.
             if self._current_control_type != self.ct_ombc:
                logger.info("Not in OMBC Mode. Offering...")
                self.control_types = [self.ct_noctrl, self.ct_ombc]
                await self.send_msg_and_await_reception_status(
                    self.asset_details.to_resource_manager_details(self.control_types)
                )
        
        #Report power device is currently consuming. 
        response = requests.get("http://10.10.20.57/rpc/Shelly.GetStatus")
        jo_response = json.loads(response.content)
        power1 = jo_response["switch:1"]["apower"]

        response = requests.get("http://10.10.20.58/rpc/Shelly.GetStatus")
        jo_response = json.loads(response.content)
        power2 = jo_response["switch:0"]["apower"]
        power3 = jo_response["switch:1"]["apower"]

        logger.info("Selected operation mode: {} @ {}W/{}W/{}W".format(self.ct_ombc.active_operation_mode.diagnostic_label, 
                                                                       power1, power2, power3))

        await self.send_msg_and_await_reception_status(
            PowerMeasurement(
                message_id=uuid.uuid4(),
                measurement_timestamp=datetime.now(timezone.utc),
                values = [
                    PowerValue(
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                        value=power1
                    ),
                    PowerValue(
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2,
                        value=power2
                    ),
                    PowerValue(
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3,
                        value=power3
                    )
                ]
            )
        )
    
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
                [CommodityQuantity.ELECTRIC_POWER_L1, CommodityQuantity.ELECTRIC_POWER_L2, CommodityQuantity.ELECTRIC_POWER_L3],
                Duration.from_milliseconds(5000),
                [Role(role=RoleType.ENERGY_CONSUMER, commodity=Commodity.ELECTRICITY)],
                None,
                "Shelly Heater 3 Phased",
                "Shelly",
                "3.6 kW Heating Rod",
                "1.0",
                "1337"
            ), self)
        
        self.add_item(self.rm0)

    async def _loop(self):
        while True:
            await asyncio.sleep(10) #validate operation constraints every 10 seconds. 
            await self.rm0.loop()

if __name__ == "__main__":
    try:
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType
    except ImportError:
        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

    async def main():
        instance = 904
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        service = ShellyS2Mock(bus, 'com.victronenergy.s2Mock', instance)
        
        service.add_item(TextItem("/ProductName", "ShellyMock"))
        service.add_item(IntegerItem("/DeviceInstance", instance))
        service.add_item(TextItem("/Mgmt/ProcessVersion", "1.0"))
        service.add_item(TextItem("/Mgmt/Connection", "dbus via aiovelib"))
        service.add_item(IntegerItem("/Connected", 1))
        service.add_item(TextItem('/CustomName', "ShellyMock {}".format(instance)))

        service.add_item(IntegerItem('/Devices/0/DeviceInstance', instance))
        service.add_item(TextItem('/Devices/0/ServiceName', service.name))
        service.add_item(TextItem('/Devices/0/CustomName', None))
        service.add_item(TextItem('/Devices/0/IpAddress', None))
        service.add_item(IntegerItem('/Devices/0/Notification', 0))

         #Some to be discussed items. Suspect to User-Configuration.
        #Actual (generic) rm may use more here, like Power and Phase(s) used by the RSS.
        #(Because a generic RM controlling a shelly / switchable output can't know.)
        service.add_item(IntegerItem('/Devices/0/S2/Priority', 30)) #Priority , EMS will read
        service.add_item(IntegerItem('/Devices/0/S2/ConsumerType', 1)) # 0=primary load, 1=secondary load, EMS will read
        
        service.setup_rm0()

        #finally register our service.
        await service.register()

        asyncio.get_event_loop().create_task(service._loop())
        await service.bus.wait_for_disconnect()
    
    asyncio.get_event_loop().run_until_complete(main())