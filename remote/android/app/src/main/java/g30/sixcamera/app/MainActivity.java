package g30.sixcamera.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

public final class MainActivity extends Activity {
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final VideoStream[] streams=new VideoStream[6];
    private LinearLayout header;
    private SixCameraGrid grid;
    private TextView summary,telemetry;
    private Button settingsButton;
    private final ThroughputMeter receiveMeter=new ThroughputMeter(5000);
    private double encodedMbps;
    private long responseMs,lastStateMs,sourceResyncs,connectedAt;
    private ScheduledExecutorService network;
    private SharedPreferences preferences;
    private volatile boolean active;
    private volatile int generation;
    private String host;
    private int apiPort;
    private FramedFeed feed;
    private String networkMessage="正在连接开发板…";
    private final Runnable ticker=new Runnable(){@Override public void run(){
        if(!active)return;
        int live=0;long now=android.os.SystemClock.elapsedRealtime();
        for(int i=0;i<6;i++){grid.tile(i).refresh();if(streams[i]!=null&&streams[i].lastRenderedMs>0&&now-streams[i].lastRenderedMs<1800)live++;}
        summary.setText("G30 六路监控  ·  "+live+"/6 路显示  ·  "+networkMessage);
        FramedFeed current=feed;
        if(current!=null) {
            double rate=receiveMeter.sample(now,current.receivedPayloadBytes.get());
            boolean recentState=lastStateMs>0&&now-lastStateMs<5000;
            long age=current.lastPacketMs>0?now-current.lastPacketMs:Long.MAX_VALUE;
            long retries=Math.max(0,current.connectionAttempts.get()-1);
            String encoded=recentState?String.format(java.util.Locale.ROOT,"%.2f",encodedMbps):"—";
            String response=recentState?responseMs+"ms":"—";
            String stalled=age>1800?(current.lastPacketMs==0?" · 等待视频":" · 无视频 "+age/1000+"s"):"";
            boolean insufficient=recentState&&now-connectedAt>8000&&encodedMbps>.1&&rate<encodedMbps*.8;
            if(insufficient&&stalled.isEmpty())stalled=" · 接收不足";
            telemetry.setText(String.format(java.util.Locale.ROOT,"接收(5s) %.2f Mbps · 编码 %s Mbps · 响应 %s · 重连 %d · 追帧 %d%s",rate,encoded,response,retries,sourceResyncs,stalled));
            telemetry.setTextColor(age<1800&&recentState&&!insufficient?0xFF9CE8C7:0xFFFFCD82);
        }
        ui.postDelayed(this,1000);
    }};
    private int dp(int n){return Math.round(n*getResources().getDisplayMetrics().density);}
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        preferences=getSharedPreferences("g30-sixcam",MODE_PRIVATE);
        host=preferences.getString("host","192.168.144.10");apiPort=preferences.getInt("port",8096);
        LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(0xFF080E14);
        header=new LinearLayout(this);header.setGravity(Gravity.CENTER_VERTICAL);header.setPadding(dp(12),0,dp(8),0);header.setBackgroundColor(0xFF101D2B);
        LinearLayout headings=new LinearLayout(this);headings.setOrientation(LinearLayout.VERTICAL);headings.setGravity(Gravity.CENTER_VERTICAL);
        summary=new TextView(this);summary.setTextColor(Color.WHITE);summary.setTextSize(14);summary.setSingleLine(true);summary.setEllipsize(TextUtils.TruncateAt.END);
        headings.addView(summary,new LinearLayout.LayoutParams(-1,-2));
        telemetry=new TextView(this);telemetry.setText("正在统计 TCP 接收速率…");telemetry.setTextColor(0xFF9CE8C7);telemetry.setTextSize(12);telemetry.setSingleLine(true);telemetry.setEllipsize(TextUtils.TruncateAt.END);
        headings.addView(telemetry,new LinearLayout.LayoutParams(-1,-2));
        header.addView(headings,new LinearLayout.LayoutParams(0,-1,1));
        settingsButton=new Button(this);settingsButton.setText("连接设置");settingsButton.setTextSize(12);settingsButton.setOnClickListener(v->settings());
        header.addView(settingsButton,new LinearLayout.LayoutParams(dp(108),dp(42)));
        root.addView(header,new LinearLayout.LayoutParams(-1,dp(64)));
        grid=new SixCameraGrid(this);root.addView(grid,new LinearLayout.LayoutParams(-1,0,1));
        for(int i=0;i<6;i++){final int index=i;grid.tile(i).setOnClickListener(v->select(grid.selected()<0?index:-1));}
        setContentView(root);immersive();
    }
    private void immersive(){getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY|View.SYSTEM_UI_FLAG_FULLSCREEN|View.SYSTEM_UI_FLAG_HIDE_NAVIGATION|View.SYSTEM_UI_FLAG_LAYOUT_STABLE);}
    @Override public void onWindowFocusChanged(boolean focused){super.onWindowFocusChanged(focused);if(focused)immersive();}
    @Override public void onStart(){super.onStart();active=true;connect();ui.post(ticker);}
    @Override public void onStop(){active=false;disconnect();ui.removeCallbacksAndMessages(null);super.onStop();}
    private void select(int index){
        grid.select(index);summary.setVisibility(index<0?View.VISIBLE:View.GONE);settingsButton.setVisibility(index<0?View.VISIBLE:View.GONE);
        LinearLayout.LayoutParams params=(LinearLayout.LayoutParams)header.getLayoutParams();params.height=dp(index<0?64:36);header.setLayoutParams(params);immersive();
    }
    @Override public void onBackPressed(){if(grid.selected()>=0)select(-1);else super.onBackPressed();}

    private void connect() {
        disconnect();
        final int current=++generation;
        final String address=host;
        final int port=apiPort;
        networkMessage=address+":"+port+" 连接中";
        receiveMeter.reset();encodedMbps=0;lastStateMs=0;responseMs=0;sourceResyncs=0;connectedAt=android.os.SystemClock.elapsedRealtime();
        for(int i=0;i<6;i++) {
            VideoStream stream=new VideoStream(i,5700+i*2);streams[i]=stream;
            grid.tile(i).attach(stream);grid.tile(i).sourceStatus("等待板端状态");stream.start(false);
        }
        network=Executors.newSingleThreadScheduledExecutor();
        final VideoStream[] targets=streams.clone();
        feed=new FramedFeed(address,port,targets);feed.start();
        network.scheduleWithFixedDelay(()->heartbeat(current,address,port),200,2000,TimeUnit.MILLISECONDS);
    }
    private void disconnect() {
        generation++;
        ScheduledExecutorService old=network;network=null;if(old!=null)old.shutdownNow();
        if(feed!=null){feed.stop();feed=null;}
        for(int i=0;i<6;i++){if(streams[i]!=null)streams[i].stop();streams[i]=null;if(grid!=null)grid.tile(i).attach(null);}
    }
    private void heartbeat(int current,String address,int port) {
        if(!active||generation!=current)return;
        try {
            long began=android.os.SystemClock.elapsedRealtime();
            JSONObject state=request(address,port,"GET","/api/state",null);
            long roundTrip=android.os.SystemClock.elapsedRealtime()-began;
            JSONObject mode=state.getJSONObject("mode");
            int width=mode.getInt("width"),height=mode.getInt("height"),fps=mode.getInt("fps");
            double mbps=state.getInt("bitrate_per_camera")/1000000.0;
            boolean test=state.optBoolean("test_pattern");
            JSONArray cameras=state.getJSONArray("cameras");
            String[] labels=new String[6];
            double encodedTotal=0;
            for(int i=0;i<6;i++) {
                JSONObject camera=cameras.getJSONObject(i),source=camera.getJSONObject("source"),encoded=camera.getJSONObject("transcode");
                labels[i]=test?"测试图":!source.optBoolean("online")?"USB 相机未就绪":!encoded.optBoolean("online")?"编码未就绪":"H.264";
                if(source.optBoolean("online")&&encoded.optBoolean("online"))encodedTotal+=encoded.optDouble("encoded_mbps",0);
            }
            final double sourceMbps=encodedTotal;
            if(!active||generation!=current)return;
            ui.post(()->{
                if(!active||generation!=current)return;
                encodedMbps=sourceMbps;responseMs=roundTrip;sourceResyncs=state.optLong("http_resyncs_total",0);lastStateMs=android.os.SystemClock.elapsedRealtime();
                networkMessage=(test?"【测试图】 ":"")+address+"  ·  TCP  ·  "+String.format(java.util.Locale.ROOT,"%.0fM/路",mbps);
                for(int i=0;i<6;i++){if(streams[i]!=null)streams[i].setMode(width,height,fps);grid.tile(i).mode(width,height);grid.tile(i).sourceStatus(labels[i]);}
            });
        } catch(Exception error) {
            if(!active||generation!=current)return;
            ui.post(()->{if(active&&generation==current){networkMessage=address+":"+port+" · "+error.getMessage();for(int i=0;i<6;i++)grid.tile(i).sourceStatus("连接重试");}});
        }
    }
    private static JSONObject request(String host,int port,String method,String path,JSONObject data) throws Exception {
        HttpURLConnection connection=(HttpURLConnection)new URL("http",host,port,path).openConnection();
        connection.setConnectTimeout(1800);connection.setReadTimeout(1800);connection.setRequestMethod(method);
        connection.setRequestProperty("Connection","close");connection.setUseCaches(false);
        try {
            if(data!=null) {
                byte[] bytes=data.toString().getBytes(StandardCharsets.UTF_8);connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type","application/json");connection.setFixedLengthStreamingMode(bytes.length);
                try(java.io.OutputStream output=connection.getOutputStream()){output.write(bytes);}
            }
            int code=connection.getResponseCode();
            InputStream source=code>=400?connection.getErrorStream():connection.getInputStream();
            if(source==null)throw new IllegalStateException("HTTP "+code);
            ByteArrayOutputStream bytes=new ByteArrayOutputStream();
            try(InputStream input=source) {
                byte[] buffer=new byte[8192];int read;
                while((read=input.read(buffer))>=0){if(bytes.size()+read>262144)throw new IllegalStateException("状态响应过大");bytes.write(buffer,0,read);}
            }
            JSONObject value=new JSONObject(new String(bytes.toByteArray(),StandardCharsets.UTF_8));
            if(code>=400)throw new IllegalStateException(value.optString("error","HTTP "+code));return value;
        } finally { connection.disconnect(); }
    }
    private void settings() {
        LinearLayout form=new LinearLayout(this);form.setOrientation(LinearLayout.VERTICAL);form.setPadding(dp(20),dp(8),dp(20),dp(8));
        EditText hostInput=new EditText(this);hostInput.setSingleLine(true);hostInput.setHint("开发板 IP");hostInput.setText(host);form.addView(hostInput);
        EditText portInput=new EditText(this);portInput.setSingleLine(true);portInput.setInputType(InputType.TYPE_CLASS_NUMBER);portInput.setHint("HTTP 端口");portInput.setText(String.valueOf(apiPort));form.addView(portInput);
        TextView explanation=new TextView(this);explanation.setText("板端默认 192.168.144.10:8096。六路 H.264 共用一个 TCP 连接。\n接收：APP 实收 H.264 净荷的近 5 秒均速；编码：板端六路实际编码速率。响应是状态 HTTP 耗时，重连是额外连接尝试次数。追帧是板端积压后等关键帧恢复的累计次数，不是网络丢包率。以上不是光功率衰减或链路最大容量。\n每路 2M/6M/8M 在板端 scripts/set-bitrate.sh 设置。\n点击画面放大，再点画面或返回键回到六宫格；放大时保留顶部统计。");explanation.setPadding(0,dp(10),0,dp(8));form.addView(explanation);
        TextView codecs=new TextView(this);codecs.setText(VideoStream.decoderCapabilities());codecs.setTextSize(10);form.addView(codecs);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("G30 六路监控 · 连接设置").setView(form)
            .setNegativeButton("取消",null).setPositiveButton("保存并重连",null).create();
        dialog.setOnShowListener(ignored->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            String next=hostInput.getText().toString().trim();int nextPort;
            try{nextPort=Integer.parseInt(portInput.getText().toString());}catch(NumberFormatException error){portInput.setError("端口无效");return;}
            if(!next.matches("[A-Za-z0-9.-]{1,253}")||next.contains("..")){hostInput.setError("填写 IP 或主机名，不带 http://");return;}
            if(nextPort<1024||nextPort>65535){portInput.setError("端口范围 1024～65535");return;}
            disconnect();host=next;apiPort=nextPort;preferences.edit().putString("host",host).putInt("port",apiPort).apply();
            if(active)connect();dialog.dismiss();immersive();
        }));dialog.show();
    }
}
