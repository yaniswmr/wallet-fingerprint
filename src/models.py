from dataclasses import dataclass, asdict


@dataclass
class Tier:
    priority_fee: float  # Gwei
    max_fee: float       # Gwei


@dataclass
class GasFees:
    low:      Tier
    medium:   Tier
    high:     Tier
    base_fee: float  # Gwei (estimatedBaseFee)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResult:
    n_blocks:    int
    p_low:       int
    p_med:       int
    p_high:      int
    mean_mape:   float
    std_mape:    float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MultiplierResult:
    m_low:   float
    m_med:   float
    m_high:  float
    mape:    float

    def formula_str(self, priority_params: "SearchResult") -> str:
        p = priority_params
        lines = [
            f"priorityFee_low    = mean(tips[{p.p_low}th pct], last {p.n_blocks} blocks)",
            f"priorityFee_medium = mean(tips[{p.p_med}th pct], last {p.n_blocks} blocks)",
            f"priorityFee_high   = mean(tips[{p.p_high}th pct], last {p.n_blocks} blocks)",
            f"baseFee            = baseFeePerGas of latest block",
            f"maxFee_low         = baseFee × {self.m_low:.4f} + priorityFee_low",
            f"maxFee_medium      = baseFee × {self.m_med:.4f} + priorityFee_medium",
            f"maxFee_high        = baseFee × {self.m_high:.4f} + priorityFee_high",
        ]
        return "\n".join(lines)
