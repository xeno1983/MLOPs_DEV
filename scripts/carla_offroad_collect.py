#!/usr/bin/env python3
"""CARLA Town01 + Tesla Cybertruck 走行 + rosbag2 録画（ROS 不要・Python 3.10 + carla-0-9-15）。

既定:
  マップ  Town01  https://carla.readthedocs.io/en/latest/map_town01/
  車両    vehicle.tesla.cybertruck
  センサ  RGB×6 / LiDAR×3 / GNSS / IMU（+ odometry / speed）

  source ~/.venv-carla915/bin/activate
  python scripts/carla_offroad_collect.py --map Town01 --sync

操作: WASD 走行、B 録画 ON/OFF、ESC 終了
"""
from __future__ import annotations

import argparse
import atexit
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

try:
    import carla
except ImportError:
    print("carla を import できません。scripts/setup-carla-offroad-env.sh を実行してください。", file=sys.stderr)
    sys.exit(2)

try:
    import pygame
except ImportError:
    print("pygame が必要です: pip install pygame", file=sys.stderr)
    sys.exit(2)

# --- 構成（docs: Town01 / Tesla Cybertruck / 可視光カメラ複数・LiDAR複数・GNSS/IMU）---
DEFAULT_MAP = "Town01"
VEHICLE_BP = "vehicle.tesla.cybertruck"

# 可視光 RGB カメラ（車両ローカル座標、yaw deg）
CAMERA_SPECS: list[dict[str, Any]] = [
    {"id": "front", "x": 2.5, "y": 0.0, "z": 1.8, "yaw": 0.0, "fov": "90"},
    {"id": "front_left", "x": 2.2, "y": -0.7, "z": 1.6, "yaw": -55.0, "fov": "90"},
    {"id": "front_right", "x": 2.2, "y": 0.7, "z": 1.6, "yaw": 55.0, "fov": "90"},
    {"id": "rear", "x": -2.4, "y": 0.0, "z": 1.6, "yaw": 180.0, "fov": "90"},
    {"id": "left", "x": 0.4, "y": -1.1, "z": 1.6, "yaw": -90.0, "fov": "90"},
    {"id": "right", "x": 0.4, "y": 1.1, "z": 1.6, "yaw": 90.0, "fov": "90"},
]

# LiDAR（複数）。channels / range は 0.9.15 ray_cast 属性
LIDAR_SPECS: list[dict[str, Any]] = [
    {
        "id": "top",
        "x": 0.0,
        "y": 0.0,
        "z": 2.2,
        "yaw": 0.0,
        "channels": "32",
        "range": "50.0",
        "points_per_second": "120000",
        "rotation_frequency": "20.0",
        "upper_fov": "10.0",
        "lower_fov": "-30.0",
    },
    {
        "id": "front",
        "x": 2.3,
        "y": 0.0,
        "z": 1.5,
        "yaw": 0.0,
        "channels": "16",
        "range": "40.0",
        "points_per_second": "80000",
        "rotation_frequency": "20.0",
        "upper_fov": "5.0",
        "lower_fov": "-20.0",
    },
    {
        "id": "rear",
        "x": -2.3,
        "y": 0.0,
        "z": 1.5,
        "yaw": 180.0,
        "channels": "16",
        "range": "40.0",
        "points_per_second": "80000",
        "rotation_frequency": "20.0",
        "upper_fov": "5.0",
        "lower_fov": "-20.0",
    },
]


def _quat_from_yaw(yaw_deg: float):
    yaw = math.radians(yaw_deg)
    return (0.0, 0.0, math.sin(yaw * 0.5), math.cos(yaw * 0.5))


class BagWriter:
    """rosbags Writer: 多カメラ / 多 LiDAR / GNSS / IMU / odom / speed。"""

    def __init__(self, out_dir: Path, camera_ids: list[str], lidar_ids: list[str]):
        from rosbags.rosbag2 import Writer
        from rosbags.typesys import Stores, get_typestore

        if out_dir.exists():
            raise FileExistsError(f"already exists: {out_dir}")
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        store = getattr(Stores, "ROS2_JAZZY", None) or getattr(Stores, "ROS2_HUMBLE", Stores.LATEST)
        self.typestore = get_typestore(store)
        self.writer = Writer(out_dir, version=9)
        self.writer.__enter__()

        self.Image = self.typestore.types["sensor_msgs/msg/Image"]
        self.PointCloud2 = self.typestore.types["sensor_msgs/msg/PointCloud2"]
        self.PointField = self.typestore.types["sensor_msgs/msg/PointField"]
        self.NavSatFix = self.typestore.types["sensor_msgs/msg/NavSatFix"]
        self.NavSatStatus = self.typestore.types["sensor_msgs/msg/NavSatStatus"]
        self.Imu = self.typestore.types["sensor_msgs/msg/Imu"]
        self.Odometry = self.typestore.types["nav_msgs/msg/Odometry"]
        self.Float32 = self.typestore.types["std_msgs/msg/Float32"]
        self.Header = self.typestore.types["std_msgs/msg/Header"]
        self.Time = self.typestore.types["builtin_interfaces/msg/Time"]
        self.PoseWithCovariance = self.typestore.types["geometry_msgs/msg/PoseWithCovariance"]
        self.Pose = self.typestore.types["geometry_msgs/msg/Pose"]
        self.Point = self.typestore.types["geometry_msgs/msg/Point"]
        self.Quaternion = self.typestore.types["geometry_msgs/msg/Quaternion"]
        self.TwistWithCovariance = self.typestore.types["geometry_msgs/msg/TwistWithCovariance"]
        self.Twist = self.typestore.types["geometry_msgs/msg/Twist"]
        self.Vector3 = self.typestore.types["geometry_msgs/msg/Vector3"]

        self.conn_cam: dict[str, Any] = {}
        for cid in camera_ids:
            topic = f"/carla/ego_vehicle/rgb/{cid}/image"
            self.conn_cam[cid] = self.writer.add_connection(
                topic, "sensor_msgs/msg/Image", typestore=self.typestore
            )
        # AutoE2E / 旧 rig 互換: front を従来トピックにも書く
        self.conn_cam_legacy = self.writer.add_connection(
            "/carla/ego_vehicle/rgb/image", "sensor_msgs/msg/Image", typestore=self.typestore
        )

        self.conn_lidar: dict[str, Any] = {}
        for lid in lidar_ids:
            topic = f"/carla/ego_vehicle/lidar/{lid}/points"
            self.conn_lidar[lid] = self.writer.add_connection(
                topic, "sensor_msgs/msg/PointCloud2", typestore=self.typestore
            )

        self.conn_gnss = self.writer.add_connection(
            "/carla/ego_vehicle/gnss", "sensor_msgs/msg/NavSatFix", typestore=self.typestore
        )
        self.conn_imu = self.writer.add_connection(
            "/carla/ego_vehicle/imu", "sensor_msgs/msg/Imu", typestore=self.typestore
        )
        self.conn_odom = self.writer.add_connection(
            "/carla/ego_vehicle/odometry", "nav_msgs/msg/Odometry", typestore=self.typestore
        )
        self.conn_spd = self.writer.add_connection(
            "/carla/ego_vehicle/speed", "std_msgs/msg/Float32", typestore=self.typestore
        )
        self.n = 0
        self.out_dir = out_dir

    def _stamp(self, t_ns: int):
        return self.Time(sec=int(t_ns // 1_000_000_000), nanosec=int(t_ns % 1_000_000_000))

    def _write_image(self, conn, t_ns: int, frame_id: str, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        data = np.ascontiguousarray(bgr, dtype=np.uint8).reshape(-1)
        img = self.Image(
            header=self.Header(stamp=self._stamp(t_ns), frame_id=frame_id),
            height=h,
            width=w,
            encoding="bgr8",
            is_bigendian=0,
            step=w * 3,
            data=data,
        )
        self.writer.write(conn, t_ns, self.typestore.serialize_cdr(img, "sensor_msgs/msg/Image"))

    def _write_lidar(self, conn, t_ns: int, frame_id: str, raw: bytes) -> None:
        # CARLA lidar: float32 x,y,z,intensity (left-handed Y). ROS: flip Y.
        pts = np.frombuffer(raw, dtype=np.float32).reshape(-1, 4).copy()
        if pts.size == 0:
            return
        pts[:, 1] *= -1.0
        data = pts.astype(np.float32).tobytes()
        fields = [
            self.PointField(name="x", offset=0, datatype=7, count=1),  # FLOAT32=7
            self.PointField(name="y", offset=4, datatype=7, count=1),
            self.PointField(name="z", offset=8, datatype=7, count=1),
            self.PointField(name="intensity", offset=12, datatype=7, count=1),
        ]
        msg = self.PointCloud2(
            header=self.Header(stamp=self._stamp(t_ns), frame_id=frame_id),
            height=1,
            width=int(pts.shape[0]),
            fields=fields,
            is_bigendian=False,
            point_step=16,
            row_step=16 * int(pts.shape[0]),
            data=np.frombuffer(data, dtype=np.uint8).copy(),
            is_dense=True,
        )
        self.writer.write(conn, t_ns, self.typestore.serialize_cdr(msg, "sensor_msgs/msg/PointCloud2"))

    def write(
        self,
        t_ns: int,
        cameras: dict[str, np.ndarray],
        lidars: dict[str, bytes],
        gnss,
        imu,
        loc,
        rot,
        vel,
    ) -> None:
        for cid, bgr in cameras.items():
            if bgr is None or cid not in self.conn_cam:
                continue
            self._write_image(self.conn_cam[cid], t_ns, f"camera_{cid}_optical", bgr)
            if cid == "front":
                self._write_image(self.conn_cam_legacy, t_ns, "camera_front_optical", bgr)

        for lid, raw in lidars.items():
            if raw is None or lid not in self.conn_lidar:
                continue
            self._write_lidar(self.conn_lidar[lid], t_ns, f"lidar_{lid}", raw)

        if gnss is not None:
            fix = self.NavSatFix(
                header=self.Header(stamp=self._stamp(t_ns), frame_id="gnss"),
                status=self.NavSatStatus(status=0, service=1),
                latitude=float(gnss.latitude),
                longitude=float(gnss.longitude),
                altitude=float(gnss.altitude),
                position_covariance=np.zeros(9, dtype=np.float64),
                position_covariance_type=0,
            )
            self.writer.write(
                self.conn_gnss, t_ns, self.typestore.serialize_cdr(fix, "sensor_msgs/msg/NavSatFix")
            )

        if imu is not None:
            # CARLA IMU: compass is yaw from North (deg). Use as orientation yaw.
            compass = float(getattr(imu, "compass", 0.0) or 0.0)
            qx, qy, qz, qw = _quat_from_yaw(-compass)
            ax = float(imu.accelerometer.x)
            ay = float(-imu.accelerometer.y)
            az = float(imu.accelerometer.z)
            gx = float(imu.gyroscope.x)
            gy = float(-imu.gyroscope.y)
            gz = float(imu.gyroscope.z)
            cov0 = np.zeros(9, dtype=np.float64)
            imu_msg = self.Imu(
                header=self.Header(stamp=self._stamp(t_ns), frame_id="imu"),
                orientation=self.Quaternion(x=qx, y=qy, z=qz, w=qw),
                orientation_covariance=cov0,
                angular_velocity=self.Vector3(x=gx, y=gy, z=gz),
                angular_velocity_covariance=cov0,
                linear_acceleration=self.Vector3(x=ax, y=ay, z=az),
                linear_acceleration_covariance=cov0,
            )
            self.writer.write(
                self.conn_imu, t_ns, self.typestore.serialize_cdr(imu_msg, "sensor_msgs/msg/Imu")
            )

        qx, qy, qz, qw = _quat_from_yaw(rot.yaw)
        speed = float(math.sqrt(vel.x**2 + vel.y**2 + vel.z**2))
        cov0 = np.zeros(36, dtype=np.float64)
        odom = self.Odometry(
            header=self.Header(stamp=self._stamp(t_ns), frame_id="map"),
            child_frame_id="base_link",
            pose=self.PoseWithCovariance(
                pose=self.Pose(
                    position=self.Point(x=float(loc.x), y=float(-loc.y), z=float(loc.z)),
                    orientation=self.Quaternion(x=qx, y=qy, z=qz, w=qw),
                ),
                covariance=cov0,
            ),
            twist=self.TwistWithCovariance(
                twist=self.Twist(
                    linear=self.Vector3(x=speed, y=0.0, z=0.0),
                    angular=self.Vector3(x=0.0, y=0.0, z=0.0),
                ),
                covariance=cov0,
            ),
        )
        self.writer.write(self.conn_odom, t_ns, self.typestore.serialize_cdr(odom, "nav_msgs/msg/Odometry"))
        self.writer.write(
            self.conn_spd,
            t_ns,
            self.typestore.serialize_cdr(self.Float32(data=speed), "std_msgs/msg/Float32"),
        )
        self.n += 1

    def close(self) -> None:
        self.writer.__exit__(None, None, None)


def _spawn_vehicle(world: "carla.World", bp_lib, vehicle_bp: str = VEHICLE_BP) -> "carla.Vehicle":
    try:
        bp = bp_lib.find(vehicle_bp)
    except IndexError as exc:
        raise RuntimeError(
            f"車両 blueprint が見つかりません: {vehicle_bp} "
            f"(https://carla.readthedocs.io/en/latest/catalogue_vehicles/)"
        ) from exc
    bp.set_attribute("role_name", "hero")
    if bp.has_attribute("color"):
        try:
            bp.set_attribute("color", "180,180,180")
        except Exception:
            pass
    spawns = world.get_map().get_spawn_points()
    if not spawns:
        raise RuntimeError("no spawn points on map")
    for sp in spawns:
        actor = world.try_spawn_actor(bp, sp)
        if actor is not None:
            return actor
    raise RuntimeError(f"failed to spawn {vehicle_bp}")


def _attach_sensors(
    world: "carla.World",
    vehicle: "carla.Vehicle",
    bp_lib,
    width: int,
    height: int,
) -> tuple[list, dict, dict, dict, dict]:
    """Returns (actors, latest_cam, latest_lidar, latest_gnss, latest_imu)."""
    actors: list = []
    latest_cam: dict[str, Any] = {s["id"]: None for s in CAMERA_SPECS}
    latest_lidar: dict[str, Any] = {s["id"]: None for s in LIDAR_SPECS}
    latest_gnss: dict[str, Any] = {"msg": None}
    latest_imu: dict[str, Any] = {"msg": None}

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

        def _on_image(image: "carla.Image", _cid=cid) -> None:
            array = np.frombuffer(image.raw_data, dtype=np.uint8).reshape((image.height, image.width, 4))
            latest_cam[_cid] = array[:, :, :3].copy()

        cam.listen(_on_image)
        actors.append(cam)

    for spec in LIDAR_SPECS:
        lidar_bp = bp_lib.find("sensor.lidar.ray_cast")
        for key in (
            "channels",
            "range",
            "points_per_second",
            "rotation_frequency",
            "upper_fov",
            "lower_fov",
        ):
            if lidar_bp.has_attribute(key):
                lidar_bp.set_attribute(key, str(spec[key]))
        lidar_bp.set_attribute("role_name", spec["id"])
        tf = carla.Transform(
            carla.Location(x=spec["x"], y=spec["y"], z=spec["z"]),
            carla.Rotation(yaw=spec["yaw"]),
        )
        lidar = world.spawn_actor(lidar_bp, tf, attach_to=vehicle)
        lid = spec["id"]

        def _on_lidar(meas: "carla.LidarMeasurement", _lid=lid) -> None:
            latest_lidar[_lid] = bytes(meas.raw_data)

        lidar.listen(_on_lidar)
        actors.append(lidar)

    gnss_bp = bp_lib.find("sensor.other.gnss")
    gnss_bp.set_attribute("role_name", "gnss")
    gnss = world.spawn_actor(gnss_bp, carla.Transform(), attach_to=vehicle)

    def _on_gnss(event: "carla.GnssMeasurement") -> None:
        latest_gnss["msg"] = event

    gnss.listen(_on_gnss)
    actors.append(gnss)

    imu_bp = bp_lib.find("sensor.other.imu")
    imu_bp.set_attribute("role_name", "imu")
    imu = world.spawn_actor(imu_bp, carla.Transform(), attach_to=vehicle)

    def _on_imu(event: "carla.IMUMeasurement") -> None:
        latest_imu["msg"] = event

    imu.listen(_on_imu)
    actors.append(imu)

    return actors, latest_cam, latest_lidar, latest_gnss, latest_imu


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("-p", "--port", type=int, default=2000)
    ap.add_argument("--map", default=DEFAULT_MAP, help="既定 Town01（公式マップ）")
    ap.add_argument("--vehicle", default=VEHICLE_BP, help="既定 vehicle.tesla.cybertruck")
    ap.add_argument("--sync", action="store_true", help="同期モード（録画推奨）")
    ap.add_argument("--res", default="800x600")
    ap.add_argument("--out-root", default="", help="bag 出力親（既定: Autoware datasets/carla_bags）")
    ap.add_argument("--run-id", default="", help="空なら時刻スタンプ")
    args = ap.parse_args()
    width, height = (int(x) for x in args.res.split("x"))

    out_root = Path(args.out_root) if args.out_root else Path(
        "/mnt/c/Users/xeno1/Autoware_e2e/datasets/carla_bags"
    )
    if not out_root.exists():
        win = Path(r"C:\Users\xeno1\Autoware_e2e\datasets\carla_bags")
        out_root = win if win.parent.exists() else Path.cwd() / "carla_bags"
    out_root.mkdir(parents=True, exist_ok=True)

    pygame.init()
    pygame.font.init()
    display = pygame.display.set_mode((width, height))
    pygame.display.set_caption("CARLA Town01 Cybertruck (WASD / B=record / ESC)")
    font = pygame.font.SysFont("consolas", 16)

    client = carla.Client(args.host, args.port)
    client.set_timeout(120.0)
    print(f"loading map {args.map} ...")
    world = None
    candidates = [args.map] + [m for m in ("Town01", "Town01_Opt") if m != args.map]
    last_exc: Exception | None = None
    for name in candidates:
        try:
            world = client.load_world(name)
            args.map = name
            break
        except Exception as exc:
            last_exc = exc
            print(f"load_world({name}) failed: {exc}", file=sys.stderr)
    if world is None:
        print(f"could not load any of {candidates}: {last_exc}", file=sys.stderr)
        return 1

    original = None
    if args.sync:
        original = world.get_settings()
        st = world.get_settings()
        st.synchronous_mode = True
        st.fixed_delta_seconds = 0.05
        world.apply_settings(st)

        def _restore_async() -> None:
            try:
                if original is not None:
                    world.apply_settings(original)
            except Exception:
                pass

        atexit.register(_restore_async)

    bp_lib = world.get_blueprint_library()
    vehicle = _spawn_vehicle(world, bp_lib, args.vehicle)
    print("spawned", vehicle.type_id, "on", args.map)

    actors, latest_cam, latest_lidar, latest_gnss, latest_imu = _attach_sensors(
        world, vehicle, bp_lib, width, height
    )
    print(
        f"sensors: RGB×{len(CAMERA_SPECS)} LiDAR×{len(LIDAR_SPECS)} GNSS IMU"
    )

    control = carla.VehicleControl()
    bag: BagWriter | None = None
    clock = pygame.time.Clock()
    run_id = args.run_id or time.strftime("town01_%Y%m%d_%H%M%S")

    print("Controls: WASD throttle/steer, SPACE brake, B record, ESC quit")
    print(f"bag out root: {out_root}")

    try:
        while True:
            if args.sync:
                world.tick()
            else:
                world.wait_for_tick()
            clock.tick_busy_loop(20)

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise SystemExit
                if event.type == pygame.KEYUP and event.key == pygame.K_ESCAPE:
                    raise SystemExit
                if event.type == pygame.KEYUP and event.key == pygame.K_b:
                    if bag is None:
                        dest = out_root / run_id
                        if dest.exists():
                            run_id = time.strftime("town01_%Y%m%d_%H%M%S")
                            dest = out_root / run_id
                        bag = BagWriter(
                            dest,
                            [s["id"] for s in CAMERA_SPECS],
                            [s["id"] for s in LIDAR_SPECS],
                        )
                        print(f"RECORD ON -> {dest}")
                    else:
                        n = bag.n
                        path = bag.out_dir
                        bag.close()
                        bag = None
                        print(f"RECORD OFF ({n} frames) -> {path}")

            keys = pygame.key.get_pressed()
            control.throttle = 0.6 if keys[pygame.K_w] or keys[pygame.K_UP] else 0.0
            control.brake = 1.0 if keys[pygame.K_s] or keys[pygame.K_DOWN] or keys[pygame.K_SPACE] else 0.0
            steer = 0.0
            if keys[pygame.K_a] or keys[pygame.K_LEFT]:
                steer -= 0.4
            if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
                steer += 0.4
            control.steer = steer
            control.reverse = bool(keys[pygame.K_q])
            vehicle.apply_control(control)

            display.fill((20, 20, 20))
            img = latest_cam.get("front")
            if img is not None:
                surface = pygame.surfarray.make_surface(img[:, :, ::-1].swapaxes(0, 1))
                surface = pygame.transform.scale(surface, (width, height))
                display.blit(surface, (0, 0))

            tf = vehicle.get_transform()
            vel = vehicle.get_velocity()
            speed = math.sqrt(vel.x**2 + vel.y**2 + vel.z**2)
            status = (
                f"{args.map} / {vehicle.type_id.split('.')[-1]}  "
                f"spd={speed*3.6:.1f}km/h  "
                f"cam={sum(1 for v in latest_cam.values() if v is not None)}/{len(CAMERA_SPECS)}  "
                f"lid={sum(1 for v in latest_lidar.values() if v is not None)}/{len(LIDAR_SPECS)}  "
                f"rec={'ON '+str(bag.n) if bag else 'off'}"
            )
            display.blit(font.render(status, True, (255, 255, 0)), (10, 10))
            pygame.display.flip()

            if bag is not None and img is not None:
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

    finally:
        if bag is not None:
            bag.close()
            print(f"closed bag ({bag.n} frames)")
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
        if original is not None:
            world.apply_settings(original)
        pygame.quit()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("interrupted")
        raise SystemExit(130)
