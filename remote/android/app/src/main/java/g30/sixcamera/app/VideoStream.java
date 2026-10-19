package g30.sixcamera.app;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaFormat;
import android.os.Build;
import android.util.Log;
import android.view.Surface;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import java.util.Locale;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** One RTP receiver and one MediaCodec worker per camera, adapted from G20 VideoPanel.
 * Fullscreen never calls start/stop, changes the surface, or creates another decoder.
 */
public final class VideoStream {
    private static final String TAG="G30Video";
    private static final class Frame {
        final byte[] data;final boolean key;final long arrivedUs;
        Frame(byte[] data,boolean key){this.data=data;this.key=key;this.arrivedUs=System.nanoTime()/1000;}
    }
    private final int index,port;
    private final ArrayBlockingQueue<Frame> frames=new ArrayBlockingQueue<>(3);
    private final AtomicBoolean running=new AtomicBoolean(false),needKey=new AtomicBoolean(true),reset=new AtomicBoolean(false);
    public final AtomicLong receivedPackets=new AtomicLong(),renderedFrames=new AtomicLong(),losses=new AtomicLong(),resets=new AtomicLong();
    public final AtomicLong receivedPayloadBytes=new AtomicLong();
    public volatile long lastRenderedMs;
    public volatile String status="等待连接",codecName="",expectedHost="";
    public volatile boolean bound;
    private volatile Surface surface;
    private volatile int width=800,height=600,fps=30;
    private volatile DatagramSocket socket;
    private Thread receiveThread,decodeThread;
    private MediaCodec decoder;
    private Surface decodingSurface;
    private int decodingWidth,decodingHeight;

    public VideoStream(int index,int port){this.index=index;this.port=port;}
    public void setSurface(Surface value){surface=value;}
    public void setMode(int w,int h,int rate) {
        if(w!=width||h!=height||rate!=fps){width=w;height=h;fps=rate;discontinuity();}
    }
    public int port(){return port;}
    private void discontinuity(){losses.incrementAndGet();needKey.set(true);reset.set(true);frames.clear();}
    public void start() {
        start(true);
    }
    public void start(boolean udp) {
        if(!running.compareAndSet(false,true))return;
        if(udp)receiveThread=new Thread(this::receive,"g30-rtp-"+index);
        decodeThread=new Thread(this::decode,"g30-decoder-"+index);
        if(receiveThread!=null)receiveThread.start();decodeThread.start();
    }
    public void acceptAccessUnit(byte[] data,boolean key){if(running.get())offer(data,key);}
    public void transportReset(){discontinuity();}
    public void stop() {
        running.set(false);bound=false;
        DatagramSocket current=socket;if(current!=null)current.close();
        if(receiveThread!=null)receiveThread.interrupt();
        if(decodeThread!=null)decodeThread.interrupt();
        frames.clear();needKey.set(true);status="已暂停";
    }
    private void offer(byte[] data,boolean key) {
        if(needKey.get()&&!key)return;
        if(key)needKey.set(false);
        if(!frames.offer(new Frame(data,key))) {
            discontinuity();
            if(key){needKey.set(false);frames.offer(new Frame(data,true));}
        }
    }
    private void receive() {
        while(running.get()) {
            receiveOnce();
            if(running.get())try{Thread.sleep(400);}catch(InterruptedException stopped){break;}
        }
    }
    private void receiveOnce() {
        RtpH264Depacketizer parser=new RtpH264Depacketizer(new RtpH264Depacketizer.Listener(){
            @Override public void onFrame(byte[] data,boolean key,long timestamp){offer(data,key);}
            @Override public void onDiscontinuity(){discontinuity();}
        });
        try(DatagramSocket next=new DatagramSocket(null)) {
            if(!running.get())return;
            next.setReuseAddress(false);next.setReceiveBufferSize(4*1024*1024);next.setSoTimeout(700);
            next.bind(new InetSocketAddress(port));socket=next;bound=true;status="等待 camera"+index+" RTP";
            byte[] buffer=new byte[65535];DatagramPacket packet=new DatagramPacket(buffer,buffer.length);
            while(running.get()) {
                try {
                    packet.setLength(buffer.length);next.receive(packet);
                    String host=expectedHost;
                    if(!host.isEmpty()&&!packet.getAddress().getHostAddress().equals(host))continue;
                    receivedPackets.incrementAndGet();parser.consume(packet.getData(),packet.getOffset(),packet.getLength());
                } catch(SocketTimeoutException ignored) { }
            }
        } catch(Exception error) {
            if(running.get()){status="UDP "+port+"："+error.getMessage();Log.e(TAG,"camera"+index+" receiver",error);}
        } finally {bound=false;socket=null;}
    }
    private static MediaCodec createAvcDecoder() throws Exception {
        MediaCodecList list=new MediaCodecList(MediaCodecList.REGULAR_CODECS);
        for(MediaCodecInfo info:list.getCodecInfos()) {
            if(info.isEncoder())continue;
            boolean avc=false;
            for(String type:info.getSupportedTypes())if(type.equalsIgnoreCase("video/avc"))avc=true;
            if(!avc)continue;
            String name=info.getName().toLowerCase(Locale.ROOT);
            boolean hardware=Build.VERSION.SDK_INT>=29?info.isHardwareAccelerated():!(name.startsWith("omx.google.")||name.startsWith("c2.android."));
            if(hardware)return MediaCodec.createByCodecName(info.getName());
        }
        return MediaCodec.createDecoderByType("video/avc");
    }
    private void configure(Frame first,Surface target) throws Exception {
        releaseDecoder();
        byte[] sps=RtpH264Depacketizer.firstNal(first.data,7),pps=RtpH264Depacketizer.firstNal(first.data,8);
        if(sps==null||pps==null)throw new IllegalStateException("关键帧缺少 SPS/PPS");
        for(int attempt=0;attempt<2;attempt++) {
            MediaCodec candidate=createAvcDecoder();
            try {
                MediaFormat format=MediaFormat.createVideoFormat("video/avc",width,height);
                format.setByteBuffer("csd-0",ByteBuffer.wrap(sps));format.setByteBuffer("csd-1",ByteBuffer.wrap(pps));
                format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE,4*1024*1024);
                format.setInteger(MediaFormat.KEY_FRAME_RATE,fps);
                format.setInteger("priority",0);
                if(attempt==0&&Build.VERSION.SDK_INT>=30)format.setInteger(MediaFormat.KEY_LOW_LATENCY,1);
                candidate.configure(format,target,null,0);candidate.start();
                decoder=candidate;codecName=candidate.getName();decodingSurface=target;decodingWidth=width;decodingHeight=height;
                status="解码中";Log.i(TAG,"camera"+index+" "+codecName+" "+width+"x"+height);return;
            } catch(Exception error) {
                try{candidate.release();}catch(Exception ignored){}
                if(attempt==1)throw error;
            }
        }
    }
    private void releaseDecoder() {
        MediaCodec current=decoder;decoder=null;decodingSurface=null;
        if(current!=null){try{current.stop();}catch(Exception ignored){}try{current.release();}catch(Exception ignored){}}
    }
    private void decode() {
        MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();
        try {
            while(running.get()) {
                try {
                    Surface target=surface;
                    if(reset.getAndSet(false)||decodingSurface!=null&&(target!=decodingSurface||width!=decodingWidth||height!=decodingHeight)) {
                        releaseDecoder();resets.incrementAndGet();needKey.set(true);
                    }
                    if(target==null||!target.isValid()){releaseDecoder();frames.clear();needKey.set(true);Thread.sleep(20);continue;}
                    Frame frame=frames.poll(5,TimeUnit.MILLISECONDS);
                    if(frame!=null) {
                        if(System.nanoTime()/1000-frame.arrivedUs>250000){discontinuity();continue;}
                        if(decoder==null) {
                            if(!frame.key)continue;
                            configure(frame,target);needKey.set(false);
                        }
                        int input=decoder.dequeueInputBuffer(10000);
                        if(input<0){discontinuity();continue;}
                        ByteBuffer buffer=decoder.getInputBuffer(input);
                        if(buffer==null||buffer.capacity()<frame.data.length)throw new IllegalStateException("H264 帧超出解码缓冲");
                        buffer.clear();buffer.put(frame.data);
                        decoder.queueInputBuffer(input,0,frame.data.length,frame.arrivedUs,0);
                    }
                    if(decoder!=null) {
                        while(true) {
                            int output=decoder.dequeueOutputBuffer(info,0);
                            if(output>=0) {
                                boolean render=(info.flags&(MediaCodec.BUFFER_FLAG_CODEC_CONFIG|MediaCodec.BUFFER_FLAG_END_OF_STREAM))==0;
                                decoder.releaseOutputBuffer(output,render);
                                if(render){renderedFrames.incrementAndGet();lastRenderedMs=android.os.SystemClock.elapsedRealtime();status="画面正常";}
                            } else if(output==MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                                Log.i(TAG,"camera"+index+" output "+decoder.getOutputFormat());
                            } else break;
                        }
                    }
                } catch(InterruptedException stopped) { break; }
                catch(Exception error) {
                    Log.e(TAG,"camera"+index+" decode",error);status="解码重试："+error.getMessage();
                    releaseDecoder();frames.clear();needKey.set(true);resets.incrementAndGet();
                    try{Thread.sleep(300);}catch(InterruptedException stopped){break;}
                }
            }
        } finally { releaseDecoder(); }
    }
    public static String decoderCapabilities() {
        StringBuilder result=new StringBuilder();
        for(MediaCodecInfo info:new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos()) {
            if(info.isEncoder())continue;
            for(String type:info.getSupportedTypes())if(type.equalsIgnoreCase("video/avc")) {
                try {result.append(info.getName()).append("  maxInstances=").append(info.getCapabilitiesForType(type).getMaxSupportedInstances()).append('\n');}
                catch(Exception ignored){}
            }
        }
        return result.toString();
    }
}
