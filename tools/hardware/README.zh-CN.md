# 硬件联调工具

语言：[English](README.md) | 简体中文

这些工具记录本项目 RM65、SpaceMouse、RealSense、UVC 相机和串口软夹爪平台使用的联调检查。它们是可复核的工程参考，不承诺兼容所有设备 revision 或操作系统。

公开硬件平台和安全边界见 [`docs/hardware.zh-CN.md`](../../docs/hardware.zh-CN.md)。

## 安全边界

大多数工具是只读的。下表中标记为 **是** 或 **仅夹爪** 的四项可以移动硬件，运行时必须有操作者在场、工作区已清空，并准备好相应的停止控制。运动验证工具要求显式确认参数。夹爪标定工具只有在提供 `--allow-gripper` 后才会执行，并且默认要求再次确认工作区安全。

不得把机器本地 IP、序列号、COM 端口、标定值或生成的报告复制到公开配置或 issue。部分只读报告会为本机诊断显示已连接设备的 metadata。

## 工具矩阵

| 设备 | 工具 | 移动硬件 | 验证内容 | 主要依赖 |
| --- | --- | --- | --- | --- |
| RM65 | `rm65/check_connection.py` | 否 | SDK 连接和一次状态采样 | RealMan SDK |
| RM65 | `rm65/benchmark_read_latency.py` | 否 | 同步状态读取的往返延迟 | RealMan SDK |
| RM65 | `rm65/benchmark_realtime_rate.py` | 否 | 已有 UDP realtime push 频率 | RealMan SDK |
| RM65 | `rm65/check_safety_state.py` | 否 | 控制器状态、关节限制和错误标志 | RealMan SDK |
| RM65 | `rm65/verify_command_response.py` | **是：J6** | 带门控的命令到实测运动延迟 | RealMan SDK |
| RM65 | `rm65/verify_teach_pendant_estop.py` | **是：J6** | 由人工观察的示教器停止行为 | RealMan SDK |
| 相机 | `cameras/check_dual_camera_parallel.py` | 否 | RealSense 与 UVC 并行采集新鲜度 | OpenCV、RealSense SDK |
| 相机 | `cameras/benchmark_external_rgb_latency.py` | 否 | UVC 主机到达时序与消费端新鲜度 | OpenCV、Windows PnP |
| 相机 | `cameras/benchmark_realsense_latency.py` | 否 | RGB-D 主机到达时序与新鲜度 | RealSense SDK |
| 相机 | `cameras/inspect_realsense_profiles.py` | 否 | Profile、内参、firmware 和 depth scale | RealSense SDK |
| 相机 | `cameras/probe_uvc_cameras.py` | 否 | DirectShow identity 到 OpenCV index 的解析 | OpenCV、DirectShow |
| 相机 | `cameras/enumerate_dshow_video_devices.ps1` | 否 | 枚举 DirectShow 视频设备 | PowerShell、DirectShow |
| SpaceMouse | `spacemouse/benchmark_input_rate.py` | 否 | HID report 频率和最新输入年龄 | HIDAPI |
| SpaceMouse + RM65 | `spacemouse/verify_rm65_j6_control.py` | **是：J6** | 带门控的输入到命令集成 | HIDAPI、RealMan SDK |
| 串口夹爪 | `gripper/calibrate_open_range.py` | **仅夹爪** | 不移动机械臂的本机开口范围标定 | pyserial |

Python 硬件依赖通过以下命令安装：

```powershell
pip install -e ".[hardware]"
```

执行前必须先查看命令说明：

```powershell
python tools/hardware/rm65/verify_command_response.py --help
python tools/hardware/spacemouse/verify_rm65_j6_control.py --help
python tools/hardware/gripper/calibrate_open_range.py --help
```

以上示例只打印帮助，不连接硬件，也不发送命令。
