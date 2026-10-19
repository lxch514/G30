package g30.sixcamera.app;

import android.content.Context;
import android.view.View;
import android.view.ViewGroup;

/** All six TextureViews remain attached and nonzero-sized during fullscreen. */
public final class SixCameraGrid extends ViewGroup {
    private int selected=-1;
    private final CameraTile[] tiles=new CameraTile[6];
    public SixCameraGrid(Context context) {
        super(context);setClipChildren(true);setBackgroundColor(0xFF203142);
        for(int i=0;i<6;i++){tiles[i]=new CameraTile(context,i);addView(tiles[i]);}
    }
    public CameraTile tile(int i){return tiles[i];}
    public int selected(){return selected;}
    public void select(int index) {
        selected=index;
        // Keep background TextureViews drawable so their SurfaceTexture queues drain.
        // The opaque fullscreen tile covers them without detaching or hiding them.
        for(CameraTile tile:tiles){tile.setAlpha(1f);tile.setEnabled(index<0||tile.index==index);}
        if(index>=0)bringChildToFront(tiles[index]);requestLayout();
    }
    @Override protected void onMeasure(int widthSpec,int heightSpec) {
        int width=MeasureSpec.getSize(widthSpec),height=MeasureSpec.getSize(heightSpec);setMeasuredDimension(width,height);
        int gap=Math.max(2,Math.round(getResources().getDisplayMetrics().density*2));
        for(CameraTile tile:tiles) {
            boolean full=tile.index==selected;
            int col=tile.index%3,row=tile.index/3;
            int left=width*col/3+gap,top=height*row/2+gap;
            int right=width*(col+1)/3-gap,bottom=height*(row+1)/2-gap;
            tile.measure(MeasureSpec.makeMeasureSpec(full?width:Math.max(1,right-left),MeasureSpec.EXACTLY),
                         MeasureSpec.makeMeasureSpec(full?height:Math.max(1,bottom-top),MeasureSpec.EXACTLY));
        }
    }
    @Override protected void onLayout(boolean changed,int l,int t,int r,int b) {
        int width=r-l,height=b-t,gap=Math.max(2,Math.round(getResources().getDisplayMetrics().density*2));
        for(CameraTile tile:tiles) {
            if(tile.index==selected)tile.layout(0,0,width,height);
            else {int col=tile.index%3,row=tile.index/3;tile.layout(width*col/3+gap,height*row/2+gap,width*(col+1)/3-gap,height*(row+1)/2-gap);}
        }
    }
}
