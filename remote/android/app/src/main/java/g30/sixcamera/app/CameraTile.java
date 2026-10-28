package g30.sixcamera.app;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Matrix;
import android.graphics.SurfaceTexture;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.Surface;
import android.view.TextureView;
import android.widget.FrameLayout;
import android.widget.TextView;

/** TextureView allows in-place resize and correctly ordered label overlays. */
public final class CameraTile extends FrameLayout implements TextureView.SurfaceTextureListener {
    public final int index;
    private final TextureView texture;
    private final TextView labelView;
    private final TextView statusView;
    private final CameraGuideView guide;
    private Surface surface;
    private VideoStream stream;
    private int videoWidth=800,videoHeight=600;
    private long previousFrames,previousBytes,previousAt;
    private String sourceStatus="";
    public CameraTile(Context context,int index) {
        super(context);this.index=index;setBackgroundColor(Color.BLACK);setClipChildren(true);
        texture=new TextureView(context);texture.setSurfaceTextureListener(this);
        addView(texture,new LayoutParams(-1,-1));
        guide=index<2?new CameraGuideView(context,index):null;
        if(guide!=null)addView(guide,new LayoutParams(-1,-1));
        labelView=new TextView(context);labelView.setText("camera"+index);labelView.setTextColor(Color.WHITE);labelView.setTextSize(14);
        labelView.setTypeface(Typeface.MONOSPACE,Typeface.BOLD);labelView.setBackgroundColor(0xB8101D2B);labelView.setPadding(dp(6),dp(2),dp(6),dp(2));
        LayoutParams lp=new LayoutParams(-2,-2,Gravity.TOP|Gravity.LEFT);addView(labelView,lp);
        statusView=new TextView(context);statusView.setText("等待连接");statusView.setTextColor(0xFFFFCD82);statusView.setTextSize(11);
        statusView.setBackgroundColor(0xB8101D2B);statusView.setPadding(dp(7),dp(3),dp(7),dp(3));
        LayoutParams sp=new LayoutParams(-2,-2,Gravity.BOTTOM|Gravity.LEFT);sp.setMargins(dp(5),0,dp(5),dp(5));addView(statusView,sp);
        setClickable(true);
    }
    private int dp(int value){return Math.round(value*getResources().getDisplayMetrics().density);}
    public void attach(VideoStream value){stream=value;if(value!=null)value.setSurface(surface);previousFrames=0;previousBytes=0;previousAt=0;}
    public void mode(int w,int h){videoWidth=w;videoHeight=h;SurfaceTexture st=texture.getSurfaceTexture();if(st!=null)st.setDefaultBufferSize(w,h);fit();}
    public void rotation(int degrees){
        int angle=degrees==180?180:0;
        if(texture.getRotation()!=angle)texture.setRotation(angle);
    }
    public void sourceStatus(String value){sourceStatus=value;}
    public CameraGuideView guide(){return guide;}
    public void refresh() {
        VideoStream current=stream;if(current==null){if(guide!=null)guide.live(false);return;}
        long now=android.os.SystemClock.elapsedRealtime(),frames=current.renderedFrames.get();
        double fps=previousAt>0?(frames-previousFrames)*1000.0/Math.max(1,now-previousAt):0;
        long bytes=current.receivedPayloadBytes.get();
        double mbps=previousAt>0?(bytes-previousBytes)*8.0/(Math.max(1,now-previousAt)*1000.0):0;
        previousFrames=frames;previousBytes=bytes;previousAt=now;
        boolean live=current.lastRenderedMs>0&&now-current.lastRenderedMs<1800;
        if(guide!=null)guide.live(live);
        if(live) {
            statusView.setTextColor(0xFF9CE8C7);
            statusView.setText(String.format(java.util.Locale.ROOT,"%d×%d  %.1f fps  %.2f Mbps  %s",videoWidth,videoHeight,fps,mbps,sourceStatus));
        } else {
            statusView.setTextColor(0xFFFFCD82);
            statusView.setText(sourceStatus.isEmpty()?current.status:sourceStatus+" · "+current.status);
        }
    }
    private void fit() {
        int w=texture.getWidth(),h=texture.getHeight();if(w<=0||h<=0)return;
        double scale=Math.min((double)w/videoWidth,(double)h/videoHeight);
        Matrix matrix=new Matrix();matrix.setScale((float)(videoWidth*scale/w),(float)(videoHeight*scale/h),w/2f,h/2f);
        texture.setTransform(matrix);
        // Anchor to the visible image, including during fullscreen letterboxing.
        int left=(int)Math.round((w-videoWidth*scale)/2),top=(int)Math.round((h-videoHeight*scale)/2);
        if(guide!=null)guide.videoRect(left,top,w-left,h-top);
        LayoutParams labelParams=(LayoutParams)labelView.getLayoutParams();
        if(labelParams.leftMargin!=left||labelParams.topMargin!=top){labelParams.leftMargin=left;labelParams.topMargin=top;labelView.setLayoutParams(labelParams);}
    }
    @Override public void onSurfaceTextureAvailable(SurfaceTexture value,int width,int height) {
        value.setDefaultBufferSize(videoWidth,videoHeight);surface=new Surface(value);if(stream!=null)stream.setSurface(surface);fit();
    }
    @Override public void onSurfaceTextureSizeChanged(SurfaceTexture value,int width,int height){fit();}
    @Override public boolean onSurfaceTextureDestroyed(SurfaceTexture value) {
        if(stream!=null)stream.setSurface(null);if(surface!=null)surface.release();surface=null;return true;
    }
    @Override public void onSurfaceTextureUpdated(SurfaceTexture value){}
}
