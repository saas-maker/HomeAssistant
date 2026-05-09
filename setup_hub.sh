#!/bin/bash
# ==============================================================================
# VivaJot Fleet Updater (setup_hub.sh)
# ==============================================================================
# Provisioning:  ./setup_hub.sh DATA-GARDEN
# Fleet Patch:   ./setup_hub.sh --update
# ==============================================================================

CONFIG_DIR="/config"
BACKUP_DIR="/config/.vivajot_backup"
COMPONENT_DIR="/config/custom_components/viva_renamer"
BASE_URL="https://vivajot.com/Plugins/VivaJot/HomeAssistant"

# ------------------------------------------------------------------------------
# YAML SYNTAX VALIDATOR - Uses Python (guaranteed available in HA container)
# Catches broken indentation, missing colons, bad characters - the errors that
# brick a hub. Handles !secret and !include tags so they don't cause false fails.
# ------------------------------------------------------------------------------
validate_config() {
    python3 -c "
import yaml, sys
for t in ['!secret','!include','!include_dir_merge_named','!include_dir_list','!include_dir_named']:
    yaml.SafeLoader.add_constructor(t, lambda l,n: None)
try:
    yaml.safe_load(open('$CONFIG_DIR/configuration.yaml'))
    sys.exit(0)
except Exception as e:
    print('CONFIG INVALID: ' + str(e))
    sys.exit(1)
"
}

if [ "$1" == "--update" ]; then
    # ==================================================================
    # PATCH MODE - Safe fleet update with validation and rollback
    # ==================================================================

    # 1. Backup current working config
    mkdir -p "$BACKUP_DIR"
    cp "$CONFIG_DIR/configuration.yaml"  "$BACKUP_DIR/configuration.yaml"  2>/dev/null || true
    cp "$CONFIG_DIR/automations.yaml"    "$BACKUP_DIR/automations.yaml"    2>/dev/null || true
    cp "$COMPONENT_DIR/__init__.py"      "$BACKUP_DIR/__init__.py"         2>/dev/null || true

    # 2. Download new files (NEVER downloads secrets.yaml)
    curl -sLf "$BASE_URL/configuration.yaml" -o "$CONFIG_DIR/configuration.yaml"  || {
        cp "$BACKUP_DIR/configuration.yaml" "$CONFIG_DIR/configuration.yaml"
        exit 1
    }
    curl -sLf "$BASE_URL/__init__.py"        -o "$COMPONENT_DIR/__init__.py"      2>/dev/null || true
    curl -sLf "$BASE_URL/automations.yaml"   -o "$CONFIG_DIR/automations.yaml"    2>/dev/null || true

    # 3. VALIDATE before committing
    if validate_config; then
        # Config is valid - safe to restart
        ha core restart
    else
        # Config is BROKEN - restore backup, do NOT restart
        cp "$BACKUP_DIR/configuration.yaml" "$CONFIG_DIR/configuration.yaml"
        cp "$BACKUP_DIR/automations.yaml"   "$CONFIG_DIR/automations.yaml"   2>/dev/null || true
        cp "$BACKUP_DIR/__init__.py"        "$COMPONENT_DIR/__init__.py"     2>/dev/null || true
    fi

elif [ -n "$1" ]; then
    # ==================================================================
    # PROVISION MODE - First-time hub setup
    # ==================================================================
    INSTALL_ID="$1"

    # Stamp unique identity
    echo "installation_id: \"$INSTALL_ID\"" > "$CONFIG_DIR/secrets.yaml"

    # Create custom component directory
    mkdir -p "$COMPONENT_DIR"

    # Download all shared files
    curl -sLf "$BASE_URL/configuration.yaml"                           -o "$CONFIG_DIR/configuration.yaml"
    curl -sLf "$BASE_URL/__init__.py"                                  -o "$COMPONENT_DIR/__init__.py"
    curl -sLf "$BASE_URL/manifest.json"                                -o "$COMPONENT_DIR/manifest.json"
    curl -sLf "$BASE_URL/automations.yaml"                             -o "$CONFIG_DIR/automations.yaml"
    curl -sLf "$BASE_URL/saas-maker-api-project-5b20c116b047.json"     -o "$CONFIG_DIR/saas-maker-api-project-5b20c116b047.json"

    echo "Hub provisioned as: $INSTALL_ID"
else
    echo "Usage: ./setup_hub.sh <INSTALLATION-ID>  or  ./setup_hub.sh --update"
    exit 1
fi