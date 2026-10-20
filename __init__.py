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

    async def handle_voice_alert(call):
        intent = call.data.get("intent", "help")
        details = call.data.get("details", "")
        device_id = call.data.get("device_id", "")
        target_install_id = install_id

        # Support multi-apartment shared hubs: if speaker device/entity has @APT tag, append :APT
        if ":" not in str(target_install_id):
            try:
                dev_reg = dr.async_get(hass)
                ent_reg = er.async_get(hass)
                check_dev_id = device_id
                if not check_dev_id:
                    sats = [
                        e for e in ent_reg.entities.values()
                        if e.domain == "assist_satellite" and e.device_id
                    ]
                    if len(sats) == 1:
                        check_dev_id = sats[0].device_id
                if check_dev_id:
                    dev = dev_reg.async_get(check_dev_id)
                    dev_name = (dev.name_by_user or dev.name or "") if dev else ""
                    if "@" in dev_name:
                        apt = dev_name.split("@")[-1].strip().split()[0].upper()
                        if apt:
                            target_install_id = f"{target_install_id}:{apt}"
                    else:
                        for entry in er.async_entries_for_device(ent_reg, check_dev_id):
                            ename = entry.name or entry.original_name or ""
                            if "@" in ename:
                                apt = ename.split("@")[-1].strip().split()[0].upper()
                                if apt:
                                    target_install_id = f"{target_install_id}:{apt}"
                                    break
            except Exception as ex:
                _LOGGER.warning(f"VivaJot: Could not inspect device_id for apartment suffix: {ex}")

        def _post_alert():
            try:
                url = "https://vivajot.saasmaker.com/Plugins/VivaJot/alexa_api.ashx?action=voice_alert"
                clean_details = (details or "").replace("'", "").replace('"', "").replace("\\", "")
                payload = json.dumps({
                    "install_id": target_install_id,
                    "intent": intent,
                    "details": clean_details,
                }).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "VivaJot-HA-Hub/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = resp.read().decode("utf-8")
                    _LOGGER.warning(f"VivaJot Voice Alert sent ({intent}) for {target_install_id}: {body}")

                confirm = {
                    "inst_id": target_install_id,
                    "entity_id": "viva_renamer.voice_alert",
                    "action_type": "VOICE_ALERT",
                    "friendly_name": f"Alert:{intent}",
                    "location_name": "VoicePE",
                    "status": "SUCCESS",
                    "action_request": details[:200],
                }
                publisher.publish(pub_path, json.dumps(confirm).encode("utf-8"))
            except Exception as e:
                _LOGGER.error(f"VivaJot Voice Alert failed for {target_install_id}: {e}")
                try:
                    err_confirm = {
                        "inst_id": target_install_id,
                        "entity_id": "viva_renamer.voice_alert",
                        "action_type": "VOICE_ALERT",
                        "friendly_name": f"AlertErr:{intent}",
                        "location_name": "VoicePE",
                        "status": "ERROR",
                        "action_request": str(e)[:200],
                    }
                    publisher.publish(pub_path, json.dumps(err_confirm).encode("utf-8"))
                except Exception:
                    pass

        await hass.async_add_executor_job(_post_alert)

    hass.add_job(hass.services.async_register, DOMAIN, "voice_alert", handle_voice_alert)

    diag_sent = {"done": False}

    async def _auto_adopt_voice_speakers():
        try:
            # 1. Auto-adopt any discovered ESPHome Voice PE smart speakers
            adopted_any = False
            for flow in list(hass.config_entries.flow.async_progress()):
                if flow.get("handler") == "esphome" and flow.get("step_id") in (
                    "discovery_confirm",
                    "reauth_encryption_removed_confirm",
                ):
                    flow_id = flow.get("flow_id")
                    _LOGGER.info(f"VivaJot: Auto-adopting discovered ESPHome Smart Speaker (flow_id={flow_id})")
                    await hass.config_entries.flow.async_configure(flow_id, user_input={})
                    adopted_any = True

            registry = er.async_get(hass)
            speaker_entries = [
                entry for entry in registry.entities.values()
                if entry.entity_id.startswith("assist_satellite.")
            ]
            if adopted_any:
                for entry in speaker_entries:
                    final_name = entry.name or entry.original_name or entry.entity_id
                    await send_to_bq(publisher, pub_path, install_id, entry.entity_id, final_name, "GET_ALL", "SUCCESS", "{}", hass)

            # Only provision Google Cloud STT/TTS and Assist Pipeline on hubs that actually have a smart speaker
            if not speaker_entries and not adopted_any:
                return

            # 1b. Ensure ESPHome Smart Speaker software Mute switch is OFF
            # (vivajot_plug_keepalive previously turned on all switch.* entities, which turned on
            # switch.*_mute on the Voice PE speaker and lit up the half-red Muted LED ring!)
            for entry in registry.entities.values():
                if entry.platform == "esphome" and entry.domain == "switch" and "mute" in entry.entity_id:
                    st = hass.states.get(entry.entity_id)
                    if st and st.state == "on":
                        _LOGGER.warning(f"VivaJot: Turning OFF software mute switch {entry.entity_id} on Smart Speaker")
                        await hass.services.async_call(
                            "switch", "turn_off", {"entity_id": entry.entity_id}, blocking=True
                        )

            # 2. Ensure Google Cloud STT/TTS integration is imported so Voice PE has speech engines
            gc_status = "already_loaded"
            try:
                if not hass.config_entries.async_entries("google_cloud") and not hass.config_entries.flow.async_progress_by_handler("google_cloud"):
                    _LOGGER.info("VivaJot: Auto-importing Google Cloud STT/TTS integration for Voice PE...")
                    res = await hass.config_entries.flow.async_init(
                        "google_cloud",
                        context={"source": "import"},
                        data={"key_file": creds_file, "language": "en-US"},
                    )
                    gc_status = f"imported:{res.get('type')}:{res.get('reason', '')}"
            except Exception as gc_ex:
                gc_status = f"gc_err:{gc_ex}"
                _LOGGER.warning(f"VivaJot: Google Cloud STT/TTS auto-import skipped: {gc_ex}")

            # 3. Ensure the default Assist Pipeline has STT and TTS engines assigned
            pipe_status = "none"
            try:
                from homeassistant.components.assist_pipeline.pipeline import (
                    async_get_pipelines,
                    async_update_pipeline,
                )

                # Register VivaJot native STT entity directly on hubs with a smart speaker
                # (Never uses YAML stt: platform, keeping non-speaker hubs 100% untouched)
                stt_comp = hass.data.get("stt")
                if stt_comp is not None and stt_comp.get_entity("stt.vivajot_voice_stt") is None:
                    try:
                        from .stt import VivaJotSpeechToTextEntity
                        await stt_comp.async_add_entities([
                            VivaJotSpeechToTextEntity(hass, publisher, pub_path, install_id)
                        ])
                        _LOGGER.warning("VivaJot: Registered stt.vivajot_voice_stt on speaker hub")
                    except Exception as stt_ex:
                        _LOGGER.error(f"VivaJot: Failed to register stt.vivajot_voice_stt: {stt_ex}")

                for pipeline in async_get_pipelines(hass):
                    pipe_status = f"{pipeline.name}:stt={pipeline.stt_engine},tts={pipeline.tts_engine}"
                    
                    # Target STT engine is our native VivaJot Voice STT
                    desired_stt = "stt.vivajot_voice_stt"
                    if not (hass.states.get(desired_stt) or any(e.entity_id == desired_stt for e in registry.entities.values())):
                        stt_ents = [e.entity_id for e in registry.entities.values() if e.domain == "stt"]
                        if stt_ents:
                            desired_stt = stt_ents[0]

                    # Target TTS engine & matching language code
                    # (tts.google_translate_en_com requires 'en' or lowercase 'en-us'; uppercase 'en-US' fails pipeline validation!)
                    desired_tts = pipeline.tts_engine
                    if not desired_tts or desired_tts == "none" or not (hass.states.get(desired_tts) or any(e.entity_id == desired_tts for e in registry.entities.values())):
                        tts_ents = [e.entity_id for e in registry.entities.values() if e.domain == "tts"]
                        if tts_ents:
                            desired_tts = next((t for t in tts_ents if "google" in t), tts_ents[0])

                    desired_tts_lang = "en" if (desired_tts and "translate" in desired_tts) else "en-US"

                    if (
                        (desired_stt and pipeline.stt_engine != desired_stt)
                        or (desired_tts and pipeline.tts_engine != desired_tts)
                        or (pipeline.tts_language != desired_tts_lang)
                    ):
                        await async_update_pipeline(
                            hass,
                            pipeline,
                            stt_engine=desired_stt,
                            stt_language="en-US",
                            tts_engine=desired_tts,
                            tts_language=desired_tts_lang,
                            tts_voice=pipeline.tts_voice,
                        )
                        pipe_status = f"updated:stt={desired_stt},tts={desired_tts}"
                        _LOGGER.warning(
                            f"VivaJot: Auto-configured Assist Pipeline '{pipeline.name}' "
                            f"with STT={desired_stt}, TTS={desired_tts}"
                        )
                        await send_to_bq(
                            publisher, pub_path, install_id,
                            speaker_entries[0].entity_id if speaker_entries else "assist_satellite.voice_pe",
                            f"Pipeline Ready ({desired_stt}/{desired_tts})",
                            "VOICE_PIPELINE", "SUCCESS", "{}", hass
                        )
            except Exception as pipe_ex:
                pipe_status = f"pipe_err:{pipe_ex}"
                _LOGGER.debug(f"VivaJot: Assist Pipeline auto-config check skipped: {pipe_ex}")

            if not diag_sent["done"]:
                diag_sent["done"] = True
                sp_dev_id = speaker_entries[0].device_id if speaker_entries else None
                sp_states = {}
                for entry in registry.entities.values():
                    if (sp_dev_id and entry.device_id == sp_dev_id) or entry.domain in ("stt", "tts"):
                        st = hass.states.get(entry.entity_id)
                        sp_states[entry.entity_id] = st.state if st else "unloaded"
                diag_json = json.dumps({"gc": gc_status, "pipe": pipe_status, "entities": sp_states})
                await send_to_bq(
                    publisher, pub_path, install_id,
                    speaker_entries[0].entity_id if speaker_entries else "assist_satellite.voice_pe",
                    "Voice Diagnostics",
                    "VOICE_DIAG", "SUCCESS", diag_json, hass
                )
        except Exception as ex:
            _LOGGER.debug(f"VivaJot: Auto-adopt check skipped: {ex}")

    def run_speaker_adopter():
        time.sleep(15)
        while True:
            try:
                hass.add_job(_auto_adopt_voice_speakers)
            except Exception:
                pass
            time.sleep(25)

    threading.Thread(target=run_speaker_adopter, daemon=True).start()
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
            await hass.async_add_executor_job(urllib.request.urlretrieve, updater_url, updater_path)
            # Same CRLF guard the shell_command path applies with sed: a stray \r
            # makes bash fail with 127 (the June 2026 fleet outage).
            def _strip_cr():
                with open(updater_path, "rb") as fh:
                    data = fh.read()
                with open(updater_path, "wb") as fh:
                    fh.write(data.replace(b"\r\n", b"\n"))
            await hass.async_add_executor_job(_strip_cr)
            # Run the updater to completion (off the event loop), then restart HA
            # ourselves. The script cannot restart anything: the `ha` CLI does not
            # exist inside the Core container, so the old fire-and-forget Popen left
            # updated files unapplied until the 3 AM nightly restart.
            result = await hass.async_add_executor_job(
                lambda: subprocess.run(["bash", updater_path, "--update"], timeout=300)
            )
            _LOGGER.warning(f"VivaJot: Emergency update finished on {inst} (exit {result.returncode}). Restarting Home Assistant.")
            # Publish confirmation back so you can verify which hubs received the command
            confirm = {"inst_id": inst, "action_type": "EMERGENCY_UPDATE", "status": "APPLIED", "exit_code": result.returncode}
            await hass.async_add_executor_job(
                lambda: pub.publish(path, json.dumps(confirm).encode("utf-8"))
            )
            await hass.services.async_call("homeassistant", "restart", blocking=False)
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
            battery = next((e for e in entries if "_battery" in e.entity_id), None)
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
    
    # 1. ZIGBEE & SMART SPEAKER SCAN
    if not eid:
        _LOGGER.info("VivaJot: Scanning Zigbee & Smart Speaker Hardware...")
        globs = hass.data.get(DOMAIN, {})
        includes = globs.get("includes", [])
        excludes = globs.get("excludes", [])
        for entry in registry.entities.values():
            if entry.platform in ('zha', 'esphome') or entry.entity_id.startswith('assist_satellite.'):
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
                        if sib.device_id == entry.device_id and sib.entity_id != eid and "_battery" in sib.entity_id:
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