package g30.sixcamera.app;

import java.io.ByteArrayOutputStream;

/** Adapted from snappy-real RtpH264AccessUnitDepacketizer.
 * Adds bounds, timestamps, SSRC, padding and loss-to-IDR recovery.
 * Never emits a partially received access unit after a packet gap.
 */
public final class RtpH264Depacketizer {
    public interface Listener {
        void onFrame(byte[] annexB, boolean key, long timestamp);
        void onDiscontinuity();
    }
    private static final int MAX_AU = 4 * 1024 * 1024;
    private static final byte[] START = {0,0,0,1};
    private final Listener listener;
    private final ByteArrayOutputStream access = new ByteArrayOutputStream(128*1024);
    private ByteArrayOutputStream fragment;
    private int lastSequence = -1;
    private long timestamp = -1, ssrc = -1;
    private boolean damaged, waitKey = true;

    public RtpH264Depacketizer(Listener listener) { this.listener=listener; }
    private static int u(byte b) { return b & 255; }
    private static long u32(byte[] b,int o) { return ((long)u(b[o])<<24)|((long)u(b[o+1])<<16)|((long)u(b[o+2])<<8)|u(b[o+3]); }
    private void loss() {
        fragment=null;access.reset();damaged=true;waitKey=true;listener.onDiscontinuity();
    }
    public void reset() { lastSequence=-1;timestamp=-1;ssrc=-1;fragment=null;access.reset();damaged=false;waitKey=true; }

    public void consume(byte[] packet,int offset,int length) {
        if(offset<0||length<13||offset>packet.length-length)return;
        int end=offset+length;
        if((u(packet[offset])>>>6)!=2||(u(packet[offset+1])&127)!=96)return;
        int seq=(u(packet[offset+2])<<8)|u(packet[offset+3]);
        long ts=u32(packet,offset+4),source=u32(packet,offset+8);
        boolean newSource=ssrc!=-1&&source!=ssrc;
        if(newSource){loss();lastSequence=-1;timestamp=-1;}
        ssrc=source;
        if(lastSequence!=-1) {
            int delta=(seq-lastSequence)&65535;
            if(delta==0||delta>32768)return; // duplicate/late packet cannot rewind state
        }
        if(timestamp!=-1&&timestamp!=ts) {
            if(access.size()>0||fragment!=null)loss();
            access.reset();fragment=null;damaged=false;
        }
        timestamp=ts;
        if(lastSequence!=-1&&((seq-lastSequence)&65535)!=1)loss();
        lastSequence=seq;
        int payload=offset+12+4*(u(packet[offset])&15);
        if(payload>=end){loss();return;}
        if((u(packet[offset])&16)!=0) {
            if(payload+4>end){loss();return;}
            int words=(u(packet[payload+2])<<8)|u(packet[payload+3]);
            payload+=4+words*4;
        }
        if((u(packet[offset])&32)!=0) {
            int padding=u(packet[end-1]);
            if(padding==0||padding>end-payload){loss();return;}
            end-=padding;
        }
        if(payload>=end){loss();return;}
        if(!damaged) {
            int kind=u(packet[payload])&31;
            if(kind>=1&&kind<=23)append(packet,payload,end-payload);
            else if(kind==24)stap(packet,payload+1,end);
            else if(kind==28)fu(packet,payload,end);
            else loss();
        }
        if((u(packet[offset+1])&128)!=0) {
            if(fragment!=null)loss();
            if(!damaged&&access.size()>0) {
                byte[] frame=access.toByteArray();
                boolean key=containsNal(frame,5);
                if(key)waitKey=false;
                if(!waitKey)listener.onFrame(frame,key,ts);
            }
            access.reset();fragment=null;damaged=false;
        }
    }
    private void append(byte[] data,int offset,int size) {
        if(size<=0||access.size()+4+size>MAX_AU){loss();return;}
        access.write(START,0,4);access.write(data,offset,size);
    }
    private void stap(byte[] data,int offset,int end) {
        while(offset+2<=end) {
            int length=(u(data[offset])<<8)|u(data[offset+1]);offset+=2;
            if(length<=0||offset+length>end){loss();return;}
            append(data,offset,length);offset+=length;
        }
        if(offset!=end)loss();
    }
    private void fu(byte[] data,int offset,int end) {
        if(offset+2>=end){loss();return;}
        int indicator=u(data[offset]),header=u(data[offset+1]);
        boolean start=(header&128)!=0,finish=(header&64)!=0;
        if(start&&finish){loss();return;}
        if(start) {
            if(fragment!=null){loss();return;}
            fragment=new ByteArrayOutputStream(65536);
            fragment.write((indicator&224)|(header&31));
        }
        if(fragment==null){loss();return;}
        if(fragment.size()+end-offset-2>MAX_AU){loss();return;}
        fragment.write(data,offset+2,end-offset-2);
        if(finish) {
            byte[] nal=fragment.toByteArray();fragment=null;append(nal,0,nal.length);
        }
    }
    public static boolean containsNal(byte[] data,int kind) { return firstNal(data,kind)!=null; }
    public static byte[] firstNal(byte[] data,int kind) {
        for(int i=0;i+3<data.length;i++) {
            int start=-1;
            if(data[i]==0&&data[i+1]==0&&data[i+2]==1)start=i+3;
            else if(i+4<data.length&&data[i]==0&&data[i+1]==0&&data[i+2]==0&&data[i+3]==1)start=i+4;
            if(start<0||(data[start]&31)!=kind)continue;
            int end=data.length;
            for(int j=start+1;j+2<data.length;j++) {
                if(data[j]==0&&data[j+1]==0&&(data[j+2]==1||(j+3<data.length&&data[j+2]==0&&data[j+3]==1))){end=j;break;}
            }
            byte[] nal=new byte[4+end-start];System.arraycopy(START,0,nal,0,4);System.arraycopy(data,start,nal,4,end-start);return nal;
        }
        return null;
    }
}
