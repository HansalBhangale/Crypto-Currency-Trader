import pandas as pd

s = pd.read_csv('data/derived/baseline_signals.csv').tail(1)
p = pd.read_csv('data/derived/paper_portfolio.csv').tail(1)

print('=== SIGNAL ===')
print(f"  action: {s['action'].values[0]}")
print(f"  size_btc: {s['size_btc'].values[0]}")
print(f"  reason: {s['reason'].values[0]}")
print('')
print('=== PORTFOLIO ===')
print(f"  signal_action: {p['signal_action'].values[0]}")
print(f"  signal_size_btc: {p['signal_size_btc'].values[0]}")
print(f"  q_spot_btc: {p['q_spot_btc'].values[0]}")
print(f"  q_perp_btc: {p['q_perp_btc'].values[0]}")
print(f"  net_btc: {p['net_btc'].values[0]}")
print(f"  equity_after_usdt: {p['equity_after_usdt'].values[0]:.2f}")
