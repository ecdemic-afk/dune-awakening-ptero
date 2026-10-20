"""Item pictures for the web panel, from the awakening.wiki community wiki.

The server looks each item up once (public API, no key), downloads the picture
and keeps it in server/state/item-images/, so the browser only ever talks to
the panel: no third-party requests from the operator's browser, and it keeps
working when the wiki is slow. Items the wiki doesn't know are remembered for a
week so they aren't asked for on every page view.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request

API = "https://api.awakening.wiki"
MEDIA = "https://media.awakening.wiki/wiki"
# Wiki tables to try, the likeliest first for each of our categories.
WIKI_TABLES = ["items", "weapons", "garments", "consumables", "resources", "tools", "ammo", "contract_items"]
FIRST = {"weapons": "weapons", "clothing": "garments", "consumables": "consumables", "resources": "resources"}
# Whole-vehicle pictures (the wiki's vehicle pages); "Vehicle_<id>" in the API.
VEHICLE_FILES = {
    "Vehicle_Sandbike": "T_UI_IconVehCHSandBikeR_D.png", "Vehicle_Buggy": "T_UI_IconVehCHBuggyR_D.png",
    "Vehicle_Tank": "T_UI_IconVehCHTankR_D.png", "Vehicle_Sandcrawler": "T_UI_IconVehCHSandCrawlerR_D.png",
    "Vehicle_OrnithopterLight": "T_UI_IconVehCHOrniLightR_D.png",
    "Vehicle_OrnithopterMedium": "T_UI_IconVehCHOrniMediumR_D.png",
    "Vehicle_OrnithopterTransport": "T_UI_IconVehCHOrniTransportR_D.png",
    "Vehicle_TreadWheel": "T_UI_IconVehOETreadwheelR_D.png",
}
ID_RE = re.compile(r"^[A-Za-z0-9_]{1,120}$")
MAX_BYTES = 2 << 20
MISS_SECS = 7 * 86400
MAGIC = [(b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"RIFF", "image/webp"), (b"GIF8", "image/gif")]


def wiki_file_url(filename: str) -> str:
    key = filename.replace(" ", "_")
    key = key[:1].upper() + key[1:]
    h = hashlib.md5(key.encode()).hexdigest()  # MediaWiki's storage path, not security
    return f"{MEDIA}/{h[0]}/{h[:2]}/{urllib.parse.quote(key)}"


def image_type(data: bytes) -> str | None:
    return next((t for sig, t in MAGIC if data.startswith(sig)), None)


def _get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "dune-awakening-ptero (self-hosted server panel)"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read(MAX_BYTES + 1)


class ItemImages:
    def __init__(self, base: str, category_of=lambda item_id: "", fetch=_get):
        self.dir = os.path.join(base, "server", "state", "item-images")
        self.category_of, self.fetch = category_of, fetch
        self._gate = threading.BoundedSemaphore(4)  # be polite to the wiki
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._pending: set[str] = set()
        self._workers = 0

    def lookup(self, item_id: str):
        """For the web server, never blocking: (data, type), None when there is no picture,
        or "pending" while it is fetched in the background (the page asks again shortly).
        A slow wiki must not hold the browser's few connections and stall the panel's buttons."""
        if not ID_RE.match(item_id or ""):
            return None
        cached = self._read(os.path.join(self.dir, item_id + ".img"))
        if cached:
            return cached
        try:
            if time.time() - os.path.getmtime(os.path.join(self.dir, item_id + ".none")) < MISS_SECS:
                return None
        except OSError:
            pass
        with self._locks_lock:
            if item_id not in self._pending:
                self._pending.add(item_id)
                self._queue.put(item_id)
            while self._workers < 4:
                self._workers += 1
                threading.Thread(target=self._work, daemon=True, name="item-images").start()
        return "pending"

    def _work(self) -> None:
        while True:
            item_id = self._queue.get()
            try:
                self.get(item_id)
            except Exception:  # a broken download must not kill the worker
                pass
            finally:
                with self._locks_lock:
                    self._pending.discard(item_id)

    def _lock(self, item_id: str) -> threading.Lock:
        with self._locks_lock:
            return self._locks.setdefault(item_id, threading.Lock())

    def get(self, item_id: str) -> tuple[bytes, str] | None:
        if not ID_RE.match(item_id or ""):
            return None
        path, miss = os.path.join(self.dir, item_id + ".img"), os.path.join(self.dir, item_id + ".none")
        with self._lock(item_id):  # one download per item even when the page asks 40 times at once
            cached = self._read(path)
            if cached:
                return cached
            try:
                if time.time() - os.path.getmtime(miss) < MISS_SECS:
                    return None
            except OSError:
                pass
            with self._gate:
                data, sure = self._download(item_id)
            os.makedirs(self.dir, exist_ok=True)
            if data is None:
                if sure:  # the wiki answered and has no picture; a network error is retried next time
                    open(miss, "w").close()
                return None
            with open(path + ".tmp", "wb") as fh:
                fh.write(data)
            os.replace(path + ".tmp", path)
            return data, image_type(data)

    def _read(self, path: str):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return None
        kind = image_type(data)
        return (data, kind) if kind else None

    def _download(self, item_id: str) -> tuple[bytes | None, bool]:
        """(picture or None, whether a None is certain)."""
        if item_id in VEHICLE_FILES:
            try:
                data = self.fetch(wiki_file_url(VEHICLE_FILES[item_id]))
            except OSError:
                return None, False
            return (data, True) if len(data) <= MAX_BYTES and image_type(data) else (None, True)
        if item_id.startswith("Vehicle_"):  # no vehicle picture: use the catalogue item (cargo container)
            item_id = item_id[len("Vehicle_"):]
        first = FIRST.get(self.category_of(item_id), "items")
        sure = True
        for table in [first] + [t for t in WIKI_TABLES if t != first]:
            where = urllib.parse.quote(f"(item_id,eq,{item_id})", safe="(),")
            try:
                rows = json.loads(self.fetch(f"{API}/{table}?where={where}&limit=1")).get("list") or []
            except (OSError, ValueError):
                sure = False
                continue
            image = rows[0].get("image") if rows else None
            if not image:
                continue
            try:
                data = self.fetch(wiki_file_url(str(image)))
            except OSError:
                return None, False
            ok = len(data) <= MAX_BYTES and image_type(data)
            return (data, True) if ok else (None, True)
        return None, sure
