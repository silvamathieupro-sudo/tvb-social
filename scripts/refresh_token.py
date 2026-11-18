"""Prolonge le jeton Instagram de 60 jours (appelé chaque semaine).
Affiche seulement la date d'expiration ; si Meta renvoie un nouveau jeton différent,
le workflow le réenregistre dans les secrets (nécessite le secret GH_PAT)."""
import json, os, urllib.request
tok = os.environ["IG_TOKEN"].strip()
if not tok.startswith("IG"):
    print("Jeton de connexion unifiée : pas de renouvellement nécessaire ici")
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        f.write("changed=false\n")
    raise SystemExit(0)
url = "https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=" + tok
with urllib.request.urlopen(url, timeout=60) as r:
    d = json.load(r)
days = d.get("expires_in", 0) // 86400
print(f"Jeton prolongé : expire dans {days} jours")
new = d.get("access_token", tok)
with open(os.environ["GITHUB_OUTPUT"], "a") as f:
    f.write(f"changed={'true' if new != tok else 'false'}\n")
if new != tok:
    with open("/tmp/new_token", "w") as f:
        f.write(new)
