import unittest
from unittest.mock import patch
import pandas as pd
from src.backtester.engine import BacktestEngine
class TimingTests(unittest.TestCase):
 def test_next_open_and_last_day_cost(self):
  df=pd.DataFrame({"Open":[10.]*200+[20.],"Close":[10.]*201},index=pd.date_range('2020-01-01',periods=201))
  class Analyzer:
   def __init__(self,frame):self.df=frame
   def get_all_scores(self):return {"test":1.}
  engine=BacktestEngine(initial_capital=100,commission_pct=.1,slippage_pct=0)
  engine.fetcher.get_stock_history_range=lambda *a:df
  with patch('src.backtester.engine.TechnicalAnalyzer',Analyzer):
   result=engine.run('TEST',{"weights":{"technical":{"test":1}},"thresholds":{"buy":.8,"sell":.2}})
  self.assertEqual(result['trades'][0]['price'],20)
  self.assertEqual(result['final_value'],52)
  self.assertEqual(result['max_drawdown_pct'],-48)
 def test_warmup_requires_execution_row(self):
  engine=BacktestEngine();engine.fetcher.get_stock_history_range=lambda *a:pd.DataFrame({'Close':[1]*200})
  self.assertIn('Insufficient data',engine.run('TEST',{})['error'])
