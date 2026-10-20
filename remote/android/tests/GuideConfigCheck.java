import g30.sixcamera.app.GuideConfig;

/** Host-side checks for line crossing and out-of-range persisted coordinates. */
public final class GuideConfigCheck {
    private static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    private static void valid(GuideConfig config) {
        check(config.left>=0&&config.left<=40&&config.right>=0&&config.right<=40,"Horizontal margins out of bounds");
        check(config.left+config.right<100,"Visible line span must remain positive");
        if(config.camera==0) {
            check(config.upper>=0&&config.lower>=0&&config.upper<=40&&config.lower<=40,"Frame margin out of bounds");
            check(config.upper+config.lower<100,"Frame height must remain positive");
        } else check(config.upper>=0&&config.lower<=100&&config.upper<config.lower,"Red line must remain above yellow");
    }
    public static void main(String[] args) {
        GuideConfig lines=GuideConfig.defaults(1);
        check(lines.upper==70&&lines.lower==85,"Initial lines should be in lower image");
        GuideConfig red=lines.withValue(1,99),yellow=lines.withValue(3,0);
        check(red.lower==lines.lower&&red.upper<lines.lower,"Dragging red across yellow must stop at yellow");
        check(yellow.upper==lines.upper&&yellow.lower>lines.upper,"Dragging yellow across red must stop at red");
        check(lines.upper==70&&lines.lower==85,"Preview changes must not mutate the cancel snapshot");
        check(!lines.withEnabled(false).enabled&&lines.enabled,"Visibility changes must not mutate original");
        int[] bad={Integer.MIN_VALUE,-100,0,1,40,70,99,100,200,Integer.MAX_VALUE};
        for(int camera=0;camera<2;camera++)for(int upper:bad)for(int lower:bad) {
            valid(new GuideConfig(camera,true,-20,upper,200,lower));
        }
        java.util.Random random=new java.util.Random(20261020);
        for(int camera=0;camera<2;camera++) {
            GuideConfig config=GuideConfig.defaults(camera);
            for(int i=0;i<10000;i++){config=config.withValue(random.nextInt(4),random.nextInt(501)-200);valid(config);}
        }
        System.out.println("PASS: lower-image defaults, line ordering, immutable cancel state, invalid saved values, 20000 drag updates");
    }
}
