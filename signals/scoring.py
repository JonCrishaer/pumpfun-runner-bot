"""
Multi-factor scoring system for Pump.fun trading signals.

This module implements a weighted scoring algorithm that evaluates tokens
based on bonding curve progress, volume patterns, developer activity,
alpha wallet presence, and holder growth.
"""

from dataclasses import dataclass
from typing import Dict, Optional
import numpy as np

# =============================================================================
# STRATEGY PARAMETERS (BALANCED MODE)
# =============================================================================

PROGRESS_THRESHOLD = 85.0  # Minimum bonding curve progress (%)
VOLUME_MULTIPLE = 2.5    # Volume acceleration vs 7-day average
DEV_SELL_LIMIT = 0.05    # Maximum developer sell ratio (5%)
ALPHA_MINIMUM = 2        # Minimum alpha wallets required
HOLDER_GROWTH_THRESHOLD = 0.05  # Minimum holder growth per hour (5%)

# Weight configuration for scoring
WEIGHTS = {
    'progress': 0.25,
    'volume': 0.25,
    'dev_activity': 0.20,
    'alpha_presence': 0.15,
    'holder_growth': 0.15
}


@dataclass
class ScoreResult:
    """Result of signal score calculation."""
    total_score: float
    component_scores: Dict[str, float]
    signal_strength: str  # 'weak', 'moderate', 'strong', 'maximum'
    recommended_position: float  # Position size as % of portfolio


def calculate_progress_score(progress: float) -> float:
    """
    Calculate score based on bonding curve progress.
    
    Scoring:
    - 0-50%: 0 points (green zone, ignore)
    - 50-75%: 0-25 points (yellow zone, monitor)
    - 75-90%: 25-75 points (orange zone, entry zone)
    - 90-100%: 75-100 points (red zone, critical)
    
    Args:
        progress: Bonding curve progress percentage (0-100)
        
    Returns:
        Score from 0-100
    """
    if progress < 50:
        return 0.0
    elif progress < 75:
        return ((progress - 50) / 25) * 25
    elif progress < 90:
        return 25 + ((progress - 75) / 15) * 50
    else:
        return 75 + ((progress - 90) / 10) * 25


def calculate_volume_score(volume_data: Dict) -> float:
    """
    Calculate score based on volume patterns.
    
    Scoring factors:
    - Volume acceleration vs 7-day average
    - Buy/sell ratio (healthy = more buys than sells)
    
    Args:
        volume_data: Dictionary containing:
            - volume_acceleration: Ratio vs 7-day average
            - buy_sell_ratio: Ratio of buy to sell volume
            - is_healthy: Boolean indicating healthy pattern
            
    Returns:
        Score from 0-100
    """
    if not volume_data.get('is_healthy', False):
        return 0.0
    
    acceleration = volume_data.get('volume_acceleration', 0.0)
    buy_sell_ratio = volume_data.get('buy_sell_ratio', 1.0)
    
    # Volume acceleration score (0-60 points)
    if acceleration >= VOLUME_MULTIPLE * 2:
        vol_score = 60.0
    elif acceleration >= VOLUME_MULTIPLE:
        vol_score = 40.0 + ((acceleration - VOLUME_MULTIPLE) / VOLUME_MULTIPLE) * 20
    elif acceleration >= 1.0:
        vol_score = ((acceleration - 1.0) / (VOLUME_MULTIPLE - 1.0)) * 40
    else:
        vol_score = 0.0
    
    # Buy/sell ratio score (0-40 points)
    if buy_sell_ratio >= 2.0:
        ratio_score = 40.0
    elif buy_sell_ratio >= 1.0:
        ratio_score = (buy_sell_ratio - 1.0) * 40
    else:
        ratio_score = 0.0
    
    return min(100.0, vol_score + ratio_score)


def calculate_dev_score(dev_data: Dict) -> float:
    """
    Calculate score based on developer activity.
    
    Penalizes excessive developer selling.
    
    Args:
        dev_data: Dictionary containing:
            - sell_ratio: Percentage of supply sold by dev (0-1)
            - is_active: Boolean indicating recent activity
            
    Returns:
        Score from 0-100
    """
    sell_ratio = dev_data.get('sell_ratio', 0.0)
    is_active = dev_data.get('is_active', True)
    
    if not is_active:
        return 50.0  # Neutral if no recent activity
    
    if sell_ratio > DEV_SELL_LIMIT * 2:
        return 0.0  # Excessive selling
    elif sell_ratio > DEV_SELL_LIMIT:
        return 25.0 * (1 - (sell_ratio - DEV_SELL_LIMIT) / DEV_SELL_LIMIT)
    else:
        return 100.0 * (1 - sell_ratio / DEV_SELL_LIMIT)


def calculate_alpha_score(alpha_data: Dict) -> float:
    """
    Calculate score based on alpha wallet presence.
    
    Args:
        alpha_data: Dictionary containing:
            - alpha_wallets: Number of alpha wallets holding
            - total_alpha_positions: Total positions by alpha wallets
            
    Returns:
        Score from 0-100
    """
    alpha_wallets = alpha_data.get('alpha_wallets', 0)
    
    if alpha_wallets < ALPHA_MINIMUM:
        return (alpha_wallets / ALPHA_MINIMUM) * 50
    elif alpha_wallets < ALPHA_MINIMUM * 2:
        return 50 + ((alpha_wallets - ALPHA_MINIMUM) / ALPHA_MINIMUM) * 25
    else:
        return 75 + min(25, (alpha_wallets - ALPHA_MINIMUM * 2) * 2.5)


def calculate_holder_score(holder_data: Dict) -> float:
    """
    Calculate score based on holder growth rate.
    
    Args:
        holder_data: Dictionary containing:
            - growth_rate: Hourly growth rate (0.05 = 5%/hour)
            - total_holders: Current number of holders
            
    Returns:
        Score from 0-100
    """
    growth_rate = holder_data.get('growth_rate', 0.0)
    total_holders = holder_data.get('total_holders', 0)
    
    if total_holders < 10:
        return 0.0  # Too few holders
    
    if growth_rate >= HOLDER_GROWTH_THRESHOLD * 2:
        return 100.0
    elif growth_rate >= HOLDER_GROWTH_THRESHOLD:
        return 50 + ((growth_rate - HOLDER_GROWTH_THRESHOLD) / HOLDER_GROWTH_THRESHOLD) * 50
    else:
        return (growth_rate / HOLDER_GROWTH_THRESHOLD) * 50


def calculate_signal_score(
    progress: float,
    volume_data: Dict,
    dev_data: Dict,
    alpha_data: Dict,
    holder_data: Dict
) -> ScoreResult:
    """
    Calculate overall signal score from all factors.
    
    Score Interpretation:
    - 0-40: Weak (no position)
    - 40-60: Moderate (1-2% position)
    - 60-80: Strong (2-4% position)
    - 80-100: Maximum (4-5% position)
    
    Args:
        progress: Bonding curve progress percentage
        volume_data: Volume analysis results
        dev_data: Developer activity data
        alpha_data: Alpha wallet data
        holder_data: Holder growth data
        
    Returns:
        ScoreResult with total score and recommendations
    """
    # Calculate individual component scores
    component_scores = {
        'progress': calculate_progress_score(progress),
        'volume': calculate_volume_score(volume_data),
        'dev_activity': calculate_dev_score(dev_data),
        'alpha_presence': calculate_alpha_score(alpha_data),
        'holder_growth': calculate_holder_score(holder_data)
    }
    
    # Calculate weighted total score
    total_score = sum(
        component_scores[key] * WEIGHTS[key]
        for key in WEIGHTS.keys()
    )
    
    # Determine signal strength and position recommendation
    if total_score >= 80:
        signal_strength = 'maximum'
        recommended_position = 4.5
    elif total_score >= 60:
        signal_strength = 'strong'
        recommended_position = 3.0
    elif total_score >= 40:
        signal_strength = 'moderate'
        recommended_position = 1.5
    else:
        signal_strength = 'weak'
        recommended_position = 0.0
    
    return ScoreResult(
        total_score=round(total_score, 2),
        component_scores={k: round(v, 2) for k, v in component_scores.items()},
        signal_strength=signal_strength,
        recommended_position=recommended_position
    )


def quick_score(
    progress: float,
    volume_accel: float,
    dev_sell_ratio: float,
    alpha_count: int,
    holder_growth: float
) -> float:
    """
    Quick scoring function for simple use cases.
    
    Args:
        progress: Bonding curve progress (0-100)
        volume_accel: Volume acceleration vs average
        dev_sell_ratio: Developer sell ratio (0-1)
        alpha_count: Number of alpha wallets
        holder_growth: Holder growth rate per hour
        
    Returns:
        Total score from 0-100
    """
    result = calculate_signal_score(
        progress=progress,
        volume_data={'volume_acceleration': volume_accel, 'buy_sell_ratio': 1.5, 'is_healthy': True},
        dev_data={'sell_ratio': dev_sell_ratio, 'is_active': True},
        alpha_data={'alpha_wallets': alpha_count},
        holder_data={'growth_rate': holder_growth, 'total_holders': 100}
    )
    return result.total_score
