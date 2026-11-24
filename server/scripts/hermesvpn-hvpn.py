#!/usr/bin/env python3
"""Make a locked HermesVPN route file (.hvpn) for a DNS-tunnel account.
Run as ubuntu (reads ~/antifilter/.secrets, signs with ~/winapp/tunnel/.keys).
  hermesvpn-hvpn.py make NAME DAYS     -> customer account afNAME
  hermesvpn-hvpn.py owner DAYS         -> owner's test account (hrmtest)
Prints JSON: {ok, path, link, expires, id, name}. Never prints the password."""
import base64, json, subprocess, sys, time
from pathlib import Path

H = Path('/home/ubuntu')
SEC = H / 'antifilter/.secrets'
SEAL = H / 'winapp/tunnel/build/hvpn-seal'
OUT = H / 'hermesvpn/release/hvpn'
DOMAIN = 't.example.com'


def fail(msg):
    print(json.dumps({'ok': False, 'error': msg}, ensure_ascii=False)); sys.exit(1)


def main(a):
    if len(a) == 2 and a[0] == 'owner':
        user, pw, label, days = 'hrmtest', (SEC / 'dnstt_hrmtest_pw').read_text().strip(), 'owner', a[1]
        title = 'تونل DNS — تست صاحب'
    elif len(a) == 3 and a[0] == 'make':
        name, days = a[1], a[2]
        try:
            u = json.loads((SEC / 'dnstt_users.json').read_text()).get(name)
        except (OSError, ValueError):
            u = None
        if not u:
            fail(f'حساب تونل «{name}» پیدا نشد')
        if u.get('status') != 'active':
            fail(f'حساب «{name}» متوقف است؛ اول ادامه‌اش بده')
        user, pw, label, title = u['user'], u['pw'], name, 'HermesVPN — مسیر اضطراری'
    else:
        fail('usage')
    try:
        days = max(1, min(365, int(days)))
    except ValueError:
        fail('days')
    try:
        pub = json.loads((SEC / 'routes_cache.json').read_text())['dnstt_pub']
    except (OSError, ValueError, KeyError):
        fail('کلید عمومی تونل در کش نیست (یک بار «🧭 تونل DNS (حساب من)» را بزن)')
    exp = int(time.time()) + days * 86400
    rid = f'{label}-{time.strftime("%Y%m%d%H%M")}'
    cfg = {'name': title, 'mode': 'dnstt', 'ssh': {'user': user, 'pass': pw},
           'dnstt': {'domain': DOMAIN, 'pubkey': pub, 'resolver': '8.8.8.8'}, 'expires': exp, 'id': rid}
    r = subprocess.run([str(SEAL)], input=json.dumps(cfg), capture_output=True, text=True, timeout=30)
    if r.returncode or not r.stdout.startswith('HVPN1'):
        fail('ساخت فایل ناموفق: ' + (r.stderr or '').strip()[:120])
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f'HermesVPN-{label}.hvpn'
    p.write_text(r.stdout); p.chmod(0o640)
    link = 'hvpn://' + base64.urlsafe_b64encode(r.stdout.encode()).decode().rstrip('=')
    print(json.dumps({'ok': True, 'path': str(p), 'link': link, 'expires': exp, 'id': rid, 'name': label}, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1:])
