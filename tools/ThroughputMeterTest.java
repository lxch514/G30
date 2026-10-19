import g30.sixcamera.app.ThroughputMeter;

public class ThroughputMeterTest {
    private static void equal(double actual,double expected){if(Math.abs(actual-expected)>0.000001)throw new AssertionError(actual+" != "+expected);}
    public static void main(String[] args) {
        ThroughputMeter meter=new ThroughputMeter(5000);
        for(int i=0;i<=10;i++)equal(meter.sample(i*1000L,i*1500000L),i==0?0:12);
        equal(meter.sample(15000,15000000),0); // five seconds without any payload
        equal(meter.sample(16000,0),0); // new connection / counter reset
        equal(meter.sample(17000,750000),6);
        equal(meter.sample(17000,750000),6); // duplicate clock sample
        equal(meter.sample(100,1000),0); // monotonic clock restarted
        equal(meter.sample(1100,1001000),8);
        meter.reset();equal(meter.sample(2000,9000000),0);
        equal(meter.sample(7000,14000000),8);
        System.out.println("PASS throughput: steady rate, idle, byte reset, duplicate clock, clock rollback and explicit reset");
    }
}
