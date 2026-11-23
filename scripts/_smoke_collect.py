#!/usr/bin/env python3
"""Headless smoke: Town01 + Cybertruck + multi-sensor short rosbag write."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/c/Users/xeno1/MLOPS/scripts")

import carla  # noqa: E402
from carla_offroad_collect import (  # noqa: E402
    CAMERA_SPECS,
    DEFAULT_MAP,
    LIDAR_SPECS,
    VEHICLE_BP,
    BagWriter,
    _attach_sensors,
    _spawn_vehicle,
)


def main() -> int:
    host, port = "127.0.0.1", 2000
    client = carla.Client(host, port)
    client.set_timeout(120.0)
    print("server", client.get_server_version(), "client", client.get_client_version(), flush=True)

    world = client.get_world()
    cur = world.get_map().name
    print("current map", cur, flush=True)
    if DEFAULT_MAP not in cur and "Town01" not in cur:
        print(f"loading {DEFAULT_MAP}...", flush=True)
        try:
            world = client.load_world(DEFAULT_MAP)
        except Exception as exc:
            print(f"{DEFAULT_MAP} failed ({exc}); trying Town01_Opt", flush=True)
            world = client.load_world("Town01_Opt")
        print("loaded", world.get_map().name, flush=True)
    else:
        print("already on Town01 — skip reload", flush=True)

    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = 0.05
    world.apply_settings(settings)
    world.tick()

    bp = world.get_blueprint_library()
    vehicle = _spawn_vehicle(world, bp, VEHICLE_BP)
    print("spawned", vehicle.type_id, flush=True)

    actors, latest_cam, latest_lidar, latest_gnss, latest_imu = _attach_sensors(
        world, vehicle, bp, 640, 360
    )
    print(
        f"sensors RGB={len(CAMERA_SPECS)} LiDAR={len(LIDAR_SPECS)} GNSS/IMU",
        flush=True,
    )

    out = Path("/mnt/c/Users/xeno1/Autoware_e2e/datasets/carla_bags") / time.strftime(
        "smoke_%Y%m%d_%H%M%S"
    )
    if out.exists():
        import shutil

        shutil.rmtree(out)
    bag = BagWriter(
        out,
        [s["id"] for s in CAMERA_SPECS],
        [s["id"] for s in LIDAR_SPECS],
    )
    ctrl = carla.VehicleControl(throttle=0.35, steer=0.05)
    try:
        for _ in range(50):
            world.tick()
            vehicle.apply_control(ctrl)
            if latest_cam.get("front") is None:
                continue
            tf = vehicle.get_transform()
            vel = vehicle.get_velocity()
            bag.write(
                time.time_ns(),
                {k: v for k, v in latest_cam.items() if v is not None},
                {k: v for k, v in latest_lidar.items() if v is not None},
                latest_gnss.get("msg"),
                latest_imu.get("msg"),
                tf.location,
                tf.rotation,
                vel,
            )
        print("frames", bag.n, "->", out, flush=True)
        if bag.n < 5:
            raise SystemExit("too few frames recorded")
    finally:
        bag.close()
        for a in actors:
            try:
                if hasattr(a, "stop"):
                    a.stop()
                a.destroy()
            except Exception:
                pass
        vehicle.destroy()
        settings.synchronous_mode = False
        world.apply_settings(settings)
    print("SMOKE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
