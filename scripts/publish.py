"""Publie les éléments échus de queue.json : Instagram (post + story) et Page Facebook.

Secrets / variables : IG_TOKEN, PAWS_TOKEN, SCHOLAR_TOKEN (jetons utilisateur système),
<COMPTE>_IG_USER_ID, <COMPTE>_PAGE_ID, PAGES_BASE (URL GitHub Pages du dépôt).
Élément de queue.json :
  {"id", "account": "tvb"|"paws"|"scholar", "type": "reel"|"carousel", "media": [...],
   "caption", "fb_caption" (facultatif), "publish_at" (UTC), "trial", "dry_run",
   "story": true, "facebook": true, "alt_text": [...], "status": "pending"}
Chaque cible (ig, story, fb) est suivie dans "done" ; une cible en échec n'empêche pas les autres.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ACCOUNTS = {
    "tvb": ("IG_TOKEN", "IG_USER_ID", "TVB_PAGE_ID"),
    "paws": ("PAWS_TOKEN", "PAWS_IG_USER_ID", "PAWS_PAGE_ID"),
    "scholar": ("SCHOLAR_TOKEN", "SCHOLAR_IG_USER_ID", "SCHOLAR_PAGE_ID"),
}
BASE = os.environ["PAGES_BASE"].rstrip("/")
GRAPH = "https://graph.facebook.com/v24.0"
QUEUE = "queue.json"
TOKEN = API = IG_ID = PAGE_ID = None


def use_account(name):
    global TOKEN, API, IG_ID, PAGE_ID
    tok_var, id_var, page_var = ACCOUNTS[name]
    TOKEN = (os.environ.get(tok_var) or "").strip()
    if not TOKEN:
        raise RuntimeError(f"secret {tok_var} absent")
    API = "https://graph.instagram.com/v24.0" if TOKEN.startswith("IG") else GRAPH
    IG_ID = os.environ.get(id_var) or "me"
    PAGE_ID = os.environ.get(page_var) or ""


def call(method, path, params=None, token=None, base=None):
    params = dict(params or {})
    params["access_token"] = token or TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{base or API}/{path}"
    req = urllib.request.Request(url + "?" + data.decode()) if method == "GET" else \
        urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')}") from None


def url_of(path):
    return f"{BASE}/{path}"


def media_online(paths):
    for p in paths:
        try:
            req = urllib.request.Request(url_of(p), method="HEAD")
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status != 200:
                    return False
        except Exception:  # noqa: BLE001
            return False
    return True


def wait_ready(container_id, minutes=10):
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        st = call("GET", container_id, {"fields": "status_code,status"})
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id}: {st}")
        time.sleep(10)
    raise RuntimeError(f"container {container_id}: délai dépassé")


def ig_container(item):
    caption = item.get("caption", "")
    if item["type"] == "reel":
        p = {"media_type": "REELS", "video_url": url_of(item["media"][0]),
             "caption": caption, "share_to_feed": "true"}
        if item.get("trial"):
            p["trial_params"] = json.dumps({"graduation_strategy": "MANUAL"})
        cid = call("POST", f"{IG_ID}/media", p)["id"]
        wait_ready(cid)
        return cid
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


def ig_publish(cid):
    mid = call("POST", f"{IG_ID}/media_publish", {"creation_id": cid})["id"]
    return mid, call("GET", mid, {"fields": "permalink"}).get("permalink")


def ig_already_posted(item):
    """Cherche dans les derniers posts du compte un post avec la même légende (évite les doublons
    quand Instagram renvoie une erreur alors que la publication est passée)."""
    head = item.get("caption", "")[:80]
    try:
        posts = call("GET", f"{IG_ID}/media", {"fields": "id,caption,permalink,timestamp", "limit": 10}).get("data", [])
    except Exception:  # noqa: BLE001
        return None
    for m in posts:
        if head and (m.get("caption") or "").startswith(head):
            return m
    return None


BLOCK_MARKERS = ('"code":4,', '"code":9,', '"code":17,', '"code":32,', '"code":613,', "2207051",
                 "request limit", "action est bloquée", "action is blocked")


def is_block(err):
    return any(k in err for k in BLOCK_MARKERS)


def ig_story(item):
    m = item["media"][0]
    p = {"media_type": "STORIES"}
    p["video_url" if m.endswith(".mp4") else "image_url"] = url_of(m)
    cid = call("POST", f"{IG_ID}/media", p)["id"]
    wait_ready(cid)
    return call("POST", f"{IG_ID}/media_publish", {"creation_id": cid})["id"]


def page_token():
    return call("GET", PAGE_ID, {"fields": "access_token"}, base=GRAPH)["access_token"]


def fb_post(item):
    if not PAGE_ID:
        raise RuntimeError("ID de Page Facebook absent")
    ptok = page_token()
    text = item.get("fb_caption") or item.get("caption", "")
    if item["type"] == "reel":
        start = call("POST", f"{PAGE_ID}/video_reels", {"upload_phase": "start"}, token=ptok, base=GRAPH)
        vid = start["video_id"]
        req = urllib.request.Request(start.get("upload_url") or f"https://rupload.facebook.com/video-upload/v24.0/{vid}",
                                     data=b"", method="POST",
                                     headers={"Authorization": f"OAuth {ptok}", "file_url": url_of(item["media"][0])})
        with urllib.request.urlopen(req, timeout=300) as r:
            json.load(r)
        call("POST", f"{PAGE_ID}/video_reels", {"upload_phase": "finish", "video_id": vid,
                                                 "video_state": "PUBLISHED", "description": text},
             token=ptok, base=GRAPH)
        return vid
    ids = [call("POST", f"{PAGE_ID}/photos", {"url": url_of(m), "published": "false"}, token=ptok, base=GRAPH)["id"]
           for m in item["media"]]
    p = {"message": text}
    for i, fid in enumerate(ids):
        p[f"attached_media[{i}]"] = json.dumps({"media_fbid": fid})
    return call("POST", f"{PAGE_ID}/feed", p, token=ptok, base=GRAPH)["id"]


def main():
    queue = json.load(open(QUEUE))
    now = dt.datetime.now(dt.timezone.utc)
    changed = False
    # Compte Instagram bloqué (limite ou « action bloquée ») : plus aucune publication Instagram
    # sur ce compte pendant 24 h, Facebook continue.
    blocked = {}
    for it in queue:
        if it.get("ig_blocked_at"):
            t = dt.datetime.fromisoformat(it["ig_blocked_at"])
            if now - t < dt.timedelta(hours=24):
                acc = it.get("account", "tvb")
                blocked[acc] = max(blocked.get(acc, t), t)
    for item in queue:
        if item.get("status", "pending") != "pending":
            continue
        when = dt.datetime.fromisoformat(item["publish_at"].replace("Z", "+00:00"))
        if when > now:
            continue
        if item["type"] == "check":
            changed = True
            try:
                use_account(item.get("account", "tvb"))
                if item.get("instagram", True):
                    item["ig"] = call("GET", IG_ID, {"fields": "username"}).get("username")
                page = call("GET", PAGE_ID, {"fields": "name"}, token=page_token(), base=GRAPH)
                item.update(status="checked", page=page.get("name"))
            except Exception as e:  # noqa: BLE001
                item.update(status="error", error=str(e)[:500])
            continue
        if not media_online(item["media"]):
            if now - when > dt.timedelta(hours=3):
                item.update(status="error", error="médias introuvables sur GitHub Pages depuis 3 h")
                changed = True
            else:
                print(f"[attente] {item['id']} : médias pas encore en ligne")
            continue
        changed = True
        done = item.setdefault("done", [])
        errors = {}
        try:
            use_account(item.get("account", "tvb"))
        except Exception as e:  # noqa: BLE001
            item.update(status="error", error=str(e))
            continue
        if item.get("dry_run"):
            try:
                item["container_id"] = ig_container(item)
                item["status"] = "checked"
                print(f"[ok] {item['id']} : conteneur prêt, non publié")
            except Exception as e:  # noqa: BLE001
                item.update(status="error", error=str(e)[:500])
            continue
        acc = item.get("account", "tvb")
        ig_on = item.get("instagram", True)
        if ig_on and acc in blocked:
            item["ig_skipped"] = f"Instagram en pause (blocage du {blocked[acc].isoformat()[:16]} UTC)"
            ig_on = False
        targets = (["ig"] + (["story"] if item.get("story") else []) if ig_on else []) + (["fb"] if item.get("facebook") else [])
        for t in targets:
            if t in done:
                continue
            try:
                if t == "ig":
                    prev = ig_already_posted(item) if item.get("attempts") or item.get("errors") else None
                    if prev:
                        item.update(media_id=prev.get("id"), permalink=prev.get("permalink"), note="trouvé déjà publié")
                    else:
                        try:
                            mid, link = ig_publish(ig_container(item))
                            item.update(media_id=mid, permalink=link)
                        except Exception as e:  # noqa: BLE001
                            prev = ig_already_posted(item)
                            if prev:
                                item.update(media_id=prev.get("id"), permalink=prev.get("permalink"),
                                            note=f"erreur à la publication mais post en ligne : {str(e)[:150]}")
                            else:
                                raise
                elif t in ("story",) and acc in blocked:
                    continue
                elif t == "story":
                    item["story_id"] = ig_story(item)
                else:
                    item["fb_id"] = fb_post(item)
                done.append(t)
                print(f"[ok] {item['id']} -> {t}")
            except Exception as e:  # noqa: BLE001
                errors[t] = str(e)[:400]
                print(f"[erreur] {item['id']} -> {t} : {e}", file=sys.stderr)
                if t in ("ig", "story") and is_block(str(e)):
                    item["ig_blocked_at"] = now.isoformat()
                    blocked[acc] = now
                    item["attempts"] = 2  # passe en partial/error à cet essai, aucun nouvel essai Instagram
        if errors:
            item["errors"] = errors
            item["attempts"] = item.get("attempts", 0) + 1
            if item["attempts"] >= 3:
                item["status"] = "partial" if done else "error"
        else:
            item.pop("errors", None)
            item["status"] = "published"
            item["published_at"] = now.isoformat()
    if changed:
        json.dump(queue, open(QUEUE, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
