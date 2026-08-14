"""模型下载状态管理：供前端轮询进度、托盘显示下载状态。"""

from __future__ import annotations

import threading
import time

from tqdm.auto import tqdm


class DownloadManager:
    def __init__(self) -> None:
        self._status: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, model_name: str, total: int = 0) -> None:
        with self._lock:
            self._status[model_name] = {
                "model": model_name,
                "downloaded": 0,
                "total": total,
                "speed": 0.0,
                "status": "downloading",
                "error": None,
            }

    def update(self, model_name: str, downloaded: int, total: int, speed: float) -> None:
        with self._lock:
            self._status[model_name] = {
                "model": model_name,
                "downloaded": downloaded,
                "total": total,
                "speed": speed,
                "status": "downloading",
                "error": None,
            }

    def done(self, model_name: str) -> None:
        with self._lock:
            st = self._status.get(model_name, {})
            self._status[model_name] = {
                "model": model_name,
                "downloaded": st.get("total", 0),
                "total": st.get("total", 0),
                "speed": 0.0,
                "status": "done",
                "error": None,
            }

    def error(self, model_name: str, err: str) -> None:
        with self._lock:
            self._status[model_name] = {
                "model": model_name,
                "downloaded": 0,
                "total": 0,
                "speed": 0.0,
                "status": "error",
                "error": err,
            }

    def get(self, model_name: str) -> dict | None:
        with self._lock:
            st = self._status.get(model_name)
            return dict(st) if st else None

    def current(self) -> dict | None:
        """当前正在下载的任务（若有）。"""
        with self._lock:
            for st in self._status.values():
                if st["status"] == "downloading":
                    return dict(st)
            return None


download_manager = DownloadManager()


def make_progress_tqdm(model_name: str):
    """自定义 tqdm 子类，把 snapshot_download 的下载进度回传给 DownloadManager。"""

    class _ProgressTqdm(tqdm):
        def __init__(self, *args, **kwargs):
            desc = kwargs.get("desc", "") or (args[0] if args else "")
            self._is_transfer = "Downloading" in str(desc)
            self._model = model_name
            self._start = time.time()
            self._last_n = 0
            self._last_t = self._start
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            super().update(n)
            if not (self._is_transfer and self.total):
                return
            now = time.time()
            dt = now - self._last_t
            if dt >= 0.3:
                speed = (self.n - self._last_n) / dt if dt > 0 else 0.0
                download_manager.update(self._model, self.n, self.total, speed)
                self._last_n = self.n
                self._last_t = now

    return _ProgressTqdm
