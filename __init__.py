import logging, os, json, threading, time, fnmatch, subprocess, urllib.request
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, device_registry as dr
from google.cloud import pubsub_v1
from google.api_core.exceptions import NotFound

_LOGGER = logging.getLogger(__name__)
DOMAIN = "viva_renamer"

def setup(hass: HomeAssistant, config: dict):
    _LOGGER.info("VivaJot: Starting Zigbee Service...")
    conf = config.get(DOMAIN)
    if not conf: return False
    
    project_id, install_id = conf.get("project_id"), conf.get("installation_id")
    topic_name, creds_file = conf.get("topic"), conf.get("credentials_file")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = hass.config.path(creds_file)

    # Read entity globs from HA's already-parsed google_pubsub config (single source of truth)
    pubsub_filter = config.get("google_pubsub", {}).get("filter", {})
    hass.data[DOMAIN] = {
        "includes": pubsub_filter.get("include_entity_globs", []),
        "excludes": pubsub_filter.get("exclude_entity_globs", []),
    }
    _LOGGER.info(f"VivaJot: Loaded {len(hass.data[DOMAIN]['includes'])} include globs, {len(hass.data[DOMAIN]['excludes'])} exclude globs from google_pubsub config")

    publisher = pubsub_v1.PublisherClient()
    pub_path = publisher.topic_path(project_id, "vivajot-setup-confirmation")

    def run_listener():
        while True:
            try:
                sub = pubsub_v1.SubscriberClient()
                s_id = f"viva-cmd-{install_id.replace(' ', '_').lower()}"
                s_path, t_path = sub.subscription_path(project_id, s_id), sub.topic_path(project_id, topic_name)
                
                try: sub.get_subscription(request={"subscription": s_path})
                except NotFound: sub.create_subscription(request={"name": s_path, "topic": t_path})

                def callback(msg):
                    try:
                        raw = msg.data.decode("utf-8")
                        data = json.loads(raw)
                        target = data.get("installation_id")
                        # Allow "ALL" broadcast ONLY for emergency_update command
                        is_targeted = (target == install_id)
                        is_emergency_broadcast = (target == "ALL" and data.get("command") == "emergency_update")
                        if is_targeted or is_emergency_broadcast:
                            hass.add_job(process_command, hass, data, publisher, pub_path, raw, install_id)
                        msg.ack()
                    except Exception as e: _LOGGER.error(f"Msg Err: {e}"); msg.ack()

                _LOGGER.info("VivaJot: Listening for Pub/Sub commands...")
                sub.subscribe(s_path, callback=callback).result()
            except Exception as e: 
                _LOGGER.error(f"Listener Crashed: {e}. Reconnecting in 10s...")
                time.sleep(10)

    threading.Thread(target=run_listener, daemon=True).start()
    return True

async def process_command(hass, data, pub, path, req, inst):
    command = data.get("command")

    # --- EMERGENCY FLEET UPDATE ---
    # Bypasses configuration.yaml entirely. Downloads and runs ha_updater.sh
    # directly via Python. Trigger from Google Cloud Console:
    #   Topic: vivajot-setup
    #   Body:  {"command": "emergency_update", "installation_id": "ALL"}
    if command == "emergency_update":
        _LOGGER.warning(f"VivaJot: EMERGENCY UPDATE triggered on {inst}!")
        try:
            updater_url = "https://raw.githubusercontent.com/saas-maker/HomeAssistant/master/ha_updater.sh"
            updater_path = "/tmp/vivajot_update.sh"
            urllib.request.urlretrieve(updater_url, updater_path)
            subprocess.Popen(["bash", updater_path, "--update"])
            _LOGGER.warning(f"VivaJot: Emergency update launched on {inst}. Hub will restart shortly.")
            # Publish confirmation back so you can verify which hubs received the command
            confirm = {"inst_id": inst, "action_type": "EMERGENCY_UPDATE", "status": "LAUNCHED"}
            await hass.async_add_executor_job(
                lambda: pub.publish(path, json.dumps(confirm).encode("utf-8"))
            )
        except Exception as e:
            _LOGGER.error(f"VivaJot: Emergency update FAILED on {inst}: {e}")
        return

    if command == "permit_join":
        duration = min(data.get("duration", 60), 254)
        _LOGGER.info(f"VivaJot: Permitting Zigbee devices to join for {duration} seconds...")
        try:
            await hass.services.async_call("zha", "permit", {"duration": duration})
            _LOGGER.info("VivaJot: ZHA permit join started successfully.")
        except Exception as e:
            _LOGGER.error(f"ZHA Permit Fail: {e}")
        return

    if command == "remove_device":
        eid = data.get("entity_id")
        if eid:
            reg = er.async_get(hass)
            entry = reg.async_get(eid)
            if entry and entry.device_id:
                dev_reg = dr.async_get(hass)
                device = dev_reg.async_get(entry.device_id)
                if device:
                    ieee = next((i[1] for i in device.identifiers if i[0] == "zha"), None)
                    if ieee:
                        try:
                            await hass.services.async_call("zha", "remove", {"ieee": ieee})
                            _LOGGER.info(f"VivaJot: Removed ZHA device {eid} (IEEE: {ieee})")
                        except Exception as e:
                            _LOGGER.error(f"VivaJot: Failed to remove ZHA device {eid}: {e}")
        return

    if command == "migrate_batteries":
        _LOGGER.info(f"VivaJot: Executing one-time fleet battery migration on {inst}...")
        registry = er.async_get(hass)
        dev_map = {}
        for e in registry.entities.values():
            if e.device_id: dev_map.setdefault(e.device_id, []).append(e)
            
        for dev_id, entries in dev_map.items():
            battery = next((e for e in entries if e.entity_id.endswith("_battery")), None)
            if not battery: continue
            
            # Intelligent Parent Selection (Ignore firmware/diagnostic entities)
            valid_parents = [e for e in entries if e.entity_id != battery.entity_id and e.domain not in ('update', 'button')]
            parent = next((e for e in valid_parents if e.name), None) # Priority 1: Renamed entities
            if not parent: parent = next((e for e in valid_parents if e.domain in ('binary_sensor', 'light', 'switch')), None)
            if not parent and valid_parents: parent = valid_parents[0]

            if parent:
                p_name = parent.name or parent.original_name or parent.entity_id.split('.')[-1]
                p_base = parent.entity_id.split('.')[-1]
                b_domain = battery.entity_id.split('.')[0]
                expected_eid = f"{b_domain}.{p_base}_battery"
                expected_name = f"{p_name} Battery"
                if battery.entity_id != expected_eid or battery.name != expected_name:
                    try:
                        registry.async_update_entity(battery.entity_id, new_entity_id=expected_eid, name=expected_name)
                        _LOGGER.info(f"VivaJot: Migrated battery {battery.entity_id} -> {expected_eid}")
                    except Exception as be:
                        _LOGGER.warning(f"VivaJot: Migration fail for {battery.entity_id}: {be}")
        
        # Publish confirmation so you know which hubs successfully ran it
        confirm = {"inst_id": inst, "action_type": "MIGRATE_BATTERIES", "status": "SUCCESS"}
        await hass.async_add_executor_job(lambda: pub.publish(path, json.dumps(confirm).encode("utf-8")))
        return

    eid = data.get("entity_id")
    name = data.get("new_name")
    
    registry = er.async_get(hass)
    
    # 1. ZIGBEE SCAN
    if not eid:
        _LOGGER.info("VivaJot: Scanning Zigbee Hardware...")
        globs = hass.data.get(DOMAIN, {})
        includes = globs.get("includes", [])
        excludes = globs.get("excludes", [])
        for entry in registry.entities.values():
            if entry.platform == 'zha':
                if includes and not any(fnmatch.fnmatch(entry.entity_id, g) for g in includes):
                    continue
                if excludes and any(fnmatch.fnmatch(entry.entity_id, g) for g in excludes):
                    continue

                final_name = entry.name or entry.original_name or entry.entity_id
                await send_to_bq(pub, path, inst, entry.entity_id, final_name, "GET_ALL", "SUCCESS", req, hass)
        return

    # 2. TARGETED COMMANDS
    entry = registry.async_get(eid)
    if entry:
        try:
            if name and name.strip():
                registry.async_update_entity(eid, name=name)
                action, final_name = "RENAME", name
                
                # Automatically rename the sibling battery to match
                if entry.device_id:
                    for sib in registry.entities.values():
                        if sib.device_id == entry.device_id and sib.entity_id != eid and sib.entity_id.endswith("_battery"):
                            try:
                                b_domain = sib.entity_id.split('.')[0]
                                p_base = eid.split('.')[1]
                                new_eid = f"{b_domain}.{p_base}_battery"
                                registry.async_update_entity(sib.entity_id, new_entity_id=new_eid, name=f"{name} Battery")
                                _LOGGER.info(f"VivaJot: Renamed sibling battery {sib.entity_id} to '{name} Battery' and synced entity ID")
                            except Exception as be:
                                _LOGGER.warning(f"VivaJot: Silent fail renaming battery {sib.entity_id}: {be}")
            else:
                action, final_name = "GET_DEVICE", (entry.name or entry.original_name or eid)
            await send_to_bq(pub, path, inst, eid, final_name, action, "SUCCESS", req, hass)
        except Exception as e: _LOGGER.error(f"Op Fail: {e}")
    else: _LOGGER.warning(f"VivaJot: {eid} not found.")

async def send_to_bq(pub, path, inst, eid, full_name, action, status, req, hass):
    parts = full_name.split('.')
    domain = eid.split('.')[0]
    
    # Handle New (No Dot) vs Configured (Dot)
    # FIX: Check if the string is lowercase to verify it is a system entity_id
    if '.' in full_name and not (full_name.islower() and full_name.startswith(domain + ".")):
        f, l = parts[0], parts[1]
    else:
        f, l = full_name, "Unknown"

    payload = {
        "inst_id": inst, "entity_id": eid, "action_type": action,
        "friendly_name": f, "location_name": l, "status": status, "action_request": req
    }
    await hass.async_add_executor_job(lambda: pub.publish(path, json.dumps(payload).encode("utf-8")))