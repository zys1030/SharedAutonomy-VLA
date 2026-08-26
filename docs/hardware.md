# Hardware and Runtime Boundary

Language: English | [简体中文](hardware.zh-CN.md)

This document describes the public hardware platform, host split, timing assumptions, and safety boundary of SharedAutonomy-VLA. It is a reproducibility guide at the system level, not a machine-specific commissioning manual. The task contract is defined in [`shape_pick_place_v1`](tasks/shape_pick_place_v1.md); the locked comparison is reported in [`results.md`](results.md).

## 1. Public platform

| Component | Public role |
| --- | --- |
| RealMan RM65-class six-axis robot | Robot motion and state feedback |
| RealMan FAE2MR86M-B external serial two-finger gripper | Open/close actuation; command-only interface in the first-stage setup |
| Intel RealSense D435i | Wrist RGB-D observation |
| Logitech C920 or equivalent fixed RGB camera | External scene observation and cube-yaw measurement |
| 3Dconnexion SpaceMouse Compact | Manual Cartesian input |
| Windows robot host | Hardware access, synchronization, arbitration, safety, recording, and optional inference client |
| Separate Linux GPU host | Training and optional policy inference |

The current robot configuration also exposes wrist force/torque measurements. They describe the wrench at the end-effector as a whole; they are not direct gripper-finger force or opening feedback.

## 2. Runtime responsibilities

The Windows host keeps the safety-critical path local:

```text
camera and robot state
        → timestamped observation cache
        → human input / yaw assistance / policy suggestion
        → arbitration and safety filtering
        → robot and gripper command
        → episode recorder
```

The human controls Cartesian translation and the gripper during collection. Shared Autonomy adds a bounded local J6 yaw overlay estimated from the external camera; it does not independently execute the complete task. During learned-policy evaluation, the policy proposes actions without human action in the nominal loop, but the same local safety supervisor remains active.

The robot host records the distinction between `human`, `assist`, and safety-filtered `executed` actions, together with observation freshness, authority, and intervention metadata. Training data are exported from these native episodes rather than recorded directly in the policy action format.

## 3. Verified software environment

The following versions were used for the current hardware checks:

| Layer | Verified value |
| --- | --- |
| Python | 3.12.13 |
| LeRobot | 0.6.0 |
| RealMan SDK | `Robotic-Arm` 1.1.5 |
| RealSense SDK binding | `pyrealsense2` 2.56.5.9235 |
| SpaceMouse HID access | `hidapi` 0.15.0 |

Hardware SDKs are imported lazily. Offline imports, unit tests, and dry-run tools must remain usable when the robot, cameras, gripper, or SpaceMouse are absent.

## 4. Timing and freshness assumptions

The public operating points are:

| Path | Operating point | Interpretation |
| --- | ---: | --- |
| Wrist RGB-D and external RGB | 640 × 480 at approximately 30 FPS | Observation source; freshness is checked per frame |
| Robot realtime state push | Approximately 200 Hz in the validated setup | Feedback source; latest state is cached |
| SpaceMouse raw HID reports | Approximately 125 Hz | Translation and rotation reports alternate; complete 6-DoF state refreshes more slowly |
| Collection command path | 10 Hz default, low-follow CAN-FD | Conservative first-stage operating point |
| Processing/synchronization loops | Components may use a nominal 50 Hz schedule | This is not a claim that the collection command path must run at 50 Hz |

These are measured or selected software operating points, not hard real-time guarantees. The runtime uses monotonic host timestamps and records `frame_age`, `state_age`, and input age. Stale observations or input must cause a hold, zero action, or command rejection according to the relevant safety policy; average throughput is not sufficient evidence of freshness.

The validated camera and robot links still have long-tail scheduling and transport delays. The project therefore does not claim stable 100 Hz or 200 Hz end-to-end closed-loop control on a general-purpose Windows host.

## 5. Safety boundary

Physical motion is disabled by default. A motion-capable run requires all of the following:

- an explicit runtime motion gate and valid local configuration;
- an operator present with a usable emergency-stop path;
- read-only device and stream checks completed first;
- joint, workspace, velocity, acceleration, step-size, and freshness checks enabled;
- deadman and gripper-state checks enabled for manual collection;
- a safe stop path on input loss, state expiry, safety rejection, exception, or operator release.

The safety supervisor is local to the robot host and remains between human or policy suggestions and the physical robot. A successful dry-run or SDK connection check does not authorize motion. Hardware commissioning and any movement test require a separate operator-controlled procedure.

## 6. Hardware-specific boundaries

The first-stage gripper is a RealMan FAE2MR86M-B and is treated as a command-only device. The software records commanded travel and timing, but does not fabricate actual opening, position, current, or grasp-force feedback. Full-open and full-close are the reliable public states until a real feedback path is validated.

The wrist force/torque sensor can support future contact or force-control work, but its readings require tool and gravity calibration. It must not be interpreted as fingertip force without a task-specific model and validation.

The external camera is used as a fixed viewpoint. Its field of view must include the pickup area and the named target region. A mount change, collision, USB topology change, or camera remount requires a fresh visibility and synchronization check. A wrist-camera remount requires hand-eye validation again.

## 7. Public/private boundary

The public release may describe hardware classes, sensor roles, software versions, timing assumptions, safety behavior, and aggregate commissioning conclusions.

The following remain local or private:

- controller IP addresses, serial numbers, USB indices, and port assignments;
- calibration matrices, camera intrinsics, and device-specific profiles;
- exact workspace polygons, table geometry, task-site coordinates, and ready poses;
- gripper-specific calibration, offsets, and local motion limits;
- raw commissioning logs and machine-local paths.

These values must be supplied through local configuration or environment-specific deployment notes rather than copied into the public task or hardware contract.

## 8. Reproducibility entry points

The repository exposes separate code paths for hardware access, dry-run validation, observation synchronization, safety filtering, and episode recording. They should be exercised in this order:

1. import and schema checks without hardware;
2. read-only device and observation checks;
3. dry-run safety, IK, workspace, and freshness checks;
4. operator-controlled low-speed commissioning;
5. only then, task collection or closed-loop evaluation.

The public evaluation protocol and task-specific conditions are maintained in [`results.json`](results.json) and [`evaluation_records.csv`](evaluation_records.csv). Detailed machine commissioning records are intentionally maintained separately from this public system description.

Representative read-only checks, benchmarks, and explicitly gated motion-verification tools are indexed in [`tools/hardware/README.md`](../tools/hardware/README.md).
