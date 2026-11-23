#!/usr/bin/env python3
"""Headless Town01 + Cybertruck drive + multi-camera bag (AutoE2E-oriented)."""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, "/mnt/c/Users/xeno1/MLOPS/scripts")

import carla  # noqa: E402
from carla_offroad_collect import (  # noqa: E402
    CAMERA_SPECS,
    VEHICLE_BP,
    BagWriter,
    _spawn_vehicle,
)


def _destroy_all(world: "carla.World") -> None:
    for pattern in ("sensor.*", "vehicle.*"):
        for a in list(world.get_actors().filter(pattern)):
            try:
                if hasattr(a, "stop"):
                    a.stop()
                a.destroy()
            except Exception:
                pass
    time.sleep(0.3)


def _attach_cameras(world, vehicle, bp_lib, width: int, height: int):
    """Cameras + GNSS + IMU only (skip LiDAR for tick/write throughput)."""
    actors: list = []
    latest_cam: dict = {s["id"]: None for s in CAMERA_SPECS}
    latest_gnss: dict = {"msg": None}
    latest_imu: dict = {"msg": None}

    def _tick() -> None:
        try:
            world.wait_for_tick(seconds=5.0)
        except Exception:
            pass

    for spec in CAMERA_SPECS:
        cam_bp = bp_lib.find("sensor.camera.rgb")
        cam_bp.set_attribute("image_size_x", str(width))
        cam_bp.set_attribute("image_size_y", str(height))
        cam_bp.set_attribute("fov", str(spec["fov"]))
        cam_bp.set_attribute("role_name", spec["id"])
        tf = carla.Transform(
            carla.Location(x=spec["x"], y=spec["y"], z=spec["z"]),
            carla.Rotation(yaw=spec["yaw"]),
        )
        cam = world.spawn_actor(cam_bp, tf, attach_to=vehicle)
        cid = spec["id"]

        def _on_image(image, _cid=cid) -> None:
            array = np.frombuffer(image.raw_data, dtype=np.uint8).reshape(
                (image.height, image.width, 4)
            )
            latest_cam[_cid] = array[:, :, :3].copy()

        cam.listen(_on_image)
        actors.append(cam)
        print(f"  cam {cid}", flush=True)
        _tick()

    gnss_bp = bp_lib.find("sensor.other.gnss")
    gnss_bp.set_attribute("role_name", "gnss")
    gnss = world.spawn_actor(gnss_bp, carla.Transform(), attach_to=vehicle)

    def _on_gnss(event) -> None:
        latest_gnss["msg"] = event

    gnss.listen(_on_gnss)
    actors.append(gnss)
    _tick()

    imu_bp = bp_lib.find("sensor.other.imu")
    imu_bp.set_attribute("role_name", "imu")
    imu = world.spawn_actor(imu_bp, carla.Transform(), attach_to=vehicle)

    def _on_imu(event) -> None:
        latest_imu["msg"] = event

    imu.listen(_on_imu)
    actors.append(imu)
    _tick()

    return actors, latest_cam, latest_gnss, latest_imu


def main() -> int:
    ticks = int(sys.argv[1]) if len(sys.argv) > 1 else 260
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(60.0)
    world = client.get_world()
    print("map", world.get_map().name, flush=True)
    if "Town01" not in world.get_map().name:
        print("loading Town01...", flush=True)
        world = client.load_world("Town01")

    settings = world.get_settings()
    settings.synchronous_mode = False
    settings.fixed_delta_seconds = None
    world.apply_settings(settings)

    print("cleanup actors...", flush=True)
    _destroy_all(world)

    bp = world.get_blueprint_library()
    vehicle = _spawn_vehicle(world, bp, VEHICLE_BP)
    print("spawned", vehicle.type_id, flush=True)
    print("attaching cameras (no lidar)...", flush=True)
    actors, latest_cam, latest_gnss, latest_imu = _attach_cameras(
        world, vehicle, bp, 640, 360
    )
    print(f"sensors ok n={len(actors)}", flush=True)

    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = 0.05
    world.apply_settings(settings)
    print("sync on", flush=True)

    run_name = time.strftime("town01_%Y%m%d_%H%M%S")
    out = Path("/tmp/carla_bags") / run_name
    final = Path("/mnt/c/Users/xeno1/Autoware_e2e/datasets/carla_bags") / run_name
    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    bag = BagWriter(out, [s["id"] for s in CAMERA_SPECS], [])
    ctrl = carla.VehicleControl(throttle=0.45, steer=0.08)
    t0_ns = time.time_ns()
    dt_ns = 50_000_000
    frames = 0
    try:
        for _ in range(10):
            world.tick()
        print("warmup done cam=", latest_cam.get("front") is not None, flush=True)

        for i in range(ticks):
            world.tick()
            ctrl.steer = 0.15 if (i // 40) % 2 == 0 else -0.1
            vehicle.apply_control(ctrl)
            if latest_cam.get("front") is None:
                if i % 20 == 0:
                    print("tick", i, "waiting cam", flush=True)
                continue
            tf = vehicle.get_transform()
            vel = vehicle.get_velocity()
            bag.write(
                t0_ns + i * dt_ns,
                {k: v for k, v in latest_cam.items() if v is not None},
                {},
                latest_gnss.get("msg"),
                latest_imu.get("msg"),
                tf.location,
                tf.rotation,
                vel,
            )
            frames = bag.n
            if i % 20 == 0:
                print("tick", i, "frames", frames, flush=True)
        print("DONE frames", frames, "->", out, flush=True)
    finally:
        try:
            bag.close()
        except Exception:
            pass
        try:
            settings.synchronous_mode = False
            settings.fixed_delta_seconds = None
            world.apply_settings(settings)
        except Exception:
            pass
        for a in actors:
            try:
                if hasattr(a, "stop"):
                    a.stop()
                a.destroy()
            except Exception:
                pass
        try:
            vehicle.destroy()
        except Exception:
            pass
    if frames < 50:
        print("RECORD_FAIL frames", frames, flush=True)
        return 1
    if final.exists():
        shutil.rmtree(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    print("copying", out, "->", final, flush=True)
    shutil.copytree(out, final)
    print("RECORD_OK", final.name, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
