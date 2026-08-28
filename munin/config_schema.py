"""Munin's declared config surface — rendered by the generic desktop panel."""

from plugins.memory.config_schema import (
    KIND_SECRET,
    KIND_TEXT,
    ProviderConfigSchema,
    ProviderField,
)

CONFIG_SCHEMA = ProviderConfigSchema(
    name="munin",
    label="Munin Context Core",
    fields=(
        ProviderField(
            key="MUNIN_API_KEY",
            label="API Key",
            kind=KIND_SECRET,
            description="Munin Context Core API key (munin.kalera.dev).",
        ),
        ProviderField(
            key="MUNIN_PROJECT",
            label="Project ID",
            kind=KIND_TEXT,
            description="Active project id, e.g. proj_hermes-mac-mini-m4.",
        ),
        ProviderField(
            key="MUNIN_BASE_URL",
            label="Base URL",
            kind=KIND_TEXT,
            description="API base (default https://munin.kalera.dev).",
        ),
        ProviderField(
            key="MUNIN_TIMEOUT",
            label="Timeout (s)",
            kind=KIND_TEXT,
            description="Request timeout in seconds (default 30).",
        ),
    ),
)
