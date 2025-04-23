#!/usr/bin/env python
 
# imports
import sys
import os
import logging
import os
import platform
import asyncio
from builtins import Exception, int, str
from concurrent.futures import ThreadPoolExecutor
from typing import Dict

sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/s2')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/aiovelib')

from aiovelib.service import IntegerItem, TextItem, DoubleItem
from aiovelib.service import Service

#s2 related stuff
from s2 import S2ResourceManagerItem
from s2python.s2_control_type import S2ControlType, PEBCControlType

class RM1():
    class rm_control_type(PEBCControlType):
        def activate(self, conn):
            #TODO: Implement
            print("Received Activate Command")
            return super().activate(conn)
        
        def deactivate(self, conn):
            #TODO: Implement
            print("Received Deactivate Command")
            return super().deactivate(conn)
        
    rm_control_type = rm_control_type()

class RM2():
    class rm_control_type(PEBCControlType):
        def activate(self, conn):
            #TODO: Implement
            print("Received Activate Command")
            return super().activate(conn)
        
        def deactivate(self, conn):
            #TODO: Implement
            print("Received Deactivate Command")
            return super().deactivate(conn)
        
    rm_control_type = rm_control_type()

class ShellyS2Mock(Service):
    def __init__(self, bus, name, instance):
        self.instance = instance
        super().__init__(bus, "{}.m_{}".format(name, instance))

    def setup_rm1(self):
        self.rm1 = RM1()
        self.rm2 = RM2()
        self.add_item(S2ResourceManagerItem('/Devices/0/S2', [self.rm1.rm_control_type], None))
        self.add_item(S2ResourceManagerItem('/Devices/1/S2', [self.rm2.rm_control_type], None))

    async def _loop(self):
        while True:
            print("Looping...")

            await asyncio.sleep(10) 

if __name__ == "__main__":
    try:
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType
    except ImportError:
        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

    async def main():
        instance = 930
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        service = ShellyS2Mock(bus, 'com.victronenergy.s2Mock', instance)
        
        service.add_item(TextItem("/ProductName", "ShellyMock"))
        service.add_item(IntegerItem("/DeviceInstance", instance))
        #service.add_item(TextItem("/Mgmt/ProcessName", process_name))
        service.add_item(TextItem("/Mgmt/ProcessVersion", "1.0"))
        service.add_item(TextItem("/Mgmt/Connection", "dbus via aiovelib"))
        service.add_item(IntegerItem("/Connected", 1))
        service.add_item(TextItem('/CustomName', "ShellyMock {}".format(instance)))

        service.add_item(IntegerItem('/Devices/0/DeviceInstance', instance))
        #service.add_item(TextItem('/Devices/0/ProductName', self.productname))
        service.add_item(TextItem('/Devices/0/ServiceName', service.name))
        service.add_item(TextItem('/Devices/0/CustomName', None))
        service.add_item(TextItem('/Devices/0/IpAddress', None))
        service.add_item(IntegerItem('/Devices/0/Notification', 0))
        
        #Shelly 1: 10.10.20.57, only one rm on second port
        service.setup_rm1()
        
        #finally register our service.
        await service.register()

        asyncio.get_event_loop().create_task(service._loop())
        await service.bus.wait_for_disconnect()
    
    asyncio.get_event_loop().run_until_complete(main())