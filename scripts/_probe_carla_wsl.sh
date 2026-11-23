#!/usr/bin/env bash
set -e
echo "python: $(python3 --version)"
python3 -c 'import pygame; print("pygame", pygame.__version__)' 2>&1 || echo "pygame: MISSING"
python3 -c 'import carla; print("carla", carla.__file__)' 2>&1 || echo "carla: MISSING"
ls /home/xeno1/CARLA/PythonAPI/carla/dist/ 2>/dev/null || true
which python3.8 python3.10 python3.11 2>/dev/null || true
source /opt/ros/jazzy/setup.bash 2>/dev/null || source /opt/ros/humble/setup.bash 2>/dev/null || true
ros2 pkg list 2>/dev/null | grep -i ackermann || echo "ackermann: MISSING"
dpkg -l 'ros-*-ackermann-msgs' 2>/dev/null | tail -n +1 || true
ls /home/xeno1/CARLA/CarlaUE4/Content/Carla/Maps/Mine_01* 2>/dev/null || echo "Mine_01: MISSING"
