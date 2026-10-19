import struct,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'board'))
from rtp_h264 import RtpPacketizer,annexb_nals
S=b'\0\0\0\1'
def au(kind,length):return S+bytes((kind,))+bytes(range(1,255))*(length//254)+b'\x80'
def normalized(data):return b''.join(S+n for n in annexb_nals(data))
key=S+b'\x67\x42\x80\x28'+S+b'\x68\xce\x80'+au(0x65,9000)
delta=au(0x41,6000)
cases=[]
def add(name,packets,frames,loss=0):cases.append((name,packets,[normalized(f) for f in frames],loss))

p=RtpPacketizer(sequence=65534,ssrc=10)
add('FU-A sequence wrap and SPS PPS',list(p.access_unit(key,1000000)),[key])
p=RtpPacketizer(sequence=50,ssrc=20)
a=list(p.access_unit(key,1000000));b=list(p.access_unit(delta,1033333));c=list(p.access_unit(key,1066666));d=list(p.access_unit(key,1100000))
add('Packet loss waits for intact keyframe',a+b[:2]+b[3:]+c+d,[key,key,key],1)
p=RtpPacketizer(sequence=100,ssrc=30)
a=list(p.access_unit(key,1000000));b=list(p.access_unit(delta,1033333))
add('Late previous timestamp cannot corrupt next AU',a+b[:1]+[a[-1]]+b[1:],[key,delta])
p=RtpPacketizer(sequence=0,ssrc=40)
a=list(p.access_unit(key,1000000));b=list(p.access_unit(delta,1033333))
add('Duplicate packet ignored',a+[a[-1]]+b,[key,delta])
p=RtpPacketizer(sequence=0,ssrc=50);q=RtpPacketizer(sequence=4000,ssrc=51)
add('Encoder restart SSRC joins at IDR',list(p.access_unit(key,1000000))+list(q.access_unit(delta,1033333))+list(q.access_unit(key,1066666)),[key,key],1)
p=RtpPacketizer(sequence=0,ssrc=60)
small=S+b'\x65\x12\x80';packet=list(p.access_unit(small,1000000))[0]
extended=bytes((packet[0]|0x10|0x20,))+packet[1:12]+b'\xbe\xde\x00\x01ABCD'+packet[12:]+b'\0\0\0\x04'
add('RTP extension and padding', [extended],[small])
p=RtpPacketizer(sequence=0,ssrc=70)
nals=list(annexb_nals(S+b'\x67\x42\x80'+S+b'\x68\xce\x80'+small))
stap=bytes((24,))+b''.join(struct.pack('!H',len(n))+n for n in nals)
add('STAP-A parameter sets and IDR',[p.packet(stap,90000,True)],[b''.join(S+n for n in nals)])
p=RtpPacketizer(sequence=0,ssrc=80)
add('Delta before key is suppressed',list(p.access_unit(delta,1000000))+list(p.access_unit(key,1033333)),[key])

out=Path(sys.argv[1] if len(sys.argv)>1 else 'rtp-fixtures.bin')
with out.open('wb') as f:
    f.write(struct.pack('!I',len(cases)))
    for name,packets,frames,loss in cases:
        encoded=name.encode();f.write(struct.pack('!H',len(encoded))+encoded)
        f.write(struct.pack('!I',len(packets)))
        for packet in packets:f.write(struct.pack('!I',len(packet))+packet)
        f.write(struct.pack('!I',len(frames)))
        for frame in frames:f.write(struct.pack('!I',len(frame))+frame)
        f.write(struct.pack('!I',loss))
print('Generated',len(cases),'wire compatibility fixtures')
