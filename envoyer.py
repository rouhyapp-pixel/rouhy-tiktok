# -*- coding: utf-8 -*-
"""Envoie le prochain carrousel de la file d'attente en BROUILLON dans la boîte de réception TikTok
(API officielle, Content Posting API, post_mode MEDIA_UPLOAD). Tourne sur GitHub Actions, ordi éteint.

  python3 envoyer.py              -> envoie si un créneau de config.json (heures) est passé et pas encore servi aujourd'hui
  python3 envoyer.py --maintenant -> envoie le prochain tout de suite
  python3 envoyer.py --essai      -> montre ce qui serait envoyé, n'appelle pas TikTok

Variables d'environnement (secrets GitHub) : TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_CLE_JETONS.
Les jetons TikTok sont gardés chiffrés dans jetons.enc (illisibles sans TIKTOK_CLE_JETONS)."""
import json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ICI = os.path.dirname(os.path.abspath(__file__))
PARIS = ZoneInfo('Europe/Paris')
API = 'https://open.tiktokapis.com/v2'


def lire(nom, defaut):
    p = os.path.join(ICI, nom)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else defaut


def ecrire(nom, donnees):
    with open(os.path.join(ICI, nom), 'w', encoding='utf-8') as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2)
        f.write('\n')


def env(nom):
    v = os.environ.get(nom, '').strip()
    if not v:
        sys.exit(f'{nom} manquante')
    return v


def openssl(args, entree):
    r = subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-a', '-A', *args,
                        '-pass', 'env:TIKTOK_CLE_JETONS'], input=entree, capture_output=True)
    if r.returncode:
        sys.exit('chiffrement des jetons impossible (mauvaise TIKTOK_CLE_JETONS ?)')
    return r.stdout


def jetons_lire():
    p = os.path.join(ICI, 'jetons.enc')
    if not os.path.exists(p):
        sys.exit('jetons.enc absent : lancer tiktok_connecter.py une fois')
    return json.loads(openssl(['-d'], open(p, 'rb').read()))


def jetons_ecrire(j):
    with open(os.path.join(ICI, 'jetons.enc'), 'wb') as f:
        f.write(openssl([], json.dumps(j).encode()))


def requete(url, corps=None, jeton=None, formulaire=False):
    entetes = {}
    data = None
    if formulaire:
        data = urllib.parse.urlencode(corps).encode()
        entetes['Content-Type'] = 'application/x-www-form-urlencoded'
    elif corps is not None:
        data = json.dumps(corps).encode()
        entetes['Content-Type'] = 'application/json; charset=UTF-8'
    if jeton:
        entetes['Authorization'] = f'Bearer {jeton}'
    req = urllib.request.Request(url, data=data, headers=entetes, method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b'{}') | {'_http': e.code}


def jeton_frais():
    """Renouvelle l'accès (valable 24 h) avec le refresh token (valable 1 an), et garde le nouveau."""
    j = jetons_lire()
    r = requete(f'{API}/oauth/token/', {
        'client_key': env('TIKTOK_CLIENT_KEY'), 'client_secret': env('TIKTOK_CLIENT_SECRET'),
        'grant_type': 'refresh_token', 'refresh_token': j['refresh_token']}, formulaire=True)
    if 'access_token' not in r:
        sys.exit(f"renouvellement refusé par TikTok : {r.get('error')} {r.get('error_description', '')} -> relancer tiktok_connecter.py")
    j.update(refresh_token=r.get('refresh_token', j['refresh_token']), renouvele=datetime.now(PARIS).isoformat(timespec='minutes'))
    jetons_ecrire(j)
    return r['access_token']


def en_ligne(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=30) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    a = sys.argv[1:]
    conf = lire('config.json', {})
    file = lire('file-attente.json', [])
    envoyes = lire('envoyes.json', [])
    deja = {e['dossier'] for e in envoyes}
    maintenant = datetime.now(PARIS)

    if '--maintenant' not in a and '--essai' not in a:
        if maintenant.weekday() not in conf.get('jours', list(range(7))):
            return print(f"pas de post prévu le {maintenant:%A}")
        heures = conf.get('heures', [conf.get('heure', 19)])
        passees = sum(1 for h in heures if maintenant.hour >= h)
        if not passees:
            return print(f"trop tôt ({maintenant:%H:%M}, envois à {', '.join(f'{h} h' for h in heures)})")
        faits = sum(1 for e in envoyes if e['date'][:10] == maintenant.date().isoformat())
        if faits >= passees:
            return print(f"déjà {faits} envoi(s) aujourd'hui, prochain créneau plus tard")

    suivant = next((p for p in file if p['dossier'] not in deja), None)
    if not suivant:
        return print('file d\'attente vide : rien à envoyer')

    base = conf['base_url'].rstrip('/') + '/posts/' + suivant['dossier'] + '/'
    images = sorted(f for f in os.listdir(os.path.join(ICI, 'posts', suivant['dossier'])) if f.endswith('.jpg'))
    urls = [base + f for f in images]
    print(f"{suivant['dossier']} : {len(urls)} slides, « {suivant['legende']} »")
    if not 1 <= len(urls) <= 35:
        sys.exit('un carrousel TikTok doit avoir entre 1 et 35 images')
    absentes = [u for u in urls if not en_ligne(u)]
    if absentes:
        sys.exit(f'{len(absentes)} image(s) pas encore en ligne (GitHub Pages en cours ?) : {absentes[0]}')
    if '--essai' in a:
        return print('essai : rien envoyé')

    jeton = jeton_frais()
    r = requete(f'{API}/post/publish/content/init/', {
        'post_info': {'title': suivant.get('titre', '')[:90], 'description': suivant['legende'][:4000]},
        'source_info': {'source': 'PULL_FROM_URL', 'photo_cover_index': 0, 'photo_images': urls},
        'post_mode': 'MEDIA_UPLOAD',  # brouillon dans la boîte de réception TikTok : nasheed + publication dans l'app
        'media_type': 'PHOTO',
    }, jeton=jeton)
    if r.get('error', {}).get('code') != 'ok':
        sys.exit(f"TikTok a refusé : {r.get('error')}")
    pid = r['data']['publish_id']

    statut = '?'
    for _ in range(40):
        time.sleep(6)
        s = requete(f'{API}/post/publish/status/fetch/', {'publish_id': pid}, jeton=jeton)
        statut = s.get('data', {}).get('status', '?')
        if statut in ('SEND_TO_USER_INBOX', 'PUBLISH_COMPLETE', 'FAILED'):
            break
    if statut == 'FAILED':
        sys.exit(f"échec côté TikTok : {s.get('data', {}).get('fail_reason')}")

    envoyes.append({'dossier': suivant['dossier'], 'date': maintenant.isoformat(timespec='minutes'),
                    'publish_id': pid, 'statut': statut})
    ecrire('envoyes.json', envoyes)
    restants = sum(1 for p in file if p['dossier'] not in deja) - 1
    print(f'brouillon envoyé ({statut}) : ouvrir TikTok, ajouter le nasheed, publier sous 24 h. Il reste {restants} carrousel(s).')


if __name__ == '__main__':
    main()
