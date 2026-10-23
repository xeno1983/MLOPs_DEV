"""DVC 版管理。四脚 PAI / 自動運転 DVC GUI と同じ層。

実体は Garage `s3://dvc`。ポインタ（*.dvc）だけ Git に残す。
学習ホットパスでは呼ばず、ingest / dataset の区切りで fail-soft する。
"""
from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

from ugv_mlops.settings import settings

PYTHON = sys.executable


def repo_root() -> Path:
    env = os.environ.get("DVC_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def initialized() -> bool:
    return (repo_root() / ".dvc" / "config").exists()


def run(args: list[str], timeout: int = 3600) -> dict:
    root = repo_root()
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID") or settings.minio_root_user,
        "AWS_SECRET_ACCESS_KEY": os.environ.get("AWS_SECRET_ACCESS_KEY") or settings.minio_root_password,
        "AWS_DEFAULT_REGION": settings.s3_region,
        "AWS_EC2_METADATA_DISABLED": "true",
    }
    proc = subprocess.run(
        args,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    return {
        "cmd": " ".join(args),
        "code": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
        "ok": proc.returncode == 0,
    }


def dvc(*args: str, timeout: int = 3600) -> dict:
    return run([PYTHON, "-m", "dvc", *args], timeout=timeout)


def _ensure_git_safe() -> None:
    root = repo_root()
    run(["git", "config", "--global", "--add", "safe.directory", str(root)])


def default_remote() -> str:
    return os.environ.get("DVC_REMOTE") or "garage"


def ensure_remote() -> dict:
    """ホストは garage（localhost:9000）、コンテナは DVC_REMOTE=garage-docker。"""
    if not initialized():
        init = dvc("init", "--subdir")
        if not init["ok"]:
            init = dvc("init")
        if not init["ok"]:
            return init
    return {"ok": True, "remote": default_remote(), "remotes": remotes()}


def remotes() -> list[dict]:
    if not initialized():
        return []
    cfg = (repo_root() / ".dvc" / "config").read_text(encoding="utf-8")
    local = repo_root() / ".dvc" / "config.local"
    extra = local.read_text(encoding="utf-8") if local.exists() else ""
    parsed = _parse_ini(cfg + "\n" + extra)
    default = parsed.get("core", {}).get("remote")
    out = []
    for section, values in parsed.items():
        if "remote" in section and '"' in section:
            name = section.split('"')[1]
            out.append(
                {
                    "name": name,
                    "url": values.get("url") or "",
                    "default": name == default,
                    "endpointurl": values.get("endpointurl"),
                }
            )
    return out


def _parse_ini(text: str) -> dict[str, dict[str, str]]:
    current = ""
    data: dict[str, dict[str, str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            data.setdefault(current, {})
            continue
        if "=" in line and current:
            key, value = line.split("=", 1)
            data[current][key.strip()] = value.strip()
    return data


def status() -> dict:
    root = repo_root()
    skip = {".venv", ".git", "node_modules", "__pycache__"}
    pointers = sorted(
        str(p.relative_to(root).as_posix())
        for p in root.rglob("*.dvc")
        if not skip.intersection(p.parts)
    )
    version = dvc("version", timeout=30) if _dvc_importable() else {"ok": False, "stdout": "dvc not installed"}
    return {
        "ok": initialized(),
        "root": str(root),
        "initialized": initialized(),
        "dvc_installed": _dvc_importable(),
        "dvc_version": (version.get("stdout") or version.get("stderr") or "").splitlines()[:1],
        "remotes": remotes(),
        "pointers": pointers[:80],
        "detail": "ポインタは Git、実体は Garage s3://dvc。区切りでのみ add/push。",
    }


def _dvc_importable() -> bool:
    try:
        import dvc  # noqa: F401

        return True
    except Exception:
        return False


def _rel(path: Path) -> str:
    return path.resolve().relative_to(repo_root().resolve()).as_posix()


def publish_mission(mission_id: str, push: bool = True) -> dict:
    """raw + parsed を DVC 追跡して Garage へ。無ければ inbox をフォールバック。"""
    if not _dvc_importable():
        return {"ok": False, "error": "dvc not installed (requirements-dvc.txt)"}
    from ugv_mlops.bag_pipeline import parsed_dir, raw_dir, store_mission

    if raw_dir(mission_id).exists() or parsed_dir(mission_id).exists():
        return store_mission(mission_id, push=push)
    dest = repo_root() / "data" / "inbox" / mission_id
    src = Path(settings.inbox_dir) / mission_id
    if not dest.exists() and src.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dest.resolve():
            import shutil

            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            dest = src
    if not dest.exists():
        return {"ok": False, "error": f"mission folder missing: {src}"}
    return _add_and_push(dest, push=push)


def publish_dataset(dataset_version_id: str, object_uri: str, push: bool = True) -> dict:
    """データセット zip を data/dvc/datasets/<id>/ に展開して追跡。"""
    if not _dvc_importable():
        return {"ok": False, "error": "dvc not installed (requirements-dvc.txt)"}
    dest = repo_root() / "data" / "dvc" / "datasets" / dataset_version_id
    dest.mkdir(parents=True, exist_ok=True)
    from ugv_mlops.storage import get_file, parse_s3_uri

    bucket, key = parse_s3_uri(object_uri)
    zip_path = dest / "dataset.zip"
    get_file(bucket, key, zip_path)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest / "unpacked")
    except zipfile.BadZipFile:
        pass
    return _add_and_push(dest, push=push)


def pull(target: str | None = None) -> dict:
    if not _dvc_importable():
        return {"ok": False, "error": "dvc not installed"}
    ensure_remote()
    _ensure_git_safe()
    args = ["pull", "-r", default_remote()]
    if target:
        args.append(target)
    return dvc(*args)


def _add_and_push(path: Path, push: bool) -> dict:
    _ensure_git_safe()
    ready = ensure_remote()
    if not ready.get("ok") and not initialized():
        return {"ok": False, "ensure_remote": ready}
    rel = _rel(path)
    added = dvc("add", rel)
    pushed = (
        dvc("push", "-r", default_remote(), rel)
        if push and added["ok"]
        else {"ok": True, "skipped": not push}
    )
    pointer = path.parent / f"{path.name}.dvc"
    return {
        "ok": bool(added["ok"] and pushed.get("ok", True)),
        "path": rel,
        "pointer": str(pointer.relative_to(repo_root()).as_posix()) if pointer.exists() else None,
        "add": added,
        "push": pushed,
        "remote": ready,
    }
