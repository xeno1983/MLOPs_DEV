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


def _is_posix_abs(raw: str) -> bool:
    s = raw.replace("\\", "/")
    return s.startswith("/") and not (len(s) > 1 and s[1] == ":")


def resolve_carla_root() -> Path | None:
    candidates: list[str] = []
    wsl_home = _wsl_home()
    for raw in (
        settings.carla_root,
        os.environ.get("CARLA_ROOT", ""),
        r"C:\CARLA",
        r"C:\Carla",
        str(Path.home() / "CARLA"),
        f"{wsl_home}/CARLA" if wsl_home else "",
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
        # Linux パスは Windows で Path.resolve() すると壊れるので先に WSL 検査
        if _is_posix_abs(raw):
            if _wsl_looks_like_carla(raw) or _looks_like_carla_root(Path(raw)):
                return Path(raw)
            continue
        p = Path(raw)
        if _looks_like_carla_root(p):
            return p
        if _wsl_looks_like_carla(str(p)):
            return p
    return None


def _looks_like_carla_root(root: Path) -> bool:
    try:
        if not root.is_dir():
            return False
    except OSError:
        return False
    names = {p.name for p in root.iterdir()} if root.is_dir() else set()
    markers = {"CarlaUE4.sh", "CarlaUE4.exe", "CarlaUE4", "CarlaUE4-Linux-Shipping"}
    return bool(names & markers) or (root / "CarlaUE4.sh").is_file() or (root / "CarlaUE4.exe").is_file()


def _wsl_home() -> str | None:
    if not sys.platform.startswith("win") or not shutil.which("wsl"):
        return None
    try:
        completed = subprocess.run(
            ["wsl", "bash", "-lc", "printf %s \"$HOME\""],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        home = (completed.stdout or "").strip()
        return home or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _wsl_looks_like_carla(linux_path: str) -> bool:
    """WSL 内の CarlaUE4.sh 有無（Windows から Linux パスを見るとき）。"""
    if not linux_path.startswith("/") or not sys.platform.startswith("win"):
        return False
    if not shutil.which("wsl"):
        return False
    q = linux_path.replace("'", "'\\''")
    inner = (
        f"test -f '{q}/CarlaUE4.sh' -o -f '{q}/CarlaUE4.exe' "
        f"-o -d '{q}/CarlaUE4' && echo yes || echo no"
    )
    try:
        completed = subprocess.run(
            _wsl_bash_cmd(inner),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return (completed.stdout or "").strip().splitlines()[-1:] == ["yes"]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return False


def _wsl_test(inner_test: str) -> bool:
    if not sys.platform.startswith("win") or not shutil.which("wsl"):
        return False
    try:
        completed = subprocess.run(
            _wsl_bash_cmd(f"{inner_test} && echo yes || echo no"),
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        return (completed.stdout or "").strip().splitlines()[-1:] == ["yes"]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return False


def prepare() -> dict:
    """シミュ起動前の前提チェックとディレクトリ準備（ダウンロードは scripts/setup-carla）。"""
    aw = resolve_autoware_root()
    bag_dir = aw / "datasets" / "carla_bags"
    try:
        bag_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    settings.work_path.joinpath("carla").mkdir(parents=True, exist_ok=True)

    root = resolve_carla_root()
    mining = aw / "autoware.off-road" / "Simulation" / "CARLA" / "scripts" / "mining_sim.py"
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, fix: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    wsl_ok = bool(shutil.which("wsl")) if sys.platform.startswith("win") else True
    add(
        "wsl",
        wsl_ok if sys.platform.startswith("win") else True,
        "WSL あり" if wsl_ok or not sys.platform.startswith("win") else "WSL なし",
        "wsl --install 後に Ubuntu を用意" if not wsl_ok else "",
    )
    add(
        "carla_root",
        root is not None,
        str(root) if root else "未検出",
        "scripts/setup-carla.ps1 -Download を実行し .env の CARLA_ROOT を設定",
    )
    mine_ok = False
    if root is not None:
        linux = _to_wsl_path(root) if sys.platform.startswith("win") else str(root)
        mine_ok = _wsl_test(
            f"ls '{linux}'/CarlaUE4/Content/Carla/Maps/Mine_01* "
            f"'{linux}'/Import/Mine_01* >/dev/null 2>&1"
        )
        if not mine_ok:
            try:
                mine_ok = root.exists() and any(root.rglob("Mine_01*"))
            except OSError:
                mine_ok = False
    add(
        "mine_01_map",
        mine_ok,
        "Mine_01 あり" if mine_ok else "Mine_01 マップ未配置（mining_sim が load_world で失敗する）",
        "CARLA Import に Mine_01 を入れ、ImportAssets.sh を実行（Autoware off-road 手順）",
    )
    add(
        "mining_sim",
        mining.is_file(),
        str(mining) if mining.is_file() else "mining_sim.py なし",
        "AUTOWARE_E2E_ROOT を確認",
    )
    ros_ok = _wsl_test("test -f /opt/ros/humble/setup.bash -o -f /opt/ros/jazzy/setup.bash") or (
        not sys.platform.startswith("win")
        and (Path("/opt/ros/humble/setup.bash").is_file() or Path("/opt/ros/jazzy/setup.bash").is_file())
    )
    add(
        "ros2_wsl",
        ros_ok,
        "ROS 2 setup.bash あり" if ros_ok else "ROS 2 未検出",
        "WSL に ros-humble-desktop または jazzy を導入",
    )
    add(
        "bag_output_dir",
        bag_dir.is_dir(),
        str(bag_dir),
        "",
    )
    probe = probe_carla()
    add(
        "carla_tcp",
        bool(probe.get("listening")),
        f"{probe.get('host')}:{probe.get('port')} "
        + ("応答あり（起動済み）" if probe.get("listening") else "未起動（準備完了後に carla-launch）"),
        "",
    )
    mode = (settings.carla_launch_mode or "wsl").strip().lower()
    configured = bool((settings.carla_root or "").strip() or os.environ.get("CARLA_ROOT"))
    add(
        "env_carla_root",
        configured,
        settings.carla_root or os.environ.get("CARLA_ROOT") or "未設定",
        ".env に CARLA_ROOT=/home/<user>/CARLA と CARLA_LAUNCH_MODE=wsl",
    )

    ready = all(
        c["ok"]
        for c in checks
        if c["name"] in {"wsl", "carla_root", "mining_sim", "ros2_wsl", "bag_output_dir", "env_carla_root"}
    )
    # Mine_01 と TCP は launch 直前条件（インストール準備の必須からは外す）
    return {
        "ok": ready,
        "ready_to_install_or_launch": ready,
        "ready_for_mining_sim": ready and mine_ok,
        "carla_root": str(root) if root else None,
        "launch_mode": mode,
        "autoware_e2e_root": str(aw),
        "bag_dir": str(bag_dir),
        "probe": probe,
        "checks": checks,
        "next": (
            ["python -m ugv_mlops.cli carla-launch --component stack --wait-ready-sec 60"]
            if ready and mine_ok
            else (
                ["scripts/setup-carla.ps1 -Download"]
                if root is None
                else [
                    "Mine_01 を CARLA に配置",
                    "python -m ugv_mlops.cli carla-launch --component server --wait-ready-sec 60",
                ]
            )
        ),
        "setup_script": "scripts/setup-carla.ps1 -Download",
    }


def probe_host() -> str:
    """コンテナ内からホスト上の CARLA を突くときの宛先。"""
    host = (settings.carla_host or "127.0.0.1").strip()
    if in_container() and host in {"127.0.0.1", "localhost", "::1"}:
        return os.environ.get("CARLA_PROBE_HOST", "host.docker.internal")
    return host


def probe_carla(host: str | None = None, port: int | None = None, timeout: float = 0.6) -> dict:
    host = host or probe_host()
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
    # 同期実行用マーカー（_spawn ではなく launch() が scripts/run-carla-wsl.sh を待つ）
    linux_root = _to_wsl_path(carla_root)
    if sys.platform.startswith("win"):
        script = Path(__file__).resolve().parents[2] / "scripts" / "run-carla-wsl.sh"
        return ["__carla_wsl_sync__", linux_root, str(script), quality]
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
            f'source /opt/ros/humble/setup.bash 2>/dev/null || '
            f'source /opt/ros/jazzy/setup.bash 2>/dev/null; '
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


def _to_wsl_path(path: Path | str) -> str:
    """C:\\foo → /mnt/c/foo。既に POSIX ならそのまま（resolve しない）。"""
    s = str(path).strip().replace("\\", "/")
    if _is_posix_abs(s):
        return s
    # Windows パス。resolve は POSIX 風を壊すので使わない
    win = str(path)
    try:
        completed = subprocess.run(
            ["wsl", "wslpath", "-a", win],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    if len(win) >= 2 and win[1] == ":":
        drive = win[0].lower()
        rest = win[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"
    return s


REQUEST_REL = Path("carla") / "request.json"


def request_path() -> Path:
    p = settings.work_path / REQUEST_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write_host_request(action: str, components: list[str] | None = None) -> dict:
    """Catalog(コンテナ) → ホスト agent への起動依頼（共有 data/work）。"""
    comps = _normalize_components(components) if action == "launch" else (
        _normalize_components(components) if components else list(COMPONENTS)
    )
    body = {
        "action": action,
        "components": comps,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "status": "queued",
    }
    path = request_path()
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "request_path": str(path), **body}


def read_host_request() -> dict | None:
    path = request_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def mark_host_request(status: str, detail: dict | None = None) -> None:
    cur = read_host_request() or {}
    cur["status"] = status
    cur["updated_at"] = datetime.now(timezone.utc).isoformat()
    if detail:
        cur["result"] = detail
    request_path().write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")


def process_host_request() -> dict | None:
    """ホスト上で request.json を実行（agent / host worker から呼ぶ）。"""
    req = read_host_request()
    if not req or req.get("status") not in {None, "queued", "pending"}:
        return None
    if req.get("status") == "running":
        return req
    mark_host_request("running")
    action = (req.get("action") or "launch").strip().lower()
    comps = req.get("components") or ["server"]
    try:
        if action == "stop":
            result = stop(comps)
        else:
            result = launch(comps, wait_ready_sec=90)
        mark_host_request("done", result)
        return {"ok": True, "action": action, "result": result}
    except Exception as exc:  # noqa: BLE001
        mark_host_request("failed", {"error": str(exc)})
        return {"ok": False, "action": action, "error": str(exc)}


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

    try:
        from ugv_mlops.db import audit, connect

        with connect() as conn:
            audit(
                conn,
                "pipeline.sim_generate",
                "carla",
                "launch",
                {"components": wanted, "started": started, "probe": probe_carla()},
            )
    except Exception:
        # DB / ドライバ未導入でも CLI 起動は成功扱い
        pass

    return {
        "ok": True,
        "started": started,
        "probe": probe_carla(),
        "ready": ready,
        "runtime_path": str(runtime_path()),
        "next": "mining_sim 操作後 ros2 bag record → POST /carla/import",
        "ready_ok": bool(ready.get("ok")) if isinstance(ready, dict) else ready is None,
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
    # Windows+WSL の server は run-carla-wsl.sh を同期実行（:2000 まで待つ）
    if cmd and cmd[0] == "__carla_wsl_sync__":
        linux_root, script, quality = cmd[1], Path(cmd[2]), cmd[3]
        linux_script = _to_wsl_path(script) if script.is_file() else ""
        if not script.is_file():
            raise FileNotFoundError(f"run-carla-wsl.sh がありません: {script}")
        inner = (
            f"sed -i 's/\\r$//' '{linux_script}' 2>/dev/null; "
            f"export DISPLAY=:0; "
            f"CARLA_ROOT='{linux_root}' CARLA_QUALITY='{quality}' CARLA_MODE=window "
            f"bash '{linux_script}'"
        )
        wsl_cmd = _wsl_bash_cmd(inner)
        completed = subprocess.run(
            wsl_cmd,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        out = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
        if completed.returncode != 0 and "READY port 2000" not in out:
            raise RuntimeError(
                f"CARLA WSL 起動失敗 rc={completed.returncode}: {out[-800:]}"
            )
        return {
            "pid": None,
            "wsl_pid": None,
            "cmd": wsl_cmd,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "mode": "wsl-sync",
            "log": "/tmp/carla-ue4.log",
            "output": out[-500:],
        }

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
