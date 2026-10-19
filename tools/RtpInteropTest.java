import g30.sixcamera.app.RtpH264Depacketizer;
import java.io.*;
import java.util.*;

/** Executes the actual Android RTP parser on Python-generated wire fixtures. */
public class RtpInteropTest {
    public static void main(String[] args) throws Exception {
        int passed=0;
        try(DataInputStream input=new DataInputStream(new FileInputStream(args[0]))) {
            int cases=input.readInt();
            for(int test=0;test<cases;test++) {
                String name=input.readUTF();
                List<byte[]> frames=new ArrayList<>();int[] losses={0};
                RtpH264Depacketizer parser=new RtpH264Depacketizer(new RtpH264Depacketizer.Listener(){
                    public void onFrame(byte[] data,boolean key,long timestamp){frames.add(data);}
                    public void onDiscontinuity(){losses[0]++;}
                });
                int count=input.readInt();
                for(int i=0;i<count;i++){byte[] packet=new byte[input.readInt()];input.readFully(packet);parser.consume(packet,0,packet.length);}
                int expected=input.readInt();
                if(frames.size()!=expected)throw new AssertionError(name+": expected "+expected+" frames, got "+frames.size());
                for(int i=0;i<expected;i++){byte[] data=new byte[input.readInt()];input.readFully(data);if(!Arrays.equals(data,frames.get(i)))throw new AssertionError(name+": frame mismatch "+i);}
                int minimumLoss=input.readInt();if(losses[0]<minimumLoss)throw new AssertionError(name+": missing discontinuity");
                System.out.println("PASS "+name+" frames="+frames.size()+" discontinuities="+losses[0]);passed++;
            }
        }
        System.out.println("All "+passed+" RTP interoperability cases passed");
    }
}
