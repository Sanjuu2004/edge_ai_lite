"""alerts/mqtt_publisher.py — moved unchanged from ppe_system/backend/alerts/mqtt_publisher.py"""

import json
import time
import paho.mqtt.client as mqtt

class MQTTPublisher:
    def __init__(self, broker="localhost", port=1883, topic="ppe/alerts", client_id=None):
        self.topic  = topic
        # Unique per instance by default (uuid suffix) -- a fixed shared
        # client_id across multiple running apps (e.g. PPE + Driver +
        # Healthcare each connecting as "ppe_system") causes the broker
        # to evict whichever connected first every time another instance
        # with the same ID connects, producing an endless connect/
        # disconnect flap. Callers (app_factory.py) pass a per-solution
        # ID explicitly so this is deterministic, not just accidentally
        # unique.
        if client_id is None:
            import uuid
            client_id = f"edge_ai_{uuid.uuid4().hex[:8]}"
        self.client = mqtt.Client(client_id=client_id)
        self.connected = False

        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        try:
            self.client.connect(broker, port, keepalive=60)
            self.client.loop_start()
            time.sleep(0.5)
        except Exception as e:
            print(f"[MQTT] Could not connect to broker at {broker}:{port} — {e}")
            print("[MQTT] Alerts will be logged locally only")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            print("[MQTT] Connected to broker")
        else:
            print(f"[MQTT] Connection failed with code {rc}")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        print("[MQTT] Disconnected from broker")

    def publish(self, alert: dict):
        payload = json.dumps({
            "person_id":      alert["person_id"],
            "violation_type": alert["violation_type"],
            "timestamp":      alert["timestamp"],
            "bbox":           alert["bbox"]
        })

        if self.connected:
            result = self.client.publish(self.topic, payload, qos=1)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"[MQTT] Publish failed: {result.rc}")
        else:
            print(f"[MQTT] (offline) Alert: {payload}")

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
