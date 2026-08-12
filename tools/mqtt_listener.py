"""
MQTT Listener - Subscribes to all topics and logs all messages.
Reads MQTT_BROKER from .env file.

Usage:
    python tools/mqtt_listener.py
"""

import os
import sys
import json
import logging
from datetime import datetime

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MQTT] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("mqtt_listener")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

MQTT_BROKER_URL = os.getenv("MQTT_BROKER", "mqtt://localhost:1883")

# Parse  mqtt://host:port  or  mqtt://host
def _parse_broker(url: str) -> tuple[str, int]:
    url = url.strip()
    if "://" in url:
        url = url.split("://", 1)[1]
    if ":" in url:
        host, port_str = url.rsplit(":", 1)
        return host, int(port_str)
    return url, 1883

BROKER_HOST, BROKER_PORT = _parse_broker(MQTT_BROKER_URL)
SUBSCRIBE_TOPIC = "#"          # wildcard – listen to ALL topics

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        logger.info("Connected to broker %s:%s", BROKER_HOST, BROKER_PORT)
        client.subscribe(SUBSCRIBE_TOPIC)
        logger.info("Subscribed to topic: %s", SUBSCRIBE_TOPIC)
    else:
        logger.error("Connection failed – reason code: %s", reason_code)


def on_disconnect(client, userdata, disconnect_flags, reason_code=None, properties=None):
    logger.warning("Disconnected from broker (reason_code=%s). Reconnecting …", reason_code)


def on_message(client, userdata, msg: mqtt.MQTTMessage):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    topic = msg.topic
    qos = msg.qos
    retained = msg.retain

    # Try to pretty-print JSON, fall back to raw string
    raw = msg.payload.decode("utf-8", errors="replace")
    try:
        payload = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, ValueError):
        payload = raw

    separator = "─" * 60
    print(
        f"\n{separator}\n"
        f"  Time     : {timestamp}\n"
        f"  Topic    : {topic}\n"
        f"  QoS      : {qos}  |  Retained: {retained}\n"
        f"  Payload  :\n{payload}\n"
        f"{separator}"
    )


def on_subscribe(client, userdata, mid, reason_codes, properties=None):
    granted = [str(rc) for rc in reason_codes]
    logger.info("Subscription confirmed (mid=%s, granted QoS=%s)", mid, granted)


def on_log(client, userdata, level, buf):
    logger.debug("[paho] %s", buf)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("Starting MQTT listener …")
    logger.info("Broker : %s:%s", BROKER_HOST, BROKER_PORT)
    logger.info("Topic  : %s  (all topics)", SUBSCRIBE_TOPIC)

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="tolery-mqtt-listener",
        clean_session=True,
    )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.on_subscribe = on_subscribe
    # Uncomment to see low-level paho logs:
    # client.on_log = on_log

    client.reconnect_delay_set(min_delay=2, max_delay=30)

    try:
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        logger.info("Interrupted by user – shutting down.")
        client.disconnect()
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
