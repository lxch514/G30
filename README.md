# G30 六路摄像头监控

板端项目路径：`/root/G30`。APP 名称：**G30 六路监控**。包名：`g30.sixcamera.app`，启动 Activity：`g30.sixcamera.app/.MainActivity`。

这是独立的视频项目，复用已有六路 USB 采集/MPP 编码、多路 TCP 数据封装，以及 snappy-real 的 Android MediaCodec 播放逻辑。没有复制电机、清洗、风机、CAN、串口或 MQTT 控制逻辑，不修改 `/root/snappy-real`。

## 默认行为

- 六台 `0bda:2292` UVC 摄像头，两个 Type-C 口各接一个 Hub、各三台；启动脚本运行时将两个 Type-C 控制器设为 Host。
- 当前六路 **1920×1080 / 30fps / H.264 / 每路 2Mbps**；早期 640×480 档位的验证记录保留作对照。
- APP 自动连接保存的开发板地址，默认图传口 `192.168.144.10:8096`，默认单连接 TCP。
- 主界面 3 列 × 2 行，`camera0`～`camera5` 贴住实际视频的左上角，字号 14sp；放大后同样跟随图像边缘。
- 保持原始宽高比完整显示，不拉伸、不裁剪，剩余区域为黑边。
- 单击任意画面进入全屏；再次点击画面或按返回键回到六宫格。
- v1.3.0：camera0 四周参考框，camera1 上红下黄两条平行横线；顶部“参考线”可选通道，或长按对应画面调整。两路可独立开关、调节、保存，保存完成后正常重上电仍保留；普通升级使用 install -r。详见 `docs/辅助线使用说明.md`。
- 顶部显示六路视频实收 Mbps（近 5 秒）、板端实际编码 Mbps、状态响应耗时、重连与追帧次数；放大单路时仍保留统计。每个画面底部显示该路码率与帧率。
- 全屏操作只改变布局；不重连视频，不创建第七个解码器，六个显示表面均保持连接。
- 相机断连后，板端采集监督进程会重新枚举并恢复六路；期间 APP 显示相机/连接状态。
- APP 仅使用 TCP，六路共用一个视频连接；状态请求也使用 HTTP/TCP。每个观看端有一份网络流量；编码结果共享。
- 不持续录制视频、不自动改动网卡地址、不注册系统开机自启服务。

## 文件布局

```text
/root/G30/
├─ board/                  USB 采集、硬件 H.264、RTP 和 HTTP API
│  ├─ uvc_cap_test.c       原六路采集适配：逐路 USB 额度与完整帧检查
│  ├─ jpeg_frame.h         JPEG 标记完整性检查
│  ├─ uvc_cap_test         setup 后生成的 ARM64 程序
│  ├─ h264_encoder.py     原六路编码适配版
│  ├─ capture_supervisor.py
│  ├─ rtp_h264.py
│  ├─ g30_service.py
│  ├─ debs/               libuvc/libusb 构建依赖
│  └─ root/               setup 解出的私有依赖
├─ config/g30.json         默认尺寸、帧率、码率、API 端口
├─ scripts/               setup / start / stop / set-bitrate
├─ remote/android/        完整 Windows/Linux Gradle Android 工程
├─ tools/                 RTP 互通与六路解码验证工具
├─ docs/                  复用说明、验证记录和用户操作说明
├─ dist/                  项目源码 ZIP 和已构建 APK
├─ logs/                  仅本项目的日志
└─ run/frames/            启动时挂载的专用 tmpfs 最新帧目录
```

`run/frames` 使用以本项目目录为挂载点的 tmpfs，避免连续把 JPEG 写入板载存储。停止脚本只卸载名为 `g30-frames` 的本项目 tmpfs。

## 板端首次构建与运行

在开发板终端以 root 执行：

```bash
cd /root/G30
bash scripts/setup.sh
bash scripts/start.sh
```

板端此前已完成 setup；2026-10-20 本次只更新 APP 和清理闲置临时文件，图传服务保持停止。需要使用时执行 start，不需要安装 APK 前重复 setup。

日常命令：

```bash
# 启动或重启六路采集和 H.264 服务
bash /root/G30/scripts/start.sh

# 停止本项目，不停止 snappy-real
bash /root/G30/scripts/stop.sh

# 六路采集和编码状态
curl -s http://127.0.0.1:8096/api/state | python3 -m json.tool

# 日志
tail -n 60 /root/G30/logs/capture.log
tail -n 60 /root/G30/logs/service.log
```

重启板子后执行 start 即可。程序会等待六台匹配相机接入；API 已启动不等于六路已经在线，应看 `all_online` 和每路 `source/transcode`。

## 2M、6M、8M

参数单位是**每路 Mbps**，不是六路总码率。只重启本项目 H.264 服务，保留 USB 采集：

```bash
bash /root/G30/scripts/set-bitrate.sh 2
bash /root/G30/scripts/set-bitrate.sh 6
bash /root/G30/scripts/set-bitrate.sh 8
```

六路目标合计分别是 12、36、48Mbps，另外有 TCP/IP 等开销。切换时 APP 自动重连并等待关键帧。当前已实测的交付档位为每路 2M，未把 6M/8M 视为已完成现场验收。

## Windows 构建、安装

推荐将源码包中的 `G30` 解压到 `D:\WORK\G30`。这是新目录，不覆盖现有 snappy-real Android 工程。

CMD：

```bat
D:
cd /d D:\WORK\G30\remote\android

set "JAVA_HOME=D:\G20\Java\jdk-11.0.32"
set "ANDROID_HOME=C:\Users\klnyf\AppData\Local\Android\Sdk"
set "PATH=%JAVA_HOME%\bin;%ANDROID_HOME%\platform-tools;%PATH%"

gradlew.bat --stop
gradlew.bat clean
gradlew.bat assembleDebug

adb devices

adb install -r app\build\outputs\apk\debug\app-debug.apk
adb shell am force-stop g30.sixcamera.app
adb shell am start -n g30.sixcamera.app/.MainActivity
```

首次或普通升级均使用 `install -r`，可保留 APP 中保存的板端地址及两路辅助线设置。不要先卸载或清除本 APP 数据。**不要卸载 `snappy.remote.app`，那是原来的 K2 APP。** 如果 adb 显示多台设备，请在每条 adb 命令中加入 `-s 目标序列号`。

工程使用 JDK 11、Gradle 7.3.3、Android Gradle Plugin 7.2.2、compileSdk/targetSdk 32、minSdk 26。安装目标 G30 为 Android 14；无需依赖云卓专用播放器 AAR 或 RCSDK。首次构建需要能解析 Gradle/Google/Maven 依赖，或有对应缓存。

`dist/g30-sixcamera-debug.apk` 可直接安装。当前 v1.3.0 构建和签名检查通过；G30 暂未接 USB，尚未安装。2026-10-19 的 camera0 主要功能实机测试是旧版证据，本次 camera1 与重新上电保留仍待实机验证。普通升级使用 install -r，无需卸载。

## 光纤网络连接

实际连接为六台相机 → 两个 Type-C 口各接 Hub → 开发板；板端 eth0 经 GR03 与 G30 的专用百兆光纤链路传输视频。没有继续使用 Host 排针转 USB。板端检查到：

```text
eth1：192.168.1.239/23
eth0：192.168.144.10/24
```

APP 右上角“连接设置”填写 **192.168.144.10，端口 8096**，固定使用 TCP。192.168.1.239 是电脑 SSH 管理地址，也可在接入对应管理网络时用于测试。G30 的 IP/掩码和路由需要与实际图传网络匹配，程序不擅自更改双方网络配置。

默认 TCP 连接流程：

```text
G30 发起 HTTP/TCP 到 192.168.144.10:8096/api/feed
→ 同一个 TCP 连接接收六路 H.264
→ 六个 Android MediaCodec 解码器
```

本 APP 只需图传链路双向允许 TCP 8096，不需要反向 UDP 映射，也不占用原 G20 的 5600/5602 视频端口和 14550 系列控制端口。工程内保留的 RTP 模块和测试工具用于对照原 G20 逻辑，不参与 APP 的通信。出现“USB 相机未就绪”时，应先查板端相机枚举和采集日志。

**144 网关**：同一个 192.168.144.0/24 网段内直接通信不需要网关转发。说明书要求 192.168.144.11 网关的段落针对“共享图传到电脑”等跨网段场景；不是看本 APP 必须替换板端默认网关。板端已经能在 eth0 看到 .11 邻居；本项目不把 eth1 的现有默认路由改到 .11。专用光纤接口在提供的说明书中未画出，没有据此虚构其模式菜单。

对频和光纤连接正常后，安装并打开本 APP，检查默认地址即可。先用 G30 浏览器打开 `http://192.168.144.10:8096/api/state`，能看到 JSON 就说明 G30 到板端 TCP 可达。这里是状态接口，不显示视频。无需再打开官方云台 FPV/QGC，也无需在官方软件中配置六条 RTSP 地址；本项目不提供 RTSP 服务。

用户确认已启用光纤模式。本次 G30 路由曾走 ar_net0，视频接收约 7.4Mbps；随后切到 eth0，纯 TCP 实测接口接收约 95.5Mbps。请以当前 `ip route get 192.168.144.10` 的结果和 APP 实收速率判断当前路径，不只看线缆已插入或板端 eth0 协商 100M。没有擅自修改 G30 的路由、无线开关或图传模式。

## 顶部带宽统计

详见 `docs/带宽统计与实测.md`。接收计数来自 APP 真正读到的 H.264 字节，不是把配置的 2M×6 固定显示出来；不包括 TCP/IP、重传及链路层开销。编码是板端实际产出速率，统计窗口不同会有小幅差异。

“响应”是状态 HTTP 请求的完整耗时，包含服务端处理，不等于纯网络 RTT 或画面延迟。“重连”是本轮 APP 连接的额外 TCP 连接尝试；“追帧”是本轮板端服务中 TCP 订阅积压后重新等待各路 IDR 的次数，不是丢包率。停止/重启 APP 或板端服务后，相应累计计数会重新开始。稳定运行 8 秒后，若接收低于编码的 80%，顶部会提示“接收不足”。

这不是光功率仪，也不显示虚构的 dBm/衰减百分比。TCP 会重传，不能根据两侧码率差直接计算网络丢包率或光功率衰减；应结合接收速率、追帧/重连增量和各路帧率观察。光功率需要光模块诊断或光功率计。

### Type-C 重启后的处理

本次实机发现板子重启后 `fc000000.usb`、`fc400000.usb` 回到 Device 模式，此时两个 Hub 和六台相机全部消失。运行时切到 Host 后，六台立即恢复枚举。`start.sh` 已调用 `prepare-usb-host.sh` 完成此步骤；不修改设备树或系统启动文件，也不改 Type-C 供电策略。启动时若发现该口已有活动的 USB Device 连接，会提示冲突并停止。停止视频项目后保留 Host 角色；板子重启后再执行 start。

camera 编号按 USB Bus 升序、Hub 物理端口降序分配，不依赖 `/dev/videoN`；固定两个 Hub 与相机的插口位置，避免编号变化。

## 改分辨率或帧率

停止本项目后编辑 `config/g30.json` 的 `width/height/fps`，再 start。只能选择摄像头真正支持的 MJPEG 模式。APP 从 API 获取尺寸和帧率后自动跟随，仍保持完整比例。

当前已实测六路 1920×1080/30fps、每路 2Mbps，APP 无需重装。1080p 修复还调整了 USB 采集额度，不能只改尺寸后套用所有相机。当前 `payload_limits` 按 camera0～5 依次为 `[3072,1024,1024,1536,1536,1536]`，覆盖统一的 `payload_limit`；单位为 USB 每微帧字节额度，与网络视频码率不同。它针对当前两个 Hub 和相机插口验证，请保持插口位置。详见 `docs/重连排查与修复.md`，上午的历史修复记录保留于 `docs/camera2-1080p修复说明.md`。其他模式、场景变化及更换相机仍需复测。

## 验证结果与边界

详见 `docs/验证记录.md`、`docs/http-capture.json` 和 `docs/http-decode.json`。

已完成：板端 C 编译与插件检查；六台真实 UVC 同时采集与硬件 H.264 编码；从板端到电脑通过一个 HTTP/TCP 连接同时接收六路、每路 300 帧；六路码流再用 FFmpeg 各解码 300 帧、合计 1800 帧，无解码错误；原 G20 RTP 对照协议的 8 类互通测试；Android debug APK 构建。

后续已完成 G30 的 ADB 安装、六路真实画面显示、逐路放大和返回、前后台恢复及光纤 TCP 吞吐测试，详见最新验证记录。尚未完成 6M/8M 现场测试、长时间可靠性、光学端到端延迟和光功率测量。MediaCodec 的 maxInstances 是能力上限提示，实际可并发数量还受运行资源影响；APP 设置页显示解码器能力，并在每路输出解码故障状态。

## 依赖与原项目保护

setup 只把随包 deb 解压到 `/root/G30/board/root`，不全局安装或替换库。依赖板端已有 gcc、Python GI/GStreamer、Rockchip MPP 插件与驱动、curl、util-linux。

运行时只使用本项目路径存储程序、配置、PID、日志与最新帧；USBFS 内存额度低于 64MB 时，start 会像原六路程序一样做运行时提高，并通过驱动 debugfs 接口把两路 Type-C 切到 Host。没有修改持久化内核参数、网络配置、原清洗工程或系统服务。
