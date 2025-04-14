try:
    import dbus_fast
except ImportError:
    from dbus_next.service import method
else:
    from dbus_fast.service import method


from aiovelib.service import Item


IFACE="com.victronenergy.VregLink"


class VregLinkItem(Item):

    def __init__(self, path, get_vreg, set_vreg):
        super().__init__(path)
        self.name = IFACE
        self.get_vreg = get_vreg
        self.set_vreg = set_vreg

    @method()
    async def GetVreg(self, vreg: 'q') -> 'qay':
        res = await self.get_vreg(vreg)
        return [
            int.from_bytes(res[:2], 'big'),
            res[2:]
        ]

    @method()
    async def SetVreg(self, vreg: 'q', data: 'ay') -> 'qay':
        res = await self.set_vreg(vreg, data)
        return [
            int.from_bytes(res[:2], 'big'),
            res[2:]
        ]
