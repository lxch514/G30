package g30.sixcamera.app;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** Same 16-byte six-camera framing as the proven browser H.264 transport.
 * Outbound-only TCP works through GR03 forwarding without reverse UDP mappings.
 */
public final class FramedFeed {
    private final String host;
    private final int port;
    private final VideoStream[] targets;
    private final AtomicBoolean running=new AtomicBoolean();
    private volatile HttpURLConnection connection;
    private Thread worker;
    public final AtomicLong receivedPayloadBytes=new AtomicLong(),connectionAttempts=new AtomicLong();
    public volatile long lastPacketMs;
    public FramedFeed(String host,int port,VideoStream[] targets){this.host=host;this.port=port;this.targets=targets;}
    public void start(){if(!running.compareAndSet(false,true))return;worker=new Thread(this::run,"g30-six-http-feed");worker.start();}
    public void stop(){running.set(false);HttpURLConnection c=connection;if(c!=null)c.disconnect();if(worker!=null)worker.interrupt();}
    private void run() {
        while(running.get()) {
            HttpURLConnection current=null;
            try {
                connectionAttempts.incrementAndGet();
                for(VideoStream target:targets){target.transportReset();target.status="TCP 等待关键帧";}
                current=(HttpURLConnection)new URL("http",host,port,"/api/feed").openConnection();connection=current;
                current.setConnectTimeout(2500);current.setReadTimeout(5000);current.setUseCaches(false);
                current.setRequestProperty("Connection","close");
                if(current.getResponseCode()!=200)throw new IllegalStateException("HTTP "+current.getResponseCode());
                try(DataInputStream input=new DataInputStream(new BufferedInputStream(current.getInputStream(),65536))) {
                    while(running.get()) {
                        int camera=input.readUnsignedByte(),flags=input.readUnsignedByte();
                        input.readUnsignedShort();input.readLong();
                        long length=Integer.toUnsignedLong(input.readInt());
                        if(camera>=6||length<4||length>4*1024*1024)throw new IllegalStateException("无效 H264 数据包");
                        byte[] data=new byte[(int)length];input.readFully(data);
                        VideoStream target=targets[camera];target.receivedPackets.incrementAndGet();
                        receivedPayloadBytes.addAndGet(length);target.receivedPayloadBytes.addAndGet(length);
                        lastPacketMs=android.os.SystemClock.elapsedRealtime();
                        target.acceptAccessUnit(data,(flags&1)!=0);
                    }
                }
            } catch(Exception error) {
                if(running.get())for(VideoStream target:targets)target.status="TCP 重连："+error.getMessage();
            } finally {
                if(current!=null)current.disconnect();connection=null;
            }
            if(running.get())try{Thread.sleep(800);}catch(InterruptedException stopped){break;}
        }
    }
}
