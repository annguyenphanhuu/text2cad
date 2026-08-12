"""
Cost Tracker for OpenAI API calls
Tracks token usage and costs for each chain invocation
"""
import logging
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# OpenAI Pricing Table (per 1M tokens)
MODEL_PRICING = {
    "gpt-4.1-2025-04-14": {
        "input": 2.00,
        "cached_input": 0.50,
        "output": 8.00
    },
    "o4-mini-2025-04-16": {
        "input": 1.10,
        "cached_input": 0.275,
        "output": 4.40
    },
    "o3-2025-04-16": {
        "input": 2.00,
        "cached_input": 0.50,
        "output": 8.00
    },
    "gpt-4o-mini-2024-07-18": {
        "input": 0.150,
        "cached_input": 0.075,
        "output": 0.600
    },
    "gpt-5.2-2025-12-11": {
        "input": 5.00,  # Estimated pricing for GPT-5.2 (future model)
        "cached_input": 1.25,
        "output": 15.00
    },
    "gpt-5-mini": {
        "input": 2.00,  # gpt-5-mini pricing
        "cached_input": 0.50,
        "output": 8.00
    },
    "gpt-5.4-2026-03-05": {
        "input": 2.50,  # gpt-5.4-2026-03-05 pricing
        "cached_input": 0.25,
        "output": 15.00
    },
    # Fallback for unknown models
    "unknown": {
        "input": 2.00,
        "cached_input": 0.50,
        "output": 8.00
    }
}


class ChainCostInfo:
    """Information about a single chain call cost"""
    def __init__(self, chain_name: str, prompt_tokens: int, completion_tokens: int, 
                 total_tokens: int, total_cost: float, model_name: str = None, timestamp: str = None):
        self.chain_name = chain_name
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.total_cost = total_cost
        self.model_name = model_name or "unknown"
        self.timestamp = timestamp or datetime.now().isoformat()
    
    def __repr__(self):
        return (f"ChainCostInfo(chain={self.chain_name}, "
                f"model={self.model_name}, "
                f"prompt_tokens={self.prompt_tokens}, "
                f"completion_tokens={self.completion_tokens}, "
                f"total_tokens={self.total_tokens}, "
                f"cost=${self.total_cost:.6f})")


class CostTracker:
    """
    Tracks OpenAI API costs across multiple chain invocations in a single request.
    
    Usage:
        tracker = CostTracker(session_id="session_123")
        tracker.add_chain_cost("greeting_classification", callback)
        total = tracker.get_total_cost()
        report = tracker.get_detailed_report()
    """
    
    def __init__(self, session_id: str = None, user_id: str = None):
        self.session_id = session_id or "unknown"
        self.user_id = user_id or "anonymous"
        self.chain_costs: List[ChainCostInfo] = []
        self.start_time = datetime.now()
    
    def add_chain_cost(self, chain_name: str, callback, model_name: str = "unknown") -> ChainCostInfo:
        """
        Add cost information from a LangChain callback.
        
        Args:
            chain_name: Name of the chain (e.g., "greeting_classification")
            callback: OpenAI callback object with token usage info
            model_name: Name of the model used (e.g., "gpt-4.1-2025-04-14")
            
        Returns:
            ChainCostInfo object with the cost details
        """
        # Verify token math (silent check)
        if callback.total_tokens != callback.prompt_tokens + callback.completion_tokens:
            logger.error(
                f"[TOKEN_ERROR] {chain_name} | Token math mismatch! "
                f"{callback.prompt_tokens} + {callback.completion_tokens} ≠ {callback.total_tokens}"
            )
        
        # Calculate accurate per-token costs using pricing table
        pricing = MODEL_PRICING.get(model_name, MODEL_PRICING["unknown"])
        input_cost = (callback.prompt_tokens * pricing["input"]) / 1_000_000
        output_cost = (callback.completion_tokens * pricing["output"]) / 1_000_000
        calculated_total = input_cost + output_cost
        
        # Use calculated cost if callback.total_cost is 0 (unknown model)
        # Otherwise use callback.total_cost (includes reasoning tokens)
        final_cost = callback.total_cost if callback.total_cost > 0 else calculated_total
        
        cost_info = ChainCostInfo(
            chain_name=chain_name,
            prompt_tokens=callback.prompt_tokens,
            completion_tokens=callback.completion_tokens,
            total_tokens=callback.total_tokens,
            total_cost=final_cost,  # Use final_cost instead of callback.total_cost
            model_name=model_name
        )
        
        self.chain_costs.append(cost_info)
        
        # Concise cost log
        logger.info(
            f"[COST] {chain_name} | {model_name} | "
            f"In: {cost_info.prompt_tokens:,}t (${input_cost:.4f}) | "
            f"Out: {cost_info.completion_tokens:,}t (${output_cost:.4f}) | "
            f"Total: ${final_cost:.4f}"
        )
        
        # Only warn if significant cost mismatch (reasoning tokens)
        if callback.total_cost > 0:  # Only check if callback provided a cost
            cost_diff = abs(callback.total_cost - calculated_total)
            if cost_diff > 0.001:  # More than 0.1 cent
                estimated_reasoning = int((cost_diff * 1_000_000) / pricing["output"])
                logger.warning(
                    f"[REASONING] {chain_name} | Hidden tokens: ~{estimated_reasoning:,}t | "
                    f"Extra cost: ${cost_diff:.4f}"
                )
        else:
            # Callback returned 0 cost - using calculated cost
            logger.debug(
                f"[COST_CALC] {chain_name} | Callback cost=0, using calculated: ${calculated_total:.4f}"
            )
        
        return cost_info
    
    def add_direct_api_cost(self, chain_name: str, prompt_tokens: int, completion_tokens: int, 
                           model_name: str = "unknown", total_cost: float = None) -> ChainCostInfo:
        """
        Add cost information from direct OpenAI API calls (not using LangChain callback).
        Used for: web_search, pdf_processing, image_processing, metadata_analysis
        
        Args:
            chain_name: Name of the operation (e.g., "web_search", "pdf_processing")
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens
            model_name: Name of the model used (e.g., "gpt-5.4-2026-03-05")
            total_cost: Optional pre-calculated total cost (if available from API response)
            
        Returns:
            ChainCostInfo object with the cost details
        """
        total_tokens = prompt_tokens + completion_tokens
        
        # Calculate accurate per-token costs using pricing table
        pricing = MODEL_PRICING.get(model_name, MODEL_PRICING["unknown"])
        input_cost = (prompt_tokens * pricing["input"]) / 1_000_000
        output_cost = (completion_tokens * pricing["output"]) / 1_000_000
        calculated_total = input_cost + output_cost
        
        # Use provided total_cost if available, otherwise use calculated
        final_cost = total_cost if total_cost is not None else calculated_total
        
        cost_info = ChainCostInfo(
            chain_name=chain_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            total_cost=final_cost,
            model_name=model_name
        )
        
        self.chain_costs.append(cost_info)
        
        # Concise cost log
        logger.info(
            f"[COST] {chain_name} | {model_name} | "
            f"In: {prompt_tokens:,}t (${input_cost:.4f}) | "
            f"Out: {completion_tokens:,}t (${output_cost:.4f}) | "
            f"Total: ${final_cost:.4f}"
        )
        
        return cost_info
    
    def get_total_cost(self) -> float:
        """Get total cost across all chain calls"""
        return sum(cost.total_cost for cost in self.chain_costs)
    
    def get_total_tokens(self) -> int:
        """Get total tokens used across all chain calls"""
        return sum(cost.total_tokens for cost in self.chain_costs)
    
    def get_total_prompt_tokens(self) -> int:
        """Get total prompt tokens across all chain calls"""
        return sum(cost.prompt_tokens for cost in self.chain_costs)
    
    def get_total_completion_tokens(self) -> int:
        """Get total completion tokens across all chain calls"""
        return sum(cost.completion_tokens for cost in self.chain_costs)
    
    def get_detailed_report(self) -> str:
        """
        Generate a detailed cost report for logging.
        
        Returns:
            Formatted string with cost breakdown
        """
        if not self.chain_costs:
            return f"[COST_REPORT] Session {self.session_id}: No OpenAI calls tracked"
        
        lines = [
            "=" * 80,
            f"[COST] Session: {self.session_id} | Duration: {(datetime.now() - self.start_time).total_seconds():.1f}s",
            "-" * 80
        ]
        
        for i, cost in enumerate(self.chain_costs, 1):
            # Calculate accurate per-token costs using pricing table
            pricing = MODEL_PRICING.get(cost.model_name, MODEL_PRICING["unknown"])
            input_cost = (cost.prompt_tokens * pricing["input"]) / 1_000_000
            output_cost = (cost.completion_tokens * pricing["output"]) / 1_000_000
            
            # Compact one-line format
            lines.append(
                f"[COST] {i}. {cost.chain_name:<30} | {cost.model_name:<25} | "
                f"In: {cost.prompt_tokens:>6,}t (${input_cost:>7.4f}) | "
                f"Out: {cost.completion_tokens:>4,}t (${output_cost:>7.4f}) | "
                f"Total: ${cost.total_cost:>7.4f}"
            )
        
        lines.extend([
            "-" * 80,
            f"[COST] TOTAL | Chains: {len(self.chain_costs)} | "
            f"In: {self.get_total_prompt_tokens():,}t | "
            f"Out: {self.get_total_completion_tokens():,}t | "
            f"Total: {self.get_total_tokens():,}t | "
            f"COST: ${self.get_total_cost():.4f}",
            "=" * 80
        ])
        
        return "\n".join(lines)
    
    def get_summary_dict(self) -> Dict[str, Any]:
        """
        Get cost summary as a dictionary for API responses.
        
        Returns:
            Dictionary with cost summary
        """
        return {
            "session_id": self.session_id,
            "total_chains": len(self.chain_costs),
            "total_prompt_tokens": self.get_total_prompt_tokens(),
            "total_completion_tokens": self.get_total_completion_tokens(),
            "total_tokens": self.get_total_tokens(),
            "total_cost_usd": round(self.get_total_cost(), 6),
            "chain_breakdown": [
                {
                    "chain_name": cost.chain_name,
                    "model_name": cost.model_name,
                    "prompt_tokens": cost.prompt_tokens,
                    "completion_tokens": cost.completion_tokens,
                    "total_tokens": cost.total_tokens,
                    "cost_usd": round(cost.total_cost, 6),
                    "timestamp": cost.timestamp
                }
                for cost in self.chain_costs
            ]
        }
    
    def reset(self):
        """Reset the tracker for a new request"""
        self.chain_costs = []
        self.start_time = datetime.now()
        self._request_start_index = 0
        logger.debug(f"[COST_TRACKER] Reset tracker for session {self.session_id}")

    def start_request(self):
        """
        Mark the start of a new request turn.
        Call this at the beginning of each process_request_with_progress() call
        so that get_request_summary() returns ONLY cost for the current turn,
        not accumulated cost from previous turns in the same session.
        """
        self._request_start_index = len(self.chain_costs)
        self.request_start_time = datetime.now()
        logger.debug(
            f"[COST_TRACKER] New request turn started at index {self._request_start_index} "
            f"for session {self.session_id}"
        )

    def get_request_cost(self) -> float:
        """
        Get total cost for the CURRENT request turn only
        (since the last start_request() call).
        """
        idx = getattr(self, '_request_start_index', 0)
        return sum(cost.total_cost for cost in self.chain_costs[idx:])

    def get_request_summary(self) -> Dict[str, Any]:
        """
        Get cost summary for the CURRENT request turn only.
        Safe to call after every turn — never suppressed by a _logged flag.

        Returns:
            Dictionary with cost breakdown for this request turn only
        """
        idx = getattr(self, '_request_start_index', 0)
        request_chains = self.chain_costs[idx:]

        if not request_chains:
            return {
                "request_total_cost_usd": 0.0,
                "request_total_tokens": 0,
                "request_prompt_tokens": 0,
                "request_completion_tokens": 0,
                "request_chains": 0,
                "chain_breakdown": []
            }

        total_cost = sum(c.total_cost for c in request_chains)
        total_tokens = sum(c.total_tokens for c in request_chains)
        prompt_tokens = sum(c.prompt_tokens for c in request_chains)
        completion_tokens = sum(c.completion_tokens for c in request_chains)

        return {
            "request_total_cost_usd": round(total_cost, 6),
            "request_total_tokens": total_tokens,
            "request_prompt_tokens": prompt_tokens,
            "request_completion_tokens": completion_tokens,
            "request_chains": len(request_chains),
            "chain_breakdown": [
                {
                    "chain_name": c.chain_name,
                    "model_name": c.model_name,
                    "prompt_tokens": c.prompt_tokens,
                    "completion_tokens": c.completion_tokens,
                    "cost_usd": round(c.total_cost, 6),
                }
                for c in request_chains
            ]
        }

    def log_request_cost(self, label: str = "REQUEST") -> Dict[str, Any]:
        """
        Log cost for the current request turn only (since last start_request() call).
        Never suppressed — always reports per-turn breakdown with aligned table format.

        Args:
            label: Short label identifying the caller (e.g. "process_request_with_progress")

        Returns:
            Request cost summary dict (same as get_request_summary())
        """
        summary = self.get_request_summary()
        idx = getattr(self, '_request_start_index', 0)
        request_chains = self.chain_costs[idx:]

        duration = (
            (datetime.now() - self.request_start_time).total_seconds()
            if hasattr(self, "request_start_time")
            else 0.0
        )

        if request_chains:
            total_in   = summary["request_prompt_tokens"]
            total_out  = summary["request_completion_tokens"]
            total_tok  = summary["request_total_tokens"]
            total_cost = summary["request_total_cost_usd"]

            lines = [
                "=" * 80,
                f"[COST] 💰 Turn cost | {label}",
                f"[COST]    session={self.session_id} | duration={duration:.1f}s",
                "-" * 80,
            ]

            for i, c in enumerate(request_chains, 1):
                pricing   = MODEL_PRICING.get(c.model_name, MODEL_PRICING["unknown"])
                in_cost   = (c.prompt_tokens   * pricing["input"])  / 1_000_000
                out_cost  = (c.completion_tokens * pricing["output"]) / 1_000_000
                lines.append(
                    f"[COST] {i:>2}. {c.chain_name:<30} | {c.model_name:<25} | "
                    f"In: {c.prompt_tokens:>6,}t (${in_cost:>7.4f}) | "
                    f"Out: {c.completion_tokens:>4,}t (${out_cost:>7.4f}) | "
                    f"Total: ${c.total_cost:>7.4f}"
                )

            lines.extend([
                "-" * 80,
                f"[COST] TOTAL | Chains: {len(request_chains)} | "
                f"In: {total_in:,}t | "
                f"Out: {total_out:,}t | "
                f"Tokens: {total_tok:,}t | "
                f"COST: ${total_cost:.4f}",
                "=" * 80,
            ])
            logger.info("\n".join(lines))

        else:
            logger.info(
                f"[COST] {label} | session={self.session_id} | "
                f"duration={duration:.1f}s | no OpenAI calls in this turn"
            )

        return summary
