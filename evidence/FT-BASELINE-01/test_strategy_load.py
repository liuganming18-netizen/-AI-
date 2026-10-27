import sys
from pathlib import Path
from freqtrade.configuration import Configuration
from freqtrade.resolvers.strategy_resolver import StrategyResolver

config_path = Path("evidence/FT-BASELINE-01/user_data/config.json")
config = Configuration.from_files([str(config_path)])
config['strategy_path'] = 'evidence/FT-BASELINE-01/user_data/strategies'
config['strategy'] = 'WyckoffEventTrendBaseline'

try:
    strategy = StrategyResolver.load_strategy(config)
    print("SUCCESS: Loaded strategy:", strategy.get_strategy_name())
    print("Timeframe:", strategy.timeframe)
    print("Minimal ROI:", strategy.minimal_roi)
    print("Stoploss:", strategy.stoploss)
    print("Informative pairs:", strategy.informative_pairs())
except Exception as e:
    print("FAILED to load strategy:", e)
    import traceback
    traceback.print_exc()
