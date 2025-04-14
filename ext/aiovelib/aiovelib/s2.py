import time
import math
import logging
import asyncio

try:
    import dbus_fast
except ImportError:
    from dbus_next.service import method, signal
else:
    from dbus_fast.service import method, signal

from aiovelib.service import Item


logger = logging.getLogger(__name__)


IFACE="com.victronenergy.S2"


class S2ServerItem(Item):

    client_id = None
    keep_alive_interval = None
    keep_alive_leeway = None
    last_seen = None

    _runningloop: asyncio.AbstractEventLoop
    _s2_dbus_disconnect_event: asyncio.Event

    def __init__(self, path):
        super().__init__(path)

        self.name = IFACE
        self._runningloop = asyncio.get_running_loop()

    @property
    def is_connected(self):
        return self.client_id is not None

    async def _create_connection(self, client_id: str, keep_alive_interval: int):
        self.client_id = client_id
        self.keep_alive_interval = keep_alive_interval
        self.keep_alive_leeway = int(math.ceil(0.2 * keep_alive_interval))
        self.last_seen = time.time()

        self._s2_dbus_disconnect_event = asyncio.Event()
        self._runningloop.create_task(self._monitor_keep_alive())

    async def _destroy_connection(self):
        self._s2_dbus_disconnect_event.set()

        self.client_id = None
        self.keep_alive_interval = None
        self.last_seen = None

    async def _monitor_keep_alive(self):
        diff = self.keep_alive_interval \
               + self.keep_alive_leeway

        while not self._s2_dbus_disconnect_event.is_set():
            await asyncio.sleep(self.keep_alive_interval)
            if self.last_seen and time.time() - self.last_seen > diff:
                logger.warning(f"{ self.client_id } missed KeepAlive")
                self._send_disconnect('KeepAlive missed')
                await self._destroy_connection()

    @method('Connect')
    async def _on_connect(self, client_id: 's', keep_alive_interval: 'i') -> 'b':
        if not self.is_connected:
            await self._create_connection(client_id, keep_alive_interval)
            return True
        else:
            return False

    @method('Disconnect')
    async def _on_disconnect(self, client_id: 's'):
        if client_id != self.client_id:
            self._send_disconnect('Not connected', client_id)
            return

        await self._destroy_connection()

    @method('Message')
    async def _on_message(self, client_id: 's', message: 's'):
        if client_id != self.client_id:
            self._send_disconnect('Not connected', client_id)
            return

        await self._on_s2_message(message)

    @method('KeepAlive')
    async def _on_keep_alive(self, client_id: 's') -> 'b':
        if client_id != self.client_id:
            self._send_disconnect('Not connected', client_id)
            return False

        self.last_seen = time.time()
        return True

    @signal('Message')
    def _send_message(self, message: str) -> 'ss':
        if not self.is_connected:
            raise Exception("No client connected")
        return [self.client_id, message]

    @signal('Disconnect')
    def _send_disconnect(self, reason: str, client_id=None) -> 'ss':
        return [client_id if client_id else self.client_id, reason]
