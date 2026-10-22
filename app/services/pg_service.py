"""确保配置所指向的「本机 PostgreSQL 服务」处于运行状态（仅 Windows）。

背景：部分用户的 PostgreSQL Windows 服务没有随开机启动（启动类型为「手动」或
服务启动失败），进入会话界面时数据库连不上，接口报 500。

本模块做两件事：
1. 运行期（studio 启动时）：解析 ``configs/*.json`` 里的连接串；若 host 为本机，
   按端口匹配到对应的 ``postgresql*`` Windows 服务；未运行则尝试启动。
   普通用户没有启动服务的权限，故**静默启动**失败时会（可选地）弹一次 UAC
   提权来启动（每次进程内每个服务最多提权一次，避免反复弹窗）。
2. 安装期（``setup.ps1``）：把检测到的 PostgreSQL 服务启动类型设为「自动」，
   从根上保证开机即启动（见该脚本）。

本模块只依赖标准库，供启动早期（app.main 的 lifespan 后台线程）调用。
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

DEFAULT_PG_PORT = 5432

_LOCAL_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "local",
}

_ATTEMPTED_ELEVATION: set[str] = set()
_LOCK = threading.Lock()

# 启动活动跟踪：供接口判断「数据库还没就绪是因为服务仍在启动」，
# 从而给用户显示「正在加载，请稍候」而不是一堆报错。
_ACTIVITY_LOCK = threading.Lock()
_ACTIVITY: dict[str, float | int] = {"in_progress": 0, "pending_until": 0.0}
# 一旦发起过启动（静默或提权），在此窗口内都认为「可能马上就绪」，
# 覆盖低配机器上服务启动 + 初次恢复的较长耗时。
_STARTUP_GRACE = 300.0


def _mark_pending(seconds: float = _STARTUP_GRACE) -> None:
    with _ACTIVITY_LOCK:
        _ACTIVITY["pending_until"] = max(float(_ACTIVITY["pending_until"]), time.time() + seconds)


def startup_in_progress() -> bool:
    """是否有本机 PG 服务正被（或刚被）拉起——用于区分「仍在加载」与「确实失败」。"""
    with _ACTIVITY_LOCK:
        if int(_ACTIVITY["in_progress"]) > 0:
            return True
        return time.time() < float(_ACTIVITY["pending_until"])


def _log(msg: str) -> None:
    """pythonw 下 stdout/stderr 可能为 None，写入时静默失败即可。"""
    try:
        stream = sys.stderr or sys.stdout
        if stream is not None:
            stream.write(msg + "\n")
            stream.flush()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 连接串解析
# --------------------------------------------------------------------------- #
def parse_conn_host_port(conn_string: str) -> tuple[str | None, int | None]:
    """从连接串里取出 host / 端口，兼容 URI 与 key=value 两种写法。"""
    s = (conn_string or "").strip()
    if not s:
        return None, None
    host: str | None = None
    port: int | None = None

    if "://" in s:
        try:
            u = urlsplit(s)
            host = u.hostname or None
            port = u.port
        except ValueError:
            host = None
    else:
        for m in re.finditer(r"(?:^|\s)(host|port)\s*=\s*'?([^'\s]+)'?", s):
            key = m.group(1).lower()
            val = m.group(2)
            if key == "host":
                host = val
            elif key == "port":
                try:
                    port = int(val)
                except ValueError:
                    pass

    if host and not port:
        port = DEFAULT_PG_PORT
    return host, port


def is_local_host(host: str | None) -> bool:
    if not host:
        return True
    h = host.strip().strip("[]").lower()
    return (
        h in _LOCAL_HOSTS
        or h.startswith("127.")
        or h == (os.environ.get("COMPUTERNAME") or "").lower()
    )


# --------------------------------------------------------------------------- #
# Windows 服务探测 / 启停（ctypes 调 SCM，避免依赖 pywin32、避免 sc 输出本地化）
# --------------------------------------------------------------------------- #
SC_MANAGER_CONNECT = 0x0001
SERVICE_QUERY_STATUS = 0x0004
SERVICE_START = 0x0010

SERVICE_STOPPED = 0x0001
SERVICE_START_PENDING = 0x0002
SERVICE_STOP_PENDING = 0x0003
SERVICE_RUNNING = 0x0004
SERVICE_CONTINUE_PENDING = 0x0005
SERVICE_PAUSE_PENDING = 0x0006
SERVICE_PAUSED = 0x0007

ERROR_ACCESS_DENIED = 5
ERROR_SERVICE_ALREADY_RUNNING = 1056

_advapi32 = None


def _advapi():
    global _advapi32
    if _advapi32 is None:
        from ctypes import wintypes

        class SERVICE_STATUS(ctypes.Structure):
            _fields_ = [
                ("dwServiceType", wintypes.DWORD),
                ("dwCurrentState", wintypes.DWORD),
                ("dwControlsAccepted", wintypes.DWORD),
                ("dwWin32ExitCode", wintypes.DWORD),
                ("dwServiceSpecificExitCode", wintypes.DWORD),
                ("dwCheckPoint", wintypes.DWORD),
                ("dwWaitHint", wintypes.DWORD),
            ]

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi32.OpenSCManagerW.restype = wintypes.HANDLE
        advapi32.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        advapi32.OpenServiceW.restype = wintypes.HANDLE
        advapi32.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
        advapi32.QueryServiceStatus.restype = wintypes.BOOL
        advapi32.QueryServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(SERVICE_STATUS)]
        advapi32.StartServiceW.restype = wintypes.BOOL
        advapi32.StartServiceW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPCWSTR),
        ]
        advapi32.CloseServiceHandle.restype = wintypes.BOOL
        advapi32.CloseServiceHandle.argtypes = [wintypes.HANDLE]
        _advapi32 = (advapi32, SERVICE_STATUS)
    return _advapi32


def _query_state(name: str) -> int | None:
    advapi32, SERVICE_STATUS = _advapi()
    hscm = advapi32.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not hscm:
        return None
    try:
        hsvc = advapi32.OpenServiceW(hscm, name, SERVICE_QUERY_STATUS)
        if not hsvc:
            return None
        try:
            st = SERVICE_STATUS()
            if not advapi32.QueryServiceStatus(hsvc, ctypes.byref(st)):
                return None
            return int(st.dwCurrentState)
        finally:
            advapi32.CloseServiceHandle(hsvc)
    finally:
        advapi32.CloseServiceHandle(hscm)


def _try_start(name: str) -> int | None:
    """尝试静默启动服务。成功返回 None，失败返回 Win32 错误码。"""
    advapi32, _ = _advapi()
    hscm = advapi32.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not hscm:
        return ctypes.get_last_error() or -1
    try:
        hsvc = advapi32.OpenServiceW(hscm, name, SERVICE_START)
        if not hsvc:
            return ctypes.get_last_error() or -1
        try:
            if advapi32.StartServiceW(hsvc, 0, None):
                return None
            return ctypes.get_last_error() or -1
        finally:
            advapi32.CloseServiceHandle(hsvc)
    finally:
        advapi32.CloseServiceHandle(hscm)


def _wait_running(name: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _query_state(name) == SERVICE_RUNNING:
            return True
        time.sleep(0.5)
    return False


def _elevated_start(name: str) -> bool:
    """通过 UAC 提权，用 PowerShell Start-Service 启动服务。"""
    try:
        params = (
            '-NoProfile -ExecutionPolicy Bypass -Command '
            f'"Start-Service -Name \'{name}\'"'
        )
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "powershell.exe", params, None, 0
        )
        return int(rc) > 32
    except Exception:
        return False


def ensure_service_running(
    name: str, *, allow_elevation: bool = True, wait: float = 20.0
) -> str:
    """确保某个服务在运行。

    返回状态字符串：running / started / starting / denied / error:<code> / not-found。
    """
    with _ACTIVITY_LOCK:
        _ACTIVITY["in_progress"] = int(_ACTIVITY["in_progress"]) + 1
    try:
        state = _query_state(name)
        if state is None:
            return "not-found"
        if state == SERVICE_RUNNING:
            return "running"
        if state in (
            SERVICE_START_PENDING,
            SERVICE_CONTINUE_PENDING,
            SERVICE_PAUSE_PENDING,
            SERVICE_STOP_PENDING,
        ):
            # 服务正在启动/恢复中：告诉上层「仍在加载」，别催。
            _mark_pending()
            return "starting"

        err = _try_start(name)
        if err is None:
            _mark_pending()
            return "started" if _wait_running(name, wait) else "starting"
        if err == ERROR_SERVICE_ALREADY_RUNNING:
            return "running"

        if err == ERROR_ACCESS_DENIED and allow_elevation:
            with _LOCK:
                already = name in _ATTEMPTED_ELEVATION
                _ATTEMPTED_ELEVATION.add(name)
            if already:
                return "denied"
            _log(f"[pg-service] 需要管理员权限启动服务 {name}，正在请求提权…")
            launched = _elevated_start(name)
            if launched:
                # 用户已通过 UAC 授权、提升进程已启动 → 服务很可能正在起来
                _mark_pending()
                return "started" if _wait_running(name, wait) else "starting"
            return "denied"

        return f"error:{err}"
    finally:
        with _ACTIVITY_LOCK:
            _ACTIVITY["in_progress"] = max(0, int(_ACTIVITY["in_progress"]) - 1)


# --------------------------------------------------------------------------- #
# 服务发现：注册表拿服务名单与数据目录，postgresql.conf 拿端口
# --------------------------------------------------------------------------- #
def _extract_service_paths(cmdline: str) -> tuple[str | None, str | None]:
    """从服务命令行里取 (数据目录, 安装根目录)。

    形如：``"C:\\...\\bin\\pg_ctl.exe" runservice -N "svc" -D "C:\\...\\data" -w``
    """
    data_dir = None
    m = re.search(r'-D\s+("([^"]+)"|(\S+))', cmdline)
    if m:
        data_dir = m.group(2) or m.group(3)

    base_dir = None
    m2 = re.match(r'\s*"([^"]+)"', cmdline) or re.match(r"\s*(\S+)", cmdline)
    if m2:
        exe = Path(m2.group(1))
        if exe.parent.name.lower() == "bin":
            base_dir = str(exe.parent.parent)
    return data_dir, base_dir


def _scan_port(text: str) -> int | None:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"port\s*=\s*'?(\d+)'?", line)
        if m:
            return int(m.group(1))
    return None


def _read_conf_port(data_dir: str | None) -> int | None:
    """从 data 目录的 conf 里读端口；目录不可读（Windows 安装默认锁 ACL）返回 None。"""
    if not data_dir:
        return None
    d = Path(data_dir)
    read_ok = False
    for fname in ("postgresql.auto.conf", "postgresql.conf"):
        try:
            text = (d / fname).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        read_ok = True
        port = _scan_port(text)
        if port is not None:
            return port
    # 目录可读但没写 port → PostgreSQL 默认 5432
    if read_ok:
        return DEFAULT_PG_PORT
    return None


def _read_summary_port(base_dir: str | None) -> int | None:
    """从安装根目录的 installation_summary.log 里读端口（EDB 官方安装器会写）。"""
    if not base_dir:
        return None
    try:
        text = (Path(base_dir) / "installation_summary.log").read_text(
            encoding="utf-8", errors="ignore"
        )
    except OSError:
        return None
    m = re.search(r"Database Port:\s*(\d+)", text)
    return int(m.group(1)) if m else None


def _registry_installations() -> dict[str, dict]:
    """读 PostgreSQL 安装注册表（EDB 安装器写）。

    路径：``HKLM\\SOFTWARE[\\WOW6432Node]\\PostgreSQL\\Installations\\<id>``，
    含 ``Service ID`` / ``Port`` / ``Data Directory`` / ``Base Directory``。
    不同版本/位数的安装器字段可能不全，这里尽力收集，供服务发现兜底。

    返回：``{ <service_id 小写>: {"port": int|None, "data_dir": str, "base_dir": str} }``。
    """
    result: dict[str, dict] = {}
    try:
        import winreg
    except Exception:
        return result

    locations = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\PostgreSQL\Installations"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\PostgreSQL\Installations"),
    ]
    for hive, sub in locations:
        try:
            root = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    keyname = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                entry: dict = {}
                try:
                    k = winreg.OpenKey(root, keyname)
                    try:
                        for valname, field in (
                            ("Service ID", "service_id"),
                            ("Port", "port"),
                            ("Data Directory", "data_dir"),
                            ("Base Directory", "base_dir"),
                        ):
                            try:
                                v, _ = winreg.QueryValueEx(k, valname)
                            except OSError:
                                continue
                            if field == "port":
                                try:
                                    entry["port"] = int(str(v).strip())
                                except (TypeError, ValueError):
                                    pass
                            elif v:
                                entry[field] = str(v)
                    finally:
                        k.Close()
                except OSError:
                    pass
                sid = str(entry.get("service_id") or keyname).lower()
                if entry:
                    result.setdefault(sid, entry)
        finally:
            root.Close()
    return result


def list_pg_services() -> list[dict]:
    """列出本机所有 PostgreSQL 服务：name / display / data_dir / port。

    发现方式对「版本 / 位数 / 安装器」尽量宽松：
    - 服务名以 ``postgresql`` / ``pgsql`` 开头，**或** 显示名含 ``PostgreSQL``；
    - 端口依次尝试：安装注册表的 ``Port`` → ``postgresql.conf`` → ``installation_summary.log``。
    """
    services: list[dict] = []
    try:
        import winreg
    except Exception:
        return services

    installs = _registry_installations()

    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services")
    except OSError:
        return services

    try:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1

            display = ""
            img = ""
            try:
                sub = winreg.OpenKey(root, name)
                try:
                    try:
                        display, _ = winreg.QueryValueEx(sub, "DisplayName")
                    except OSError:
                        pass
                    try:
                        img, _ = winreg.QueryValueEx(sub, "ImagePath")
                    except OSError:
                        pass
                finally:
                    sub.Close()
            except OSError:
                pass

            lname = name.lower()
            display_l = str(display).lower()
            if not (
                lname.startswith("postgresql")
                or lname.startswith("pgsql")
                or "postgresql" in display_l
            ):
                continue

            data_dir = base_dir = None
            if img:
                data_dir, base_dir = _extract_service_paths(os.path.expandvars(str(img)))

            info = installs.get(lname) or {}
            if not data_dir:
                data_dir = info.get("data_dir")
            if not base_dir:
                base_dir = info.get("base_dir")

            port = info.get("port")
            if port is None:
                port = _read_conf_port(data_dir)
            if port is None:
                port = _read_summary_port(base_dir)

            services.append(
                {
                    "name": name,
                    "display": str(display),
                    "data_dir": data_dir,
                    "port": port,
                }
            )
    finally:
        root.Close()
    return services


def _match_service(services: list[dict], port: int) -> dict | None:
    exact = [s for s in services if s.get("port") == port]
    if exact:
        return exact[0]
    unknown = [s for s in services if s.get("port") is None]
    if len(unknown) == 1:
        return unknown[0]
    return None


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def _iter_config_conn_strings(base_dir: Path | str):
    cfg_dir = Path(base_dir) / "configs"
    skip = {
        "default",
        "settings",
        "ui_prefs",
        "drafts",
        "common_prompts",
        "store_migrations",
        "identity_migration",
        "floating_stop_pos",
    }
    for path in sorted(cfg_dir.glob("*.json")):
        if path.stem in skip:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        pg = data.get("postgres") or {}
        prefix = pg.get("prefix") or ""
        db = pg.get("checkpoint_database") or ""
        suffix = pg.get("suffix") or ""
        if prefix:
            # 名称可能是中文/带空格：URI 里的数据库名要百分号编码（与 db_names 一致）。
            yield prefix + quote(str(db), safe="") + suffix


def _local_pg_ports(base_dir: Path | str) -> set[int]:
    ports: set[int] = set()
    for conn in _iter_config_conn_strings(base_dir):
        host, port = parse_conn_host_port(conn)
        if is_local_host(host) and port:
            ports.add(port)
    return ports


def ensure_for_conn_string(conn_string: str, *, allow_elevation: bool = True) -> str | None:
    """针对单个连接串：若是本机且能匹配到服务，确保其运行。"""
    if sys.platform != "win32":
        return None
    host, port = parse_conn_host_port(conn_string)
    if not (is_local_host(host) and port):
        return None
    svc = _match_service(list_pg_services(), port)
    if not svc:
        return None
    return ensure_service_running(svc["name"], allow_elevation=allow_elevation)


def ensure_pg_services_at_startup(
    base_dir: Path | str, *, allow_elevation: bool = True
) -> list[dict]:
    """扫描所有配置，启动匹配到的本机 PostgreSQL 服务。返回各项结果（供日志）。"""
    if sys.platform != "win32":
        return []
    ports = _local_pg_ports(base_dir)
    if not ports:
        return []
    # 存在本机目标库时，先给一个较短的「可能还在就绪」缓冲窗口；一旦真正发起
    # 启动，会被 ensure_service_running 延长到更长的 grace。
    _mark_pending(30.0)

    services = list_pg_services()
    results: list[dict] = []
    for port in sorted(ports):
        svc = _match_service(services, port)
        if not svc:
            results.append({"port": port, "service": None, "status": "no-service"})
            _log(f"[pg-service] 端口 {port}：未找到本机 PostgreSQL 服务")
            continue
        status = ensure_service_running(svc["name"], allow_elevation=allow_elevation)
        results.append({"port": port, "service": svc["name"], "status": status})
        _log(f"[pg-service] 端口 {port}：服务 {svc['name']} → {status}")
    return results
