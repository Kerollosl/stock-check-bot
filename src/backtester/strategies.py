"""Predefined trading strategies with different weight configurations for backtesting."""


STRATEGIES = {
    "momentum": {
        "name": "Momentum Hunter",
        "description": "Heavily weights trend-following signals (MACD, cross, market trend)",
        "weights": {
            "technical": {
                "macd": 0.20,
                "golden_death_cross": 0.15,
                "rsi": 0.05,
                "bollinger": 0.05,
                "volume_trend": 0.10,
            },
            "fundamental": {
                "earnings_surprise": 0.05,
                "pe_ratio": 0.05,
                "revenue_growth": 0.05,
                "earnings_growth": 0.05,
            },
            "macro": {
                "fed_funds_rate": 0.05,
                "yield_curve": 0.05,
                "market_trend": 0.10,
                "vix": 0.03,
                "sector_rotation": 0.02,
            },
        },
        "thresholds": {
            "strong_buy": 0.72,
            "buy": 0.58,
            "hold_upper": 0.58,
            "hold_lower": 0.42,
            "sell": 0.42,
            "strong_sell": 0.28,
        },
    },
    "value": {
        "name": "Value Investor",
        "description": "Prioritizes fundamentals: earnings, P/E, growth metrics",
        "weights": {
            "technical": {
                "macd": 0.05,
                "golden_death_cross": 0.05,
                "rsi": 0.08,
                "bollinger": 0.02,
                "volume_trend": 0.02,
            },
            "fundamental": {
                "earnings_surprise": 0.18,
                "pe_ratio": 0.15,
                "revenue_growth": 0.12,
                "earnings_growth": 0.12,
            },
            "macro": {
                "fed_funds_rate": 0.06,
                "yield_curve": 0.05,
                "market_trend": 0.04,
                "vix": 0.03,
                "sector_rotation": 0.03,
            },
        },
        "thresholds": {
            "strong_buy": 0.68,
            "buy": 0.55,
            "hold_upper": 0.55,
            "hold_lower": 0.45,
            "sell": 0.45,
            "strong_sell": 0.32,
        },
    },
    "macro_driven": {
        "name": "Macro Driven",
        "description": "Lets macro conditions (rates, yield curve, VIX) dominate decisions",
        "weights": {
            "technical": {
                "macd": 0.07,
                "golden_death_cross": 0.05,
                "rsi": 0.05,
                "bollinger": 0.03,
                "volume_trend": 0.03,
            },
            "fundamental": {
                "earnings_surprise": 0.07,
                "pe_ratio": 0.05,
                "revenue_growth": 0.03,
                "earnings_growth": 0.03,
            },
            "macro": {
                "fed_funds_rate": 0.15,
                "yield_curve": 0.15,
                "market_trend": 0.12,
                "vix": 0.10,
                "sector_rotation": 0.07,
            },
        },
        "thresholds": {
            "strong_buy": 0.70,
            "buy": 0.56,
            "hold_upper": 0.56,
            "hold_lower": 0.44,
            "sell": 0.44,
            "strong_sell": 0.30,
        },
    },
    "balanced": {
        "name": "Balanced Blend",
        "description": "Equal emphasis across technical, fundamental, and macro signals",
        "weights": {
            "technical": {
                "macd": 0.08,
                "golden_death_cross": 0.08,
                "rsi": 0.06,
                "bollinger": 0.05,
                "volume_trend": 0.04,
            },
            "fundamental": {
                "earnings_surprise": 0.10,
                "pe_ratio": 0.08,
                "revenue_growth": 0.06,
                "earnings_growth": 0.06,
            },
            "macro": {
                "fed_funds_rate": 0.08,
                "yield_curve": 0.08,
                "market_trend": 0.08,
                "vix": 0.08,
                "sector_rotation": 0.07,
            },
        },
        "thresholds": {
            "strong_buy": 0.70,
            "buy": 0.55,
            "hold_upper": 0.55,
            "hold_lower": 0.45,
            "sell": 0.45,
            "strong_sell": 0.30,
        },
    },
    "contrarian": {
        "name": "Contrarian Dip Buyer",
        "description": "Buys fear and sells greed — heavy RSI, VIX, and Bollinger weight",
        "weights": {
            "technical": {
                "macd": 0.05,
                "golden_death_cross": 0.03,
                "rsi": 0.18,
                "bollinger": 0.15,
                "volume_trend": 0.05,
            },
            "fundamental": {
                "earnings_surprise": 0.07,
                "pe_ratio": 0.07,
                "revenue_growth": 0.03,
                "earnings_growth": 0.03,
            },
            "macro": {
                "fed_funds_rate": 0.05,
                "yield_curve": 0.05,
                "market_trend": 0.05,
                "vix": 0.14,
                "sector_rotation": 0.05,
            },
        },
        "thresholds": {
            "strong_buy": 0.68,
            "buy": 0.55,
            "hold_upper": 0.55,
            "hold_lower": 0.45,
            "sell": 0.45,
            "strong_sell": 0.32,
        },
    },
}
