# Spot-bot-1

Termux-friendly Bybit spot trading bot.

## What this app does

- Demo-mode bot with local simulated funds
- Real Bybit spot mode using your Bybit API keys
- Analyzes 7-day 1-hour and recent 5-minute candles
- Uses buy-low, sell-high strategy with a minimum 5% profit target
- Tracks BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, FARTCOINUSDT
- Also includes the top 3 gainers from Bybit spot market
- Displays values in Philippine peso (PHP)

## Install

1. Install Python and pip on Termux:
   - `pkg install python`
   - `python3 -m pip install --upgrade pip`
2. Install dependencies:
   - `python3 -m pip install -r requirements.txt`

## Run

1. Start the bot:
   - `python3 spotbot.py`
2. Use the menu to:
   - view portfolio
   - run analysis and trading cycle
   - configure Bybit API keys
   - switch between demo, real, or testnet mode
   - enable automatic real order execution
   - edit strategy and watch symbols

## Notes

- Demo mode is the default safe mode. It does not send real orders.
- Real mode requires valid Bybit spot API keys.
- The bot uses 100% USDT wallet balance for buys and sells when signals occur.
- If a symbol is already held, the bot waits until the sell target is reached.

## Configuration

Edit `config.json` to customize:

- `mode`
- `use_real_spot`
- `api_key`
- `api_secret`
- `fiat_currency`
- `watch_symbols`
- `min_sell_profit_pct`
- `buy_buffer_pct`
- `gainers_count`
