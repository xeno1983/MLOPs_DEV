"""CARLA / mining_sim のホスト起動（compose 外）。

Catalog コンテナ内ではプロセスを起さない。起動実体は:
  - host worker の job `carla_launch` / `carla_stop`
  - ホスト上の CLI `ugv-mlops carla-launch`
  - scripts/carla-launch.ps1（ランチャー）
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ugv_mlops.carla_bridge import resolve_autoware_root
from ugv_mlops.settings import settings

RUNTIME_REL = Path("carla") / "runtime.json"
COMPONENTS = ("server", "mining_sim")


def in_container() -> bool:
    if os.environ.get("UGV_IN_CONTAINER", "").strip() in {"1", "true", "yes"}:
        return True
    return Path("/.dockerenv").exists()


def runtime_path() -> Path:
    p = settings.work_path / RUNTIME_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_runtime() -> dict:
    path = runtime_path()
    if not path.is_file():
        return {"components": {}, "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"components": {}, "updated_at": None}
    if not isinstance(data, dict):
        return {"components": {}, "updated_at": None}
    data.setdefault("components", {})
    return data


def save_runtime(data: dict) -> None:
    data = {**data, "updated_at": datetime.now(timezone.utc).isoformat()}
    runtime_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_carla_root() -> Path | None:
    candidates: list[str] = []
    for raw in (
        settings.carla_root,
        os.environ.get("CARLA_ROOT", ""),
        r"C:\CARLA",
        r"C:\Carla",
        str(Path.home() / "CARLA"),
        "/opt/carla",
        "/opt/CARLA_0.9.15",
        "/home/carla/CARLA",
    ):
        if raw and raw.strip():
            candidates.append(raw.strip())
    seen: set[str] = set()
    for raw in candidates:
        key = raw.replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        p = Path(raw)
        if _looks_like_carla_root(p):
            return p
    return None


def _looks_like_carla_root(root: Path) -> bool:
    if not root.is_dir():
        return False
    names = {p.name for p in root.iterdir()} if root.is_dir() else set()
    markers = {"CarlaUE4.sh", "CarlaUE4.exe", "CarlaUE4", "CarlaUE4-Linux-Shipping"}
    return bool(names & markers) or (root / "CarlaUE4.sh").is_file() or (root / "CarlaUE4.exe").is_file()


def probe_carla(host: str | None = None, port: int | None = None, timeout: float = 0.6) -> dict:
    host = host or settings.carla_host
    port = int(port or settings.carla_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        ok = True
        err = None
    except OSError as exc:
        ok = False
        err = str(exc)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return {"listening": ok, "host": host, "port": port, "error": err}


def launch_status() -> dict:
    root = resolve_carla_root()
    aw = resolve_autoware_root()
    mining = aw / "autoware.off-road" / "Simulation" / "CARLA" / "scripts" / "mining_sim.py"
    runtime = load_runtime()
    probe = probe_carla()
    comps = runtime.get("components") or {}
    return {
        "ok": True,
        "in_container": in_container(),
        "launch_mode": settings.carla_launch_mode,
        "carla_root": str(root) if root else None,
        "carla_root_configured": bool((settings.carla_root or "").strip() or os.environ.get("CARLA_ROOT")),
        "probe": probe,
        "runtime": runtime,
        "server_tracked": bool(comps.get("server")),
        "mining_sim_tracked": bool(comps.get("mining_sim")),
        "mining_sim_script": str(mining) if mining.is_file() else None,
        "host_worker_required": in_container(),
        "hint": (
            "Catalog はコンテナ内のため、起動は host worker（runner=host）か "
            "ホスト CLI / scripts/carla-launch.ps1 が実行する。"
            if in_container()
            else "このプロセスから直接起動できます（carla-launch）。"
        ),
    }


def build_server_command(carla_root: Path) -> list[str]:
    mode = (settings.carla_launch_mode or "wsl").strip().lower()
    quality = settings.carla_quality or "Low"
    if mode == "script":
        script = Path(settings.carla_launch_script or "scripts/carla-launch.ps1")
        if not script.is_file():
            raise FileNotFoundError(f"carla_launch_script がありません: {script}")
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Component",
            "server",
        ]
    if mode == "native":
        exe = carla_root / "CarlaUE4.exe"
        if not exe.is_file():
            raise FileNotFoundError(f"CarlaUE4.exe がありません: {exe}")
        return [str(exe), f"-quality-level={quality}"]
    # wsl（既定）: Windows ホストから WSL 内の CarlaUE4.sh
    linux_root = _to_wsl_path(carla_root)
    if sys.platform.startswith("win"):
        inner = (
            f'cd "{linux_root}" && export CARLA_ROOT="{linux_root}" && '
            f"nohup ./CarlaUE4.sh -quality-level={quality} > /tmp/carla-ue4.log 2>&1 & echo $!"
        )
        return _wsl_bash_cmd(inner)
    sh = carla_root / "CarlaUE4.sh"
    if not sh.is_file():
        raise FileNotFoundError(
            f"CarlaUE4.sh が見つかりません: {sh}（CARLA_ROOT / settings.carla_root を確認）"
        )
    return ["bash", "-lc", f'cd "{carla_root}" && ./CarlaUE4.sh -quality-level={quality}']


def build_mining_sim_command() -> list[str]:
    aw = resolve_autoware_root()
    script = aw / "autoware.off-road" / "Simulation" / "CARLA" / "scripts" / "mining_sim.py"
    if not script.is_file():
        raise FileNotFoundError(f"mining_sim.py がありません: {script}")
    extra = (settings.carla_mining_extra_args or "--sync").strip()
    py = "python3"
    host = settings.carla_host
    port = settings.carla_port
    if sys.platform.startswith("win") and (settings.carla_launch_mode or "wsl").strip().lower() == "wsl":
        linux_script = _to_wsl_path(script)
        linux_cwd = _to_wsl_path(script.parent.parent)
        inner = (
            f'source /opt/ros/humble/setup.bash 2>/dev/null; '
            f'cd "{linux_cwd}" && '
            f'nohup {py} "{linux_script}" --host {host} -p {port} {extra} '
            f'> /tmp/carla-mining-sim.log 2>&1 & echo $!'
        )
        return _wsl_bash_cmd(inner)
    return [py, str(script), "--host", host, "-p", str(port), *extra.split()]


def _wsl_bash_cmd(inner: str) -> list[str]:
    cmd = ["wsl"]
    distro = (settings.carla_wsl_distro or "").strip()
    if distro:
        cmd.extend(["-d", distro])
    cmd.extend(["-e", "bash", "-lc", inner])
    return cmd


def _to_wsl_path(path: Path) -> str:
    """C:\\foo → /mnt/c/foo。既に POSIX ならそのまま。"""
    s = str(path)
    if s.startswith("/") and not (len(s) > 1 and s[1] == ":"):
        return s.replace("\\", "/")
    try:
        completed = subprocess.run(
            ["wsl", "wslpath", "-a", s],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    # フォールバック
    if len(s) >= 2 and s[1] == ":":
        drive = s[0].lower()
        rest = s[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"
    return s.replace("\\", "/")


def launch(
    components: list[str] | None = None,
    *,
    dry_run: bool = False,
    wait_ready_sec: float = 0,
) -> dict:
    """server / mining_sim を起動。コンテナ内では拒否（host job へ）。"""
    if in_container() and not dry_run:
        raise RuntimeError(
            "Catalog コンテナ内では CARLA を起動できません。"
            "POST /carla/launch（host job）かホスト CLI / scripts/carla-launch.ps1 を使ってください。"
        )
    wanted = _normalize_components(components)
    carla_root = resolve_carla_root()
    planned: list[dict] = []
    runtime = load_runtime()
    comps = dict(runtime.get("components") or {})

    for name in wanted:
        if name == "server":
            if not carla_root:
                if dry_run:
                    planned.append(
                        {
                            "component": name,
                            "cmd": ["$CARLA_ROOT/CarlaUE4.sh", f"-quality-level={settings.carla_quality or 'Low'}"],
                            "missing_root": True,
                        }
                    )
                    continue
                raise FileNotFoundError(
                    "CARLA_ROOT が見つかりません。.env の CARLA_ROOT または settings.carla_root を設定してください。"
                )
            cmd = build_server_command(carla_root)
        else:
            try:
                cmd = build_mining_sim_command()
            except FileNotFoundError:
                if dry_run:
                    planned.append(
                        {
                            "component": name,
                            "cmd": ["python", "mining_sim.py", "--sync"],
                            "missing_script": True,
                        }
                    )
                    continue
                raise
        planned.append({"component": name, "cmd": cmd})

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "planned": [
                {
                    "component": p["component"],
                    "cmd": p["cmd"],
                    **{k: p[k] for k in ("missing_root", "missing_script") if k in p},
                }
                for p in planned
            ],
            "carla_root": str(carla_root) if carla_root else None,
            "probe": probe_carla(),
        }

    started: list[dict] = []
    for item in planned:
        name = item["component"]
        cmd = item["cmd"]
        info = _spawn(name, cmd)
        comps[name] = info
        started.append({"component": name, **info})

    runtime["components"] = comps
    runtime["carla_root"] = str(carla_root) if carla_root else None
    save_runtime(runtime)

    ready = None
    if wait_ready_sec > 0 and "server" in wanted:
        ready = _wait_port(wait_ready_sec)

    from ugv_mlops.db import audit, connect

    try:
        with connect() as conn:
            audit(
                conn,
                "pipeline.sim_generate",
                "carla",
                "launch",
                {"components": wanted, "started": started, "probe": probe_carla()},
            )
    except Exception:
        # DB 未起動でも CLI 起動は成功扱い
        pass

    return {
        "ok": True,
        "started": started,
        "probe": probe_carla(),
        "ready": ready,
        "runtime_path": str(runtime_path()),
        "next": "mining_sim 操作後 ros2 bag record → POST /carla/import",
    }


def stop(components: list[str] | None = None) -> dict:
    if in_container():
        raise RuntimeError(
            "コンテナ内では停止できません。host worker の carla_stop かホスト CLI を使ってください。"
        )
    wanted = _normalize_components(components) if components else list(COMPONENTS)
    runtime = load_runtime()
    comps = dict(runtime.get("components") or {})
    stopped: list[dict] = []
    for name in wanted:
        info = comps.pop(name, None)
        detail = _terminate(name, info)
        stopped.append({"component": name, **detail})
    # WSL フォールバック: 追跡 PID が無くてもプロセス名で止める
    if "server" in wanted and (settings.carla_launch_mode or "wsl").strip().lower() == "wsl":
        _wsl_pkill("CarlaUE4")
    if "mining_sim" in wanted and (settings.carla_launch_mode or "wsl").strip().lower() == "wsl":
        _wsl_pkill("mining_sim.py")
    runtime["components"] = comps
    save_runtime(runtime)
    return {"ok": True, "stopped": stopped, "probe": probe_carla()}


def _normalize_components(components: list[str] | None) -> list[str]:
    if not components:
        return ["server"]
    out: list[str] = []
    for raw in components:
        name = (raw or "").strip().lower().replace("-", "_")
        if name in {"stack", "all", "both"}:
            return ["server", "mining_sim"]
        if name in {"carla", "ue4", "server"}:
            name = "server"
        if name in {"mining", "sim", "mining_sim"}:
            name = "mining_sim"
        if name not in COMPONENTS:
            raise ValueError(f"unknown component: {raw}（server / mining_sim / stack）")
        if name not in out:
            out.append(name)
    return out


def _spawn(component: str, cmd: list[str]) -> dict:
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    # WSL nohup 起動は stdout に PID が返る
    if cmd and cmd[0] == "wsl" and "nohup" in " ".join(cmd):
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        pid_txt = (completed.stdout or "").strip().splitlines()
        wsl_pid = None
        if pid_txt and pid_txt[-1].isdigit():
            wsl_pid = int(pid_txt[-1])
        if completed.returncode != 0 and wsl_pid is None:
            raise RuntimeError(
                f"{component} 起動失敗: rc={completed.returncode} "
                f"stderr={(completed.stderr or '')[:500]}"
            )
        return {
            "pid": None,
            "wsl_pid": wsl_pid,
            "cmd": cmd,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "mode": settings.carla_launch_mode,
            "log": "/tmp/carla-ue4.log" if component == "server" else "/tmp/carla-mining-sim.log",
        }

    log_dir = settings.work_path / "carla"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{component}.log"
    log_f = open(log_path, "ab")
    proc = subprocess.Popen(
        cmd,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        cwd=str(settings.work_path),
        creationflags=creationflags,
        start_new_session=not sys.platform.startswith("win"),
    )
    return {
        "pid": proc.pid,
        "wsl_pid": None,
        "cmd": cmd,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": settings.carla_launch_mode,
        "log": str(log_path),
    }


def _terminate(component: str, info: dict | None) -> dict:
    if not info:
        return {"found": False}
    pid = info.get("pid")
    wsl_pid = info.get("wsl_pid")
    killed = False
    if pid:
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
            else:
                os.kill(int(pid), signal.SIGTERM)
            killed = True
        except (OSError, ValueError):
            pass
    if wsl_pid:
        _wsl_bash(f"kill {int(wsl_pid)} 2>/dev/null || true")
        killed = True
    return {"found": True, "killed": killed, "pid": pid, "wsl_pid": wsl_pid, "component": component}


def _wsl_pkill(pattern: str) -> None:
    _wsl_bash(f"pkill -f '{pattern}' 2>/dev/null || true")


def _wsl_bash(inner: str) -> None:
    if not sys.platform.startswith("win"):
        subprocess.run(["bash", "-lc", inner], capture_output=True, check=False)
        return
    if not shutil.which("wsl"):
        return
    cmd = _wsl_bash_cmd(inner)
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _wait_port(timeout_sec: float) -> dict:
    deadline = time.time() + timeout_sec
    last = probe_carla()
    while time.time() < deadline:
        last = probe_carla()
        if last.get("listening"):
            return {"ok": True, **last}
        time.sleep(1.0)
    return {"ok": False, **last}
