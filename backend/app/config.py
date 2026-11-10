import json
import os

from dotenv import load_dotenv

load_dotenv()


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default) or default


def flag(key: str, default: bool = False) -> bool:
    return env(key, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


class Settings:
    APP_NAME = env("APP_NAME", "UBTI Voice Agent")
    DEV_MODE = flag("DEV_MODE")
    JWT_SECRET = env("JWT_SECRET", "change-me")
    JWT_EXPIRE_HOURS = int(env("JWT_EXPIRE_HOURS", "24"))
    # The JWT_SECRET you used before (e.g. "change-me"): only used to READ provider tokens saved with it.
    OLD_JWT_SECRET = env("OLD_JWT_SECRET")
    # Encrypts stored provider tokens. If empty, a key derived from JWT_SECRET is used (the old behaviour).
    ENCRYPTION_KEY = env("ENCRYPTION_KEY")
    # Signs every callback / media URL we hand to Vobiz, Twilio and ACS. Defaults to a key derived from JWT_SECRET.
    WEBHOOK_SECRET = env("WEBHOOK_SECRET")
    # Temporary switch while you move existing numbers to the new signed webhook URLs.
    ALLOW_UNSIGNED_WEBHOOKS = flag("ALLOW_UNSIGNED_WEBHOOKS")
    # Lets custom functions / knowledge URLs / webhooks reach private or internal addresses (local testing only).
    ALLOW_PRIVATE_URLS = flag("ALLOW_PRIVATE_URLS")

    PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    FRONTEND_ORIGIN = env("FRONTEND_ORIGIN", "http://localhost:5173")
    STATIC_DIR = env("STATIC_DIR", "./static")          # built React app (served by FastAPI in the container)
    UPLOAD_DIR = env("UPLOAD_DIR", "./uploads")          # local storage fallback when no Azure Blob is configured
    TMP_DIR = env("TMP_DIR", "/tmp/ubti")
    ADMIN_EMAIL = env("ADMIN_EMAIL")
    ADMIN_PASSWORD = env("ADMIN_PASSWORD")
    AUTO_MIGRATE = flag("AUTO_MIGRATE", True)

    # ---- database (Microsoft Fabric SQL database) ----
    FABRIC_SQL_SERVER = env("FABRIC_SQL_SERVER")
    FABRIC_SQL_DATABASE = env("FABRIC_SQL_DATABASE")
    ODBC_DRIVER = env("ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    FABRIC_AUTH_MODE = env("FABRIC_AUTH_MODE", "cli")    # cli | sp | msi | interactive
    AZURE_TENANT_ID = env("AZURE_TENANT_ID")
    AZURE_CLIENT_ID = env("AZURE_CLIENT_ID")
    AZURE_CLIENT_SECRET = env("AZURE_CLIENT_SECRET")

    # ---- Redis (optional - needed only when you run more than one replica) ----
    REDIS_URL = env("REDIS_URL")

    # ---- storage for recordings and voice-clone audio ----
    AZURE_STORAGE_CONNECTION_STRING = env("AZURE_STORAGE_CONNECTION_STRING")
    AZURE_STORAGE_CONTAINER = env("AZURE_STORAGE_CONTAINER", "ubti-voice")

    # ---- Azure Voice Live ----
    VL_ENDPOINT = env("AZURE_VOICELIVE_ENDPOINT").rstrip("/")
    VL_KEY = env("AZURE_VOICELIVE_API_KEY")
    VL_MODEL = env("AZURE_VOICELIVE_MODEL", "gpt-realtime")
    VL_API_VERSION = env("AZURE_VOICELIVE_API_VERSION", "2025-10-01")

    # ---- Azure Speech (voice preview + personal voice) ----
    SPEECH_KEY = env("AZURE_SPEECH_KEY")
    SPEECH_REGION = env("AZURE_SPEECH_REGION", "eastus")

    # ---- Azure OpenAI (post-call analysis, transfer summaries, knowledge embeddings) ----
    AOAI_ENDPOINT = env("AZURE_OPENAI_ENDPOINT").rstrip("/")
    AOAI_KEY = env("AZURE_OPENAI_API_KEY")
    AOAI_DEPLOYMENT = env("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    AOAI_EMBED_DEPLOYMENT = env("AZURE_OPENAI_EMBED_DEPLOYMENT")   # e.g. text-embedding-3-small; empty = keyword search
    AOAI_API_VERSION = env("AZURE_OPENAI_API_VERSION", "2024-10-21")

    # ---- knowledge base ----
    KB_INLINE_CHARS = int(env("KB_INLINE_CHARS", "12000"))   # up to this size the whole KB goes into the prompt

    # ---- telephony ----
    VOBIZ_AUTH_ID = env("VOBIZ_AUTH_ID")
    VOBIZ_AUTH_TOKEN = env("VOBIZ_AUTH_TOKEN")
    VOBIZ_API_BASE = env("VOBIZ_API_BASE", "https://api.vobiz.ai/api/v1").rstrip("/")
    TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN")
    # Azure Communication Services (Call Automation: ACS numbers + Direct Routing SIP trunks)
    ACS_CONNECTION_STRING = env("ACS_CONNECTION_STRING")

    # ---- pricing (USD). Override with JSON to match your Azure / carrier price sheet. ----
    PRICING_JSON = env("PRICING_JSON")

    @property
    def WS_BASE(self) -> str:
        return self.PUBLIC_BASE_URL.replace("https://", "wss://").replace("http://", "ws://")

    @property
    def pricing_override(self) -> dict:
        try:
            return json.loads(self.PRICING_JSON) if self.PRICING_JSON else {}
        except Exception:
            return {}


settings = Settings()
