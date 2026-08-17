"""alerts/speaker_alert.py — moved unchanged from ppe_system/backend/alerts/speaker_alert.py"""

import threading
import subprocess
import time
import os
from collections import defaultdict

class SpeakerAlert:
    def __init__(self, cooldown_seconds=15):
        self.cooldown  = cooldown_seconds
        self.last_play = defaultdict(float)
        self._lock     = threading.Lock()

        result = subprocess.run(["which", "espeak"], capture_output=True)
        self.has_espeak = result.returncode == 0

        if not self.has_espeak:
            print("[Speaker] espeak not found — installing...")
            os.system("sudo apt install espeak -y")
            self.has_espeak = True

        print("[Speaker] Audio alert ready")

    def alert(self, person_id: int, violation_type: str):
        key = f"{person_id}_{violation_type}"
        now = time.time()

        with self._lock:
            if now - self.last_play[key] < self.cooldown:
                return
            self.last_play[key] = now

        threading.Thread(
            target=self._play,
            args=(person_id, violation_type),
            daemon=True
        ).start()

    def _play(self, person_id: int, violation_type: str):
        messages = {
            "no_helmet":         f"Warning! Person {person_id} is not wearing a helmet!",
            "no_vest":           f"Warning! Person {person_id} is not wearing a safety vest!",
            "no_helmet_no_vest": f"Alert! Person {person_id} has no helmet and no vest!",
        }
        msg = messages.get(violation_type, f"PPE violation detected for person {person_id}")

        try:
            subprocess.run(
                ["espeak", "-v", "en", "-s", "140", "-a", "200", msg],
                timeout=10,
                capture_output=True
            )
        except Exception as e:
            print(f"[Speaker] Error: {e}")
