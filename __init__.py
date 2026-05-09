import logging, os, json, threading, time
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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
                        if data.get("installation_id") == install_id:
                            hass.add_job(process_command, hass, data.get("entity_id"), data.get("new_name"), 
                                        publisher, pub_path, raw, install_id)
                        msg.ack()
                    except Exception as e: _LOGGER.error(f"Msg Err: {e}"); msg.ack()

                _LOGGER.info("VivaJot: Listening for Pub/Sub commands...")
                sub.subscribe(s_path, callback=callback).result()
            except Exception as e: 
                _LOGGER.error(f"Listener Crashed: {e}. Reconnecting in 10s...")
                time.sleep(10)

    threading.Thread(target=run_listener, daemon=True).start()
    return True

async def process_command(hass, eid, name, pub, path, req, inst):
    registry = er.async_get(hass)
    
    # 1. ZIGBEE SCAN (Matches your '15 Devices' screenshot)
    if not eid:
        _LOGGER.info("VivaJot: Scanning Zigbee Hardware...")
        for entry in registry.entities.values():
            # STRICT FILTER: Only Zigbee (zha), Primary (No Category), and Tangible Domains
            if entry.platform == 'zha' and not entry.entity_category:
                if entry.entity_id.split('.')[0] in ['light', 'switch', 'binary_sensor', 'sensor']:
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