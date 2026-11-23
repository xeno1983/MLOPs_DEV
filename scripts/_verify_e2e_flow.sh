#!/usr/bin/env bash
# Town07 公開経路: bag生成 → 検証（import/ingest は Windows 側で続行）
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export SDL_VIDEODRIVER=dummy
export PYGAME_HIDE_SUPPORT_PROMPT=1
export PYTHONUNBUFFERED=1
source "$HOME/.venv-carla915/bin/activate"

# disable egg if present
SP="$(python -c 'import site; print(site.getsitepackages()[0])')"
if [[ -f "$SP/carla.pth" ]]; then mv -f "$SP/carla.pth" "$SP/carla.pth.egg.bak"; fi

OUT="/mnt/c/Users/xeno1/Autoware_e2e/datasets/carla_bags/flow_$(date +%Y%m%d_%H%M%S)"
python - <<PY
import sys, time
from pathlib import Path
import numpy as np
import carla
sys.path.insert(0, "/mnt/c/Users/xeno1/MLOPS/scripts")
from carla_offroad_collect import BagWriter, _spawn_vehicle

client = carla.Client("127.0.0.1", 2000)
client.set_timeout(90.0)
print("server", client.get_server_version(), flush=True)
world = client.get_world()
if "Town07" not in world.get_map().name:
    print("load Town07", flush=True)
    world = client.load_world("Town07")
settings = world.get_settings()
settings.synchronous_mode = True
settings.fixed_delta_seconds = 0.05
world.apply_settings(settings)
bp = world.get_blueprint_library()
vehicle = _spawn_vehicle(world, bp)
print("spawned", vehicle.type_id, flush=True)
cam_bp = bp.find("sensor.camera.rgb")
cam_bp.set_attribute("image_size_x", "640")
cam_bp.set_attribute("image_size_y", "360")
cam = world.spawn_actor(cam_bp, carla.Transform(carla.Location(x=2.8, z=1.6)), attach_to=vehicle)
latest = {"img": None}
cam.listen(lambda im: latest.__setitem__("img", np.frombuffer(im.raw_data, dtype=np.uint8).reshape((im.height, im.width, 4))[:, :, :3].copy()))
out = Path("$OUT")
if out.exists():
    import shutil; shutil.rmtree(out)
bag = BagWriter(out)
ctrl = carla.VehicleControl(throttle=0.35, steer=0.08)
try:
    for _ in range(30):
        world.tick()
        vehicle.apply_control(ctrl)
        img = latest["img"]
        if img is None:
            continue
        tf = vehicle.get_transform(); vel = vehicle.get_velocity()
        bag.write(time.time_ns(), img, tf.location, tf.rotation, vel)
    print("FRAMES", bag.n, "OUT", out, flush=True)
    if bag.n < 5:
        raise SystemExit("too few frames")
finally:
    bag.close()
    cam.stop(); cam.destroy(); vehicle.destroy()
    settings.synchronous_mode = False
    world.apply_settings(settings)
print("BAG_OK", flush=True)
PY

echo "VERIFY_BAG=$OUT"
ls -la "$OUT"
