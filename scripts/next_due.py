"""Secondes à attendre avant le prochain réveil du robot (-1 = rien en attente)."""
import datetime as dt
import json

CAP = 5 * 3600 + 30 * 60  # une tâche GitHub ne peut pas dépasser 6 h

queue = json.load(open("queue.json"))
now = dt.datetime.now(dt.timezone.utc)
waits = []
for item in queue:
    if item.get("status", "pending") != "pending":
        continue
    when = dt.datetime.fromisoformat(item["publish_at"].replace("Z", "+00:00"))
    secs = (when - now).total_seconds()
    waits.append(600 if secs <= 0 else secs + 60)  # déjà échu (médias absents, nouvel essai) : 10 min
print(int(min(min(waits), CAP)) if waits else -1)
