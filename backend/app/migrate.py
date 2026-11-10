"""Idempotent schema setup for the Fabric SQL database.

Safe to run on an empty database and on the existing one: it creates missing tables, adds missing columns and
publishes version 1 of every agent that has never been published. Runs at startup when AUTO_MIGRATE=true, or by
hand:  python -m app.migrate
"""
import json
import logging

from . import db

log = logging.getLogger("migrate")

ID = "NVARCHAR(36) NOT NULL PRIMARY KEY"
REF = "NVARCHAR(36) NULL"

TABLES = {
    "tenants": f"""id {ID}, name NVARCHAR(200) NOT NULL, [plan] NVARCHAR(30) NULL, status NVARCHAR(20) NULL,
        country NVARCHAR(10) NULL, timezone NVARCHAR(60) NULL, minutes_quota INT NULL, max_concurrent_calls INT NULL,
        created_at DATETIME2 NULL""",
    "users": f"""id {ID}, tenant_id {REF}, email NVARCHAR(200) NOT NULL, name NVARCHAR(200) NULL, role NVARCHAR(20) NULL,
        password_hash NVARCHAR(300) NULL, is_superadmin BIT NULL, is_active BIT NULL, created_at DATETIME2 NULL,
        last_login_at DATETIME2 NULL""",
    "agents": f"""id {ID}, tenant_id {REF}, name NVARCHAR(200) NULL, first_message NVARCHAR(MAX) NULL,
        system_prompt NVARCHAR(MAX) NULL, language NVARCHAR(20) NULL, auto_detect_language BIT NULL,
        voice_type NVARCHAR(30) NULL, voice_name NVARCHAR(120) NULL, voice_rate FLOAT NULL,
        speaker_profile_id NVARCHAR(100) NULL, model NVARCHAR(60) NULL, temperature FLOAT NULL,
        max_call_seconds INT NULL, transfer_number NVARCHAR(40) NULL, end_call_enabled BIT NULL,
        interrupt_sensitivity NVARCHAR(10) NULL, status NVARCHAR(20) NULL, created_at DATETIME2 NULL,
        updated_at DATETIME2 NULL""",
    "agent_tools": f"""id {ID}, tenant_id {REF}, agent_id {REF}, name NVARCHAR(64) NOT NULL, description NVARCHAR(MAX) NULL,
        method NVARCHAR(10) NULL, url NVARCHAR(2000) NULL, headers_json NVARCHAR(MAX) NULL,
        parameters_json NVARCHAR(MAX) NULL, wait_message NVARCHAR(500) NULL, timeout_sec INT NULL, enabled BIT NULL,
        created_at DATETIME2 NULL""",
    "agent_versions": f"""id {ID}, tenant_id {REF}, agent_id {REF}, version INT NOT NULL, config_json NVARCHAR(MAX) NOT NULL,
        note NVARCHAR(500) NULL, created_by {REF}, created_at DATETIME2 NULL""",
    "phone_numbers": f"""id {ID}, tenant_id {REF}, provider NVARCHAR(20) NULL, number NVARCHAR(40) NOT NULL,
        label NVARCHAR(200) NULL, agent_id {REF}, country NVARCHAR(10) NULL, status NVARCHAR(20) NULL,
        created_at DATETIME2 NULL""",
    "provider_credentials": f"""id {ID}, tenant_id {REF}, provider NVARCHAR(20) NULL, account_id NVARCHAR(300) NULL,
        auth_token_enc NVARCHAR(MAX) NULL, updated_at DATETIME2 NULL""",
    "calls": f"""id {ID}, tenant_id {REF}, agent_id {REF}, phone_number_id {REF}, campaign_id {REF},
        provider NVARCHAR(20) NULL, direction NVARCHAR(20) NULL, from_number NVARCHAR(60) NULL,
        to_number NVARCHAR(200) NULL, status NVARCHAR(20) NULL, provider_call_id NVARCHAR(200) NULL,
        started_at DATETIME2 NULL, answered_at DATETIME2 NULL, ended_at DATETIME2 NULL, duration_sec INT NULL,
        cost_usd FLOAT NULL, transferred BIT NULL, error NVARCHAR(1000) NULL, summary NVARCHAR(MAX) NULL,
        sentiment NVARCHAR(20) NULL, outcome NVARCHAR(100) NULL, language NVARCHAR(30) NULL""",
    "call_transcripts": f"""id {ID}, call_id {REF}, role NVARCHAR(20) NULL, text NVARCHAR(MAX) NULL, offset_ms INT NULL,
        created_at DATETIME2 NULL""",
    "knowledge_docs": f"""id {ID}, tenant_id {REF}, agent_id {REF}, name NVARCHAR(300) NULL, source_type NVARCHAR(20) NULL,
        content NVARCHAR(MAX) NULL, chars INT NULL, created_at DATETIME2 NULL""",
    "knowledge_chunks": f"""id {ID}, doc_id {REF}, tenant_id {REF}, agent_id {REF}, seq INT NULL, text NVARCHAR(MAX) NULL,
        embedding VARBINARY(MAX) NULL, created_at DATETIME2 NULL""",
    "voices": f"""id {ID}, tenant_id {REF}, name NVARCHAR(200) NULL, locale NVARCHAR(20) NULL, talent_name NVARCHAR(200) NULL,
        company_name NVARCHAR(200) NULL, consent_path NVARCHAR(500) NULL, sample_path NVARCHAR(500) NULL,
        status NVARCHAR(30) NULL, speaker_profile_id NVARCHAR(100) NULL, error NVARCHAR(1000) NULL,
        review_note NVARCHAR(500) NULL, created_by {REF}, created_at DATETIME2 NULL""",
    "api_keys": f"""id {ID}, tenant_id {REF}, name NVARCHAR(200) NULL, key_prefix NVARCHAR(20) NULL,
        key_hash NVARCHAR(100) NULL, revoked BIT NULL, created_at DATETIME2 NULL, last_used_at DATETIME2 NULL""",
    "webhooks": f"""id {ID}, tenant_id {REF}, url NVARCHAR(2000) NULL, events NVARCHAR(300) NULL, secret NVARCHAR(100) NULL,
        active BIT NULL, created_at DATETIME2 NULL, last_status NVARCHAR(20) NULL, last_fired_at DATETIME2 NULL""",
    "audit_logs": f"""id {ID}, tenant_id {REF}, user_id {REF}, action NVARCHAR(100) NULL, detail NVARCHAR(1000) NULL,
        created_at DATETIME2 NULL""",
    "campaigns": f"""id {ID}, tenant_id {REF}, name NVARCHAR(200) NULL, agent_id {REF}, phone_number_id {REF},
        status NVARCHAR(20) NULL, concurrency INT NULL, total INT NULL, completed INT NULL, failed INT NULL,
        created_at DATETIME2 NULL, started_at DATETIME2 NULL""",
    "campaign_contacts": f"""id {ID}, campaign_id {REF}, phone NVARCHAR(40) NULL, name NVARCHAR(200) NULL,
        status NVARCHAR(20) NULL, attempts INT NULL, call_id {REF}""",
}

# Columns added on top of the original schema: (table, column, definition)
COLUMNS = [
    ("tenants", "webhook_key", "NVARCHAR(64) NULL"),
    ("tenants", "pii_redaction", "BIT NULL"),
    ("tenants", "retention_days", "INT NULL"),
    ("tenants", "timezone", "NVARCHAR(60) NULL"),
    ("tenants", "max_concurrent_calls", "INT NULL"),
    # agents - call handling
    ("agents", "start_speaker", "NVARCHAR(10) NULL"),
    ("agents", "begin_delay_ms", "INT NULL"),
    ("agents", "silence_end_seconds", "INT NULL"),
    ("agents", "reminder_seconds", "INT NULL"),
    ("agents", "reminder_max", "INT NULL"),
    ("agents", "voicemail_detection", "BIT NULL"),
    ("agents", "voicemail_action", "NVARCHAR(20) NULL"),
    ("agents", "voicemail_message", "NVARCHAR(MAX) NULL"),
    ("agents", "ivr_hangup", "BIT NULL"),
    ("agents", "screening_message", "NVARCHAR(1000) NULL"),
    ("agents", "ring_seconds", "INT NULL"),
    ("agents", "dtmf_enabled", "BIT NULL"),
    ("agents", "dtmf_timeout_ms", "INT NULL"),
    ("agents", "dtmf_terminator", "NVARCHAR(2) NULL"),
    ("agents", "dtmf_max_digits", "INT NULL"),
    ("agents", "recording_enabled", "BIT NULL"),
    ("agents", "boosted_keywords", "NVARCHAR(MAX) NULL"),
    ("agents", "transfer_mode", "NVARCHAR(10) NULL"),
    ("agents", "transfer_ring_seconds", "INT NULL"),
    ("agents", "analysis_fields_json", "NVARCHAR(MAX) NULL"),
    ("agents", "variables_json", "NVARCHAR(MAX) NULL"),
    ("agents", "published_json", "NVARCHAR(MAX) NULL"),
    ("agents", "published_version", "INT NULL"),
    ("agents", "published_at", "DATETIME2 NULL"),
    ("agent_tools", "retries", "INT NULL"),
    # calls
    ("calls", "campaign_id", "NVARCHAR(36) NULL"),
    ("calls", "ended_reason", "NVARCHAR(40) NULL"),
    ("calls", "in_voicemail", "BIT NULL"),
    ("calls", "call_successful", "BIT NULL"),
    ("calls", "analysis_json", "NVARCHAR(MAX) NULL"),
    ("calls", "variables_json", "NVARCHAR(MAX) NULL"),
    ("calls", "metadata_json", "NVARCHAR(MAX) NULL"),
    ("calls", "usage_json", "NVARCHAR(MAX) NULL"),
    ("calls", "cost_ai_usd", "FLOAT NULL"),
    ("calls", "cost_telephony_usd", "FLOAT NULL"),
    ("calls", "recording_path", "NVARCHAR(500) NULL"),
    ("calls", "agent_version", "INT NULL"),
    ("calls", "transfer_target", "NVARCHAR(60) NULL"),
    # knowledge
    ("knowledge_docs", "source_url", "NVARCHAR(2000) NULL"),
    ("knowledge_docs", "chunks", "INT NULL"),
    ("knowledge_docs", "embedded", "BIT NULL"),
    # campaigns
    ("campaigns", "window_start", "NVARCHAR(5) NULL"),
    ("campaigns", "window_end", "NVARCHAR(5) NULL"),
    ("campaigns", "window_days", "NVARCHAR(20) NULL"),
    ("campaigns", "timezone", "NVARCHAR(60) NULL"),
    ("campaigns", "max_attempts", "INT NULL"),
    ("campaigns", "retry_delay_min", "INT NULL"),
    ("campaigns", "finished_at", "DATETIME2 NULL"),
    ("campaign_contacts", "variables_json", "NVARCHAR(MAX) NULL"),
    ("campaign_contacts", "next_attempt_at", "DATETIME2 NULL"),
    ("campaign_contacts", "last_attempt_at", "DATETIME2 NULL"),
    ("campaign_contacts", "last_error", "NVARCHAR(500) NULL"),
    ("webhooks", "last_error", "NVARCHAR(500) NULL"),
]

INDEXES = [
    ("ix_calls_tenant_started", "calls", "tenant_id, started_at"),
    ("ix_calls_status", "calls", "status"),
    ("ix_calls_provider_call", "calls", "provider_call_id"),
    ("ix_transcripts_call", "call_transcripts", "call_id"),
    ("ix_chunks_tenant", "knowledge_chunks", "tenant_id, agent_id"),
    ("ix_cc_campaign_status", "campaign_contacts", "campaign_id, status"),
    ("ix_versions_agent", "agent_versions", "agent_id, version"),
]


def run():
    created, added = 0, 0
    for table, cols in TABLES.items():
        if db.scalar("SELECT OBJECT_ID(?)", f"dbo.{table}") is None:
            db.execute(f"CREATE TABLE dbo.{table} ({cols})")
            created += 1
            log.info("created table %s", table)
    for table, col, definition in COLUMNS:
        if db.scalar("SELECT COL_LENGTH(?, ?)", f"dbo.{table}", col) is None:
            db.execute(f"ALTER TABLE dbo.{table} ADD [{col}] {definition}")
            added += 1
    for name, table, cols in INDEXES:
        try:
            if not db.scalar("SELECT 1 FROM sys.indexes WHERE name = ?", name):
                db.execute(f"CREATE INDEX {name} ON dbo.{table} ({cols})")
        except Exception as e:      # an index is an optimisation - never block startup on it
            log.warning("index %s not created: %s", name, e)
    published = publish_unpublished()
    log.info("migration done: %d tables created, %d columns added, %d agents published", created, added, published)


def publish_unpublished() -> int:
    """Agents created before versioning: publish their current settings as version 1 so live calls keep working."""
    from .services.versions import snapshot
    n = 0
    for a in db.query("SELECT * FROM agents WHERE published_json IS NULL"):
        cfg = snapshot(a)
        db.insert("agent_versions", {"tenant_id": a["tenant_id"], "agent_id": a["id"], "version": 1,
                                     "config_json": json.dumps(cfg, default=str), "note": "Initial version (migration)",
                                     "created_at": db.now()})
        db.update("agents", a["id"], {"published_json": json.dumps(cfg, default=str), "published_version": 1,
                                      "published_at": db.now()})
        n += 1
    return n


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
