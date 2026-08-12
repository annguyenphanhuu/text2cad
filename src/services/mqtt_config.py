#!/usr/bin/env python3
"""
MQTT Configuration for FreeCAD Job Monitoring
Provides centralized configuration for MQTT broker connection and topics
"""

import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class MQTTConfig:
    """MQTT broker configuration"""
    
    def __init__(self):
        # Parse MQTT broker URL from environment
        broker_url = os.getenv("MQTT_BROKER", "mqtt://mqtt-docker-api.cleverapps.io:11071")
        self.broker_host, self.broker_port = self._parse_broker_url(broker_url)
        
        # MQTT connection settings
        self.client_id = os.getenv("MQTT_CLIENT_ID", "tolery_api_ai_listener")
        self.keepalive = int(os.getenv("MQTT_KEEPALIVE", "60"))
        self.clean_session = os.getenv("MQTT_CLEAN_SESSION", "true").lower() == "true"
        
        # MQTT authentication (if required)
        self.username = os.getenv("MQTT_USERNAME", None)
        self.password = os.getenv("MQTT_PASSWORD", None)
        
        # Topic patterns
        self.progress_topic_pattern = "freecad/progress/+"
        self.status_topic_pattern = "freecad/status/+"
        
        # QoS levels
        self.qos_level = int(os.getenv("MQTT_QOS", "1"))  # 0, 1, or 2
        
        # Reconnection settings
        self.reconnect_delay_min = int(os.getenv("MQTT_RECONNECT_DELAY_MIN", "1"))
        self.reconnect_delay_max = int(os.getenv("MQTT_RECONNECT_DELAY_MAX", "60"))
        
    def _parse_broker_url(self, broker_url: str) -> tuple[str, int]:
        """
        Parse MQTT broker URL to extract host and port
        
        Args:
            broker_url: MQTT broker URL (e.g., mqtt://host:port)
            
        Returns:
            Tuple of (host, port)
        """
        # Remove mqtt:// prefix if present
        if broker_url.startswith("mqtt://"):
            broker_url = broker_url[7:]
        
        # Split host and port
        if ":" in broker_url:
            parts = broker_url.split(":")
            host = parts[0]
            port = int(parts[1])
        else:
            host = broker_url
            port = 1883  # Default MQTT port
        
        return host, port
    
    def get_progress_topic(self, user_id: str) -> str:
        """Get progress topic for specific user"""
        return f"freecad/progress/{user_id}"
    
    def get_status_topic(self, user_id: str) -> str:
        """Get status topic for specific user"""
        return f"freecad/status/{user_id}"
    
    def __repr__(self) -> str:
        return (
            f"MQTTConfig(broker={self.broker_host}:{self.broker_port}, "
            f"client_id={self.client_id}, qos={self.qos_level})"
        )


# Global configuration instance
_mqtt_config: Optional[MQTTConfig] = None


def get_mqtt_config() -> MQTTConfig:
    """
    Get global MQTT configuration instance
    
    Returns:
        MQTTConfig instance
    """
    global _mqtt_config
    if _mqtt_config is None:
        _mqtt_config = MQTTConfig()
    return _mqtt_config

