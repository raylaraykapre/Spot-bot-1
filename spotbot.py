#!/usr/bin/env python3
"""Bybit Spot Bot for Termux

This simple CLI app supports demo-mode trading and real Bybit spot trading.
It analyzes 7-day 1h and recent 5m candles to identify buy-low / sell-high levels.
"""

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta

CONFIG_PATH = "config.json"
STATE_PATH = "state.json"
DEFAULT_CONFIG = {
    "mode": "demo",
    "use_real_spot": False,
    "use_testnet": False,
    "api_key": "",
    "api_secret": "",
    "fiat_currency": "PHP",
    "base_currency": "USDT",
    "watch_symbols": [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "FARTCOINUSDT"
    ],
    "min_sell_profit_pct": 5.0,
    "buy_buffer_pct": 1.0,
    "gainers_count": 3,
    "demo_start_balance": 1000.0,
    "update_interval_seconds": 300,
    "auto_real_trading": false
}

DEFAULT_STATE = {
    "positions": {},
    "demo_balance": {
        "USDT": DEFAULT_CONFIG["demo_start_balance"]
    },
    "trade_history": [],
    "last_run": None
}

SUPPORTED_INTERVALS = {"1h": 168, "5m": 288}


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    with open(path, "w", encoding="utf-8") as f:
        json.dump(default, f, indent=2)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def ensure_config():
    config = load_json(CONFIG_PATH, DEFAULT_CONFIG.copy())
    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
            changed = True
    if changed:
        save_json(CONFIG_PATH, config)
    return config


def ensure_state(config):
    state = load_json(STATE_PATH, DEFAULT_STATE.copy())
    if "demo_balance" not in state:
        state["demo_balance"] = {"USDT": config["demo_start_balance"]}
    if "positions" not in state:
        state["positions"] = {}
    if "trade_history" not in state:
        state["trade_history"] = []
    if "last_run" not in state:
        state["last_run"] = None
    save_json(STATE_PATH, state)
    return state


class BybitSpotClient:
    def __init__(self, api_key="", api_secret="", testnet=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"

    def _sign(self, params):
        query_string = urllib.parse.urlencode(sorted(params.items()))
        return hmac.new(self.api_secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()

    def _execute(self, method, path, params=None):
        params = params or {}
        url = self.base_url + path
        try:
            if method == "GET":
                query_string = urllib.parse.urlencode(params)
                url_with_params = f"{url}?{query_string}" if query_string else url
                req = urllib.request.Request(url_with_params, method="GET")
            else:
                data = urllib.parse.urlencode(params).encode("utf-8")
                req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=20) as response:
                payload_text = response.read().decode("utf-8")
                payload = json.loads(payload_text)
            if not payload.get("ret_code") == 0 and payload.get("ret_code") is not None:
                raise RuntimeError(payload)
            if payload.get("ret_msg") and payload["ret_msg"].lower().startswith("invalid"):
                raise RuntimeError(payload)
            return payload.get("result", payload)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Bybit API network error: {exc}")
        except Exception as exc:
            raise RuntimeError(f"Bybit API error: {exc}")

    def public_get(self, path, params=None):
        return self._execute("GET", path, params)

    def private_get(self, path, params=None):
        params = params or {}
        params["api_key"] = self.api_key
        params["timestamp"] = int(time.time() * 1000)
        params["recv_window"] = 5000
        params["sign"] = self._sign(params)
        return self._execute("GET", path, params)

    def private_post(self, path, params=None):
        params = params or {}
        params["api_key"] = self.api_key
        params["timestamp"] = int(time.time() * 1000)
        params["recv_window"] = 5000
        params["sign"] = self._sign(params)
        return self._execute("POST", path, params)

    def get_klines(self, symbol, interval, limit):
        limit = min(limit, 200)
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        result = self.public_get("/spot/quote/v1/kline", params)
        if not isinstance(result, list):
            raise RuntimeError(f"Unexpected kline response: {result}")
        candles = []
        for item in result:
            candles.append({
                "open_time": int(item[0]) // 1000,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5])
            })
        return candles

    def get_tickers(self, symbol=None):
        params = {"symbol": symbol} if symbol else {}
        return self.public_get("/spot/quote/v1/ticker/24hr", params)

    def get_current_price(self, symbol):
        data = self.get_tickers(symbol)
        if isinstance(data, dict) and "lastPrice" in data:
            return float(data["lastPrice"])
        raise RuntimeError(f"Unable to read current price for {symbol}")

    def get_account(self):
        return self.private_get("/spot/v1/account")

    def get_asset_balance(self, asset):
        account = self.get_account()
        balances = account
        if isinstance(account, dict):
            for key in ("balances", "result", "rows", "data"):
                if key in account and isinstance(account[key], list):
                    balances = account[key]
                    break
        if isinstance(balances, list):
            for item in balances:
                if str(item.get("coin", item.get("symbol", ""))).upper() == asset.upper():
                    return float(item.get("free", item.get("available", item.get("balance", 0))))
        if isinstance(balances, dict):
            return float(balances.get(asset.upper(), 0))
        return 0.0

    def list_top_gainers(self, count):
        data = self.get_tickers()
        if not isinstance(data, list):
            raise RuntimeError("Unexpected ticker list format")
        gainers = []
        for item in data:
            try:
                change_pct = float(item.get("priceChangePercent", 0))
                gainers.append((item.get("symbol"), change_pct))
            except Exception:
                continue
        gainers.sort(key=lambda x: x[1], reverse=True)
        return [symbol for symbol, _ in gainers[:count]]

    def place_market_order(self, symbol, side, qty):
        return self.private_post(
            "/spot/v1/order",
            {
                "symbol": symbol,
                "side": side.upper(),
                "orderType": "MARKET",
                "qty": str(qty),
                "timeInForce": "GTC"
            }
        )

    def get_available_usdt(self):
        return self.get_asset_balance("USDT")


def safe_round(value, precision):
    return float(f"{value:.{precision}f}")


def parse_klines_for_levels(klines_1h, klines_5m, min_profit_pct, buy_buffer_pct):
    high_7d = max(c["high"] for c in klines_1h)
    low_7d = min(c["low"] for c in klines_1h)
    now_ts = int(time.time())
    yesterday_ts = now_ts - 86400
    recent_5m = [c for c in klines_5m if c["open_time"] >= yesterday_ts]
    if recent_5m:
        low_24h = min(c["low"] for c in recent_5m)
        high_24h = max(c["high"] for c in recent_5m)
    else:
        low_24h = min(c["low"] for c in klines_5m)
        high_24h = max(c["high"] for c in klines_5m)
    support_price = min(low_7d, low_24h)
    resistance_price = max(high_7d, high_24h)
    buy_threshold = safe_round(support_price * (1 + buy_buffer_pct / 100.0), 2)
    profit_floor = buy_threshold * (1 + min_profit_pct / 100.0)
    sell_target = safe_round(max(resistance_price, profit_floor), 2)
    return {
        "low_7d": low_7d,
        "high_7d": high_7d,
        "low_24h": low_24h,
        "high_24h": high_24h,
        "buy_threshold": buy_threshold,
        "sell_target": sell_target
    }


def fetch_php_rate():
    try:
        url = "https://api.exchangerate.host/latest?base=USD&symbols=PHP"
        with urllib.request.urlopen(url, timeout=15) as response:
            data_text = response.read().decode("utf-8")
            data = json.loads(data_text)
        rate = data.get("rates", {}).get("PHP")
        if rate:
            return float(rate)
    except Exception:
        pass
    return 56.0


def print_portfolio(config, state, client=None):
    usd_to_php = fetch_php_rate()
    usd_balance = state["demo_balance"].get(config["base_currency"], 0.0)
    asset_value_php = 0.0
    if client and config["use_real_spot"]:
        try:
            usd_balance = client.get_available_usdt()
        except Exception:
            pass
    if client:
        for symbol, position in state["positions"].items():
            try:
                price = client.get_current_price(symbol)
                asset_value_php += position["qty"] * price * usd_to_php
            except Exception:
                pass
    print("\n=== Portfolio Overview ===")
    print(f"Mode: {config['mode']} ({'Real' if config['use_real_spot'] else 'Demo'})")
    print(f"USDT balance: {usd_balance:.2f}")
    print(f"Estimated USDT value in {config['fiat_currency']}: {usd_balance * usd_to_php:.2f}")
    print(f"Estimated open positions in {config['fiat_currency']}: {asset_value_php:.2f}")
    if state["positions"]:
        print("Open positions:")
        for symbol, position in state["positions"].items():
            print(f"  {symbol}: qty={position['qty']:.6f}, buy={position['buy_price']:.2f}, target={position['sell_target']:.2f}")
    else:
        print("No open positions.")
    print("==========================\n")


def build_symbol_list(config, client):
    symbols = list(dict.fromkeys(config["watch_symbols"]))
    if config["gainers_count"] > 0:
        try:
            gainers = client.list_top_gainers(config["gainers_count"])
            for symbol in gainers:
                if symbol not in symbols:
                    symbols.append(symbol)
        except Exception:
            pass
    return symbols


def analyze_symbol(symbol, client, config):
    klines_1h = client.get_klines(symbol, "1h", SUPPORTED_INTERVALS["1h"])
    klines_5m = client.get_klines(symbol, "5m", SUPPORTED_INTERVALS["5m"])
    levels = parse_klines_for_levels(klines_1h, klines_5m, config["min_sell_profit_pct"], config["buy_buffer_pct"])
    current_price = client.get_current_price(symbol)
    return {**levels, "current_price": current_price}


def build_real_position(symbol, qty, buy_price, sell_target, config, order_id=None):
    return {
        "qty": qty,
        "buy_price": buy_price,
        "sell_target": sell_target,
        "order_id": order_id,
        "mode": config["mode"],
        "timestamp": int(time.time())
    }


def real_market_buy(client, state, symbol, price, config):
    available_usdt = client.get_available_usdt()
    if available_usdt <= 0:
        raise RuntimeError("No USDT available for real buy.")
    qty = safe_round(available_usdt / price, 6)
    if qty <= 0:
        raise RuntimeError("Insufficient USDT balance for any lot size.")
    order = client.place_market_order(symbol, "BUY", qty)
    result = order if isinstance(order, dict) else {}
    executed_qty = float(result.get("qty", qty))
    avg_price = float(result.get("price", price)) if result.get("price") else price
    sell_target = max(avg_price * (1 + config["min_sell_profit_pct"] / 100.0), avg_price * (1 + config["buy_buffer_pct"] / 100.0))
    position = build_real_position(symbol, executed_qty, avg_price, sell_target, config, order_id=result.get("orderId"))
    state["positions"][symbol] = position
    state["trade_history"].append({
        "symbol": symbol,
        "side": "BUY",
        "price": avg_price,
        "qty": executed_qty,
        "mode": config["mode"],
        "timestamp": int(time.time()),
        "demo": False,
        "order": result
    })
    return position


def real_market_sell(client, state, symbol, price, config):
    position = state["positions"].get(symbol)
    if not position:
        raise RuntimeError("No tracked position available for sell.")
    qty = position["qty"]
    order = client.place_market_order(symbol, "SELL", qty)
    result = order if isinstance(order, dict) else {}
    executed_qty = float(result.get("qty", qty))
    proceeds = executed_qty * price
    state["trade_history"].append({
        "symbol": symbol,
        "side": "SELL",
        "price": price,
        "qty": executed_qty,
        "mode": config.get("mode", "real"),
        "timestamp": int(time.time()),
        "demo": False,
        "order": result
    })
    del state["positions"][symbol]
    return proceeds, result


def market_buy(state, symbol, price, config):
    usdt_balance = state["demo_balance"].get("USDT", 0.0)
    if usdt_balance <= 0:
        return None
    qty = safe_round(usdt_balance / price, 6)
    if qty <= 0:
        return None
    cost = qty * price
    state["demo_balance"]["USDT"] = max(0.0, usdt_balance - cost)
    position = {
        "qty": qty,
        "buy_price": price,
        "sell_target": max(price * (1 + config["min_sell_profit_pct"] / 100.0), price * (1 + config["buy_buffer_pct"] / 100.0)),
        "mode": config["mode"],
        "timestamp": int(time.time())
    }
    state["positions"][symbol] = position
    state["trade_history"].append({
        "symbol": symbol,
        "side": "BUY",
        "price": price,
        "qty": qty,
        "mode": config["mode"],
        "timestamp": int(time.time()),
        "demo": True
    })
    return position


def market_sell(state, symbol, price):
    position = state["positions"].get(symbol)
    if not position:
        return None
    qty = position["qty"]
    proceeds = qty * price
    state["demo_balance"]["USDT"] = state["demo_balance"].get("USDT", 0.0) + proceeds
    state["trade_history"].append({
        "symbol": symbol,
        "side": "SELL",
        "price": price,
        "qty": qty,
        "mode": "demo",
        "timestamp": int(time.time()),
        "demo": True
    })
    del state["positions"][symbol]
    return proceeds


def run_cycle(config, state, client):
    symbols = build_symbol_list(config, client)
    print(f"\nScanning {len(symbols)} symbols: {', '.join(symbols)}")
    for symbol in symbols:
        try:
            analysis = analyze_symbol(symbol, client, config)
        except Exception as exc:
            print(f"Skipping {symbol}: {exc}")
            continue
        current = analysis["current_price"]
        buy_threshold = analysis["buy_threshold"]
        sell_target = analysis["sell_target"]
        has_position = symbol in state["positions"]
        position = state["positions"].get(symbol)
        if has_position:
            print(f"{symbol}: holding qty={position['qty']:.6f}, buy={position['buy_price']:.2f}, target={position['sell_target']:.2f}, current={current:.2f}")
            if current >= position["sell_target"]:
                if config["use_real_spot"]:
                    try:
                        proceeds, order = real_market_sell(client, state, symbol, current, config)
                        print(f"REAL SOLD {symbol} qty={position['qty']:.6f} at {current:.2f} for {proceeds:.2f} USDT")
                    except Exception as exc:
                        print(f"Failed to execute real sell for {symbol}: {exc}")
                else:
                    proceeds = market_sell(state, symbol, current)
                    print(f"SOLD {symbol} at {current:.2f} for {proceeds:.2f} USDT")
            else:
                print(f"  waiting to sell when price reaches {position['sell_target']:.2f}")
        else:
            print(f"{symbol}: current={current:.2f}, buy<= {buy_threshold:.2f}, sell target={sell_target:.2f}")
            if current <= buy_threshold:
                if config["use_real_spot"]:
                    if config.get("auto_real_trading", False):
                        try:
                            position = real_market_buy(client, state, symbol, current, config)
                            print(f"REAL BOUGHT {symbol} qty={position['qty']:.6f} at price={position['buy_price']:.2f}")
                        except Exception as exc:
                            print(f"Failed real buy for {symbol}: {exc}")
                    else:
                        print(f"Signal to BUY {symbol} at {current:.2f} (target {sell_target:.2f}) but auto-real trading is disabled.")
                else:
                    position = market_buy(state, symbol, current, config)
                    if position:
                        print(f"BOUGHT {symbol} qty={position['qty']:.6f} at price={current:.2f}")
            else:
                print(f"  no buy signal. support ~{buy_threshold:.2f}, current {current:.2f}")
    state["last_run"] = datetime.utcnow().isoformat() + "Z"
    save_json(STATE_PATH, state)
    print("\nCycle complete. State saved.")


def configure_api(config):
    print("\n=== Configure Bybit API Keys ===")
    config["api_key"] = input("API key: ").strip()
    config["api_secret"] = input("API secret: ").strip()
    save_json(CONFIG_PATH, config)
    print("Saved API key configuration.")


def configure_mode(config):
    print("\n=== Trading Mode ===")
    print("1. Demo mode (simulated local trading)")
    print("2. Real Bybit spot mode")
    print("3. Real Bybit testnet mode")
    choice = input("Choose mode [1/2/3]: ").strip()
    if choice == "2":
        config["mode"] = "real"
        config["use_real_spot"] = True
        config["use_testnet"] = False
    elif choice == "3":
        config["mode"] = "real"
        config["use_real_spot"] = True
        config["use_testnet"] = True
    else:
        config["mode"] = "demo"
        config["use_real_spot"] = False
        config["use_testnet"] = False
    if config["use_real_spot"]:
        confirm = input(f"Enable automatic real order execution? [y/N]: ").strip().lower()
        config["auto_real_trading"] = confirm == "y"
    else:
        config["auto_real_trading"] = False
    save_json(CONFIG_PATH, config)
    print(f"Mode set to: {config['mode']} (testnet={config['use_testnet']}, auto_real_trading={config['auto_real_trading']})")


def edit_strategy(config):
    print("\n=== Strategy Preferences ===")
    try:
        profit = float(input(f"Min sell profit % [{config['min_sell_profit_pct']}]: ").strip() or config["min_sell_profit_pct"])
        buffer = float(input(f"Buy buffer % above support [{config['buy_buffer_pct']}]: ").strip() or config["buy_buffer_pct"])
        gainers = int(input(f"Top gainers to include [{config['gainers_count']}]: ").strip() or config["gainers_count"])
        config["min_sell_profit_pct"] = max(1.0, profit)
        config["buy_buffer_pct"] = max(0.1, buffer)
        config["gainers_count"] = max(0, gainers)
    except ValueError:
        print("Invalid input, preferences unchanged.")
        return
    save_json(CONFIG_PATH, config)
    print("Strategy preferences updated.")


def edit_pairs(config):
    print("\n=== Watch Symbols ===")
    print("Current symbols:")
    for symbol in config["watch_symbols"]:
        print(f"  - {symbol}")
    value = input("Enter comma-separated symbols to watch, or blank to keep: ").strip()
    if not value:
        return
    symbols = [sym.strip().upper() for sym in value.split(",") if sym.strip()]
    if symbols:
        config["watch_symbols"] = symbols
        save_json(CONFIG_PATH, config)
        print("Watch symbols updated.")


def main_menu():
    config = ensure_config()
    state = ensure_state(config)
    client = BybitSpotClient(config.get("api_key", ""), config.get("api_secret", ""), config.get("use_testnet", False))

    while True:
        print("\n=== Spot Bot Menu ===")
        print("1. View portfolio")
        print("2. Run analysis and trading cycle")
        print("3. Configure API keys")
        print("4. Set trading mode")
        print("5. Edit strategy preferences")
        print("6. Edit watch symbols")
        print("7. Show symbol analysis")
        print("8. Exit")
        choice = input("Choose an action: ").strip()
        if choice == "1":
            print_portfolio(config, state, client if config["use_real_spot"] else None)
        elif choice == "2":
            if config["use_real_spot"] and not config["api_key"]:
                print("Real mode requires API keys. Configure them first.")
            else:
                run_cycle(config, state, client)
                state = ensure_state(config)
        elif choice == "3":
            configure_api(config)
            client = BybitSpotClient(config.get("api_key", ""), config.get("api_secret", ""), config.get("use_testnet", False))
        elif choice == "4":
            configure_mode(config)
            client = BybitSpotClient(config.get("api_key", ""), config.get("api_secret", ""), config.get("use_testnet", False))
        elif choice == "5":
            edit_strategy(config)
        elif choice == "6":
            edit_pairs(config)
        elif choice == "7":
            symbols = build_symbol_list(config, client)
            for symbol in symbols:
                try:
                    analysis = analyze_symbol(symbol, client, config)
                except Exception as exc:
                    print(f"{symbol}: {exc}")
                    continue
                print(f"{symbol}: current={analysis['current_price']:.2f}, buy<= {analysis['buy_threshold']:.2f}, sell target={analysis['sell_target']:.2f}")
        elif choice == "8":
            print("Goodbye.")
            break
        else:
            print("Invalid choice. Enter 1-8.")


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\nInterrupted. Exiting.")
