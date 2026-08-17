#!/bin/bash
# ==============================================================================
# VivaJot Fleet Updater (ha_updater.sh)
# ==============================================================================
# Provisioning:  ./ha_updater.sh DATA-GARDEN
# Fleet Patch:   ./ha_updater.sh --update
# ==============================================================================

CONFIG_DIR="/config"
BACKUP_DIR="/config/.vivajot_backup"
STORAGE_SNAPSHOT_DIR="/config/.vivajot_backup/storage"
COMPONENT_DIR="/config/custom_components/viva_renamer"
# VIVAJOT_BASE_URL override allows canary-testing from a branch or scratch repo.
BASE_URL="${VIVAJOT_BASE_URL:-https://raw.githubusercontent.com/saas-maker/HomeAssistant/master}"

# Critical HA state that cannot be rebuilt from this repo. zigbee.db holds the
# radio's network state; the .storage files hold integrations and entity names.
STORAGE_FILES=".storage/core.config_entries .storage/core.entity_registry .storage/core.device_registry zigbee.db"

# ------------------------------------------------------------------------------
# SELF-HEAL: recover from a corrupted (zero-length) or missing core.config_entries.
# HA renames the corrupt file aside and boots with an empty registry, so the hub
# stays "alive" while every integration is gone. Restore our last-known-good
# snapshot and restart before that state sticks.
# ------------------------------------------------------------------------------
self_heal() {
    CE="$CONFIG_DIR/.storage/core.config_entries"
    [ -s "$CE" ] && return 0
    [ -s "$STORAGE_SNAPSHOT_DIR/core.config_entries" ] || return 0

    echo "VivaJot: core.config_entries missing or empty — restoring snapshot"
    for f in $STORAGE_FILES; do
        if [ -s "$STORAGE_SNAPSHOT_DIR/$(basename "$f")" ]; then
            cp "$STORAGE_SNAPSHOT_DIR/$(basename "$f")" "$CONFIG_DIR/$f"
        fi
    done
    ha core restart
    exit 0
}

# Keep only the 5 newest HA backups. The nightly backup.create automation makes
# manual backups, which HA never auto-prunes — without this, they slowly fill
# the disk (and a full disk corrupts .storage, the failure we're preventing).
prune_backups() {
    command -v jq &>/dev/null || return 0
    ha backups list --raw-json 2>/dev/null \
        | jq -r '.data.backups | sort_by(.date) | reverse | .[5:] | .[].slug' 2>/dev/null \
        | while read -r slug; do
            [ -n "$slug" ] && ha backups remove "$slug"
        done
}

# Snapshot critical state so self_heal has something to restore.
# Only overwrite the snapshot with non-empty source files.
snapshot_storage() {
    mkdir -p "$STORAGE_SNAPSHOT_DIR"
    for f in $STORAGE_FILES; do
        if [ -s "$CONFIG_DIR/$f" ]; then
            cp "$CONFIG_DIR/$f" "$STORAGE_SNAPSHOT_DIR/$(basename "$f")" 2>/dev/null || true
        fi
    done
}

# ------------------------------------------------------------------------------
# YAML SYNTAX VALIDATOR
# ------------------------------------------------------------------------------
validate_config() {
    python3 -c "
import yaml, sys
for t in ['!secret','!include','!include_dir_merge_named','!include_dir_list','!include_dir_named']:
    yaml.SafeLoader.add_constructor(t, lambda l,n: None)
try:
    yaml.safe_load(open('$CONFIG_DIR/configuration.yaml'))
    yaml.safe_load(open('$CONFIG_DIR/automations.yaml'))
    sys.exit(0)
except Exception as e:
    print('YAML INVALID: ' + str(e))
    sys.exit(1)
"
    if [ $? -ne 0 ]; then return 1; fi

    if [ -f "$COMPONENT_DIR/__init__.py" ]; then
        python3 -m py_compile "$COMPONENT_DIR/__init__.py" || return 1
    fi
    return 0
}

if [ "$1" == "--update" ]; then
    self_heal
    mkdir -p "$BACKUP_DIR"
    cp "$CONFIG_DIR/configuration.yaml"  "$BACKUP_DIR/configuration.yaml"  2>/dev/null || true
    cp "$CONFIG_DIR/automations.yaml"    "$BACKUP_DIR/automations.yaml"    2>/dev/null || true
    cp "$CONFIG_DIR/templates.yaml"      "$BACKUP_DIR/templates.yaml"      2>/dev/null || true
    cp "$COMPONENT_DIR/__init__.py"      "$BACKUP_DIR/__init__.py"         2>/dev/null || true
    cp "$COMPONENT_DIR/manifest.json"    "$BACKUP_DIR/manifest.json"       2>/dev/null || true

    fail=0
    curl -sLf "$BASE_URL/configuration.yaml" -o "$CONFIG_DIR/configuration.yaml" || fail=1
    curl -sLf "$BASE_URL/__init__.py"        -o "$COMPONENT_DIR/__init__.py"     || fail=1
    curl -sLf "$BASE_URL/automations.yaml"   -o "$CONFIG_DIR/automations.yaml"   || fail=1
    curl -sLf "$BASE_URL/templates.yaml"     -o "$CONFIG_DIR/templates.yaml"     || fail=1
    curl -sLf "$BASE_URL/manifest.json"      -o "$COMPONENT_DIR/manifest.json"   || fail=1

    if [ "$fail" == "1" ]; then
        cp "$BACKUP_DIR/configuration.yaml" "$CONFIG_DIR/configuration.yaml" 2>/dev/null || true
        cp "$BACKUP_DIR/automations.yaml"   "$CONFIG_DIR/automations.yaml"   2>/dev/null || true
        cp "$BACKUP_DIR/templates.yaml"     "$CONFIG_DIR/templates.yaml"     2>/dev/null || true
        cp "$BACKUP_DIR/__init__.py"        "$COMPONENT_DIR/__init__.py"     2>/dev/null || true
        cp "$BACKUP_DIR/manifest.json"      "$COMPONENT_DIR/manifest.json"   2>/dev/null || true
        exit 1
    fi

    if validate_config; then
        # Enforce 15-second unavailability timeout for mains-powered Zigbee devices
        # (lights use wall switches — bulb loses power, HA must detect offline quickly)
        # Gated: only rewrite core.config_entries when the value actually needs
        # changing, and only swap the file in if the result is valid non-empty JSON.
        # Rewriting this file under a live HA is a corruption risk — minimize it.
        ZHA_CONFIG="$CONFIG_DIR/.storage/core.config_entries"
        if [ -s "$ZHA_CONFIG" ] && command -v jq &>/dev/null; then
            current=$(jq '[.data.entries[] | select(.domain=="zha") | .options.custom_configuration.zha_options.consider_unavailable_mains] | first' "$ZHA_CONFIG" 2>/dev/null)
            if [ "$current" != "15" ]; then
                jq '(.data.entries[] | select(.domain=="zha") | .options.custom_configuration.zha_options.consider_unavailable_mains) = 15' \
                    "$ZHA_CONFIG" > /tmp/zha_config_tmp \
                && [ -s /tmp/zha_config_tmp ] \
                && jq empty /tmp/zha_config_tmp 2>/dev/null \
                && mv /tmp/zha_config_tmp "$ZHA_CONFIG"
            fi
        fi
        snapshot_storage
        prune_backups
        ha core restart
    else
        cp "$BACKUP_DIR/configuration.yaml" "$CONFIG_DIR/configuration.yaml"
        cp "$BACKUP_DIR/automations.yaml"   "$CONFIG_DIR/automations.yaml"   2>/dev/null || true
        cp "$BACKUP_DIR/templates.yaml"     "$CONFIG_DIR/templates.yaml"     2>/dev/null || true
        cp "$BACKUP_DIR/__init__.py"        "$COMPONENT_DIR/__init__.py"     2>/dev/null || true
        cp "$BACKUP_DIR/manifest.json"      "$COMPONENT_DIR/manifest.json"   2>/dev/null || true
    fi

elif [ -n "$1" ]; then
    INSTALL_ID="$1"
    echo "installation_id: \"$INSTALL_ID\"" > "$CONFIG_DIR/secrets.yaml"
    mkdir -p "$COMPONENT_DIR"
    curl -sLf "$BASE_URL/configuration.yaml" -o "$CONFIG_DIR/configuration.yaml"
    curl -sLf "$BASE_URL/__init__.py"        -o "$COMPONENT_DIR/__init__.py"
    curl -sLf "$BASE_URL/manifest.json"      -o "$COMPONENT_DIR/manifest.json"
    curl -sLf "$BASE_URL/automations.yaml"   -o "$CONFIG_DIR/automations.yaml"
    curl -sLf "$BASE_URL/templates.yaml"     -o "$CONFIG_DIR/templates.yaml"
    echo "Hub provisioned as: $INSTALL_ID"
else
    echo "Usage: ./ha_updater.sh <INSTALLATION-ID>  or  ./ha_updater.sh --update"
    exit 1
fi