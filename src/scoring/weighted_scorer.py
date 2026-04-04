import yaml
import numpy as np


class WeightedScorer:
    """Combine all indicator scores using configurable weights into a composite signal."""

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path) as f:
            config = yaml.safe_load(f)
        self.weights = config["weights"]
        self.thresholds = config["thresholds"]

    def compute_composite(
        self,
        technical_scores: dict[str, float],
        fundamental_scores: dict[str, float],
        macro_scores: dict[str, float],
    ) -> dict:
        weighted_sum = 0.0
        total_weight = 0.0
        breakdown = {}

        for category, scores in [
            ("technical", technical_scores),
            ("fundamental", fundamental_scores),
            ("macro", macro_scores),
        ]:
            cat_weights = self.weights.get(category, {})
            for signal_name, score in scores.items():
                weight = cat_weights.get(signal_name, 0.0)
                weighted_sum += score * weight
                total_weight += weight
                breakdown[f"{category}.{signal_name}"] = {
                    "score": round(score, 3),
                    "weight": weight,
                    "contribution": round(score * weight, 4),
                }

        composite = weighted_sum / total_weight if total_weight > 0 else 0.5

        return {
            "composite_score": round(composite, 4),
            "signal": self._classify(composite),
            "confidence": self._confidence(composite),
            "breakdown": breakdown,
        }

    def _classify(self, score: float) -> str:
        t = self.thresholds
        if score >= t["strong_buy"]:
            return "STRONG BUY"
        elif score >= t["buy"]:
            return "BUY"
        elif score >= t["hold_lower"]:
            return "HOLD"
        elif score >= t["strong_sell"]:
            return "SELL"
        else:
            return "STRONG SELL"

    def _confidence(self, score: float) -> str:
        distance = abs(score - 0.5)
        if distance > 0.25:
            return "HIGH"
        elif distance > 0.1:
            return "MEDIUM"
        else:
            return "LOW"
