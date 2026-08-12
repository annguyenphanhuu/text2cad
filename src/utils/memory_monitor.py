import os
import logging
import psutil
from typing import Dict, Optional
from datetime import datetime

class MemoryMonitor:
    """Utility class for monitoring system memory usage."""
    
    def __init__(self, logger_name: str = 'memory_monitor'):
        self.logger = logging.getLogger(logger_name)
        self.initial_memory = None
        
    def get_memory_info(self) -> Optional[Dict]:
        """Get current memory information."""
        try:
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            
            return {
                'timestamp': datetime.now().isoformat(),
                'total_ram_gb': memory.total / (1024**3),
                'available_ram_gb': memory.available / (1024**3),
                'used_ram_gb': memory.used / (1024**3),
                'ram_percent': memory.percent,
                'total_swap_gb': swap.total / (1024**3),
                'used_swap_gb': swap.used / (1024**3),
                'swap_percent': swap.percent
            }
        except Exception as e:
            self.logger.error(f"Failed to get memory information: {e}")
            return None
    
    def log_memory_status(self, context: str = ""):
        """Log current memory status with optional context."""
        memory_info = self.get_memory_info()
        if not memory_info:
            return None
        # Memory status logging removed - no longer logging memory information
        return memory_info
    
    def set_baseline(self):
        """Set the current memory usage as baseline for comparison."""
        self.initial_memory = self.get_memory_info()
        if self.initial_memory:
            self.logger.debug(f"Memory baseline set: {self.initial_memory['used_ram_gb']:.2f} GB used")
    
    def log_memory_diff(self, context: str = ""):
        """Log memory usage difference from baseline."""
        if not self.initial_memory:
            self.logger.debug("No baseline set for memory comparison")
            return None

        current_memory = self.get_memory_info()
        if not current_memory:
            return None

        diff_gb = current_memory['used_ram_gb'] - self.initial_memory['used_ram_gb']
        diff_percent = current_memory['ram_percent'] - self.initial_memory['ram_percent']

        # Only log if there's significant change (>500MB)
        if abs(diff_gb) > 0.5:
            context_str = f" ({context})" if context else ""
            level = "warning" if diff_gb > 0 else "info"
            getattr(self.logger, level)(f"Memory change from baseline{context_str}: {diff_gb:+.3f} GB ({diff_percent:+.1f}%)")
        else:
            # Just debug log for small changes
            self.logger.debug(f"Memory change: {diff_gb:+.3f} GB")

        return {
            'diff_gb': diff_gb,
            'diff_percent': diff_percent,
            'current': current_memory,
            'baseline': self.initial_memory
        }
    
    def check_memory_health(self) -> bool:
        """Check if memory usage is within healthy limits."""
        memory_info = self.get_memory_info()
        if not memory_info:
            return False
            
        # Consider healthy if RAM usage < 80% and available RAM > 1GB
        is_healthy = (
            memory_info['ram_percent'] < 80 and 
            memory_info['available_ram_gb'] > 1.0 and
            memory_info['swap_percent'] < 50
        )
        
        if not is_healthy:
            self.logger.warning("Memory usage is outside healthy limits")
            self.log_memory_status("Health Check")
            
        return is_healthy

# Global memory monitor instance
memory_monitor = MemoryMonitor('memory_monitor') 