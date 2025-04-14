#!/usr/bin/env python
 
# imports
import sys
import os
import logging
import os
import platform
import uuid
import requests #type:ignore
import asyncio
from datetime import datetime, timedelta, timezone
import json
from builtins import Exception, int, str
from concurrent.futures import ThreadPoolExecutor
from typing import Dict
import signal

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler(sys.stdout))

sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/s2')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/aiovelib')

if sys.version_info.major == 2:
    import gobject # type: ignore
else:
    from gi.repository import GLib as gobject # type: ignore

from dbusmonitor import DbusMonitor # type: ignore
from dbus.mainloop.glib import DBusGMainLoop # type: ignore
from aiovelib.service import IntegerItem, TextItem, DoubleItem
from aiovelib.service import Service
import asyncio_glib #type:ignore
asyncio.set_event_loop_policy(asyncio_glib.GLibEventLoopPolicy())

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

EVCS_SERVICE = "com.victronenergy.evcharger"

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
    
    async def handle_instruction(self, conn, msg, send_okay):
        try:
            #logger.info("Instruction received: {}".format(msg))
            prior_id = "{}".format(self.active_operation_mode.id) if self.active_operation_mode is not None else None

            for op_mode in self.rm_item.system_description.operation_modes:
                if op_mode.id == msg.operation_mode_id:
                    logger.info("Instruction received: {}".format(op_mode.diagnostic_label))
                    self.active_operation_mode = op_mode
                    break

            self.rm_item._send_and_forget(
                OMBCStatus(
                    message_id=uuid.uuid4(),
                    active_operation_mode_id="{}".format(self.active_operation_mode.id),
                    previous_operation_mode_id=prior_id,
                    transition_timestamp=datetime.now(timezone.utc),
                    operation_mode_factor=msg.operation_mode_factor
                )
            )

            if self.active_operation_mode is not None:
                #Here we actually do, what we are supposed to do. Reporting Power is handled by loop.
                if msg.operation_mode_id == self.rm_item.stand_by_id:
                    #if charging, stop.
                    if self.rm_item.dbus_monitor.get_value(EVCS_SERVICE, "/StartStop") != False:
                        logger.info("Sending Stop!")
                        self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/StartStop", False)
                elif msg.operation_mode_id == self.rm_item.no_car_id:
                    #nothing todo, ensure off tho.
                    if self.rm_item.dbus_monitor.get_value(EVCS_SERVICE, "/StartStop") != False:
                        logger.info("Sending Stop!")
                        self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/StartStop", False)
                else:
                    #check, if a chargemode is selected - then verify the EVCS is running and select the proper amps.
                    amps = self.rm_item.charge_mode_map[msg.operation_mode_id]
                    if amps is not None:
                        logger.info("Setting amps to {}".format(amps))
                        self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/SetCurrent", amps)

                        #verify we are charging, else send Start.
                        if self.rm_item.dbus_monitor.get_value(EVCS_SERVICE, "/StartStop") != True:
                            logger.info("Sending Start!")
                            self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/StartStop", True)
            
            await send_okay

        except Exception as ex:
            logger.error("Error in handle_instructions", exc_info=ex)

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
        self.evcs_status = None
        self.stand_by_id = None
        self.no_car_id = None
        self.charged_id = None

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
                "/Ac/L3/Power" : dummy
            }
        })

        super().__init__(self.s2_path, [self.ct_noctrl, self.ct_ombc], self.asset_details)

        #find the ev charger, we have: 
        for (sn, instance) in self.dbus_monitor.get_service_list().items():
            if (sn.startswith("com.victronenergy.evcharger")):
                logger.info("Found EVCS: {}".format(sn))
                global EVCS_SERVICE
                EVCS_SERVICE = sn
        
    def _dbusValueChanged(self, dbusServiceName, dbusPath, dict, changes, deviceInstance):
        try:
            logger.info(self, "Change on dbus for {0} (new value: {1})".format(dbusServiceName, changes['Value'])) 
        except Exception as ex:
            logger.error("Exception", exc_info=ex)

    async def _destroy_connection(self):
        await super()._destroy_connection()

        #debug purpose: When we have a disconnect, simply restart the service. 
        #this ensures the services are restarted with an eventually updated file.
        logger.info("Connection destroyed, ending execution to allow service to restart.")
        sys.exit(0)

    async def loop(self):
        try:
            # check if the EVCS status has changed, based on that, we may need to offer different OMBC states available. 
            evcs_status = self.dbus_monitor.get_value(EVCS_SERVICE, "/Status")
            
            if (evcs_status != self.evcs_status):
                old_evcs_status = self.evcs_status
                self.evcs_status = evcs_status
                #has changed, determine a suitable model to be send. 
                if (self.evcs_status == 3):
                    #Only offer Charged.
                    self.charged_id = uuid.uuid4()

                    operation_modes_temp=[
                        OMBCOperationMode(
                            id=self.charged_id,
                            diagnostic_label="Charged",
                            abnormal_condition_only=False,
                            power_ranges=[
                                PowerRange(
                                    start_of_range=0,
                                    end_of_range=0,
                                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
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

                    logger.info("Only offering 'Charged' Mode.")
                    await self.send_msg_and_await_reception_status(self.system_description)

                    await self.send_msg_and_await_reception_status(
                        OMBCStatus(
                            message_id=uuid.uuid4(),
                            active_operation_mode_id="{}".format(self.charged_id),
                            operation_mode_factor=1.0, # hmmm? doesn't matter at this point.
                        )
                    )

                    for opm in self.system_description.operation_modes:
                        if opm.id == self.charged_id:
                            self.active_operation_mode = opm

                elif self.evcs_status == 0:
                    #Car has just disconnected, only offer no car. 
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
                                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
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

                elif (old_evcs_status is None or old_evcs_status==0 or old_evcs_status==3):
                    #Car state is anything but disconnected / fully charged. Offer Chargemodes. 
                    logger.info("Car connected. Offering Standby and a State per Amp")
                    self.stand_by_id = uuid.uuid4()

                    operation_modes_temp=[
                        OMBCOperationMode(
                            id=self.stand_by_id,
                            diagnostic_label="Standby",
                            abnormal_condition_only=False,
                            power_ranges=[
                                PowerRange(
                                    start_of_range=0,
                                    end_of_range=0,
                                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                                )
                            ]
                        )
                    ]

                    #EVCS would get this from settings. (Charging with 6-32A)
                    self.charge_mode_map.clear()
                    for a in range(6,33):
                        op_mode_id = uuid.uuid4()
                        self.charge_mode_map[op_mode_id] = a
                        operation_modes_temp.append(
                            OMBCOperationMode(
                                id=op_mode_id,
                                diagnostic_label="Charge {} A".format(a),
                                abnormal_condition_only=False,
                                power_ranges=[
                                    PowerRange(
                                        start_of_range=a * 240, #1 phased charging.
                                        end_of_range=a * 240,
                                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
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
                            duration=5*1000
                        )
                    ]

                    #Now, we need the transitions. We want a 15 second delay for adjusting the ChargeCurrent. 
                    #And we want a 5 Minute histerysis between Standby and any other state. 
                    #Transitions should onl be possible: Standby <-> 6 <-> 7 <-> ... <-> 15 <-> 16
                    #(So the EVCS can't jump from 6 to 16 or vice versa during operation.)
                    transitions_temp = []
                    for left in operation_modes_temp:
                        for right in operation_modes_temp:
                            if left.id != right.id: #transitions to self, we don't need.
                                if left.id == self.stand_by_id:
                                    #transition to or from standby, use 5 min hysteresis.
                                    #only legit to the 6A State.
                                    if self.charge_mode_map[right.id] == 6:
                                        logger.info("Creating transition from '{}' to '{}' with 5min Hysteresis".format(left.diagnostic_label, right.diagnostic_label))
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
                                elif right.id == self.stand_by_id:
                                    #transition to or from standby, use 5 min hysteresis.
                                    #only legit to the 6A State.
                                    if self.charge_mode_map[left.id] == 6:
                                        logger.info("Creating transition from '{}' to '{}' with 5min Hysteresis".format(left.diagnostic_label, right.diagnostic_label))
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
                                    #only legit, if amp difference is 1 between both states. 
                                    left_amp = self.charge_mode_map[left.id]
                                    right_amp = self.charge_mode_map[right.id]

                                    if (abs(left_amp - right_amp) == 1):
                                        logger.info("Creating transition from '{}' to '{}' with 15s Hysteresis".format(left.diagnostic_label, right.diagnostic_label))
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

            if self._current_control_type == self.ct_ombc:
                l1_power = self.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L1/Power") or 0.0
                l2_power = self.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L2/Power") or 0.0
                l3_power = self.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L3/Power") or 0.0
                set_current = self.dbus_monitor.get_value(EVCS_SERVICE, "/SetCurrent") or 0

                logger.info("Reporting power: {}/{}/{} (Current: {})".format(l1_power, l2_power, l3_power, set_current))

                await self.send_msg_and_await_reception_status(
                    PowerMeasurement(
                        message_id=uuid.uuid4(),
                        measurement_timestamp=datetime.now(timezone.utc),
                        values = [
                            PowerValue(
                                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                                value=l1_power
                            ),
                            PowerValue(
                                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2,
                                value=l2_power
                            ),
                            PowerValue(
                                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3,
                                value=l3_power
                            )
                        ]
                    )
                )

        except Exception as ex:
            logger.error("Exception in loop: ", exc_info=ex)
    
    async def _on_s2_message(self, message):
        #FIXME: Current S2 Implementation uses S2Parser, which fails for certain messages. So, we have to 
        #ignore confirmation errors and handle that manually until fixed. 
        try:
            jmsg = json.loads(message)
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
                [CommodityQuantity.ELECTRIC_POWER_L1],
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
            await asyncio.sleep(2)
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
        from dbus.mainloop.glib import DBusGMainLoop # type: ignore
        DBusGMainLoop(set_as_default=True)

        configure_logger()

        def restart_service(signum, frame):
            logger.info("Received sigterm, ending service.")
            sys.exit(0)

        signal.signal(signal.SIGTERM, restart_service)

        instance = 920
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        service = EVCSMock(bus, 'com.victronenergy.switch', instance)
        
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