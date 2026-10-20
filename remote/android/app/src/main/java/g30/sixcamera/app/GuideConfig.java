package g30.sixcamera.app;

/** Immutable, normalized image coordinates; camera0 uses margins, camera1 uses line heights. */
public final class GuideConfig {
    public final int camera;
    public final boolean enabled;
    public final int left,upper,right,lower;
    public GuideConfig(int camera,boolean enabled,int left,int upper,int right,int lower) {
        if(camera!=0&&camera!=1)throw new IllegalArgumentException("Unsupported guide camera");
        this.camera=camera;this.enabled=enabled;
        this.left=bound(left,0,40);this.right=bound(right,0,40);
        this.upper=bound(upper,0,camera==0?40:99);
        this.lower=bound(lower,camera==0?0:this.upper+1,camera==0?40:100);
    }
    private static int bound(int value,int min,int max){return Math.max(min,Math.min(max,value));}
    public static GuideConfig defaults(int camera) {
        return camera==0?new GuideConfig(0,true,12,15,12,15):new GuideConfig(camera,true,5,70,5,85);
    }
    public int[] values(){return new int[]{left,upper,right,lower};}
    public GuideConfig withEnabled(boolean value){return new GuideConfig(camera,value,left,upper,right,lower);}
    public GuideConfig withValue(int side,int value) {
        int[] next=values();
        if(camera==1&&side==1)value=Math.min(value,lower-1);
        if(camera==1&&side==3)value=Math.max(value,upper+1);
        next[side]=value;
        return new GuideConfig(camera,enabled,next[0],next[1],next[2],next[3]);
    }
    public String prefix(){return "guide"+camera+".";}
    public String[] keys(){return camera==0?new String[]{"left","top","right","bottom"}:new String[]{"left","red","right","yellow"};}
}
