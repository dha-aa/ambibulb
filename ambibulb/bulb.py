"""A single bulb's connection and its background worker thread.

Each Bulb owns a tinytuya device and a thread that applies the most recent
requested HSV color. The caller never blocks on bulb I/O: it drops the latest
color via `set()` and the worker catches up, reconnecting (and re-discovering
the IP) on its own if the bulb drops off the network.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .config import BulbConfig

log = logging.getLogger("ambibulb.bulb")

HSV = tuple[float, float, float]


@dataclass(frozen=True)
class BulbOptions:
    rate: int = 12            # max color commands/sec per bulb
    socket_timeout: float = 2.0
    retry_limit: int = 1
    backoff_max: float = 30.0


class Bulb:
    def __init__(self, cfg: BulbConfig, opts: BulbOptions | None = None) -> None:
        self.cfg = cfg
        self.name = cfg.name
        self.opts = opts or BulbOptions()
        self._ip = cfg.ip
        self._version = cfg.version
        self._dev = self._make_device(self._ip)

        self._target: HSV | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._connected = False
        self._ever_ok = False
        self._mode_set = False
        self._thread = threading.Thread(
            target=self._run, name=f"bulb:{self.name}", daemon=True
        )

    # -- lifecycle ---------------------------------------------------------- #
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def set(self, hsv: HSV) -> None:
        with self._lock:
            self._target = hsv

    # -- connection helpers ------------------------------------------------- #
    def _make_device(self, address):
        import tinytuya  # lazy: keep config/color usable without it installed

        dev = tinytuya.BulbDevice(
            dev_id=self.cfg.id,
            address=address,
            local_key=self.cfg.key,
            version=self._version,
            persist=True,
        )
        dev.set_socketTimeout(self.opts.socket_timeout)
        dev.set_socketRetryLimit(self.opts.retry_limit)
        return dev

    def _rediscover(self) -> bool:
        """Locate the bulb's current IP by device id (handles DHCP changes)."""
        import tinytuya

        try:
            info = tinytuya.find_device(dev_id=self.cfg.id)
        except Exception:
            info = None
        ip = (info or {}).get("ip")
        if not ip:
            return False
        if ip != self._ip:
            log.info("%s: IP moved %s -> %s", self.name, self._ip, ip)
        self._ip = ip
        if (info or {}).get("version"):
            self._version = float(info["version"])
        self._dev = self._make_device(self._ip)
        return True

    # -- worker ------------------------------------------------------------- #
    def _run(self) -> None:
        last_sent: HSV | None = None
        last_send_t = 0.0
        fails = 0
        backoff = 1.0
        min_send = 1.0 / max(1, self.opts.rate)

        while not self._stop.is_set():
            with self._lock:
                hsv = self._target
            if hsv is None or hsv == last_sent:
                self._stop.wait(0.02)
                continue
            wait = min_send - (time.time() - last_send_t)
            if wait > 0:
                self._stop.wait(wait)
                continue
            try:
                if not self._mode_set:
                    self._dev.turn_on()
                    self._dev.set_mode("colour")
                    self._mode_set = True
                self._dev.set_hsv(*hsv, nowait=True)
                last_send_t = time.time()
                last_sent = hsv
                fails = 0
                backoff = 1.0
                if not self._connected:
                    self._connected = True
                    log.info("%s: %sconnected",
                             self.name, "re" if self._ever_ok else "")
                    self._ever_ok = True
            except Exception as exc:
                fails += 1
                self._mode_set = False
                self._connected = False
                last_sent = None
                if fails <= 2:
                    self._stop.wait(0.5)
                    continue
                log.warning("%s: offline (%s); rediscovering, retry in %.0fs",
                            self.name, exc, backoff)
                rediscovered = self._rediscover()
                self._stop.wait(0 if rediscovered else backoff)
                backoff = min(self.opts.backoff_max, backoff * 2)
                continue
            self._stop.wait(0.01)
