import httpx
from typing import Any, List, Optional, Dict
from sqlalchemy import select
from chatbot_ai_system.database.session import AsyncSessionLocal
from chatbot_ai_system.database.models import SystemSetting
from chatbot_ai_system.config.settings import get_settings

class SettingsManager:
    """Manages dynamic system settings with validation."""

    def __init__(self):
        self.settings = get_settings()

    async def get_setting(self, key: str) -> Any:
        """Get setting from DB, fall back to environment settings."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
            db_setting = result.scalar_one_or_none()
            if db_setting:
                return db_setting.value
            
            # Fallback to pydantic settings
            if hasattr(self.settings, key):
                return getattr(self.settings, key)
            return None

    async def set_setting(self, key: str, value: Any, description: Optional[str] = None):
        """Save setting to DB after validation."""
        # Validation logic based on key
        if key == "ollama_model":
            await self._validate_ollama_model(value)
        elif key == "ollama_base_url":
            # Just basic string validation for now
            if not value.startswith("http"):
                 raise ValueError("Ollama base URL must start with http/https")
        elif key in ["openai_api_key", "anthropic_api_key", "gemini_api_key"]:
            if not value or len(value) < 10:
                raise ValueError(f"Invalid API key for {key}")
        elif key == "default_llm_provider":
            if value not in ["ollama", "openai", "anthropic", "gemini"]:
                raise ValueError(f"Unsupported provider: {value}")
        elif key == "mcp_servers":
             # Value should be a list of MCPServerConfig-like dicts
             await self._validate_mcp_configs(value)
        elif key == "personal_integrations":
            await self._validate_personal_integrations(value)

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
            db_setting = result.scalar_one_or_none()
            
            if db_setting:
                db_setting.value = value
                if description:
                    db_setting.description = description
            else:
                db_setting = SystemSetting(key=key, value=value, description=description)
                db.add(db_setting)
            
            await db.commit()

    async def _validate_ollama_model(self, model_name: str):
        """Check if model exists in Ollama."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.settings.ollama_base_url}/api/tags")
                if response.status_code == 200:
                    models = [m["name"] for m in response.json().get("models", [])]
                    # Check for exact match or model:latest or model:version
                    if model_name not in models:
                        base_model = model_name.split(":")[0]
                        if not any(m.startswith(f"{base_model}:") for m in models) and model_name != "gpt-4o-mini":
                             if not model_name.startswith("gpt-"):
                                raise ValueError(f"Model '{model_name}' not found locally in Ollama. Please 'ollama pull {model_name}' first.")
                else:
                    if not model_name.startswith("gpt-"):
                        raise ValueError(f"Failed to reach Ollama at {self.settings.ollama_base_url}")
        except Exception as e:
            if isinstance(e, ValueError):
                raise
            print(f"Warning: model validation failed: {str(e)}")

    async def _validate_mcp_configs(self, configs: List[Dict[str, Any]]):
        """Validate MCP server configurations."""
        for config in configs:
            required = ["name", "command", "args"]
            for field in required:
                if field not in config:
                    raise ValueError(f"MCP config for '{config.get('name', 'unknown')}' missing required field: {field}")

    async def _validate_personal_integrations(self, value: Dict[str, Any]):
        """Validate personal integrations config."""
        if not isinstance(value, dict):
            raise ValueError("personal_integrations must be a dict")
        for platform, cfg in value.items():
            if not isinstance(cfg, dict):
                raise ValueError(f"personal_integrations.{platform} must be a dict")
            if "fields" in cfg and not isinstance(cfg["fields"], dict):
                raise ValueError(f"personal_integrations.{platform}.fields must be a dict")
            if "permissions" in cfg and not isinstance(cfg["permissions"], dict):
                raise ValueError(f"personal_integrations.{platform}.permissions must be a dict")

    async def get_available_ollama_models(self) -> List[str]:
        """Fetch models installed on the Ollama host."""
        try:
             async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.settings.ollama_base_url}/api/tags")
                if response.status_code == 200:
                    return [m["name"] for m in response.json().get("models", [])]
                return []
        except Exception:
            return []

settings_manager = SettingsManager()
