"""Validate reference-safe GOP recovery without reconnecting a TCP subscriber."""
import queue,struct,sys,threading,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from h264_encoder import H264Hub

class TcpResyncTests(unittest.TestCase):
    def test_overflow_requires_new_idr_for_each_camera(self):
        hub=H264Hub.__new__(H264Hub)
        hub.channels=[None]*6;hub.lock=threading.RLock();hub.subscribers=set();hub.tcp_resyncs=0
        tcp=hub.subscribe(recover=True);legacy=hub.subscribe()
        for i in range(6):hub.publish(i,b'\0\0\1\x65',True,100+i)
        for i in range(114):hub.publish(i%6,b'\0\0\1\x41',False,200+i)
        hub.publish(2,b'\0\0\1\x41',False,400)
        self.assertFalse(tcp.closed.is_set());self.assertTrue(legacy.closed.is_set())
        self.assertTrue(tcp.queue.empty());self.assertEqual(hub.tcp_resyncs,1)
        for i in range(6):hub.publish(i,b'\0\0\1\x41',False,500+i)
        self.assertTrue(tcp.queue.empty())
        hub.publish(4,b'\0\0\1\x65',True,600)
        hub.publish(0,b'\0\0\1\x41',False,601)
        hub.publish(4,b'\0\0\1\x41',False,602)
        got=[struct.unpack('!BBHQI',tcp.queue.get_nowait()[:16])[:2] for _ in range(2)]
        self.assertEqual(got,[(4,1),(4,0)]);self.assertTrue(tcp.queue.empty())

if __name__=='__main__':unittest.main()
