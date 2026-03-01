"""
Position Sizing Module for Pump.fun Trading System

Implements Kelly Criterion with fractional Kelly adjustment and sequential entry scaling.
"""

import logging
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from datetime import datetime
import numpy as np

from .models import (
    KellyParameters,
    PositionConstraints,
    PositionStage,
    RiskCheckResult,
    RiskLevel,
    DEFAULT_KELLY_PARAMS,
    DEFAULT_POSITION_CONSTRAINTS
)

logger = logging.getLogger(__name__)


@dataclass
class PositionSizeResult:
    """Result of position size calculation"""
    target_size_sol: float
    target_size_usd: float
    target_portfolio_pct: float
    kelly_fraction: float
    kelly_raw: float
    stage: PositionStage
    can_enter: bool
    reason: Optional[str] = None
    safety_limits_applied: Dict[str, Any] = None


class KellyCriterion:
    """
    Kelly Criterion implementation for optimal position sizing.
    
    Given Pump.fun parameters:
    - Base graduation rate: ~0.8%
    - With strong signals: ~10% graduation probability
    - Win return: 5-10x (avg 7.5x)
    - Loss: ~-90%
    
    Applies fractional Kelly (0.25-0.5) for safety.
    """
    
    def __init__(self, params: Optional[KellyParameters] = None):
        self.params = params or DEFAULT_KELLY_PARAMS
    
    def calculate_kelly_fraction(self) -> float:
        """
        Calculate the Kelly Criterion fraction.
        
        Kelly Formula: f* = (p*b - q) / b
        Where:
        - p = probability of win
        - q = probability of loss (1-p)
        - b = win/loss ratio (net odds received)
        
        For our case:
        - Win multiple: 7.5x (so b = 7.5 - 1 = 6.5 net profit)
        - Loss: -90% (so loss = 0.9)
        - b = 6.5 / 0.9 ≈ 7.22
        """
        p = self.params.win_probability
        q = self.params.loss_probability
        
        # Net win multiple (excluding principal)
        net_win = self.params.win_return_multiple - 1.0
        loss_amount = self.params.loss_percentage
        
        # Odds ratio
        b = net_win / loss_amount if loss_amount > 0 else 0
        
        # Kelly fraction
        if b <= 0:
            return 0.0
        
        kelly = (p * b - q) / b
        
        # Kelly should never recommend more than p (probability of win)
        kelly = min(kelly, p)
        
        return max(0.0, kelly)
    
    def calculate_fractional_kelly(self, fraction: Optional[float] = None) -> float:
        """
        Calculate fractional Kelly position size.
        
        Args:
            fraction: Kelly fraction (0.25-0.5), uses params default if None
            
        Returns:
            Fractional Kelly position size as portfolio percentage
        """
        kelly = self.calculate_kelly_fraction()
        frac = fraction or self.params.fractional_kelly
        
        return kelly * frac
    
    def get_position_size_range(self) -> Tuple[float, float]:
        """
        Get recommended position size range based on fractional Kelly.
        
        Returns:
            Tuple of (min_pct, max_pct) as portfolio percentages
        """
        # Conservative: 0.25 Kelly
        conservative = self.calculate_fractional_kelly(0.25)
        # Aggressive: 0.5 Kelly
        aggressive = self.calculate_fractional_kelly(0.50)
        
        # Clamp to reasonable bounds (1-5% as specified)
        conservative = max(0.01, min(0.05, conservative))
        aggressive = max(0.01, min(0.05, aggressive))
        
        return conservative, aggressive


class PositionSizer:
    """
    Position sizing with sequential entry scaling.
    
    Sequential Entry Strategy:
    - 75% progress + volume: 25% of target position
    - 85% + dev stability + alpha: 50% of target (75% cumulative)
    - 90% + sustained momentum: 100% of target
    """
    
    def __init__(
        self,
        kelly_params: Optional[KellyParameters] = None,
        constraints: Optional[PositionConstraints] = None
    ):
        self.kelly = KellyCriterion(kelly_params)
        self.constraints = constraints or DEFAULT_POSITION_CONSTRAINTS
        self.logger = logging.getLogger(__name__)
    
    def calculate_target_position_size(
        self,
        portfolio_value_sol: float,
        portfolio_value_usd: float,
        signal_strength: float = 1.0,
        confidence: float = 1.0
    ) -> PositionSizeResult:
        """
        Calculate target position size based on Kelly Criterion.
        
        Args:
            portfolio_value_sol: Total portfolio value in SOL
            portfolio_value_usd: Total portfolio value in USD
            signal_strength: Signal strength multiplier (0.5-1.5)
            confidence: Confidence in the trade (0.5-1.0)
            
        Returns:
            PositionSizeResult with sizing details
        """
        safety_limits = {}
        
        # Calculate raw Kelly fraction
        kelly_raw = self.kelly.calculate_kelly_fraction()
        kelly_pct = self.kelly.calculate_fractional_kelly()
        
        # Apply signal strength and confidence adjustments
        adjusted_kelly = kelly_pct * signal_strength * confidence
        
        # Calculate position sizes
        target_pct = adjusted_kelly
        target_sol = portfolio_value_sol * target_pct
        target_usd = portfolio_value_usd * target_pct
        
        # Apply safety constraints
        # 1. Maximum single position limit (5%)
        if target_pct > self.constraints.max_single_position_pct:
            safety_limits['max_position_cap'] = {
                'original_pct': target_pct,
                'capped_pct': self.constraints.max_single_position_pct
            }
            target_pct = self.constraints.max_single_position_pct
            target_sol = portfolio_value_sol * target_pct
            target_usd = portfolio_value_usd * target_pct
        
        # 2. Minimum position size (0.5%)
        if target_pct < self.constraints.min_position_pct:
            safety_limits['below_minimum'] = {
                'calculated_pct': target_pct,
                'minimum_pct': self.constraints.min_position_pct
            }
            can_enter = False
            reason = f"Position size {target_pct:.2%} below minimum {self.constraints.min_position_pct:.2%}"
            return PositionSizeResult(
                target_size_sol=0.0,
                target_size_usd=0.0,
                target_portfolio_pct=0.0,
                kelly_fraction=kelly_pct,
                kelly_raw=kelly_raw,
                stage=PositionStage.INITIAL,
                can_enter=False,
                reason=reason,
                safety_limits_applied=safety_limits
            )
        
        self.logger.info(
            f"Target position calculated: {target_pct:.2%} of portfolio "
            f"({target_sol:.4f} SOL, ${target_usd:.2f})"
        )
        
        return PositionSizeResult(
            target_size_sol=target_sol,
            target_size_usd=target_usd,
            target_portfolio_pct=target_pct,
            kelly_fraction=kelly_pct,
            kelly_raw=kelly_raw,
            stage=PositionStage.INITIAL,
            can_enter=True,
            safety_limits_applied=safety_limits
        )
    
    def calculate_sequential_entry(
        self,
        target_position_sol: float,
        target_position_usd: float,
        current_progress: float,
        volume_confirmed: bool,
        dev_stability: bool = False,
        alpha_confirmed: bool = False,
        momentum_sustained: bool = False,
        current_stage: PositionStage = PositionStage.INITIAL
    ) -> PositionSizeResult:
        """
        Calculate position size for sequential entry scaling.
        
        Args:
            target_position_sol: Full target position in SOL
            target_position_usd: Full target position in USD
            current_progress: Current bonding curve progress (0-1)
            volume_confirmed: Whether volume spike is confirmed
            dev_stability: Whether dev stability is confirmed
            alpha_confirmed: Whether alpha signals confirmed
            momentum_sustained: Whether momentum is sustained
            current_stage: Current entry stage
            
        Returns:
            PositionSizeResult for this entry
        """
        target_pct = 0.0
        stage = current_stage
        can_enter = False
        reason = None
        
        # Determine entry stage and size
        if current_stage == PositionStage.INITIAL:
            # Check for initial entry trigger: 75% + volume
            if current_progress >= self.constraints.initial_progress_threshold:
                if volume_confirmed:
                    target_pct = self.constraints.initial_entry_pct
                    stage = PositionStage.INITIAL
                    can_enter = True
                else:
                    reason = "Volume confirmation required for initial entry"
            else:
                reason = f"Progress {current_progress:.1%} below threshold {self.constraints.initial_progress_threshold:.1%}"
        
        elif current_stage == PositionStage.SCALED:
            # Check for scaled entry trigger: 85% + dev stability + alpha
            if current_progress >= self.constraints.scaled_progress_threshold:
                if dev_stability and alpha_confirmed:
                    # Add 50% more (total 75%)
                    target_pct = self.constraints.scaled_entry_pct
                    stage = PositionStage.SCALED
                    can_enter = True
                else:
                    missing = []
                    if not dev_stability:
                        missing.append("dev stability")
                    if not alpha_confirmed:
                        missing.append("alpha confirmation")
                    reason = f"Missing: {', '.join(missing)}"
            else:
                reason = f"Progress {current_progress:.1%} below scaled threshold {self.constraints.scaled_progress_threshold:.1%}"
        
        elif current_stage == PositionStage.FULL:
            # Check for full entry trigger: 90% + sustained momentum
            if current_progress >= self.constraints.full_progress_threshold:
                if momentum_sustained:
                    # Add remaining 25% (total 100%)
                    target_pct = self.constraints.full_entry_pct
                    stage = PositionStage.FULL
                    can_enter = True
                else:
                    reason = "Sustained momentum required for full entry"
            else:
                reason = f"Progress {current_progress:.1%} below full threshold {self.constraints.full_progress_threshold:.1%}"
        
        # Calculate actual entry size
        entry_sol = target_position_sol * target_pct
        entry_usd = target_position_usd * target_pct
        
        return PositionSizeResult(
            target_size_sol=entry_sol,
            target_size_usd=entry_usd,
            target_portfolio_pct=target_pct,
            kelly_fraction=0.0,  # Not applicable for sequential entry
            kelly_raw=0.0,
            stage=stage,
            can_enter=can_enter,
            reason=reason
        )
    
    def check_portfolio_limits(
        self,
        current_pre_graduation_exposure_pct: float,
        current_position_count: int,
        proposed_addition_pct: float
    ) -> RiskCheckResult:
        """
        Check if proposed position violates portfolio limits.
        
        Args:
            current_pre_graduation_exposure_pct: Current pre-grad exposure
            current_position_count: Number of current positions
            proposed_addition_pct: Proposed new position as % of portfolio
            
        Returns:
            RiskCheckResult with pass/fail status
        """
        details = {
            'current_exposure_pct': current_pre_graduation_exposure_pct,
            'current_positions': current_position_count,
            'proposed_addition_pct': proposed_addition_pct
        }
        
        # Check max pre-graduation exposure (30%)
        new_exposure = current_pre_graduation_exposure_pct + proposed_addition_pct
        if new_exposure > self.constraints.max_pre_graduation_exposure:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                message=f"Pre-graduation exposure would exceed {self.constraints.max_pre_graduation_exposure:.0%}",
                details={**details, 'would_be_exposure': new_exposure},
                recommended_action="Wait for positions to graduate or reduce existing exposure"
            )
        
        # Check max simultaneous positions (20)
        if current_position_count >= self.constraints.max_simultaneous_positions:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.MEDIUM,
                message=f"Maximum {self.constraints.max_simultaneous_positions} positions reached",
                details=details,
                recommended_action="Close or graduate existing positions before adding new ones"
            )
        
        # Check minimum diversification (10 positions)
        if current_position_count < self.constraints.min_simultaneous_positions:
            if new_exposure > self.constraints.max_pre_graduation_exposure * 0.8:
                return RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.MEDIUM,
                    message="Insufficient diversification - add more positions",
                    details={**details, 'min_positions': self.constraints.min_simultaneous_positions},
                    recommended_action="Add more positions to reach minimum diversification"
                )
        
        return RiskCheckResult(
            passed=True,
            risk_level=RiskLevel.LOW,
            message="Portfolio limits check passed",
            details={**details, 'new_exposure': new_exposure}
        )
    
    def get_entry_plan(
        self,
        token_address: str,
        target_position_sol: float,
        target_position_usd: float,
        current_progress: float
    ) -> Dict[str, Any]:
        """
        Generate a complete entry plan for a token.
        
        Args:
            token_address: Token address
            target_position_sol: Full target position in SOL
            target_position_usd: Full target position in USD
            current_progress: Current bonding curve progress
            
        Returns:
            Dictionary with entry plan details
        """
        plan = {
            'token_address': token_address,
            'target_position_sol': target_position_sol,
            'target_position_usd': target_position_usd,
            'current_progress': current_progress,
            'stages': []
        }
        
        # Stage 1: Initial entry at 75%
        stage1 = self.calculate_sequential_entry(
            target_position_sol=target_position_sol,
            target_position_usd=target_position_usd,
            current_progress=max(current_progress, 0.75),
            volume_confirmed=True,
            current_stage=PositionStage.INITIAL
        )
        plan['stages'].append({
            'stage': 'initial',
            'trigger': '75% progress + volume spike',
            'size_pct': 0.25,
            'size_sol': target_position_sol * 0.25,
            'size_usd': target_position_usd * 0.25,
            'cumulative_pct': 0.25
        })
        
        # Stage 2: Scaled entry at 85%
        stage2 = self.calculate_sequential_entry(
            target_position_sol=target_position_sol,
            target_position_usd=target_position_usd,
            current_progress=0.85,
            volume_confirmed=True,
            dev_stability=True,
            alpha_confirmed=True,
            current_stage=PositionStage.SCALED
        )
        plan['stages'].append({
            'stage': 'scaled',
            'trigger': '85% progress + dev stability + alpha',
            'size_pct': 0.50,
            'size_sol': target_position_sol * 0.50,
            'size_usd': target_position_usd * 0.50,
            'cumulative_pct': 0.75
        })
        
        # Stage 3: Full entry at 90%
        stage3 = self.calculate_sequential_entry(
            target_position_sol=target_position_sol,
            target_position_usd=target_position_usd,
            current_progress=0.90,
            volume_confirmed=True,
            dev_stability=True,
            alpha_confirmed=True,
            momentum_sustained=True,
            current_stage=PositionStage.FULL
        )
        plan['stages'].append({
            'stage': 'full',
            'trigger': '90% progress + sustained momentum',
            'size_pct': 1.00,
            'size_sol': target_position_sol * 1.00,
            'size_usd': target_position_usd * 1.00,
            'cumulative_pct': 1.00
        })
        
        return plan


# Convenience function for quick position sizing
def calculate_position_size(
    portfolio_value_sol: float,
    portfolio_value_usd: float,
    signal_strength: float = 1.0,
    confidence: float = 1.0
) -> PositionSizeResult:
    """
    Quick function to calculate position size.
    
    Args:
        portfolio_value_sol: Portfolio value in SOL
        portfolio_value_usd: Portfolio value in USD
        signal_strength: Signal strength (0.5-1.5)
        confidence: Confidence level (0.5-1.0)
        
    Returns:
        PositionSizeResult
    """
    sizer = PositionSizer()
    return sizer.calculate_target_position_size(
        portfolio_value_sol=portfolio_value_sol,
        portfolio_value_usd=portfolio_value_usd,
        signal_strength=signal_strength,
        confidence=confidence
    )
