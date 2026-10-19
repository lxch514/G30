import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rtp_h264 import RtpPacketizer,annexb_nals

class PacketizerTests(unittest.TestCase):
    def test_fragmentation_wire_properties_and_reassembly(self):
        original=b'\0\0\0\1\x67\x42\0\x28'+b'\0\0\1\x68\xce\x06'+b'\0\0\0\1\x65'+bytes(range(1,255))*40
        encoder=RtpPacketizer(sequence=65534,ssrc=0x12345678)
        packets=list(encoder.access_unit(original,1234567890123))
        self.assertTrue(all(len(p)<=1200 for p in packets))
        self.assertEqual(sum(bool(p[1]&128) for p in packets),1)
        self.assertTrue(packets[-1][1]&128)
        self.assertEqual([struct.unpack('!H',p[2:4])[0] for p in packets],[(65534+i)&65535 for i in range(len(packets))])
        self.assertEqual(len({p[4:8] for p in packets}),1)
        recovered=[];fragment=None
        for packet in packets:
            payload=packet[12:];kind=payload[0]&31
            if kind!=28:recovered.append(payload)
            else:
                if payload[1]&128:fragment=bytearray(((payload[0]&224)|(payload[1]&31),))
                self.assertIsNotNone(fragment)
                fragment.extend(payload[2:])
                if payload[1]&64:recovered.append(bytes(fragment));fragment=None
        self.assertEqual(recovered,list(annexb_nals(original)))

    def test_timestamp_wrap_and_mtu_validation(self):
        packet=next(RtpPacketizer().access_unit(b'\0\0\1\x65\x80',(2**32+90)*1000//90))
        self.assertLess(struct.unpack('!I',packet[4:8])[0],100)
        with self.assertRaises(ValueError):RtpPacketizer(1501)

if __name__=='__main__':unittest.main()
