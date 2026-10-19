# Deploying on AWS EC2 behind Caddy (step by step)

This guide assumes you work in the **AWS web console** and connect to the
server with **EC2 Instance Connect** (the *Connect* button, which opens a
terminal in your browser). No AWS command line or SSH key on your laptop is
needed. Expect about 45 minutes the first time.

What you end up with: one EC2 server running two containers.

```
your browser ──HTTPS──▶ Caddy (certificate, username/password sign-in)
                            │  private Docker network, never exposed
                            ▼
                        the engine (port 8000, reachable only from Caddy)
                            │
                        disk volume "dpre-data" (database and uploads)
```

- **Caddy** gets a free HTTPS certificate from Let's Encrypt, asks every visitor
  for a username and password, and tells the engine who signed in and which
  roles they have.
- **The engine** has no public port. It refuses to start on a public address
  without something like Caddy in front of it, and it only trusts identity
  information that comes from Caddy.

The files used are in `deploy/ec2-caddy/` in this repository:
`compose.yml` (the two containers), `setup.sh` (asks questions and writes the
config for you), and `Caddyfile.example` (what the config looks like, for
reference).

> **How to use the browser terminal.** Paste with **Ctrl+Shift+V** on Windows
> or Linux, **Cmd+V** on Mac (or right-click → Paste). Copy by selecting text,
> then **Ctrl+Shift+C** / **Cmd+C**. Run commands one block at a time and wait
> for the prompt (`ubuntu@ip-...:~$`) to come back before pasting the next.

---

## Before you start: you need a domain name

HTTPS certificates are issued for a name, not an IP address. Use either:

- **A subdomain of a domain you already own**, e.g. `dpre.yourcompany.com`
  (you will add one DNS record in step 4). This is the right choice for
  anything you show to other people.
- **For a quick trial with no domain**, a free `sslip.io` name built from the
  server's IP, e.g. `3-110-22-33.sslip.io` for IP `3.110.22.33`. It needs no
  DNS setup; skip step 4.

---

## Step 1. Launch the server

1. Sign in to the AWS console. In the top-right corner, pick the **Region**
   closest to you (e.g. *US East (N. Virginia)*, *Asia Pacific (Mumbai)*).
   Everything below happens in that region.
2. Search for **EC2** in the top search bar and open it.
3. Click **Launch instance** (orange button).
4. Fill in the form:

   | Field | What to choose |
   | --- | --- |
   | **Name** | `dpre` |
   | **Application and OS Images** | **Ubuntu** → *Ubuntu Server 24.04 LTS*, architecture **64-bit (x86)** |
   | **Instance type** | `t3.small` (2 GiB memory). `t3.micro` works for a trial but is tight. |
   | **Key pair (login)** | **Create new key pair** → name `dpre-key`, type RSA, format `.pem` → **Create** (it downloads; keep it safe as a backup way in. You won't need it for the browser terminal.) |

5. **Network settings** → keep the default VPC and subnet, make sure
   **Auto-assign public IP** is *Enable*, then:
   - ✅ **Allow SSH traffic from** → choose **Anywhere** (needed for the
     browser *Connect* button; Ubuntu only accepts key-based logins, never
     passwords). If you know how, you can tighten this later, see
     *Troubleshooting*.
   - ✅ **Allow HTTPS traffic from the internet**
   - ✅ **Allow HTTP traffic from the internet** (Let's Encrypt uses it to
     issue the certificate)
6. **Configure storage** → set the size to **20 GiB**, type **gp3**. Click
   **Advanced**, expand the volume, and set **Encrypted** to **Yes** (the
   server will hold client metadata). Leave the KMS key as default.
7. Click **Launch instance**, then **View all instances**. Wait until
   *Instance state* is **Running** and *Status check* shows **2/2 checks
   passed** (2–3 minutes; click the refresh icon).

**Cost:** roughly USD 20 a month in most regions for a `t3.small` running
24×7 (server, disk and public IP). A *stopped* instance only costs the disk and
the Elastic IP; *terminating* deletes everything.

## Step 2. Give the server a fixed IP address (Elastic IP)

Without this, the public IP changes every time the server is stopped and
started, and your domain would stop pointing at it.

1. In the EC2 left menu: **Network & Security → Elastic IPs**.
2. **Allocate Elastic IP address** → **Allocate**.
3. Select the new address → **Actions → Associate Elastic IP address**.
4. **Instance**: pick `dpre` → **Associate**.
5. Note the address (e.g. `3.110.22.33`). This is your server's IP from now on.

## Step 3. Open the browser terminal

1. **Instances** → tick `dpre` → **Connect** (top of the page).
2. Tab **EC2 Instance Connect** → *Connection type*: **Connect using a Public
   IP** → username `ubuntu` → **Connect**.
3. A new browser tab opens with a black terminal ending in `ubuntu@ip-...:~$`.

If it fails to connect, see *Troubleshooting* at the bottom.

## Step 4. Point your domain at the server

*Skip this step if you are using an `sslip.io` name.*

At whoever hosts your domain's DNS, add one record:

| Type | Name / Host | Value | TTL |
| --- | --- | --- | --- |
| `A` | `dpre` (gives `dpre.yourcompany.com`) | your Elastic IP | 300 |

In your DNS provider's dashboard (GoDaddy, Namecheap, Cloudflare, etc.), open
*DNS / Manage DNS → Add record* and enter the values above. On Cloudflare, set
the proxy status to **DNS only** (grey cloud), otherwise Caddy cannot get its
certificate.

Check it from the browser terminal (repeat until it prints your Elastic IP; it
usually takes a few minutes):

```bash
getent hosts dpre.yourcompany.com
```

## Step 5. Install Docker

Paste into the browser terminal:

```bash
sudo apt-get update && sudo apt-get -y upgrade
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
```

If you see a purple *Pending kernel upgrade* or *Daemons using outdated
libraries* screen, press **Enter** to accept the defaults.

Now **close the terminal tab and click Connect again** (step 3), so the new
Docker permission takes effect. Then check:

```bash
docker --version
docker compose version
docker run --rm hello-world
```

The last command should print *Hello from Docker!*.

## Step 6. Download the application

The repository is private, so the server needs permission to read it. A
**deploy key** gives this one server read-only access to this one repository.

1. Create the key on the server and print its public half:

   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "dpre-ec2"
   cat ~/.ssh/id_ed25519.pub
   ```

2. Copy the whole line it prints (starts with `ssh-ed25519`).
3. In GitHub, open the repository → **Settings → Deploy keys → Add deploy
   key** → Title `dpre-ec2`, paste the key, leave **Allow write access
   unticked** → **Add key**.
4. Back in the terminal, download the code:

   ```bash
   ssh-keyscan github.com >> ~/.ssh/known_hosts
   sudo mkdir -p /opt/dpre && sudo chown ubuntu: /opt/dpre
   git clone git@github.com:cloudkatasani/data-product-recommendation-engine.git /opt/dpre
   cd /opt/dpre/deploy/ec2-caddy
   ls
   ```

   You should see `compose.yml`, `setup.sh`, `Caddyfile.example`.

   (To deploy a branch other than the default one, add
   `--branch <branch-name>` to the `git clone` line.)

## Step 7. Configure: domain and users

Run the setup script and answer its questions:

```bash
cd /opt/dpre/deploy/ec2-caddy
./setup.sh
```

It asks for:

1. **Domain name**: `dpre.yourcompany.com` (or your `sslip.io` name).
2. **Your email**: Let's Encrypt only uses it for certificate expiry notices.
3. **Users**, one at a time: a username, a role and a password (at least 12
   characters; nothing shows while you type, which is normal). Roles:

   | Choice | Role | Can do |
   | --- | --- | --- |
   | `1` | admin | upload extracts, run the pipeline, review candidates, steward metrics, download seeds |
   | `2` | reviewer | read and accept/reject candidates |
   | `3` | steward | read and resolve metric conflicts |
   | `4` | council | approve score weights and waive gates |

   Create at least one **admin** for yourself. Give **council** to a
   different person from the reviewers; the engine refuses weight approval
   by someone whose own reviews trained those weights.

The first password takes a little longer because Docker downloads the Caddy
image. When it finishes it says `Wrote .env and Caddyfile ...`.

To add or remove users later, run `./setup.sh` again (it asks for everyone
again and replaces the list), then `docker compose restart caddy`.

## Step 8. Start it

```bash
cd /opt/dpre/deploy/ec2-caddy
docker compose up -d --build
```

The first run builds the engine image (about a minute). Then check:

```bash
docker compose ps
```

Both `dpre-dpre-1` and `dpre-caddy-1` should say **Up**; after about 30
seconds the engine also shows **(healthy)**. Now watch Caddy get the
certificate:

```bash
docker compose logs caddy | grep -i certificate
```

Look for `certificate obtained successfully`. Press **q** if a pager opens.

## Step 9. Open the application

1. On your own computer, open `https://dpre.yourcompany.com` (your domain).
2. The browser asks for a username and password: use one you created in
   step 7.
3. To prove it end to end: **Start → Automated**, pick an industry (e.g.
   *utility*), and run it. You need the *admin* role for this.

From the terminal you can also check who the engine thinks you are:

```bash
curl -s -u youruser https://dpre.yourcompany.com/api/v1/whoami
```

(It prompts for the password.) The answer names your user, its roles and
`"auth_method": "trusted_proxy"`.

**You are done.** The containers restart by themselves if the server reboots.

---

## Day-to-day operations

All commands run from `/opt/dpre/deploy/ec2-caddy` in the browser terminal.

| Task | Command |
| --- | --- |
| See if it is running | `docker compose ps` |
| Engine logs (last 100 lines) | `docker compose logs --tail 100 dpre` |
| Follow logs live | `docker compose logs -f` (Ctrl+C to stop) |
| Restart everything | `docker compose restart` |
| Stop (keeps data) | `docker compose down` |
| Start again | `docker compose up -d` |
| Add/remove/change users | `./setup.sh` then `docker compose restart caddy` |

**Never** run `docker compose down -v` or `docker volume rm`: the `-v` deletes
the database.

### Updating to a new version

```bash
cd /opt/dpre && git pull
cd deploy/ec2-caddy && docker compose up -d --build
```

Take a backup first (below). The database upgrades itself on start.

### Backups

All data lives in the Docker volume `dpre-data` on the server's disk.

**Automatic daily disk snapshots** (recommended, set up once in the console):

1. EC2 left menu: **Elastic Block Store → Lifecycle Manager → Create
   lifecycle policy**.
2. **EBS snapshot policy** → Next.
3. **Target resource types**: *Instance* → *Target resource tags*: key
   `Name`, value `dpre` → Next.
4. Schedule: every **24 hours**, retain **14** snapshots → **Create policy**.

**A copy of the database on demand** (safe while the app runs):

```bash
cd /opt/dpre/deploy/ec2-caddy
docker compose exec dpre python -c "import sqlite3; s=sqlite3.connect('/var/lib/dpre/private/engine.db'); d=sqlite3.connect('/var/lib/dpre/private/engine.backup.db'); s.backup(d); d.close()"
docker compose cp dpre:/var/lib/dpre/private/engine.backup.db ~/engine-$(date +%F).db
ls -lh ~/engine-*.db
```

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| **Connect** button fails: *Failed to connect to your instance* | The security group does not allow SSH from EC2 Instance Connect. **Instances → dpre → Security tab →** click the security group → **Edit inbound rules** → make sure there is a rule *SSH, port 22, source 0.0.0.0/0*. (Tighter option: source = the prefix list `com.amazonaws.<your-region>.ec2-instance-connect`; type `ec2-instance-connect` in the source box to find it.) Also check the instance has a public IP. |
| `permission denied ... docker.sock` | You did not reconnect after step 5. Close the terminal tab and **Connect** again. |
| `git clone` says *Permission denied (publickey)* | The deploy key was not added to *this* repository, or was pasted incompletely. Repeat step 6.1–6.3. |
| `setup.sh: Permission denied` | Run `chmod +x setup.sh` then `./setup.sh`. |
| `docker compose up` says *set DPRE_DOMAIN in .env* | Run `./setup.sh` first, from `/opt/dpre/deploy/ec2-caddy`. |
| Browser: *This site can't be reached* | DNS not pointing at the Elastic IP yet (`getent hosts yourdomain`), or HTTP/HTTPS missing from the security group (add *HTTP 80* and *HTTPS 443* from *Anywhere-IPv4*). |
| Browser: certificate warning, or Caddy logs show `challenge failed` | Port 80 is not open, DNS points somewhere else, or Cloudflare proxy (orange cloud) is on. Fix, then `docker compose restart caddy`. |
| Password prompt keeps coming back | Wrong username or password. Re-run `./setup.sh` and `docker compose restart caddy`. |
| Page shows `DPRE-HTTP-001` | The domain you typed in the browser differs from the one in `.env`. Re-run `./setup.sh` with the exact name, then `docker compose up -d`. |
| Page says the request carries no identity (`DPRE-AUTH-001`) | `compose.yml` was edited and Caddy is no longer at `172.30.0.10`. Restore it with `git checkout compose.yml && docker compose up -d`. |
| Action refused with `DPRE-AUTH-002` | Your user lacks the role (e.g. only admins can run the pipeline). Change it with `./setup.sh`. |
| Engine not *(healthy)* | `docker compose logs --tail 50 dpre` shows why. |

---

## Optional improvements

- **Single sign-on (Google, Microsoft, Okta) instead of passwords.** Replace
  `basic_auth` in the Caddyfile with Caddy's `forward_auth` pointing at an
  `oauth2-proxy` container, and copy its user and group headers into
  `X-Forwarded-User` / `X-Forwarded-Roles`. The engine side does not change.
- **Machine clients with API tokens.** The password sign-in uses the
  `Authorization` header, so tokens need their own hostname without
  `basic_auth`, plus `DPRE_TOKENS` on the engine (`docs/deployment.md`).
- **Only allow your office or VPN.** In the security group, change the
  *HTTPS 443* source from *Anywhere* to your network range. Keep *HTTP 80*
  open to everyone so the certificate can renew.

## How it fits together (for the curious)

- `compose.yml` puts both containers on a private network `172.30.0.0/24`
  with fixed addresses: Caddy `172.30.0.10`, engine `172.30.0.20`. Only Caddy
  publishes ports (80 and 443).
- The engine runs with `DPRE_HOST=0.0.0.0` and
  `DPRE_TRUSTED_PROXY=172.30.0.10`: it accepts `X-Forwarded-User` and
  `X-Forwarded-Roles` only from Caddy's address, and because it is not bound
  to loopback, the development sign-in header is switched off.
- The Caddyfile written by `setup.sh` checks the password, removes any
  identity headers the visitor sent, and sets its own from the signed-in user.
- `DPRE_ALLOWED_HOSTS` is your domain, so requests addressed to any other name
  are refused.
- General engine configuration is in `docs/deployment.md`; the security
  reasoning is in `docs/security.md`.
