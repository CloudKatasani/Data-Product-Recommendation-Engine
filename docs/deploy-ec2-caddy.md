# Deploying on AWS EC2 behind Caddy (step by step)

This guide adds the engine to an **EC2 server that already runs Caddy** for
another app, the same way that app was deployed:

- the code is cloned from GitHub with a plain HTTPS link (the repository is
  public, so no key is needed);
- the app runs as a background service listening only on `127.0.0.1`;
- Caddy gets the HTTPS certificate and forwards a new domain name to it.

You do everything in the **browser terminal** (EC2 console → your instance →
**Connect** → **EC2 Instance Connect** → **Connect**). Expect about 20
minutes.

```
your browser ──HTTPS──▶ Caddy (already on the server)
                          ├── your-other-app.example.com → 127.0.0.1:3000
                          └── dpre.example.com           → 127.0.0.1:8000  (this app)
```

**One thing that differs from the other app:** this engine won't let anyone
in without knowing who they are, so Caddy also asks for a **username and
password** and passes the signed-in name and roles to the app. Step 5 sets
that up.

> **Using the browser terminal.** Paste with **Ctrl+Shift+V** (Windows/Linux)
> or **Cmd+V** (Mac), or right-click → Paste. Paste one block at a time and
> wait for the `ubuntu@ip-...:~$` prompt before the next.

---

## Step 1. Choose a domain name and point it at the server

> **`dpre.example.com` in this guide is only an example.** Everywhere you see
> it, type your own name instead. If you paste it unchanged, the browser warns
> *Your connection is not private* (`ERR_CERT_COMMON_NAME_INVALID`).

Pick a name for this app under a domain you own. If your other app is at
`adpm.mycompany.com`, use `dpre.mycompany.com`. To see which domain the other
app uses:

```bash
sudo grep -nE '^[^#[:space:]].*\{' /etc/caddy/Caddyfile
```

**No domain of your own?** Use a free name built from the server's IP. It
needs no DNS record, so you can skip the rest of this step:

```bash
echo "$(curl -s https://checkip.amazonaws.com | tr . -).sslip.io"
```

It prints something like `3-110-22-33.sslip.io`. Use it only if the server has
an Elastic IP; otherwise the name breaks when the IP changes.

To use your own domain, add one record in your DNS provider's dashboard, the
same way you did for the other app:

| Type | Name / Host | Value | TTL |
| --- | --- | --- | --- |
| `A` | `dpre` | the server's **Elastic IP** (the same IP the other app uses) | 300 |

(On Cloudflare, set the proxy status to **DNS only**, the grey cloud.)

Check it from the browser terminal. Repeat until it prints your server's IP:

```bash
getent hosts dpre.example.com     # your name here
```

## Step 2. Download the code

```bash
cd ~
git clone https://github.com/CloudKatasani/Data-Product-Recommendation-Engine.git
cd ~/Data-Product-Recommendation-Engine
ls
```

You should see `dpre`, `docs`, `README.md` and others. This creates a new
folder `~/Data-Product-Recommendation-Engine`, next to
`~/AgenticDataProductManagement`.

## Step 3. Check the code runs

Nothing needs installing: the app uses only Python's standard library, and
Ubuntu already has `python3`. (Type `python3`, not `python`.)

```bash
cd ~/Data-Product-Recommendation-Engine
python3 --version
python3 -m dpre run automated --industry utility
```

`python3 --version` must be 3.11 or newer (Ubuntu 24.04 has 3.12). The second
command prints a run summary with candidates. That proves the code works.

Also check nothing else is already using port 8000:

```bash
sudo ss -ltnp | grep ':8000 ' || echo "port 8000 is free"
```

If it prints something other than `port 8000 is free`, tell whoever helps you,
or change `8000` to `8001` everywhere in steps 4 and 5.

## Step 4. Run the app as a background service

First, **set your domain** in a variable so the commands below can use it.
Replace `dpre.example.com` with your real name from step 1:

```bash
DOMAIN=dpre.example.com
```

(If you close the terminal tab, run this line again before continuing.)

Install the service. It starts the app now and again after every reboot:

```bash
sed "s/dpre.example.com/$DOMAIN/" ~/Data-Product-Recommendation-Engine/deploy/ec2-caddy/dpre.service \
  | sudo tee /etc/systemd/system/dpre.service > /dev/null
mkdir -p ~/dpre-data
sudo systemctl daemon-reload
sudo systemctl enable --now dpre
sudo systemctl status dpre --no-pager
```

Look for **`active (running)`** in green. Then check the app answers locally:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

It prints a short JSON answer with `"status": "ok"`.

The app keeps its data (database and uploads) in `~/dpre-data`.

## Step 5. Add the site to Caddy, with a sign-in

### 5.1 Check Caddy

```bash
sudo systemctl status caddy --no-pager
```

Look for **`active (running)`**. If it says `Unit caddy.service could not be
found`, Caddy is not installed the usual way. See *Appendix: Caddy is not
installed* at the bottom.

### 5.2 Back up the current Caddy config

```bash
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.backup-$(date +%F)
```

### 5.3 Create your password

Choose a username (e.g. your first name, lowercase) and run:

```bash
USERNAME=yourname
HASH=$(caddy hash-password)
```

It asks for the password twice. Nothing appears while you type, which is
normal. Use at least 12 characters.

### 5.4 Add the site block

This appends a new site to the end of `/etc/caddy/Caddyfile`. Your other
app's site is left untouched.

```bash
sudo tee -a /etc/caddy/Caddyfile > /dev/null <<EOF

# Data Product Recommendation Engine
$DOMAIN {
	request_body {
		max_size 64MB
	}

	basicauth {
		$USERNAME $HASH
	}

	map {http.auth.user.id} {dpre_roles} {
		$USERNAME "operator,reviewer,steward,catalog_admin"
		default "reviewer"
	}

	reverse_proxy 127.0.0.1:8000 {
		header_up -X-DPRE-Identity
		header_up -Authorization
		header_up X-Forwarded-User {http.auth.user.id}
		header_up X-Forwarded-Roles {dpre_roles}
	}
}
EOF
```

### 5.5 Check the config and reload Caddy

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

The last line must say **`Valid configuration`**. If it doesn't, nothing has
changed yet; see *Troubleshooting*. Then:

```bash
sudo systemctl reload caddy
```

Reloading does not interrupt your other app.

### 5.6 Wait for the certificate

```bash
sleep 20; sudo journalctl -u caddy --since "5 min ago" --no-pager | grep -i "certificate obtained"
```

You should see a line containing `certificate obtained successfully` and your
domain.

## Step 6. Open the app

1. On your laptop, open `https://` followed by your name from step 1 (e.g. `https://dpre.mycompany.com`).
2. The browser asks for a username and password: use the ones from step 5.3.
3. Try it: **Start → Automated**, pick an industry (e.g. *utility*), and run
   it.

To see who the app thinks you are, open
`https://<your name>/api/v1/whoami`. It shows your username, your
roles and `"auth_method": "trusted_proxy"`.

**You are done.**

---

## Adding more users

Each person needs their own username, so the app records who approved or
rejected what.

1. Create the hash in the terminal: `caddy hash-password` (it prints a line
   starting with `$2a$`; select it and copy it).
2. Open the config: `sudo nano /etc/caddy/Caddyfile`
3. In the `$DOMAIN { ... }` block:
   - under `basicauth {`, add a line: `newuser <pasted hash>`
   - under `map ... {dpre_roles} {`, add a line: `newuser "reviewer"`
4. Save with **Ctrl+O**, **Enter**, then exit with **Ctrl+X**.
5. Run step 5.5 again (validate, then reload).

Roles you can give (several, comma separated, inside the quotes):

| Role | Can do |
| --- | --- |
| `operator` | upload extracts and run the pipeline |
| `reviewer` | accept or reject candidates |
| `steward` | resolve metric conflicts, accept metric names |
| `catalog_admin` | download seeds and catalog payloads |
| `council` | approve score weights and waive gates. Give this to someone who does **not** review: the app refuses weight approval by a person whose own reviews trained those weights. |

A user not listed under `map` gets `reviewer`.

To remove someone, delete their two lines and repeat steps 4–5.

## Day-to-day commands

| Task | Command |
| --- | --- |
| Is the app running? | `sudo systemctl status dpre --no-pager` |
| App logs (last 100 lines) | `sudo journalctl -u dpre -n 100 --no-pager` |
| Follow app logs live | `sudo journalctl -u dpre -f` (Ctrl+C to stop) |
| Restart the app | `sudo systemctl restart dpre` |
| Caddy logs | `sudo journalctl -u caddy -n 100 --no-pager` |

## Updating to a new version

```bash
cd ~/Data-Product-Recommendation-Engine
git pull
sudo systemctl restart dpre
```

Take a backup first (below). The database upgrades itself when the app starts.

## Backups

All the app's data is in `~/dpre-data`. To make a safe copy of the database
while the app is running:

```bash
python3 -c "import sqlite3; s=sqlite3.connect('/home/ubuntu/dpre-data/private/engine.db'); d=sqlite3.connect('/home/ubuntu/engine-$(date +%F).db'); s.backup(d); d.close()"
ls -lh ~/engine-*.db
```

For automatic protection, set up daily snapshots of the server's disk in the
console: **EC2 → Elastic Block Store → Lifecycle Manager → Create lifecycle
policy → EBS snapshot policy**, target the instance by its `Name` tag,
every 24 hours, keep 14.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Command 'python' not found` | Use `python3`. |
| `No module named dpre` | You are not in the code folder. Run `cd ~/Data-Product-Recommendation-Engine` first. |
| `systemctl status dpre` is not running | `sudo journalctl -u dpre -n 50 --no-pager` shows why. A `refusing to bind` line means `/etc/systemd/system/dpre.service` was edited; repeat step 4. |
| `caddy validate` says `unrecognized directive: basic_auth` | Your Caddy is older than 2.8 and spells it `basicauth`. Fix it with `sudo sed -i 's/\bbasic_auth\b/basicauth/' /etc/caddy/Caddyfile`, then validate again. (Newer Caddy accepts `basicauth` too, with a deprecation warning you can ignore.) |
| `caddy validate` fails | Usually the domain line is empty because `DOMAIN`/`USERNAME`/`HASH` were not set in this terminal tab. Restore the backup: `sudo cp /etc/caddy/Caddyfile.backup-<date> /etc/caddy/Caddyfile`, then repeat step 5 from 5.3 after running the `DOMAIN=...` line again. |
| Browser: *This site can't be reached* | DNS not pointing at the server yet (`getent hosts <your name>`). |
| *Your connection is not private*, `ERR_CERT_COMMON_NAME_INVALID` | The example name `dpre.example.com` (or another placeholder) was used instead of your real one. Fix both files: `REAL=<your name>`, then `sudo sed -i "s/dpre\.example\.com/$REAL/g" /etc/caddy/Caddyfile /etc/systemd/system/dpre.service`, then repeat step 5.5 and run `sudo systemctl daemon-reload && sudo systemctl restart dpre`. |
| Certificate warning, or Caddy log shows `challenge failed` | DNS points somewhere else, or Cloudflare proxy (orange cloud) is on. Fix, then `sudo systemctl reload caddy`. |
| Password prompt keeps coming back | Wrong username or password. Create a new hash and replace it in the Caddyfile (see *Adding more users*). |
| Page shows `DPRE-HTTP-001` | The domain in the browser differs from the one in the service. Run the `DOMAIN=...` line with the exact name, repeat step 4. |
| Page shows `DPRE-AUTH-001` (no identity) | The Caddy block is missing the `header_up X-Forwarded-User` line, or points at the wrong port. Compare with step 5.4. |
| Action refused with `DPRE-AUTH-002` | Your user lacks that role. Add it in the Caddyfile `map` block and reload. |
| `502 Bad Gateway` | Caddy cannot reach the app: `sudo systemctl restart dpre` and check its logs. |

## Appendix: Caddy is not installed

If step 5.1 found no Caddy service, check whether the other app runs Caddy in
Docker (`sudo docker ps | grep -i caddy`). If it does, don't install a second
Caddy (both would need ports 80 and 443). Ask for help adding this site to that
Caddy instead.

If there is no Caddy at all, install it (official Caddy packages for Ubuntu):

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
```

The new install comes with a sample `/etc/caddy/Caddyfile` that serves a
welcome page on port 80. Empty it with
`echo | sudo tee /etc/caddy/Caddyfile`, then continue with step 5.2. Make sure
the instance's security group allows **HTTP (80)** and **HTTPS (443)** from
anywhere.

## How it fits together (for the curious)

- The service (`deploy/ec2-caddy/dpre.service`) runs
  `python3 -m dpre serve --host 127.0.0.1 --port 8000`, so the app is not
  reachable from the internet directly, only through Caddy.
- `DPRE_TRUSTED_PROXY=127.0.0.1` tells the app to accept the
  `X-Forwarded-User` and `X-Forwarded-Roles` headers from Caddy.
- Caddy's site block (`deploy/ec2-caddy/Caddyfile.snippet` is a reference copy)
  checks the password, removes any identity headers the visitor sent, and sets
  its own from the signed-in user. So a visitor cannot claim to be someone
  else.
- `DPRE_ALLOWED_HOSTS` is your domain, so requests addressed to any other name
  are refused.
- General configuration is in `docs/deployment.md`; the security reasoning is
  in `docs/security.md`.
