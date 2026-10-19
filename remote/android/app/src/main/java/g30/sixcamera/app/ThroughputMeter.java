package g30.sixcamera.app;

import java.util.ArrayDeque;

/** Rolling application payload rate. Call with one monotonic clock and byte counter. */
public final class ThroughputMeter {
    private static final class Sample {
        final long time,bytes;
        Sample(long time,long bytes){this.time=time;this.bytes=bytes;}
    }
    private final long windowMs;
    private final ArrayDeque<Sample> samples=new ArrayDeque<>();
    public ThroughputMeter(long windowMs){if(windowMs<=0)throw new IllegalArgumentException("window");this.windowMs=windowMs;}
    public void reset(){samples.clear();}
    public double sample(long nowMs,long totalBytes) {
        if(!samples.isEmpty()) {
            Sample last=samples.peekLast();
            if(nowMs<last.time||totalBytes<last.bytes)samples.clear();
            else if(nowMs==last.time)samples.removeLast();
        }
        samples.addLast(new Sample(nowMs,totalBytes));
        long cutoff=nowMs-windowMs;
        while(samples.size()>2) {
            Sample first=samples.removeFirst();
            if(samples.peekFirst().time>cutoff){samples.addFirst(first);break;}
        }
        Sample first=samples.peekFirst();long elapsed=nowMs-first.time;
        return elapsed>0?(totalBytes-first.bytes)*8.0/(elapsed*1000.0):0;
    }
}
