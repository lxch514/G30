"""RFC 6184 single-NAL/FU-A packetization, PT=96, 90 kHz, MTU=1200.

Keeps the wire format used by snappy-real's rtph264pay / MediaCodec player.
One encoded access unit has one timestamp and exactly one final marker bit.
"""
import re
import secrets
import struct

START_CODES = re.compile(b'\x00\x00(?:\x00)?\x01')

def annexb_nals(data):
    starts = list(START_CODES.finditer(data))
    for index, match in enumerate(starts):
        end = starts[index+1].start() if index+1 < len(starts) else len(data)
        nal = data[match.end():end]
        if nal:
            yield nal

class RtpPacketizer:
    def __init__(self, mtu=1200, sequence=None, ssrc=None):
        if not 256 <= mtu <= 1400:
            raise ValueError('MTU must be between 256 and 1400')
        self.mtu = mtu
        self.sequence = secrets.randbits(16) if sequence is None else sequence & 0xffff
        self.ssrc = secrets.randbits(32) if ssrc is None else ssrc & 0xffffffff

    def packet(self, payload, timestamp, marker):
        header = struct.pack('!BBHII', 0x80, 96 | (0x80 if marker else 0),
                             self.sequence, timestamp & 0xffffffff, self.ssrc)
        self.sequence = (self.sequence + 1) & 0xffff
        return header + payload

    def access_unit(self, data, capture_us):
        nals = list(annexb_nals(data))
        timestamp = capture_us * 90 // 1000
        maximum = self.mtu - 12
        for index, nal in enumerate(nals):
            last_nal = index == len(nals)-1
            if len(nal) <= maximum:
                yield self.packet(nal, timestamp, last_nal)
                continue
            indicator = (nal[0] & 0xe0) | 28
            kind = nal[0] & 31
            fragment_size = maximum - 2
            for offset in range(1, len(nal), fragment_size):
                fragment = nal[offset:offset+fragment_size]
                start, end = offset == 1, offset+len(fragment) == len(nal)
                payload = bytes((indicator, kind | (0x80 if start else 0) | (0x40 if end else 0))) + fragment
                yield self.packet(payload, timestamp, last_nal and end)
