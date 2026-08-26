# Hardware commissioning tools

Language: English | [简体中文](README.zh-CN.md)

These tools document the checks used to commission the project's RM65,
SpaceMouse, RealSense, UVC camera, and serial soft-gripper stack. They are
reproducible engineering references, not a promise of compatibility with every
device revision or operating system.

The public platform and safety boundary are described in [`docs/hardware.md`](../../docs/hardware.md).

## Safety boundary

Most tools are read-only. The four rows marked **Yes** or **Gripper only** can
move hardware and must be run with an operator present, a clear workspace, and
the appropriate stop control ready. Motion verification tools require an
explicit confirmation flag. The gripper calibration tool is print-only unless
`--allow-gripper` is supplied and asks for a second workspace-clear
confirmation by default.

Never copy machine-local IP addresses, serial numbers, COM ports, calibration
values, or generated reports into a public configuration or issue. Some
read-only reports intentionally display connected-device metadata for local
diagnosis.

## Tool matrix

| Device | Tool | Moves hardware | What it verifies | Main dependency |
| --- | --- | --- | --- | --- |
| RM65 | `rm65/check_connection.py` | No | SDK connection and one state sample | RealMan SDK |
| RM65 | `rm65/benchmark_read_latency.py` | No | Synchronous state-read round-trip time | RealMan SDK |
| RM65 | `rm65/benchmark_realtime_rate.py` | No | Existing UDP realtime push frequency | RealMan SDK |
| RM65 | `rm65/check_safety_state.py` | No | Controller state, joint limits, and error flags | RealMan SDK |
| RM65 | `rm65/verify_command_response.py` | **Yes: J6** | Guarded command-to-observed-motion latency | RealMan SDK |
| RM65 | `rm65/verify_teach_pendant_estop.py` | **Yes: J6** | Human-observed teach-pendant stop behavior | RealMan SDK |
| Cameras | `cameras/check_dual_camera_parallel.py` | No | Parallel RealSense and UVC capture freshness | OpenCV, RealSense SDK |
| Cameras | `cameras/benchmark_external_rgb_latency.py` | No | UVC host-arrival timing and consumer freshness | OpenCV, Windows PnP |
| Cameras | `cameras/benchmark_realsense_latency.py` | No | RGB-D host-arrival timing and freshness | RealSense SDK |
| Cameras | `cameras/inspect_realsense_profiles.py` | No | Profiles, intrinsics, firmware, and depth scale | RealSense SDK |
| Cameras | `cameras/probe_uvc_cameras.py` | No | DirectShow identity to OpenCV-index resolution | OpenCV, DirectShow |
| Cameras | `cameras/enumerate_dshow_video_devices.ps1` | No | DirectShow video-device enumeration | PowerShell, DirectShow |
| SpaceMouse | `spacemouse/benchmark_input_rate.py` | No | HID report rate and latest-input age | HIDAPI |
| SpaceMouse + RM65 | `spacemouse/verify_rm65_j6_control.py` | **Yes: J6** | Guarded input-to-command integration | HIDAPI, RealMan SDK |
| Serial gripper | `gripper/calibrate_open_range.py` | **Gripper only** | Local open-range calibration without arm motion | pyserial |

The Python hardware dependencies are available through:

```powershell
pip install -e ".[hardware]"
```

Always inspect a command before execution:

```powershell
python tools/hardware/rm65/verify_command_response.py --help
python tools/hardware/spacemouse/verify_rm65_j6_control.py --help
python tools/hardware/gripper/calibrate_open_range.py --help
```

The example commands above only print help and do not connect to hardware or
send commands.
