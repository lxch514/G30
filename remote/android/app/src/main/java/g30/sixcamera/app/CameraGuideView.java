package g30.sixcamera.app;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

/** Manual image references only; no object detection or distance estimate. */
public final class CameraGuideView extends View {
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF image=new RectF(),box=new RectF();
    private boolean live;
    private GuideConfig config;

    public CameraGuideView(Context context,int camera) {
        super(context);config=GuideConfig.defaults(camera);setClickable(false);setLongClickable(false);setFocusable(false);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    }
    private float dp(float value){return value*getResources().getDisplayMetrics().density;}
    public void configure(GuideConfig value) {
        if(value.camera!=config.camera)throw new IllegalArgumentException("Guide camera mismatch");
        config=value;invalidate();
    }
    public GuideConfig config(){return config;}
    public void videoRect(float l,float t,float r,float b){image.set(l,t,r,b);invalidate();}
    public void live(boolean value){if(live!=value){live=value;invalidate();}}
    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        if(!config.enabled||image.width()<=0||image.height()<=0)return;
        int saved=canvas.save();canvas.clipRect(image);
        if(config.camera==1)drawHorizontal(canvas);else drawBoundary(canvas);
        drawCaption(canvas);canvas.restoreToCount(saved);
    }
    private void drawHorizontal(Canvas canvas) {
        float left=image.left+image.width()*config.left/100f,right=image.right-image.width()*config.right/100f;
        float red=image.top+image.height()*config.upper/100f,yellow=image.top+image.height()*config.lower/100f;
        paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(dp(1.8f));
        paint.setColor(live?0xFFFF5252:0xFF9EABB5);canvas.drawLine(left,red,right,red,paint);
        paint.setColor(live?0xFFFFD54F:0xFF9EABB5);canvas.drawLine(left,yellow,right,yellow,paint);
    }
    private void drawBoundary(Canvas canvas) {
        box.set(image.left+image.width()*config.left/100f,image.top+image.height()*config.upper/100f,
                image.right-image.width()*config.right/100f,image.bottom-image.height()*config.lower/100f);
        paint.setStyle(Paint.Style.FILL);paint.setColor(live?0x22000000:0x18000000);
        canvas.drawRect(image.left,image.top,image.right,box.top,paint);
        canvas.drawRect(image.left,box.bottom,image.right,image.bottom,paint);
        canvas.drawRect(image.left,box.top,box.left,box.bottom,paint);
        canvas.drawRect(box.right,box.top,image.right,box.bottom,paint);
        // The frame follows normalized source-image coordinates, not tile bounds.
        paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(dp(1.4f));
        paint.setColor(live?0xFFFFD54F:0xFF9EABB5);canvas.drawRect(box,paint);
        float length=Math.min(dp(15),Math.min(box.width(),box.height())*.12f);
        paint.setStrokeWidth(dp(2.3f));paint.setColor(live?0xFFFF6B63:0xFF9EABB5);
        float[] corners={box.left+length,box.top,box.left,box.top, box.left,box.top,box.left,box.top+length,
            box.right-length,box.top,box.right,box.top, box.right,box.top,box.right,box.top+length,
            box.left,box.bottom-length,box.left,box.bottom, box.left,box.bottom,box.left+length,box.bottom,
            box.right-length,box.bottom,box.right,box.bottom, box.right,box.bottom-length,box.right,box.bottom};
        canvas.drawLines(corners,paint);
    }
    private void drawCaption(Canvas canvas) {
        paint.setStyle(Paint.Style.FILL);paint.setTextSize(10*getResources().getDisplayMetrics().scaledDensity);
        paint.setTextAlign(Paint.Align.RIGHT);
        String caption=live?"参考线 · 非测距":"参考线 · 无实时画面";
        Paint.FontMetrics fm=paint.getFontMetrics();float pad=dp(4),x=image.right-dp(5),baseline=image.top+dp(3)-fm.top;
        paint.setColor(0xC0101D2B);
        canvas.drawRect(x-paint.measureText(caption)-pad,baseline+fm.top-pad/2,x+pad,baseline+fm.bottom+pad/2,paint);
        paint.setColor(live?0xFFFFD54F:0xFFCFD8DC);canvas.drawText(caption,x,baseline,paint);
    }
}
