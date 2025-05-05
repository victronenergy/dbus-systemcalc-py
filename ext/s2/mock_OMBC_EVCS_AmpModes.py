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

from dbusmonitor import DbusMonitor # type: ignore
from dbus.mainloop.glib import DBusGMainLoop # type: ignore
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

DBusGMainLoop(set_as_default=True)

EVCS_SERVICE = "com.victronenergy.evcharger.evc_HQ2326FEAJW"

class OMBCT(OMBCControlType):
    
    def __init__(self, rm_item:S2ResourceManagerItem):
        self.rm_item = rm_item
        self.active_operation_mode = None

        super().__init__()

    def activate(self, conn):
        logger.info("OMBC activated.")
        #nothing todo here, loop will handle.
    
    def deactivate(self, conn):
        #reset, so loop is resending information, when OMBC is enabled.
        #that is implemented in the loop, because the car state may change
        #during OMBC beeing active as well.
        self.rm_item.car_connected = None 
        logger.info("OMBC deactivated.")
    
    def handle_instruction(self, conn, msg, send_okay):
        logger.info("Instruction received: {}".format(msg))

        for op_mode in self.rm_item.system_description.operation_modes:
            if op_mode.id == msg.operation_mode_id:
                self.active_operation_mode = op_mode
                break

        if self.active_operation_mode is not None:
            #Here we actually do, what we are supposed to do. Reporting Power is handled by loop.
            if msg.active_operation_mode_id == self.rm_item.stand_by_id:
                #if charging, stop.
                if self.rm_item.dbus_monitor.get_value(EVCS_SERVICE, "/StartStop") != 0:
                    logger.info("Sending Stop!")
                    self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/StartStop", 0)
            elif msg.active_operation_mode_id == self.rm_item.no_car_id:
                #nothing todo, ensure off tho.
                if self.rm_item.dbus_monitor.get_value(EVCS_SERVICE, "/StartStop") != 0:
                    logger.info("Sending Stop!")
                    self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/StartStop", 0)
            else:
                #check, if a chargemode is selected - then verify the EVCS is running and select the proper amps.
                amps = self.rm_item.charge_mode_map[msg.active_operation_mode_id]
                if amps is not None:
                    logger.info("Setting amps to {}".format(amps))
                    self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/SetCurrent", amps)

                    #verify we are charging, else send Start.
                    if self.rm_item.dbus_monitor.get_value(EVCS_SERVICE, "/StartStop") != 1:
                        logger.info("Sending Start!")
                        self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/StartStop", 1)

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
        self.car_connected = None
        self.charge_mode_map = {}

        #we don't need a change handler, we read when required.
        dummy = {'code': None, 'whenToLog': 'configChange', 'accessLevel': None}
        self.dbus_monitor = DbusMonitor({
            "com.victronenergy.evcharger" : {
                "/Mode" : dummy,
                "/Status" : dummy,
                "/StartStop" : dummy,
                "/SetCurrent" : dummy,
                "/Ac/L1/Power" : dummy,
                "/Ac/L2/Power" : dummy,
                "/Ac/L3/Power" : dummy,
            }
        })

        super().__init__(self.s2_path, [self.ct_noctrl, self.ct_ombc], self.asset_details)
        
    def generate_operation_modes(self):
        #Generate the OperationModes and Transitions. We use the states
        #Standby, NoCar, 6A - 16A
        # we do this programmatically, cause this will be a lot of states and transitions.
        # For now, we assume that the power value associated with a 3 phase symmetric Load is the total,
        # i.e. 6000W symmetric load means 2000 Watt per phase.
        operation_modes_temp = []
        self.stand_by_id = uuid.uuid4()
        self.no_car_id = uuid.uuid4()

        operation_modes_temp=[
            OMBCOperationMode(
                id=self.stand_by_id,
                diagnostic_label="Standby",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
                    )
                ]
            ),
            OMBCOperationMode(
                id=self.no_car_id,
                diagnostic_label="NoCar",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
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
        for x in operation_modes_temp:
            for y in operation_modes_temp:
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

        

    async def loop(self):
        # check if car is connected or if we are charging.
        self.evcs_mode = self.dbus_monitor.get_value(EVCS_SERVICE, "/Status")
        car_connected = self.evcs_mode > 0

        if self._current_control_type == self.ct_ombc:
            if (self.car_connected is None or self.car_connected != car_connected):
                #State changed, need to update operation modes available.
                #TODO: Need to handle when car is fully charged.
                self.car_connected = car_connected
                self.stand_by_id = uuid.uuid4()
                self.no_car_id = uuid.uuid4()

                if self.car_connected:
                    #Standby + All amp states
                    logger.info("Car connected. Offering Standby and a State per Amp")

                    operation_modes_temp=[
                        OMBCOperationMode(
                            id=self.stand_by_id,
                            diagnostic_label="Standby",
                            abnormal_condition_only=False,
                            power_ranges=[
                                PowerRange(
                                    start_of_range=0,
                                    end_of_range=0,
                                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
                                )
                            ]
                        )
                    ]

                    #EVCS would get this from settings. (Charging with 6-16A)
                    self.charge_mode_map.clear()
                    for a in range(6,17):
                        op_mode_id = uuid.uuid4()
                        self.charge_mode_map[op_mode_id] = a
                        operation_modes_temp.append(
                            OMBCOperationMode(
                                id=op_mode_id,
                                diagnostic_label="Charge {} A".format(a),
                                abnormal_condition_only=False,
                                power_ranges=[
                                    PowerRange(
                                        start_of_range=a * 240 * 3, #3 phased charging.
                                        end_of_range=a * 240 * 3,
                                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
                                    )
                                ]
                            )   
                        )
                    
                    self.on_off_timer_id = uuid.uuid4()
                    self.amp_switch_timer_id = uuid.uuid4()

                    timers_temp = [
                        Timer(
                            id = self.on_off_timer_id,
                            diagnostic_label="On/Off Hysteresis 300s",
                            duration=300*1000
                        ),
                        Timer(
                            id = self.amp_switch_timer_id,
                            diagnostic_label="Amp Switch Delay",
                            duration=30*1000
                        )
                    ]

                    #Now, we need the transitions. We want a 30 second delay for adjusting the ChargeCurrent. 
                    #And we want a 5 Minute histerysis between Standby and any other state. 
                    transitions_temp = []
                    for left in operation_modes_temp:
                        for right in operation_modes_temp:
                            if left.id != right.id:
                                if left.id == self.stand_by_id or right.id == self.stand_by_id:
                                    #transition to or from standby, use 5 min hysteresis
                                    transitions_temp.append(
                                        Transition(
                                            id=uuid.uuid4(),
                                            from_=left.id,
                                            to=right.id,
                                            start_timers=[self.on_off_timer_id],
                                            blocking_timers=[self.on_off_timer_id],
                                            transition_duration=10000,
                                            abnormal_condition_only=False,
                                            transition_costs=None
                                        ) 
                                    )
                                else:
                                    #transition between ampstates, use 30 seconds hysteris.
                                    transitions_temp.append(
                                        Transition(
                                            id=uuid.uuid4(),
                                            from_=left.id,
                                            to=right.id,
                                            start_timers=[self.amp_switch_timer_id],
                                            blocking_timers=[self.amp_switch_timer_id],
                                            transition_duration=2000,
                                            abnormal_condition_only=False,
                                            transition_costs=None
                                        ) 
                                    )

                    self.system_description = OMBCSystemDescription(
                        message_id=uuid.uuid4(),
                        valid_from=datetime.now(timezone.utc),
                        operation_modes=operation_modes_temp,
                        transitions=transitions_temp,
                        timers=timers_temp
                    )

                    await self.send_msg_and_await_reception_status(self.system_description)

                    await self.send_msg_and_await_reception_status(
                        OMBCStatus(
                            message_id=uuid.uuid4(),
                            active_operation_mode_id="{}".format(self.stand_by_id),
                            operation_mode_factor=1.0, # hmmm? doesn't matter at this point.
                        )
                    )

                    for opm in self.system_description.operation_modes:
                        if opm.id == self.no_car_id:
                            self.active_operation_mode = opm

                    #that should be it.
                else:
                    #Only offer NoCar.
                    self.no_car_id = uuid.uuid4()

                    operation_modes_temp=[
                        OMBCOperationMode(
                            id=self.no_car_id,
                            diagnostic_label="No Car",
                            abnormal_condition_only=False,
                            power_ranges=[
                                PowerRange(
                                    start_of_range=0,
                                    end_of_range=0,
                                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
                                )
                            ]
                        )
                    ]

                    #Control Type has been selected by CEM. Advertise OperationModes.
                    self.system_description = OMBCSystemDescription(
                        message_id=uuid.uuid4(),
                        valid_from=datetime.now(timezone.utc),
                        operation_modes=operation_modes_temp,
                        transitions=[],
                        timers=[]
                    )

                    logger.info("Only offering 'NoCar' Mode.")
                    await self.send_msg_and_await_reception_status(self.system_description)

                    await self.send_msg_and_await_reception_status(
                        OMBCStatus(
                            message_id=uuid.uuid4(),
                            active_operation_mode_id="{}".format(self.no_car_id),
                            operation_mode_factor=1.0, # hmmm? doesn't matter at this point.
                        )
                    )

                    for opm in self.system_description.operation_modes:
                        if opm.id == self.no_car_id:
                            self.active_operation_mode = opm

                    #that should be it.

        #TODO: Implement Power Mode.
        await self.send_msg_and_await_reception_status(
            PowerMeasurement(
                message_id=uuid.uuid4(),
                measurement_timestamp=datetime.now(timezone.utc),
                values = [
                    PowerValue(
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                        value=self.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L1/Power", 0)
                    ),
                    PowerValue(
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2,
                        value=self.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L2/Power", 0)
                    ),
                    PowerValue(
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3,
                        value=self.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L3/Power", 0)
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

class EVCSMock(Service):
    
    def __init__(self, bus, name, instance):
        self.instance = instance
        super().__init__(bus, "{}.m_{}".format(name, instance))

    def setup_rm0(self):
        self.rm0 = RM0(
            '/Devices/0/S2', 
            AssetDetails(
                uuid.uuid4(),
                False,
                [CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC],
                Duration.from_milliseconds(5000),
                [Role(role=RoleType.ENERGY_CONSUMER, commodity=Commodity.ELECTRICITY)],
                None,
                "EVCS",
                "Victron EVCS",
                "22kW EVCS",
                "1.0",
                "1337"
            ), self)
        
        self.add_item(self.rm0)

    async def _loop(self):
        while True:
            await asyncio.sleep(5) #validate operation constraints every 10 seconds. 
            await self.rm0.loop()

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

if __name__ == "__main__":
    try:
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType
    except ImportError:
        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

    async def main():
        configure_logger()
        instance = 920
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        service = EVCSMock(bus, 'com.victronenergy.s2Mock', instance)
        
        service.add_item(TextItem("/ProductName", "ShellyMock"))
        service.add_item(IntegerItem("/DeviceInstance", instance))
        service.add_item(TextItem("/Mgmt/ProcessVersion", "1.0"))
        service.add_item(TextItem("/Mgmt/Connection", "dbus via aiovelib"))
        service.add_item(IntegerItem("/Connected", 1))
        service.add_item(TextItem('/CustomName', "Mock {}".format(instance)))

        service.add_item(IntegerItem('/Devices/0/DeviceInstance', instance))
        service.add_item(TextItem('/Devices/0/ServiceName', service.name))
        service.add_item(TextItem('/Devices/0/CustomName', None))
        service.add_item(TextItem('/Devices/0/IpAddress', None))
        service.add_item(IntegerItem('/Devices/0/Notification', 0))

        #Some to be discussed items. Suspect to User-Configuration.
        #Actual (generic) rm may use more here, like Power and Phase(s) used by the RSS.
        #(Because a generic RM controlling a shelly / switchable output can't know.)
        service.add_item(IntegerItem('/Devices/0/S2/Priority', 25)) #Priority , EMS will read
        service.add_item(IntegerItem('/Devices/0/S2/ConsumerType', 1)) # 0=primary load, 1=secondary load, EMS will read
        
        service.setup_rm0()

        #finally register our service.
        await service.register()

        asyncio.get_event_loop().create_task(service._loop())
        await service.bus.wait_for_disconnect()
    
    asyncio.get_event_loop().run_until_complete(main())