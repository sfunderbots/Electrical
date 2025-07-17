from .internal import (
    CAN,
    CAN_CLOCK,
    CAN_EFF_FLAG,
    CAN_ERR_FLAG,
    CAN_RTR_FLAG,
    CAN_SPEED,
    ERROR,
)

from .internal import SPIPICO as SPI
from .internal import CANFrame

class CanError:
    ERROR_OK = ERROR.ERROR_OK
    ERROR_FAIL = ERROR.ERROR_FAIL

class CanMsgFlag:
    RTR = CAN_ERR_FLAG
    EFF = CAN_EFF_FLAG

class CanMsg:
    def __init__(self, can_id = 0, data = None, flags = None):
        if flags:
            self.frame = CANFrame(can_id | flags, data)
        else:
            self.frame = CANFrame(can_id, data)
        self.is_remote_frame = self.frame.is_remote_frame
        self.is_extended_id = self.frame.is_extended_id
        self.can_id = self.frame.arbitration_id
        self.data = self.frame.data
        self.dlc = self.frame.dlc
    def _set_frame(self, frame):
        self.frame = frame
        self.is_remote_frame = self.frame.is_remote_frame
        self.is_extended_id = self.frame.is_extended_id
        self.can_id = self.frame.arbitration_id
        self.data = self.frame.data
        self.dlc = self.frame.dlc
    def _get_frame(self):
        return self.frame

SPI0_RX = 16
SPI0_CS0 = 17
SPI0_SCK = 18
SPI0_TX = 19

class CAN_1:
    ERROR = ERROR
    def __init__(self, board: str = "CANBed_RP2040", spi: int = 0, spics: int = 17):
        self.can = CAN(SPI(cs=17))
    def begin(self, bitrate: int = CAN_SPEED.CAN_250KBPS, canclock: int = CAN_CLOCK.MCP_8MHZ, mode: str = 'normal'):
        ret = self.can.reset()
        if ret != ERROR.ERROR_OK:
            return ret
        ret = self.can.setBitrate(bitrate, canclock)
        if ret != ERROR.ERROR_OK:
            return ret
        ret = self.can.setNormalMode()
        return ret
    def init_mask(self, mask, is_ext_id, mask_id):
        ret = self.can.setFilterMask(mask + 1, is_ext_id, mask_id)
        if ret != ERROR.ERROR_OK:
            return ret
        ret = self.can.setNormalMode()
        return ret
    def init_filter(self, ft, is_ext_id, filter_id):
        ret = self.can.setFilter(ft, is_ext_id, filter_id)
        if ret != ERROR.ERROR_OK:
            return ret
        ret = self.can.setNormalMode()
        return ret
    def checkReceive(self):
        return self.can.checkReceive()
    def recv(self):
        error, frame = self.can.readMessage()
        msg = CanMsg()
        msg._set_frame(frame)
        return error, msg
    def send(self, msg):
        frame = msg._get_frame()
        error = self.can.sendMessage(frame)
        return error