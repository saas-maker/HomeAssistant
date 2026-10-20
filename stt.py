"""VivaJot native Google Cloud Speech-to-Text provider for Home Assistant Assist."""

from collections.abc import AsyncIterable
import base64
import io
import json
import logging
import os
import wave
import aiohttp

from google.oauth2 import service_account
import google.auth.transport.requests

from homeassistant.components import stt
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)
_LOADED = False

async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the VivaJot STT platform from YAML."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    _LOGGER.warning("VivaJot: Initializing VivaJot Voice STT entity...")
    async_add_entities([VivaJotSpeechToTextEntity(hass)])

async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the VivaJot STT platform from config entry."""
    async_add_entities([VivaJotSpeechToTextEntity(hass)])


class VivaJotSpeechToTextEntity(stt.SpeechToTextEntity):
    """Native Google Cloud STT entity for VivaJot smart speakers."""

    def __init__(self, hass: HomeAssistant, publisher=None, pub_path=None, install_id=None) -> None:
        """Initialize the VivaJot STT entity."""
        self.hass = hass
        self._publisher = publisher
        self._pub_path = pub_path
        self._install_id = install_id
        self._attr_name = "VivaJot Voice STT"
        self._attr_unique_id = "vivajot_voice_stt"
        self.entity_id = "stt.vivajot_voice_stt"
        self._creds = None
        self._auth_request = None

    def _log_bq_sync(self, status: str, detail: str) -> None:
        """Send STT telemetry to BigQuery Device_Setup_History."""
        if not self._publisher or not self._pub_path or not self._install_id:
            return
        try:
            payload = {
                "inst_id": self._install_id,
                "entity_id": "stt.vivajot_voice_stt",
                "action_type": "VOICE_STT",
                "friendly_name": detail[:120],
                "location_name": "VoicePE",
                "status": status,
                "action_request": detail,
            }
            self._publisher.publish(self._pub_path, json.dumps(payload).encode("utf-8"))
        except Exception:
            pass

    @property
    def supported_languages(self) -> list[str]:
        """Return list of supported languages."""
        return ["en", "en-US", "en-us", "en-GB", "en-gb", "en-CA", "en-ca", "en-AU", "en-au"]

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        """Return list of supported audio formats."""
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        """Return list of supported audio codecs."""
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        """Return list of supported bit rates."""
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        """Return list of supported sample rates."""
        return [
            stt.AudioSampleRates.SAMPLERATE_16000,
            stt.AudioSampleRates.SAMPLERATE_8000,
            stt.AudioSampleRates.SAMPLERATE_44100,
            stt.AudioSampleRates.SAMPLERATE_48000,
        ]

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        """Return list of supported channels."""
        return [stt.AudioChannels.CHANNEL_MONO]

    def _get_token_sync(self) -> str:
        """Fetch or refresh the Google OAuth2 access token synchronously."""
        if self._creds is None:
            creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            if not creds_path or not os.path.exists(creds_path):
                # Search for any project credentials file in /config
                config_dir = self.hass.config.config_dir
                for fname in os.listdir(config_dir):
                    if fname.startswith("saas-maker-api-project") and fname.endswith(".json"):
                        creds_path = os.path.join(config_dir, fname)
                        break
            if not creds_path or not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"Google Cloud credentials JSON not found in {self.hass.config.config_dir}"
                )

            self._creds = service_account.Credentials.from_service_account_file(
                creds_path,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            self._auth_request = google.auth.transport.requests.Request()

        if not self._creds.valid:
            self._creds.refresh(self._auth_request)

        return self._creds.token

    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        """Process incoming audio stream and transcribe via Google Cloud STT."""
        audio_buffer = bytearray()
        async for chunk in stream:
            audio_buffer.extend(chunk)

        if not audio_buffer or len(audio_buffer) < 100:
            _LOGGER.debug(f"VivaJot STT: Audio stream too short ({len(audio_buffer)} bytes)")
            return stt.SpeechResult("", stt.SpeechResultState.ERROR)

        _LOGGER.warning(
            f"VivaJot STT: Transcribing audio stream ({len(audio_buffer)} bytes, "
            f"sample_rate={getattr(metadata, 'sample_rate', 16000)})"
        )

        sample_rate = 16000
        audio_bytes = bytes(audio_buffer)

        # If WAV with RIFF header, parse raw PCM frames and sample rate
        if audio_bytes.startswith(b"RIFF"):
            try:
                with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                    sample_rate = wf.getframerate()
                    audio_bytes = wf.readframes(wf.getnframes())
            except Exception as w_err:
                _LOGGER.debug(f"VivaJot STT: Wave parser fallback: {w_err}")
                if len(audio_bytes) > 44 and audio_bytes[:4] == b"RIFF":
                    audio_bytes = audio_bytes[44:]
        elif hasattr(metadata, "sample_rate") and metadata.sample_rate:
            sample_rate = int(metadata.sample_rate)

        # Acquire Google Cloud OAuth token
        try:
            token = await self.hass.async_add_executor_job(self._get_token_sync)
        except Exception as t_err:
            _LOGGER.error(f"VivaJot STT: Failed to obtain Google Cloud OAuth token: {t_err}")
            return stt.SpeechResult("", stt.SpeechResultState.ERROR)

        lang = getattr(metadata, "language", None) or "en-US"
        if lang == "en":
            lang = "en-US"

        payload = {
            "config": {
                "encoding": "LINEAR16",
                "sampleRateHertz": sample_rate,
                "languageCode": lang,
                "model": "command_and_search",
                "speechContexts": [
                    {
                        "phrases": [
                            "help",
                            "help me",
                            "help me please",
                            "please help",
                            "please help me",
                            "i fell",
                            "i need help",
                            "i have fallen",
                            "i've fallen",
                            "call help",
                            "emergency",
                            "i am hurt",
                            "i'm hurt",
                            "fall alert",
                            "i fell down",
                            "i'm okay",
                            "im okay",
                            "i am okay",
                            "false alarm",
                            "cancel alert",
                            "all clear",
                            "yes",
                            "no",
                        ],
                        "boost": 20.0,
                    }
                ],
            },
            "audio": {
                "content": base64.b64encode(audio_bytes).decode("ascii"),
            },
        }

        url = "https://speech.googleapis.com/v1/speech:recognize"
        session = async_get_clientsession(self.hass)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    _LOGGER.error(f"VivaJot STT: Google API error {resp.status}: {err_text}")
                    await self.hass.async_add_executor_job(
                        self._log_bq_sync, "ERROR", f"HTTP {resp.status}: {err_text[:200]}"
                    )
                    return stt.SpeechResult("", stt.SpeechResultState.ERROR)

                res_json = await resp.json()
                transcripts = []
                for res in res_json.get("results", []):
                    alts = res.get("alternatives", [])
                    if alts and "transcript" in alts[0]:
                        transcripts.append(alts[0]["transcript"])
                text = " ".join(transcripts).strip()
                _LOGGER.warning(f"VivaJot STT: Transcribed successfully -> '{text}'")
                await self.hass.async_add_executor_job(
                    self._log_bq_sync, "SUCCESS", f"Heard: '{text}' ({len(audio_bytes)}b)"
                )
                if text:
                    return stt.SpeechResult(text, stt.SpeechResultState.SUCCESS)
                else:
                    return stt.SpeechResult("", stt.SpeechResultState.ERROR)
        except Exception as req_ex:
            _LOGGER.error(f"VivaJot STT: Recognize request failed: {req_ex}")
            await self.hass.async_add_executor_job(
                self._log_bq_sync, "ERROR", f"Ex: {req_ex}"
            )
            return stt.SpeechResult("", stt.SpeechResultState.ERROR)
