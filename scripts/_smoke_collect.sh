#!/usr/bin/env bash
# Headless smoke: connect, load Town07, spawn, write a tiny bag, quit.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export DISPLAY="${DISPLAY:-:0}"
export SDL_VIDEODRIVER=dummy
export PYGAME_HIDE_SUPPORT_PROMPT=1
source "$HOME/.venv-carla915/bin/activate"

OUT="/mnt/c/Users/xeno1/Autoware_e2e/datasets/carla_bags/smoke_$(date +%Y%m%d_%H%M%S)"
python - <<PY
import math, time, sys, traceback
from pathlib import Path
import numpy as np
import carla

sys.path.insert(0, "/mnt/c/Users/xeno1/MLOPS/scripts")
from carla_offroad_collect import BagWriter, _spawn_vehicle

host, port = "127.0.0.1", 2000
client = carla.Client(host, port)
client.set_timeout(120.0)
print("server", client.get_server_version(), "client", client.get_client_version())

# Prefer currently loaded world if already Town07 to avoid long reload
world = client.get_world()
cur = world.get_map().name
print("current map", cur)
if "Town07" not in cur:
    print("loading Town07...")
    world = client.load_world("Town07")

settings = world.get_settings()
orig = settings
settings.synchronous_mode = True
settings.fixed_delta_seconds = 0.05
world.apply_settings(settings)

bp = world.get_blueprint_library()
vehicle = _spawn_vehicle(world, bp)
print("spawned", vehicle.type_id)

cam_bp = bp.find("sensor.camera.rgb")
cam_bp.set_attribute("image_size_x", "640")
cam_bp.set_attribute("image_size_y", "360")
cam_bp.set_attribute("fov", "90")
cam_tf = carla.Transform(carla.Location(x=2.8, z=1.6))
camera = world.spawn_actor(cam_bp, cam_tf, attach_to=vehicle)
latest = {"img": None}

def on_img(image):
    arr = np.frombuffer(image.raw_data, dtype=np.uint8).reshape((image.height, image.width, 4))
    latest["img"] = arr[:, :, :3].copy()

camera.listen(on_img)

out = Path(r"$OUT")
if out.exists():
    import shutil; shutil.rmtree(out)
bag = BagWriter(out)
ctrl = carla.VehicleControl(throttle=0.4, steer=0.05)

try:
    for i in range(40):
        world.tick()
        vehicle.apply_control(ctrl)
        img = latest["img"]
        if img is None:
            continue
        tf = vehicle.get_transform()
        vel = vehicle.get_velocity()
        bag.write(time.time_ns(), img, tf.location, tf.rotation, vel)
    print("frames", bag.n, "->", out)
    if bag.n < 5:
        raise SystemExit("too few frames recorded")
finally:
    bag.close()
    camera.stop(); camera.destroy(); vehicle.destroy()
    settings.synchronous_mode = False
    world.apply_settings(settings)
print("SMOKE_OK")
PY

echo "=== bag contents ==="
ls -la "$OUT"
find "$OUT" -type f | head
