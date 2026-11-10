#!/usr/bin/env python3
"""CARLA ego → ROS 2 nav_msgs/Odometry（AutoE2E / rig.carla.yaml 用）。

mining_sim は画像と speed しか出さないため、pose 教師用に本ブリッジが必要。
WSL で ROS 2 と CARLA Python API（egg）を用意して実行:

  source /opt/ros/jazzy/setup.bash   # or humble
  export PYTHONPATH=\"$CARLA_ROOT/PythonAPI/carla/dist/carla-*.egg:$PYTHONPATH\"
  python scripts/carla-odom-bridge.py --host 127.0.0.1 --port 2000
"""
from __future__ import annotations

import argparse
import math
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--topic", default="/carla/ego_vehicle/odometry")
    ap.add_argument("--frame-id", default="map")
    ap.add_argument("--child-frame-id", default="base_link")
    ap.add_argument("--rate", type=float, default=20.0)
    ap.add_argument("--role-name", default="hero", help="CARLA actor role_name（無ければ四輪を採用）")
    args = ap.parse_args()

    try:
        import carla  # type: ignore
    except ImportError:
        print("carla Python API が import できません。CARLA_ROOT の egg を PYTHONPATH に入れてください。", file=sys.stderr)
        return 2

    try:
        import rclpy
        from geometry_msgs.msg import Quaternion
        from nav_msgs.msg import Odometry
        from rclpy.node import Node
    except ImportError:
        print("rclpy / nav_msgs がありません。ROS 2 を source してください。", file=sys.stderr)
        return 2

    client = carla.Client(args.host, args.port)
    client.set_timeout(10.0)
    world = client.get_world()

    def find_ego():
        for a in world.get_actors().filter("vehicle.*"):
            if a.attributes.get("role_name") == args.role_name:
                return a
        vehs = list(world.get_actors().filter("vehicle.*"))
        return vehs[0] if vehs else None

    rclpy.init()
    node = Node("carla_odom_bridge")
    pub = node.create_publisher(Odometry, args.topic, 10)
    period = 1.0 / max(args.rate, 1.0)
    print(f"publishing {args.topic} @ {args.rate} Hz (CARLA {args.host}:{args.port})")

    try:
        while rclpy.ok():
            ego = find_ego()
            if ego is None:
                node.get_logger().warn("ego vehicle not found")
                time.sleep(1.0)
                continue
            tf = ego.get_transform()
            vel = ego.get_velocity()
            ang = ego.get_angular_velocity()
            loc, rot = tf.location, tf.rotation
            yaw = math.radians(rot.yaw)
            pitch = math.radians(rot.pitch)
            roll = math.radians(rot.roll)
            cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
            cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
            cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
            q = Quaternion()
            q.w = cr * cp * cy + sr * sp * sy
            q.x = sr * cp * cy - cr * sp * sy
            q.y = cr * sp * cy + sr * cp * sy
            q.z = cr * cp * sy - sr * sp * cy

            msg = Odometry()
            msg.header.stamp = node.get_clock().now().to_msg()
            msg.header.frame_id = args.frame_id
            msg.child_frame_id = args.child_frame_id
            msg.pose.pose.position.x = float(loc.x)
            msg.pose.pose.position.y = float(-loc.y)  # CARLA left-handed Y → ROS
            msg.pose.pose.position.z = float(loc.z)
            msg.pose.pose.orientation = q
            msg.twist.twist.linear.x = float(vel.x)
            msg.twist.twist.linear.y = float(-vel.y)
            msg.twist.twist.linear.z = float(vel.z)
            msg.twist.twist.angular.z = float(-math.radians(ang.z))
            pub.publish(msg)
            rclpy.spin_once(node, timeout_sec=0.0)
            time.sleep(period)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
