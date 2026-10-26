# Direct LAN access through an authenticated HTTPS gateway

Use this administrator-managed setup when an analyst needs to open the UI from another
computer without keeping an SSH tunnel open. The preview still listens on
`127.0.0.1:8766`. Nginx listens on the server's LAN IPv4 address at port 8766 and proxies
to that backend. An already installed internal 0.3.x UI on `127.0.0.1:8765` can use the
same gateway on port 8765.

The example addresses `192.0.2.10` (server) and `192.0.2.20` (workstation) are documentation
addresses. Replace them with your own addresses before running commands. Do not commit
your deployed configuration, password file, private key or application credentials.

## Design and prerequisites

- An existing, working UI installation on the same Ubuntu server as Nginx.
- Administrator access to install Nginx, a TLS certificate and a gateway password.
- Keep the application bound to loopback. Its launcher and Host guard reject LAN binds;
  changing `VR_DFIR_UI_HOST` to a LAN address or `0.0.0.0` is not this setup.
- The application has no independent login. The gateway authenticates the analyst over TLS.
  It is still one analyst workspace: gateway accounts do not create application roles,
  separate evidence stores or per-user authorization.
- Keep access within the intended LAN or VPN and any existing network access controls.

The [Nginx template](nginx/vr-dfir-lan.conf.example) checks the incoming Host and exact
browser Origin before translating headers for the loopback backend. An absent Origin is
allowed for command-line clients; application session and CSRF checks still apply to
state-changing API requests. Nginx does not forward its Basic authentication password to
the application. Session cookies are renamed per port and marked Secure, preserving the
application's HttpOnly and SameSite attributes. This lets both UIs share an IP without
overwriting each other's session cookie.

## 1. Check the existing backend

For an administrator-managed preview:

```bash
sudo systemctl status vr-dfir-ai-preview --no-pager -l
curl --noproxy '*' --fail --show-error --max-time 10 \
  http://127.0.0.1:8766/api/v1/status
```

If also exposing an existing internal UI, check it separately:

```bash
sudo systemctl status velociraptor-dfir-agent --no-pager -l
curl --noproxy '*' --fail --show-error --max-time 10 \
  http://127.0.0.1:8765/api/v1/status
```

Resolve backend failures before installing the gateway. The public preview installer does
not install the internal 0.3.x application. The Velociraptor server's own GUI/API ports are
independent of these two analyst UIs.

## 2. Install the gateway and set a password

```bash
sudo apt-get update
sudo apt-get install -y nginx apache2-utils
sudo touch /etc/nginx/vr-dfir.htpasswd
sudo htpasswd -B /etc/nginx/vr-dfir.htpasswd analyst
sudo chown root:www-data /etc/nginx/vr-dfir.htpasswd
sudo chmod 0640 /etc/nginx/vr-dfir.htpasswd
```

Enter a password at the prompt. It is not placed in shell history. Run `htpasswd` again
to change it. These ownership instructions assume Ubuntu's default `www-data` Nginx worker;
adjust the group if your Nginx installation uses another account. Adding the configuration
does not require changing existing virtual hosts or the Velociraptor server configuration.

## 3. Provide a TLS certificate

Use an organization-trusted certificate containing the server's LAN IP in its Subject
Alternative Name. Put the certificate and key at the paths shown in the template, or
change those two paths. Keep the key private.

For a lab, create a self-signed certificate instead. Replace the example IP in both places:

```bash
sudo install -d -m 0700 /etc/nginx/vr-dfir-tls
sudo openssl req -x509 -nodes -newkey rsa:3072 -days 365 \
  -keyout /etc/nginx/vr-dfir-tls/server.key \
  -out /etc/nginx/vr-dfir-tls/server.crt \
  -subj '/CN=192.0.2.10' \
  -addext 'subjectAltName=IP:192.0.2.10'
sudo chmod 0600 /etc/nginx/vr-dfir-tls/server.key
```

A self-signed certificate produces a browser trust warning until the client trusts the
certificate. Confirm its identity through your normal certificate-verification process.

## 4. Configure and start Nginx

From a source checkout, install the template once:

```bash
sudo install -m 0644 docs/nginx/vr-dfir-lan.conf.example \
  /etc/nginx/conf.d/vr-dfir-lan.conf
sudoedit /etc/nginx/conf.d/vr-dfir-lan.conf
```

Replace **every** `192.0.2.10` with the server's actual LAN IPv4 address. Leave the backend
addresses as `127.0.0.1`. To expose both existing UIs, also uncomment the `listen ...:8765`
line. With only the preview installed, leave it commented. Include this file from Nginx's
`http` context once; do not wrap it in another `server` block or copy its maps twice.

The explicit LAN listen address matters: Nginx and the backend share port numbers on
different local addresses. Do not replace it with a wildcard listen address.

```bash
sudo nginx -t
```

Only after the configuration test succeeds:

```bash
sudo systemctl enable nginx
sudo systemctl reload-or-restart nginx
```

If UFW is already active and blocks these connections, allow the intended workstation.
Replace both example addresses; do not enable or reset a firewall as part of this setup:

```bash
sudo ufw status
sudo ufw allow proto tcp from 192.0.2.20 to 192.0.2.10 port 8765:8766
```

For a preview-only gateway, allow port 8766 instead of the range.

## 5. Verify and open the UI

Use the real server IP below. With a trusted certificate, omit `-k`; for these self-signed
lab diagnostics only, `-k` skips certificate verification. `-u analyst` prompts for the
password rather than embedding it in a command.

```bash
# Without authentication: expect HTTP 401.
curl --noproxy '*' -k --max-time 10 -o /dev/null -w '%{http_code}\n' \
  https://192.0.2.10:8766/api/v1/status

# With authentication: expect HTTP 200 and the application's status JSON.
curl --noproxy '*' -k -u analyst --fail --show-error --max-time 10 \
  https://192.0.2.10:8766/api/v1/status

# An unrelated browser Origin must be rejected: expect HTTP 403.
curl --noproxy '*' -k -u analyst --max-time 10 \
  -H 'Origin: https://unrelated.example' -o /dev/null -w '%{http_code}\n' \
  https://192.0.2.10:8766/api/v1/session
```

Repeat for port 8765 if enabled. Open `https://SERVER_IP:8766/` in your workstation browser
and enter the gateway credentials. Open `https://SERVER_IP:8765/` for the optional internal
UI. Use HTTPS on the gateway ports; the local backend URLs remain HTTP.

In both browser tabs, verify a session is obtained, evidence loads, and an intended
state-changing action such as saving an analyst review still succeeds. A successful status
request alone does not verify CSRF/session behavior or a live model analysis.

## Troubleshooting and rollback

| Symptom | Check |
|---|---|
| Local backend works, workstation connection times out | Routing, UFW and upstream network controls; the configured LAN IP must belong to this server |
| Connection refused | Nginx service and explicit listen address; inspect `sudo ss -lntp` |
| Nginx will not start | `sudo nginx -t` and `sudo journalctl -u nginx -n 60 --no-pager`; check existing port listeners and default virtual hosts |
| HTTP 401 or repeated password prompts | Gateway username/password, password-file permissions and the browser's saved credentials |
| HTTP 421 | Browser hostname and incoming Host must match the IP configured in the template |
| HTTP 403 on a browser action | Exact external HTTPS Origin and port; preserve session-cookie and CSRF headers and the template's maps |
| HTTP 502 | Correct loopback backend running on the corresponding port |
| HTTP 200 but no page assets | Build/install the frontend assets; gateway configuration does not build the UI |

To remove only this gateway, move its config out of the `*.conf` include and reload after
checking the remaining configuration:

```bash
sudo mv /etc/nginx/conf.d/vr-dfir-lan.conf \
  /etc/nginx/conf.d/vr-dfir-lan.conf.disabled
sudo nginx -t && sudo systemctl reload nginx
```

Remove any firewall rule created for this gateway if it is no longer needed. Both application
services retain their loopback listeners, state, credentials and original SSH access method.
