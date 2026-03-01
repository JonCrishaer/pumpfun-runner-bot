"""
Pattern Recognition Filters for Pump.fun Trading Signals

Distinguishes between healthy and concerning patterns across:
- Volume signatures
- Holder dynamics
- Trading behavior
- Price action
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Set, Tuple
from collections import defaultdict
import numpy as np
import pandas as pd
from scipy import stats


class PatternHealth(Enum):
    """Health classification for patterns."""
    HEALTHY = "healthy"
    CONCERNING = "concerning"
    NEUTRAL = "neutral"
    SUSPICIOUS = "suspicious"


class PatternType(Enum):
    """Types of patterns to detect."""
    VOLUME_SIGNATURE = "volume_signature"
    HOLDER_DYNAMICS = "holder_dynamics"
    TRADING_BEHAVIOR = "trading_behavior"
    PRICE_ACTION = "price_action"
    WALLET_DISTRIBUTION = "wallet_distribution"


@dataclass
class PatternMatch:
    """A detected pattern with confidence."""
    pattern_type: PatternType
    health: PatternHealth
    name: str
    confidence: float  # 0-1
    description: str
    indicators: Dict[str, any]
    severity: str  # "low", "medium", "high"


@dataclass
class FilterResult:
    """Result from applying all filters."""
    token_address: str
    overall_health: PatternHealth
    health_score: float  # 0-100
    patterns: List[PatternMatch]
    warnings: List[str]
    red_flags: List[str]
    is_tradeable: bool
    timestamp: pd.Timestamp


class VolumePatternFilter:
    """Filter for volume signature patterns."""
    
    def analyze(
        self,
        volume_series: pd.Series,
        buy_volume: pd.Series,
        sell_volume: pd.Series,
        timestamps: pd.Series
    ) -> List[PatternMatch]:
        """
        Analyze volume patterns.
        
        Args:
            volume_series: Total volume over time
            buy_volume: Buy volume over time
            sell_volume: Sell volume over time
            timestamps: Corresponding timestamps
            
        Returns:
            List of detected patterns
        """
        patterns = []
        
        # Check for sustained acceleration
        accel_pattern = self._check_acceleration(volume_series, timestamps)
        if accel_pattern:
            patterns.append(accel_pattern)
        
        # Check for buyer dominance
        dominance_pattern = self._check_buyer_dominance(
            buy_volume, sell_volume
        )
        if dominance_pattern:
            patterns.append(dominance_pattern)
        
        # Check for volume decline
        decline_pattern = self._check_volume_decline(volume_series, timestamps)
        if decline_pattern:
            patterns.append(decline_pattern)
        
        # Check for extreme concentration
        concentration_pattern = self._check_volume_concentration(volume_series)
        if concentration_pattern:
            patterns.append(concentration_pattern)
        
        return patterns
    
    def _check_acceleration(
        self,
        volume_series: pd.Series,
        timestamps: pd.Series
    ) -> Optional[PatternMatch]:
        """Check for sustained volume acceleration."""
        if len(volume_series) < 6:
            return None
        
        # Calculate hourly changes
        df = pd.DataFrame({
            'volume': volume_series,
            'timestamp': pd.to_datetime(timestamps)
        })
        df = df.sort_values('timestamp')
        df['hour'] = df['timestamp'].dt.floor('H')
        hourly = df.groupby('hour')['volume'].sum()
        
        if len(hourly) < 3:
            return None
        
        # Fit trend
        x = np.arange(len(hourly))
        slope, _, r_value, _, _ = stats.linregress(x, hourly.values)
        
        # Check for acceleration
        if slope > 0 and r_value > 0.5:
            return PatternMatch(
                pattern_type=PatternType.VOLUME_SIGNATURE,
                health=PatternHealth.HEALTHY,
                name="sustained_acceleration",
                confidence=min(r_value, 0.95),
                description="Volume showing sustained upward trend",
                indicators={
                    'slope': slope,
                    'r_squared': r_value ** 2,
                    'trend_direction': 'up'
                },
                severity="low"
            )
        
        return None
    
    def _check_buyer_dominance(
        self,
        buy_volume: pd.Series,
        sell_volume: pd.Series
    ) -> Optional[PatternMatch]:
        """Check for buyer dominance (>2:1 ratio)."""
        total_buy = buy_volume.sum()
        total_sell = sell_volume.sum()
        
        if total_sell == 0:
            ratio = float('inf')
        else:
            ratio = total_buy / total_sell
        
        if ratio >= 2.0:
            return PatternMatch(
                pattern_type=PatternType.VOLUME_SIGNATURE,
                health=PatternHealth.HEALTHY,
                name="buyer_dominance",
                confidence=min(ratio / 3, 0.95),
                description=f"Buy volume {ratio:.1f}x sell volume",
                indicators={
                    'buy_volume': total_buy,
                    'sell_volume': total_sell,
                    'ratio': ratio
                },
                severity="low"
            )
        elif ratio < 1.0:
            return PatternMatch(
                pattern_type=PatternType.VOLUME_SIGNATURE,
                health=PatternHealth.CONCERNING,
                name="seller_dominance",
                confidence=min((1 - ratio) * 2, 0.95),
                description=f"Sell volume exceeds buy volume ({ratio:.2f}:1)",
                indicators={
                    'buy_volume': total_buy,
                    'sell_volume': total_sell,
                    'ratio': ratio
                },
                severity="medium"
            )
        
        return None
    
    def _check_volume_decline(
        self,
        volume_series: pd.Series,
        timestamps: pd.Series
    ) -> Optional[PatternMatch]:
        """Check for declining volume trend."""
        if len(volume_series) < 6:
            return None
        
        df = pd.DataFrame({
            'volume': volume_series,
            'timestamp': pd.to_datetime(timestamps)
        })
        df = df.sort_values('timestamp')
        df['hour'] = df['timestamp'].dt.floor('H')
        hourly = df.groupby('hour')['volume'].sum()
        
        if len(hourly) < 3:
            return None
        
        x = np.arange(len(hourly))
        slope, _, r_value, _, _ = stats.linregress(x, hourly.values)
        
        if slope < 0 and r_value > 0.5:
            return PatternMatch(
                pattern_type=PatternType.VOLUME_SIGNATURE,
                health=PatternHealth.CONCERNING,
                name="volume_decline",
                confidence=min(r_value, 0.95),
                description="Volume showing sustained downward trend",
                indicators={
                    'slope': slope,
                    'r_squared': r_value ** 2,
                    'trend_direction': 'down'
                },
                severity="medium"
            )
        
        return None
    
    def _check_volume_concentration(
        self,
        volume_series: pd.Series
    ) -> Optional[PatternMatch]:
        """Check for extreme volume concentration."""
        if len(volume_series) < 10:
            return None
        
        # Calculate concentration (top 10% of periods account for what % of volume)
        sorted_vol = volume_series.sort_values(ascending=False)
        top_10_pct_count = max(1, int(len(sorted_vol) * 0.1))
        top_10_pct_volume = sorted_vol.head(top_10_pct_count).sum()
        total_volume = sorted_vol.sum()
        
        concentration = top_10_pct_volume / total_volume if total_volume > 0 else 0
        
        if concentration > 0.7:
            return PatternMatch(
                pattern_type=PatternType.VOLUME_SIGNATURE,
                health=PatternHealth.SUSPICIOUS,
                name="volume_concentration",
                confidence=min(concentration, 0.95),
                description=f"Top 10% of periods account for {concentration:.1%} of volume",
                indicators={
                    'concentration_ratio': concentration,
                    'top_periods': top_10_pct_count
                },
                severity="high"
            )
        
        return None


class HolderPatternFilter:
    """Filter for holder dynamics patterns."""
    
    def analyze(
        self,
        holder_history: pd.DataFrame,
        new_wallet_flow: pd.Series,
        retention_data: pd.Series
    ) -> List[PatternMatch]:
        """
        Analyze holder dynamics patterns.
        
        Args:
            holder_history: DataFrame with holder count over time
            new_wallet_flow: Series of new wallet inflow rates
            retention_data: Series of retention percentages
            
        Returns:
            List of detected patterns
        """
        patterns = []
        
        # Check growth acceleration
        growth_pattern = self._check_growth_acceleration(holder_history)
        if growth_pattern:
            patterns.append(growth_pattern)
        
        # Check new wallet inflow
        inflow_pattern = self._check_new_wallet_inflow(new_wallet_flow)
        if inflow_pattern:
            patterns.append(inflow_pattern)
        
        # Check retention
        retention_pattern = self._check_retention(retention_data)
        if retention_pattern:
            patterns.append(retention_pattern)
        
        return patterns
    
    def _check_growth_acceleration(
        self,
        holder_history: pd.DataFrame
    ) -> Optional[PatternMatch]:
        """Check if holder growth is accelerating."""
        if holder_history.empty or len(holder_history) < 6:
            return None
        
        # Check if required columns exist
        if 'holder_count' not in holder_history.columns:
            # Try to calculate from wallet data
            if 'wallet' in holder_history.columns:
                df = holder_history.copy()
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                # Count unique holders per timestamp
                holder_counts = df.groupby('timestamp')['wallet'].nunique().reset_index()
                holder_counts.columns = ['timestamp', 'holder_count']
                df = holder_counts
            else:
                return None
        else:
            df = holder_history.copy()
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        df = df.sort_values('timestamp')
        
        # Calculate growth rates
        df['growth_rate'] = df['holder_count'].pct_change() * 100
        
        # Check for acceleration (increasing growth rates)
        recent_rates = df['growth_rate'].dropna().tail(3)
        older_rates = df['growth_rate'].dropna().head(3)
        
        if len(recent_rates) >= 2 and len(older_rates) >= 2:
            recent_avg = recent_rates.mean()
            older_avg = older_rates.mean()
            
            if recent_avg > older_avg * 1.5 and recent_avg > 0:
                return PatternMatch(
                    pattern_type=PatternType.HOLDER_DYNAMICS,
                    health=PatternHealth.HEALTHY,
                    name="accelerating_growth",
                    confidence=min(recent_avg / 10, 0.95),
                    description="Holder growth rate is accelerating",
                    indicators={
                        'recent_growth_rate': recent_avg,
                        'older_growth_rate': older_avg,
                        'acceleration_ratio': recent_avg / older_avg if older_avg > 0 else float('inf')
                    },
                    severity="low"
                )
        
        return None
    
    def _check_new_wallet_inflow(
        self,
        new_wallet_flow: pd.Series
    ) -> Optional[PatternMatch]:
        """Check new wallet inflow rate."""
        if new_wallet_flow.empty:
            return None
        
        avg_inflow = new_wallet_flow.mean()
        
        if avg_inflow >= 5.0:  # >5% of holders per hour
            return PatternMatch(
                pattern_type=PatternType.HOLDER_DYNAMICS,
                health=PatternHealth.HEALTHY,
                name="strong_new_inflow",
                confidence=min(avg_inflow / 10, 0.95),
                description=f"Strong new wallet inflow: {avg_inflow:.1f}% of holders/hour",
                indicators={
                    'avg_inflow_rate': avg_inflow,
                    'peak_inflow': new_wallet_flow.max()
                },
                severity="low"
            )
        elif avg_inflow < 1.0:
            return PatternMatch(
                pattern_type=PatternType.HOLDER_DYNAMICS,
                health=PatternHealth.CONCERNING,
                name="weak_new_inflow",
                confidence=min((1 - avg_inflow) * 0.5, 0.95),
                description=f"Weak new wallet inflow: {avg_inflow:.1f}% of holders/hour",
                indicators={
                    'avg_inflow_rate': avg_inflow,
                    'min_inflow': new_wallet_flow.min()
                },
                severity="medium"
            )
        
        return None
    
    def _check_retention(
        self,
        retention_data: pd.Series
    ) -> Optional[PatternMatch]:
        """Check first buyer retention rate."""
        if retention_data.empty:
            return None
        
        avg_retention = retention_data.mean()
        
        if avg_retention >= 60:
            return PatternMatch(
                pattern_type=PatternType.HOLDER_DYNAMICS,
                health=PatternHealth.HEALTHY,
                name="good_retention",
                confidence=min(avg_retention / 80, 0.95),
                description=f"Good first buyer retention: {avg_retention:.1f}%",
                indicators={
                    'avg_retention': avg_retention,
                    'min_retention': retention_data.min()
                },
                severity="low"
            )
        elif avg_retention < 40:
            return PatternMatch(
                pattern_type=PatternType.HOLDER_DYNAMICS,
                health=PatternHealth.CONCERNING,
                name="poor_retention",
                confidence=min((40 - avg_retention) / 20, 0.95),
                description=f"Poor first buyer retention: {avg_retention:.1f}%",
                indicators={
                    'avg_retention': avg_retention,
                    'max_retention': retention_data.max()
                },
                severity="high"
            )
        
        return None


class TradingBehaviorFilter:
    """Filter for trading behavior patterns."""
    
    def analyze(
        self,
        trades_df: pd.DataFrame,
        wallet_stats: Dict[str, any]
    ) -> List[PatternMatch]:
        """
        Analyze trading behavior patterns.
        
        Args:
            trades_df: DataFrame with trade data
            wallet_stats: Dictionary with wallet statistics
            
        Returns:
            List of detected patterns
        """
        patterns = []
        
        # Check for bot patterns
        bot_pattern = self._detect_bot_patterns(trades_df)
        if bot_pattern:
            patterns.append(bot_pattern)
        
        # Check for wash trading
        wash_pattern = self._detect_wash_trading(trades_df)
        if wash_pattern:
            patterns.append(wash_pattern)
        
        # Check for diverse wallets
        diversity_pattern = self._check_wallet_diversity(wallet_stats)
        if diversity_pattern:
            patterns.append(diversity_pattern)
        
        return patterns
    
    def _detect_bot_patterns(
        self,
        trades_df: pd.DataFrame
    ) -> Optional[PatternMatch]:
        """Detect potential bot trading patterns."""
        if trades_df.empty or len(trades_df) < 20:
            return None
        
        df = trades_df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp')
        df['time_diff'] = df['timestamp'].diff().dt.total_seconds()
        
        # Check for uniform timing
        time_diffs = df['time_diff'].dropna()
        if len(time_diffs) > 10:
            cv = np.std(time_diffs) / np.mean(time_diffs) if np.mean(time_diffs) > 0 else float('inf')
            
            if cv < 0.2:  # Very uniform timing
                return PatternMatch(
                    pattern_type=PatternType.TRADING_BEHAVIOR,
                    health=PatternHealth.SUSPICIOUS,
                    name="bot_pattern_timing",
                    confidence=min(1 - cv * 2, 0.95),
                    description="Suspiciously uniform trade timing detected",
                    indicators={
                        'timing_cv': cv,
                        'avg_interval': np.mean(time_diffs)
                    },
                    severity="high"
                )
        
        # Check for round amounts
        amounts = df['amount'].values
        round_amounts = sum(1 for a in amounts if a % 1 == 0 or a % 0.5 == 0)
        round_ratio = round_amounts / len(amounts)
        
        if round_ratio > 0.8:
            return PatternMatch(
                pattern_type=PatternType.TRADING_BEHAVIOR,
                health=PatternHealth.SUSPICIOUS,
                name="bot_pattern_amounts",
                confidence=round_ratio,
                description=f"High proportion of round trade amounts ({round_ratio:.1%})",
                indicators={
                    'round_amount_ratio': round_ratio,
                    'total_trades': len(amounts)
                },
                severity="medium"
            )
        
        return None
    
    def _detect_wash_trading(
        self,
        trades_df: pd.DataFrame
    ) -> Optional[PatternMatch]:
        """Detect potential wash trading."""
        if trades_df.empty or 'counterparty' not in trades_df.columns:
            return None
        
        # Check for repeated wallet pairs
        df = trades_df.copy()
        wallet_pairs = df.groupby(['wallet', 'counterparty']).size()
        
        # High frequency between same pairs suggests wash trading
        if len(wallet_pairs) > 0:
            max_frequency = wallet_pairs.max()
            avg_frequency = wallet_pairs.mean()
            
            if max_frequency > avg_frequency * 5 and max_frequency > 5:
                return PatternMatch(
                    pattern_type=PatternType.TRADING_BEHAVIOR,
                    health=PatternHealth.SUSPICIOUS,
                    name="potential_wash_trading",
                    confidence=min(max_frequency / 20, 0.95),
                    description=f"Suspicious trading frequency between wallet pairs",
                    indicators={
                        'max_pair_frequency': max_frequency,
                        'avg_frequency': avg_frequency,
                        'ratio': max_frequency / avg_frequency if avg_frequency > 0 else float('inf')
                    },
                    severity="high"
                )
        
        return None
    
    def _check_wallet_diversity(
        self,
        wallet_stats: Dict[str, any]
    ) -> Optional[PatternMatch]:
        """Check for diverse wallet participation."""
        unique_wallets = wallet_stats.get('unique_wallets', 0)
        
        if unique_wallets >= 50:
            return PatternMatch(
                pattern_type=PatternType.WALLET_DISTRIBUTION,
                health=PatternHealth.HEALTHY,
                name="diverse_participation",
                confidence=min(unique_wallets / 100, 0.95),
                description=f"Diverse wallet participation: {unique_wallets} unique wallets",
                indicators={
                    'unique_wallets': unique_wallets
                },
                severity="low"
            )
        elif unique_wallets < 10:
            return PatternMatch(
                pattern_type=PatternType.WALLET_DISTRIBUTION,
                health=PatternHealth.CONCERNING,
                name="low_diversity",
                confidence=min((10 - unique_wallets) / 10, 0.95),
                description=f"Low wallet diversity: only {unique_wallets} unique wallets",
                indicators={
                    'unique_wallets': unique_wallets
                },
                severity="medium"
            )
        
        return None


class PatternFilterEngine:
    """Main engine for pattern filtering."""
    
    def __init__(self):
        """Initialize pattern filter engine."""
        self.volume_filter = VolumePatternFilter()
        self.holder_filter = HolderPatternFilter()
        self.behavior_filter = TradingBehaviorFilter()
    
    def analyze(
        self,
        token_address: str,
        volume_data: Dict[str, pd.Series],
        holder_data: Dict[str, any],
        trade_data: pd.DataFrame,
        wallet_stats: Dict[str, any]
    ) -> FilterResult:
        """
        Run all pattern filters and return combined result.
        
        Args:
            token_address: Token address
            volume_data: Dict with volume series
            holder_data: Dict with holder metrics
            trade_data: Trade DataFrame
            wallet_stats: Wallet statistics
            
        Returns:
            FilterResult with all patterns and health assessment
        """
        all_patterns = []
        
        # Volume patterns
        volume_patterns = self.volume_filter.analyze(
            volume_data.get('total', pd.Series()),
            volume_data.get('buy', pd.Series()),
            volume_data.get('sell', pd.Series()),
            volume_data.get('timestamps', pd.Series())
        )
        all_patterns.extend(volume_patterns)
        
        # Holder patterns
        holder_patterns = self.holder_filter.analyze(
            holder_data.get('history', pd.DataFrame()),
            holder_data.get('new_flow', pd.Series()),
            holder_data.get('retention', pd.Series())
        )
        all_patterns.extend(holder_patterns)
        
        # Trading behavior patterns
        behavior_patterns = self.behavior_filter.analyze(
            trade_data,
            wallet_stats
        )
        all_patterns.extend(behavior_patterns)
        
        # Calculate overall health
        health, score, warnings, red_flags = self._assess_overall_health(
            all_patterns
        )
        
        # Determine if tradeable
        is_tradeable = (
            health != PatternHealth.SUSPICIOUS and
            len(red_flags) == 0 and
            score >= 50
        )
        
        return FilterResult(
            token_address=token_address,
            overall_health=health,
            health_score=score,
            patterns=all_patterns,
            warnings=warnings,
            red_flags=red_flags,
            is_tradeable=is_tradeable,
            timestamp=pd.Timestamp.now()
        )
    
    def _assess_overall_health(
        self,
        patterns: List[PatternMatch]
    ) -> Tuple[PatternHealth, float, List[str], List[str]]:
        """
        Assess overall health from all patterns.
        
        Returns:
            Tuple of (health, score, warnings, red_flags)
        """
        if not patterns:
            return PatternHealth.NEUTRAL, 50, [], []
        
        # Count patterns by health
        healthy_count = sum(1 for p in patterns if p.health == PatternHealth.HEALTHY)
        concerning_count = sum(1 for p in patterns if p.health == PatternHealth.CONCERNING)
        suspicious_count = sum(1 for p in patterns if p.health == PatternHealth.SUSPICIOUS)
        
        # Calculate weighted score
        healthy_score = healthy_count * 20
        concerning_score = concerning_count * -15
        suspicious_score = suspicious_count * -30
        
        base_score = 50
        total_score = base_score + healthy_score + concerning_score + suspicious_score
        total_score = max(0, min(100, total_score))
        
        # Determine health
        if suspicious_count > 0:
            health = PatternHealth.SUSPICIOUS
        elif concerning_count > healthy_count:
            health = PatternHealth.CONCERNING
        elif healthy_count > concerning_count:
            health = PatternHealth.HEALTHY
        else:
            health = PatternHealth.NEUTRAL
        
        # Extract warnings and red flags
        warnings = [
            p.description for p in patterns
            if p.health == PatternHealth.CONCERNING
        ]
        
        red_flags = [
            p.description for p in patterns
            if p.health == PatternHealth.SUSPICIOUS
        ]
        
        return health, total_score, warnings, red_flags


# Convenience functions
def quick_pattern_check(
    volume_series: pd.Series,
    holder_count: int,
    unique_wallets: int
) -> Dict[str, any]:
    """Quick pattern health check."""
    patterns = []
    
    # Volume check
    if len(volume_series) >= 3:
        recent_avg = volume_series.tail(3).mean()
        older_avg = volume_series.head(3).mean()
        
        if recent_avg > older_avg * 1.2:
            patterns.append("volume_accelerating")
        elif recent_avg < older_avg * 0.8:
            patterns.append("volume_declining")
    
    # Holder check
    if holder_count >= 100:
        patterns.append("strong_holder_base")
    elif holder_count < 20:
        patterns.append("weak_holder_base")
    
    # Wallet diversity
    if unique_wallets >= 50:
        patterns.append("diverse_wallets")
    elif unique_wallets < 10:
        patterns.append("concentrated_wallets")
    
    # Determine health
    healthy_patterns = ['volume_accelerating', 'strong_holder_base', 'diverse_wallets']
    concerning_patterns = ['volume_declining', 'weak_holder_base', 'concentrated_wallets']
    
    healthy_count = sum(1 for p in patterns if p in healthy_patterns)
    concerning_count = sum(1 for p in patterns if p in concerning_patterns)
    
    if concerning_count > healthy_count:
        health = "concerning"
    elif healthy_count > concerning_count:
        health = "healthy"
    else:
        health = "neutral"
    
    return {
        'patterns': patterns,
        'health': health,
        'healthy_count': healthy_count,
        'concerning_count': concerning_count
    }
