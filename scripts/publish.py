"""Publie sur Instagram (@getvisablueprint) les éléments échus de queue.json.

Variables d'environnement : IG_TOKEN, PAWS_TOKEN, SCHOLAR_TOKEN (secrets GitHub), *_IG_USER_ID, PAGES_BASE.
Chaque élément de queue.json :
  {"id": "2026-11-19-reel-A", "type": "reel" | "carousel", "media": ["queue/2026-11-19/visa-A.mp4"],
   "caption": "...", "publish_at": "2026-11-19T14:00:00Z", "trial": false, "dry_run": false,
   "alt_text": ["...", "..."] (facultatif, carrousel), "status": "pending"}
status passe à "published" (avec media_id et permalink), "checked" (dry_run réussi) ou "error".
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Un compte par marque : "account" dans queue.json (tvb par défaut).
ACCOUNTS = {
    "tvb": ("IG_TOKEN", "IG_USER_ID"),
    "paws": ("PAWS_TOKEN", "PAWS_IG_USER_ID"),
    "scholar": ("SCHOLAR_TOKEN", "SCHOLAR_IG_USER_ID"),
}
BASE = os.environ["PAGES_BASE"].rstrip("/")
TOKEN = API = IG_ID = None


def use_account(name):
    """Sélectionne jeton, hôte de l'API et ID Instagram du compte."""
    global TOKEN, API, IG_ID
    tok_var, id_var = ACCOUNTS[name]
    TOKEN = (os.environ.get(tok_var) or "").strip()
    if not TOKEN:
        raise RuntimeError(f"secret {tok_var} absent")
    # Jeton « IG… » (connexion Instagram) -> graph.instagram.com ; sinon (connexion unifiée) -> graph.facebook.com
    API = "https://graph.instagram.com/v24.0" if TOKEN.startswith("IG") else "https://graph.facebook.com/v24.0"
    IG_ID = os.environ.get(id_var) or "me"


QUEUE = "queue.json"


def call(method, path, params=None):
    params = dict(params or {})
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} -> {e.code}: {body}") from None


def wait_ready(container_id, minutes=10):
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        st = call("GET", container_id, {"fields": "status_code,status"})
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id}: {st}")
        time.sleep(10)
    raise RuntimeError(f"container {container_id}: délai dépassé")


def url_of(path):
    return f"{BASE}/{path}"


def build(item):
    caption = item.get("caption", "")
    if item["type"] == "reel":
        params = {"media_type": "REELS", "video_url": url_of(item["media"][0]),
                  "caption": caption, "share_to_feed": "true"}
        if item.get("trial"):
            params["trial_params"] = json.dumps({"graduation_strategy": "MANUAL"})
        cid = call("POST", f"{IG_ID}/media", params)["id"]
        wait_ready(cid)
        return cid
    if item["type"] == "carousel":
        alts = item.get("alt_text") or []
        children = []
        for i, m in enumerate(item["media"]):
            p = {"image_url": url_of(m), "is_carousel_item": "true"}
            if i < len(alts) and alts[i]:
                p["alt_text"] = alts[i]
            try:
                children.append(call("POST", f"{IG_ID}/media", p)["id"])
            except RuntimeError:
                if "alt_text" not in p:
                    raise
                p.pop("alt_text")
                children.append(call("POST", f"{IG_ID}/media", p)["id"])
        for c in children:
            wait_ready(c, 5)
        cid = call("POST", f"{IG_ID}/media", {"media_type": "CAROUSEL", "children": ",".join(children),
                                        "caption": caption})["id"]
        wait_ready(cid)
        return cid
    raise ValueError(f"type inconnu : {item['type']}")


def main():
    queue = json.load(open(QUEUE))
    now = dt.datetime.now(dt.timezone.utc)
    changed = False
    for item in queue:
        if item.get("status", "pending") != "pending":
            continue
        when = dt.datetime.fromisoformat(item["publish_at"].replace("Z", "+00:00"))
        if when > now:
            continue
        changed = True
        try:
            use_account(item.get("account", "tvb"))
            cid = build(item)
            if item.get("dry_run"):
                item["status"] = "checked"
                item["container_id"] = cid
                print(f"[ok] {item['id']} : conteneur prêt, non publié (dry_run)")
                continue
            mid = call("POST", f"{IG_ID}/media_publish", {"creation_id": cid})["id"]
            info = call("GET", mid, {"fields": "permalink"})
            item.update(status="published", media_id=mid, permalink=info.get("permalink"),
                        published_at=now.isoformat())
            print(f"[ok] {item['id']} publié : {info.get('permalink')}")
        except Exception as e:  # noqa: BLE001
            item["status"] = "error"
            item["error"] = str(e)[:500]
            print(f"[erreur] {item['id']} : {e}", file=sys.stderr)
    if changed:
        json.dump(queue, open(QUEUE, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
