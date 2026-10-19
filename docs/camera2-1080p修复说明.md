# camera2 1080p 启动修复

> 本文为上午 camera2 修复的历史记录。重新接线后当前 USB 额度为 [3072,1024,1024,1536,1536,1536]；板端历史备份已按用户要求删除。最新情况见 `重连排查与修复.md`。

日期：2026-10-19。板端目录 `/root/G30`，当前 1920×1080、30fps、H.264 每路 2Mbps、六路单连接 TCP。现有 G30 APP v1.1.0 自动跟随尺寸，本次无需重新安装 APK。

## 原因与修改

原配置对六台相机统一强制 USB payload 为 1536。camera2 在 1080p 下交付的 JPEG 帧被截断，抽样 4/4 没有完整 EOI 结束标记；硬件 JPEG 解码无法持续输出，编码器反复超时重建，APP 因而显示“编码未就绪 / TCP 等待关键帧”。旧统计只查 JPEG 开头，所以看似采集 30fps，不能证明帧完整。

已在 `config/g30.json` 配置逐路额度：

```json
"payload_limit": 1536,
"payload_limits": [1024, 1024, 3072, 1536, 1536, 1536]
```

`payload_limits` 顺序对应 camera0～5，优先于统一额度。camera2 使用相机原协商的 3072，同一 Hub 的 camera0/1 使用 1024，另一 Hub 保留 1536。单纯把六路一起改为 1024 的对照试验没有修好 camera2，还影响了另一通道；最终采用逐路配置。

这是 USB 等时传输每微帧的字节额度，不是 H.264 的 2M 网络码率。USB 先传 MJPEG 到板子，板子再压缩成 2M H.264 走光纤；这两段的带宽不能混为一谈。该分配已在当前摄像头、插口和场景验证，不代表任何相机或动态复杂场景都自动适用。

`board/uvc_cap_test.c`、新增 `board/jpeg_frame.h` 会检查完整 JPEG 标记结构，跳过 APP/COM 内可能出现的缩略图结束标记，拒绝截断帧，去除完整图片后的填充。没有给残缺图片强行补结束符。API 新增 `received_frames`、`incomplete_frames`，`frames` 和采集 fps 仅统计完整帧。这是帧结构检查，不替代完整 JPEG 熵编码校验。

## 运行与复测

本次六路各 300 帧、合计 1800 帧均以真实 1920×1080 成功解码。120.37 秒连续观察六路在线，残缺帧、坏头和编码重建增量均为 0；G30 的 camera2 全屏与返回六宫格也通过。

本次已重新编译并启动板端程序。日常重启：

```bash
bash /root/G30/scripts/start.sh
```

换板完整复制项目后首次执行（需相同 BSP/MPP 环境）：

```bash
cd /root/G30
bash scripts/setup.sh
bash scripts/start.sh
```

`setup.sh` 需要先停止本项目。改尺寸或 USB 配置后重新 start；六台相机的两个 Hub 和物理插口保持本次位置，防止 camera 编号与额度对应关系改变。

```bash
python3 /root/G30/tools/validate_tcp.py
python3 /root/G30/tools/validate_capture_stability.py
```

前者检查六路各 300 帧、真实编码尺寸和 FFmpeg 解码错误；后者用 2 分钟采样检查完整帧、编码帧、残缺计数和编码重建增量。结果写在本项目 `run/validation/`，属于短时验证，不能代替长时间可靠性验收。

## 保护与备份

本次修改仅在 `/root/G30` 内进行，未修改旧 K2/snappy-real 工程或持久化网络配置。修复前程序、二进制及配置位于 `/root/G30/backups/camera2-1080-before-fix/`。其中配置本身已是用户改过的 1080p，USB 仍为统一 1536，回退会恢复本次故障状态。现场最新源码包在 `/root/G30/dist/G30-project.zip`。
