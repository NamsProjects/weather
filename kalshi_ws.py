"""
kalshi_ws.py
============
Kalshi WebSocket client — connects once per server lifetime, fans live
ticker updates to SSE subscribers via per-client queues.

Auth: RSA-signed headers passed during the HTTP upgrade handshake
(api.elections.kalshi.com style — no post-connect login message).

Requires:
    pip install websocket-client cryptography
"""

import base64
import json
import queue
import threading
import time
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
import websocket

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"


class KalshiWSClient:
    def __init__(self, key_id: str, private_key_pem: str):
        self.key_id = key_id
        pem_bytes = private_key_pem.encode() if isinstance(private_key_pem, str) else private_key_pem
        self._private_key = serialization.load_pem_private_key(pem_bytes, password=None)

        self._ws = None
        self._lock = threading.Lock()
        self._msg_id = 0

        # _wanted_tickers persists across reconnects; _subscribed_tickers resets on disconnect
        self._wanted_tickers: set = set()
        self._subscribed_tickers: set = set()
        self._market_cache: dict = {}
        self._subscribers: list = []

        self._stop = False
        self._retry_delay = 5

    # ── Auth headers (passed at WS handshake time) ────────────────────────────

    def _make_headers(self) -> dict:
        ts = int(time.time() * 1000)
        msg = f"{ts}GET{WS_PATH}"
        sig = self._private_key.sign(
            msg.encode(),
            asym_padding.PSS(mgf=asym_padding.MGF1(hashes.SHA256()), salt_length=asym_padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY":       self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(ts),
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    # ── Send ──────────────────────────────────────────────────────────────────

    def _send(self, payload: dict):
        try:
            self._ws.send(json.dumps(payload))
        except Exception as exc:
            print(f"[KalshiWS] send error: {exc}", flush=True)

    # ── WS callbacks ─────────────────────────────────────────────────────────

    def _on_open(self, ws):
        self._retry_delay = 5
        print("[KalshiWS] connected and authenticated", flush=True)
        # Auth was in the handshake — subscribe immediately
        with self._lock:
            to_sub = list(self._wanted_tickers - self._subscribed_tickers)
        if to_sub:
            self._do_subscribe(to_sub)

    def _on_message(self, ws, raw: str):
        try:
            msg = json.loads(raw)
        except Exception:
            return

        msg_type = msg.get("type", "")

        if msg_type == "subscribed":
            tickers = msg.get("msg", {}).get("market_tickers", [])
            with self._lock:
                self._subscribed_tickers.update(tickers)
            print(f"[KalshiWS] subscribed to {len(tickers)} tickers", flush=True)

        elif msg_type == "ticker":
            ticker_data = msg.get("msg", {})
            ticker = ticker_data.get("market_ticker")
            if ticker:
                with self._lock:
                    self._market_cache[ticker] = ticker_data
                    subs = list(self._subscribers)
                self._fan_out(ticker, ticker_data, subs)

        elif msg_type == "error":
            print(f"[KalshiWS] server error: {msg}", flush=True)

    def _on_error(self, ws, error):
        print(f"[KalshiWS] error: {error}", flush=True)

    def _on_close(self, ws, code, msg):
        with self._lock:
            self._subscribed_tickers.clear()
        print(f"[KalshiWS] closed (code={code})", flush=True)
        if not self._stop:
            print(f"[KalshiWS] reconnecting in {self._retry_delay}s…", flush=True)
            time.sleep(self._retry_delay)
            self._retry_delay = min(self._retry_delay * 2, 60)
            self._connect_internal()

    # ── Fan-out ───────────────────────────────────────────────────────────────

    def _fan_out(self, ticker: str, data: dict, subs: list):
        dead = []
        for q in subs:
            try:
                q.put_nowait({"ticker": ticker, "data": data})
            except queue.Full:
                dead.append(q)
        if dead:
            with self._lock:
                for q in dead:
                    try:
                        self._subscribers.remove(q)
                    except ValueError:
                        pass

    # ── Subscription ──────────────────────────────────────────────────────────

    def _do_subscribe(self, tickers: list):
        self._send({
            "id": self._next_id(),
            "cmd": "subscribe",
            "params": {"channels": ["ticker"], "market_tickers": tickers},
        })

    def subscribe_tickers(self, tickers: list):
        with self._lock:
            new = [t for t in tickers if t not in self._wanted_tickers]
            if not new:
                return
            self._wanted_tickers.update(new)
        # _on_open handles subscription if we're not connected yet;
        # if already open, subscribe now
        if self._ws and self._ws.sock and self._ws.sock.connected:
            self._do_subscribe(new)

    # ── SSE subscriber management ─────────────────────────────────────────────

    def add_subscriber(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subscribers.append(q)
        return q

    def remove_subscriber(self, q: queue.Queue):
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def get_snapshot(self, tickers: list) -> list:
        with self._lock:
            return [self._market_cache[t] for t in tickers if t in self._market_cache]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _connect_internal(self):
        headers = self._make_headers()
        self._ws = websocket.WebSocketApp(
            WS_URL,
            header=headers,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    def start(self):
        self._stop = False
        threading.Thread(target=self._connect_internal, daemon=True).start()

    def stop(self):
        self._stop = True
        if self._ws:
            self._ws.close()


# ── Singleton ─────────────────────────────────────────────────────────────────

_client: "KalshiWSClient | None" = None


def init_ws_client(key_id: str, private_key_pem: str) -> KalshiWSClient:
    global _client
    _client = KalshiWSClient(key_id, private_key_pem)
    _client.start()
    return _client


def get_ws_client() -> "KalshiWSClient | None":
    return _client
