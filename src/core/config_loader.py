"""
Configuration Loader for TextToCAD Multi-User Resource Pools
- Loads configuration from YAML files
- Supports environment-specific overrides (development, production, testing)
- Environment variable substitution
- Configuration validation
- Hot-reload capability for development
"""

import os
import yaml
import logging
from typing import Dict, Any, Optional, Union
from pathlib import Path
import re
from datetime import datetime

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Configuration-related errors"""
    pass


class ConfigLoader:
    """
    Configuration loader for resource pools
    
    Features:
    - YAML configuration loading
    - Environment-specific overrides
    - Environment variable substitution
    - Configuration validation
    - Hot-reload support
    - Default value handling
    """
    
    def __init__(self, config_path: Optional[str] = None, environment: Optional[str] = None):
        """
        Initialize configuration loader
        
        Args:
            config_path: Path to configuration file (default: config/pool_config.yaml)
            environment: Environment name (development, production, testing)
        """
        # Determine config path
        if config_path is None:
            # Default to config/pool_config.yaml relative to project root
            project_root = Path(__file__).parent.parent.parent
            config_path = project_root / "config" / "pool_config.yaml"
        
        self.config_path = Path(config_path)
        
        # Determine environment
        self.environment = environment or os.getenv("ENVIRONMENT", "development")
        
        # Configuration cache
        self._config_cache: Optional[Dict[str, Any]] = None
        self._last_modified: Optional[float] = None
        
        # Environment variable pattern for substitution
        self._env_var_pattern = re.compile(r'\$\{([^}]+)\}')
        
        logger.info(f"[CONFIG] Initialized ConfigLoader")
        logger.info(f"[CONFIG] Config path: {self.config_path}")
        logger.info(f"[CONFIG] Environment: {self.environment}")
    
    def _substitute_env_vars(self, value: Any) -> Any:
        """
        Recursively substitute environment variables in configuration values
        
        Args:
            value: Configuration value (can be string, dict, list, etc.)
            
        Returns:
            Value with environment variables substituted
        """
        if isinstance(value, str):
            # Find all environment variable references
            def replace_env_var(match):
                env_var = match.group(1)
                # Support default values: ${VAR:default_value}
                if ':' in env_var:
                    var_name, default_value = env_var.split(':', 1)
                    return os.getenv(var_name.strip(), default_value.strip())
                else:
                    env_value = os.getenv(env_var)
                    if env_value is None:
                        logger.warning(f"[CONFIG] Environment variable '{env_var}' not found")
                        return match.group(0)  # Return original if not found
                    return env_value
            
            return self._env_var_pattern.sub(replace_env_var, value)
        
        elif isinstance(value, dict):
            return {k: self._substitute_env_vars(v) for k, v in value.items()}
        
        elif isinstance(value, list):
            return [self._substitute_env_vars(item) for item in value]
        
        else:
            return value
    
    def _load_yaml_file(self) -> Dict[str, Any]:
        """
        Load and parse YAML configuration file
        
        Returns:
            Parsed configuration dictionary
            
        Raises:
            ConfigurationError: If file cannot be loaded or parsed
        """
        try:
            if not self.config_path.exists():
                raise ConfigurationError(f"Configuration file not found: {self.config_path}")
            
            with open(self.config_path, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file)
            
            if config is None:
                raise ConfigurationError("Configuration file is empty or invalid")
            
            logger.debug(f"[CONFIG] Loaded configuration from {self.config_path}")
            return config
            
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Failed to parse YAML configuration: {e}")
        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration file: {e}")
    
    def _apply_environment_overrides(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply environment-specific configuration overrides
        
        Args:
            config: Base configuration dictionary
            
        Returns:
            Configuration with environment overrides applied
        """
        if self.environment in config:
            env_config = config[self.environment]
            logger.debug(f"[CONFIG] Applying {self.environment} environment overrides")
            
            # Deep merge environment configuration
            config = self._deep_merge(config, env_config)
        
        return config
    
    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep merge two dictionaries
        
        Args:
            base: Base dictionary
            override: Override dictionary
            
        Returns:
            Merged dictionary
        """
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def _validate_configuration(self, config: Dict[str, Any]) -> None:
        """
        Validate configuration structure and required fields
        
        Args:
            config: Configuration dictionary to validate
            
        Raises:
            ConfigurationError: If configuration is invalid
        """
        required_sections = ['redis', 'llm_pool', 'rag_pool', 'freecad_pool', 'global']
        
        for section in required_sections:
            if section not in config:
                raise ConfigurationError(f"Required configuration section '{section}' is missing")
        
        # Validate Redis configuration
        redis_config = config['redis']
        if 'url' not in redis_config:
            raise ConfigurationError("Redis URL is required")
        
        # Validate LLM pool configuration
        llm_config = config['llm_pool']
        required_llm_fields = ['min_connections', 'max_connections', 'models']
        for field in required_llm_fields:
            if field not in llm_config:
                raise ConfigurationError(f"LLM pool configuration missing required field: {field}")
        
        # Validate model configurations
        models = llm_config['models']
        if not isinstance(models, dict) or len(models) == 0:
            raise ConfigurationError("At least one LLM model configuration is required")
        
        for model_name, model_config in models.items():
            if 'name' not in model_config:
                raise ConfigurationError(f"Model '{model_name}' missing required 'name' field")
        
        # Validate RAG pool configuration
        rag_config = config['rag_pool']
        required_rag_fields = ['faiss_index_path', 'metadata_json_path', 'embedding_model']
        for field in required_rag_fields:
            if field not in rag_config:
                raise ConfigurationError(f"RAG pool configuration missing required field: {field}")
        
        # Validate FreeCAD pool configuration
        freecad_config = config['freecad_pool']
        required_freecad_fields = ['server_url', 'min_clients', 'max_clients']
        for field in required_freecad_fields:
            if field not in freecad_config:
                raise ConfigurationError(f"FreeCAD pool configuration missing required field: {field}")
        
        logger.debug("[CONFIG] Configuration validation passed")
    
    def _should_reload(self) -> bool:
        """
        Check if configuration file has been modified and should be reloaded
        
        Returns:
            True if configuration should be reloaded
        """
        try:
            current_modified = self.config_path.stat().st_mtime
            
            if self._last_modified is None or current_modified > self._last_modified:
                return True
            
            return False
            
        except Exception as e:
            logger.warning(f"[CONFIG] Failed to check file modification time: {e}")
            return False
    
    def load_config(self, force_reload: bool = False) -> Dict[str, Any]:
        """
        Load configuration with caching and hot-reload support
        
        Args:
            force_reload: Force reload even if cached version exists
            
        Returns:
            Complete configuration dictionary
            
        Raises:
            ConfigurationError: If configuration cannot be loaded or is invalid
        """
        # Check if reload is needed
        if not force_reload and self._config_cache is not None and not self._should_reload():
            return self._config_cache
        
        try:
            logger.info("[CONFIG] Loading configuration...")
            
            # Load base configuration
            config = self._load_yaml_file()
            
            # Apply environment overrides
            config = self._apply_environment_overrides(config)
            
            # Substitute environment variables
            config = self._substitute_env_vars(config)
            
            # Validate configuration
            self._validate_configuration(config)
            
            # Update cache
            self._config_cache = config
            self._last_modified = self.config_path.stat().st_mtime
            
            logger.info("[CONFIG] ✅ Configuration loaded successfully")
            logger.info(f"[CONFIG] Environment: {self.environment}")
            logger.info(f"[CONFIG] Redis URL: {config['redis']['url']}")
            logger.info(f"[CONFIG] LLM Pool: {config['llm_pool']['min_connections']}-{config['llm_pool']['max_connections']} connections")
            logger.info(f"[CONFIG] RAG Pool: {config['rag_pool']['pool_size']} retrievers")
            logger.info(f"[CONFIG] FreeCAD Pool: {config['freecad_pool']['min_clients']}-{config['freecad_pool']['max_clients']} clients")
            
            return config
            
        except Exception as e:
            logger.error(f"[CONFIG] Failed to load configuration: {e}")
            raise ConfigurationError(f"Configuration loading failed: {e}")
    
    def get_section(self, section_name: str, default: Any = None) -> Any:
        """
        Get specific configuration section
        
        Args:
            section_name: Name of configuration section (supports dot notation)
            default: Default value if section not found
            
        Returns:
            Configuration section value
        """
        config = self.load_config()
        
        # Support dot notation (e.g., "llm_pool.models.default")
        keys = section_name.split('.')
        value = config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value
    
    def get_redis_config(self) -> Dict[str, Any]:
        """Get Redis configuration"""
        return self.get_section('redis', {})
    
    def get_llm_pool_config(self) -> Dict[str, Any]:
        """Get LLM pool configuration"""
        return self.get_section('llm_pool', {})
    
    def get_rag_pool_config(self) -> Dict[str, Any]:
        """Get RAG pool configuration"""
        return self.get_section('rag_pool', {})
    
    def get_freecad_pool_config(self) -> Dict[str, Any]:
        """Get FreeCAD pool configuration"""
        return self.get_section('freecad_pool', {})
    
    def get_global_config(self) -> Dict[str, Any]:
        """Get global configuration"""
        return self.get_section('global', {})
    
    def get_model_config(self, model_type: str = 'default') -> Dict[str, Any]:
        """
        Get specific model configuration
        
        Args:
            model_type: Model type (default, advanced, expert)
            
        Returns:
            Model configuration dictionary
        """
        models = self.get_section('llm_pool.models', {})
        return models.get(model_type, models.get('default', {}))
    
    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.environment == 'development'
    
    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.environment == 'production'
    
    def is_testing(self) -> bool:
        """Check if running in testing environment"""
        return self.environment == 'testing'
    
    def get_debug_mode(self) -> bool:
        """Get debug mode setting"""
        return self.get_section('global.debug', False)
    
    def get_log_level(self) -> str:
        """Get logging level"""
        return self.get_section('global.log_level', 'INFO')
    
    def get_metrics_config(self) -> Dict[str, Any]:
        """Get metrics configuration"""
        return {
            'enabled': self.get_section('global.enable_metrics', True),
            'interval': self.get_section('global.metrics_interval', 60),
            'detailed': self.get_section('production.enable_detailed_metrics', False) if self.is_production() else False
        }
    
    def get_health_check_intervals(self) -> Dict[str, int]:
        """Get health check intervals for all services"""
        base_intervals = {
            'redis': self.get_section('redis.health_check_interval', 30),
            'llm': self.get_section('llm_pool.health_check_interval', 60),
            'freecad': self.get_section('freecad_pool.health_check_interval', 120)
        }
        
        # Apply environment-specific overrides
        env_intervals = self.get_section(f'{self.environment}.health_check_intervals', {})
        base_intervals.update(env_intervals)
        
        return base_intervals
    
    def reload_config(self) -> Dict[str, Any]:
        """
        Force reload configuration from file
        
        Returns:
            Reloaded configuration dictionary
        """
        logger.info("[CONFIG] Force reloading configuration...")
        return self.load_config(force_reload=True)
    
    def get_config_summary(self) -> Dict[str, Any]:
        """
        Get configuration summary for monitoring/debugging
        
        Returns:
            Configuration summary dictionary
        """
        config = self.load_config()
        
        return {
            'environment': self.environment,
            'config_path': str(self.config_path),
            'last_loaded': datetime.fromtimestamp(self._last_modified).isoformat() if self._last_modified else None,
            'debug_mode': self.get_debug_mode(),
            'log_level': self.get_log_level(),
            'pools': {
                'llm': {
                    'min_connections': config['llm_pool']['min_connections'],
                    'max_connections': config['llm_pool']['max_connections'],
                    'models': list(config['llm_pool']['models'].keys())
                },
                'rag': {
                    'pool_size': config['rag_pool']['pool_size'],
                    'embedding_model': config['rag_pool']['embedding_model']
                },
                'freecad': {
                    'min_clients': config['freecad_pool']['min_clients'],
                    'max_clients': config['freecad_pool']['max_clients'],
                    'server_url': config['freecad_pool']['server_url']
                }
            },
            'redis': {
                'url': config['redis']['url'],
                'session_ttl': config['redis']['session_ttl']
            }
        }


# Global configuration loader instance
_config_loader_instance: Optional[ConfigLoader] = None


def get_config_loader() -> ConfigLoader:
    """
    Get global configuration loader instance
    
    Returns:
        ConfigLoader instance
    """
    global _config_loader_instance
    
    if _config_loader_instance is None:
        _config_loader_instance = ConfigLoader()
    
    return _config_loader_instance


def get_config() -> Dict[str, Any]:
    """
    Get complete configuration dictionary
    
    Returns:
        Configuration dictionary
    """
    return get_config_loader().load_config()


def get_section(section_name: str, default: Any = None) -> Any:
    """
    Get specific configuration section
    
    Args:
        section_name: Section name (supports dot notation)
        default: Default value if not found
        
    Returns:
        Configuration section value
    """
    return get_config_loader().get_section(section_name, default)