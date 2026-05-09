#!/bin/bash
# ==============================================================================
# VivaJot Fleet Updater (ha_updater.sh)
# ==============================================================================
# Provisioning:  ./ha_updater.sh DATA-GARDEN
# Fleet Patch:   ./ha_updater.sh --update
# ==============================================================================

CONFIG_DIR="/config"
BACKUP_DIR="/config/.vivajot_backup"
COMPONENT_DIR="/config/custom_components/viva_renamer"
BASE_URL="https://raw.githubusercontent.com/saas-maker/HomeAssistant/master"

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
    mkdir -p "$BACKUP_DIR"
    cp "$CONFIG_DIR/configuration.yaml"  "$BACKUP_DIR/configuration.yaml"  2>/dev/null || true
    cp "$CONFIG_DIR/automations.yaml"    "$BACKUP_DIR/automations.yaml"    2>/dev/null || true
    cp "$COMPONENT_DIR/__init__.py"      "$BACKUP_DIR/__init__.py"         2>/dev/null || true
    cp "$COMPONENT_DIR/manifest.json"    "$BACKUP_DIR/manifest.json"       2>/dev/null || true

    fail=0
    curl -sLf "$BASE_URL/configuration.yaml" -o "$CONFIG_DIR/configuration.yaml" || fail=1
    curl -sLf "$BASE_URL/__init__.py"        -o "$COMPONENT_DIR/__init__.py"     || fail=1
    curl -sLf "$BASE_URL/automations.yaml"   -o "$CONFIG_DIR/automations.yaml"   || fail=1
    curl -sLf "$BASE_URL/manifest.json"      -o "$COMPONENT_DIR/manifest.json"   || fail=1

    if [ "$fail" == "1" ]; then
        cp "$BACKUP_DIR/configuration.yaml" "$CONFIG_DIR/configuration.yaml" 2>/dev/null || true
        cp "$BACKUP_DIR/automations.yaml"   "$CONFIG_DIR/automations.yaml"   2>/dev/null || true
        cp "$BACKUP_DIR/__init__.py"        "$COMPONENT_DIR/__init__.py"     2>/dev/null || true
        cp "$BACKUP_DIR/manifest.json"      "$COMPONENT_DIR/manifest.json"   2>/dev/null || true
        exit 1
    fi

    if validate_config; then
        ha core restart
    else
        cp "$BACKUP_DIR/configuration.yaml" "$CONFIG_DIR/configuration.yaml"
        cp "$BACKUP_DIR/automations.yaml"   "$CONFIG_DIR/automations.yaml"   2>/dev/null || true
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
    echo "Hub provisioned as: $INSTALL_ID"
else
    echo "Usage: ./ha_updater.sh <INSTALLATION-ID>  or  ./ha_updater.sh --update"
    exit 1
fi