import os
from typing import Dict, List


HITL_TOOL_NAMES_DEFAULT = [
    "gmail_draft",
    "telegram_send",
    "linkedin_send",
    "linkedin_send_message",
]


def get_hitl_tool_names() -> List[str]:
    env_val = os.environ.get("PERSONAL_HITL_TOOL_NAMES", "").strip()
    if env_val:
        return [t.strip() for t in env_val.split(",") if t.strip()]
    return HITL_TOOL_NAMES_DEFAULT


PERSONAL_PLATFORM_SCHEMAS: Dict[str, Dict[str, List[Dict[str, str]]]] = {
    "gmail": {
        "fields": [
            {
                "key": "MCP_CONFIG_DIR",
                "label": "MCP Config Dir",
                "placeholder": "~/.gmail-mcp",
                "required": "true",
            },
            {
                "key": "AUTH_SERVER_PORT",
                "label": "OAuth Callback Port",
                "placeholder": "3000",
                "required": "false",
            },
        ]
    },
    "telegram": {
        "fields": [
            {"key": "TG_APP_ID", "label": "App ID", "placeholder": "123456", "required": "true"},
            {
                "key": "TG_API_HASH",
                "label": "API Hash",
                "placeholder": "abcd1234...",
                "required": "true",
            },
            {
                "key": "PHONE_NUMBER",
                "label": "Phone Number",
                "placeholder": "+1 555 000 0000",
                "required": "true",
            },
            {
                "key": "PASSWORD_2FA",
                "label": "2FA Password",
                "placeholder": "Optional",
                "required": "false",
            },
        ]
    },
    "linkedin": {
        "fields": [
            {
                "key": "USER_DATA_DIR",
                "label": "User Data Dir",
                "placeholder": "~/.chatbot-ai/linkedin",
                "required": "false",
            },
            {
                "key": "STORAGE_STATE_PATH",
                "label": "Storage State Path",
                "placeholder": "~/.chatbot-ai/linkedin/storage.json",
                "required": "false",
            },
            {
                "key": "ISOLATED",
                "label": "Isolated Session",
                "placeholder": "true",
                "required": "false",
            },
        ]
    },
}
