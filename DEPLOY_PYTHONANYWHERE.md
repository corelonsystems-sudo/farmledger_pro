# Deploying FarmLedger Pro on PythonAnywhere

Target account home directory: `/home/Farmerpro`
Repository: `https://github.com/corelonsystems-sudo/farmledger_pro`
Resulting site URL: `https://Farmerpro.pythonanywhere.com`

Throughout this guide the project will live at:

```
/home/Farmerpro/farmledger_pro
```

and the virtualenv at:

```
/home/Farmerpro/.virtualenvs/farmledger
```

---

## 1. Push your local code to GitHub

Run these on your Windows machine, in the project folder:

```powershell
git status
git add -A
git commit -m "Prepare for PythonAnywhere deployment"
git push origin main
```

If the remote is not set yet:

```powershell
git remote add origin https://github.com/corelonsystems-sudo/farmledger_pro.git
git branch -M main
git push -u origin main
```

**Do not commit `db.sqlite3`** if it contains data you do not want public — the
repository is the only thing PythonAnywhere will pull. Add it to `.gitignore` and
create a fresh database on the server instead (step 6).

---

## 2. Open a Bash console on PythonAnywhere

Dashboard -> **Consoles** -> **Bash**.

Confirm where you are:

```bash
whoami        # Farmerpro
pwd           # /home/Farmerpro
```

---

## 3. Clone the repository

```bash
cd /home/Farmerpro
git clone https://github.com/corelonsystems-sudo/farmledger_pro.git
cd farmledger_pro
ls             # you should see manage.py, farmledger/, templates/, requirements.txt
```

If the repository is private, use a GitHub personal access token:

```bash
git clone https://<TOKEN>@github.com/corelonsystems-sudo/farmledger_pro.git
```

---

## 4. Create the virtualenv and install dependencies

Use **Python 3.13**, the same version you develop with locally:

```bash
mkvirtualenv --python=/usr/bin/python3.13 farmledger
# the prompt now shows (farmledger)
python --version          # must print Python 3.13.x
pip install --upgrade pip
pip install -r /home/Farmerpro/farmledger_pro/requirements.txt
```

If you already created the virtualenv with the wrong version, delete and recreate it:

```bash
deactivate 2>/dev/null
rmvirtualenv farmledger
mkvirtualenv --python=/usr/bin/python3.13 farmledger
python --version
```

If `/usr/bin/python3.13` does not exist on your account, list what is available
and pick the highest 3.13 build:

```bash
ls /usr/bin/python3.*
```

> **Django version:** Python 3.13 is only officially supported from Django 5.1.3
> onwards. `requirements.txt` currently pins `Django>=4.2,<5.1`, so change that
> line to `Django>=5.1.3,<5.2` (commit it) before installing, otherwise pip will
> install a Django that is not tested on 3.13.

Notes:

- `psycopg2-binary` may fail or be unnecessary on the free tier (no PostgreSQL).
  If it errors, install everything else:
  ```bash
  grep -v psycopg2 /home/Farmerpro/farmledger_pro/requirements.txt > /tmp/req.txt
  pip install -r /tmp/req.txt
  ```
- `redis`/`celery` install fine but **Celery will not run** on PythonAnywhere
  (no Redis, no long-running background workers on the free tier). The web app
  does not need them.

To re-enter the virtualenv in future consoles:

```bash
workon farmledger
```

---

## 5. Production settings

Edit `farmledger/settings.py` on the server (Files tab or `nano`) — or better,
change it locally, commit, and `git pull` on the server.

```python
DEBUG = False
ALLOWED_HOSTS = ['Farmerpro.pythonanywhere.com']
CSRF_TRUSTED_ORIGINS = ['https://Farmerpro.pythonanywhere.com']
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'change-me')
```

`SECRET_KEY` is currently hard-coded in `farmledger/settings.py`; generate a new
one for production:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Database: the project already falls back to SQLite when
`FARMLEDGER_USE_SQLITE` is `1`, which is the **default**, so no change is needed.
The SQLite file will be `/home/Farmerpro/farmledger_pro/db.sqlite3`.

(If you have a paid plan with MySQL/PostgreSQL, replace the `DATABASES` block
with the credentials from the **Databases** tab instead.)

---

## 6. Migrate, create a superuser, collect static files

```bash
workon farmledger
cd /home/Farmerpro/farmledger_pro
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py check --deploy
```

`collectstatic` writes to `/home/Farmerpro/farmledger_pro/staticfiles`
(`STATIC_ROOT` in settings), which is what step 8 maps.

---

## 7. Create the web app

Dashboard -> **Web** -> **Add a new web app**:

1. Domain: `Farmerpro.pythonanywhere.com` -> **Next**
2. Framework: choose **Manual configuration** (NOT the "Django" option)
3. Python version: **3.13** — it must match the virtualenv exactly

Then on the **Web** tab fill in:

| Field | Value |
|---|---|
| Source code | `/home/Farmerpro/farmledger_pro` |
| Working directory | `/home/Farmerpro/farmledger_pro` |
| Virtualenv | `/home/Farmerpro/.virtualenvs/farmledger` |

---

## 8. Static and media file mappings

On the **Web** tab, under **Static files**, add:

| URL | Directory |
|---|---|
| `/static/` | `/home/Farmerpro/farmledger_pro/staticfiles` |
| `/media/` | `/home/Farmerpro/farmledger_pro/media` |

---

## 9. Edit the WSGI configuration file

On the **Web** tab click the WSGI configuration file link
(`/var/www/Farmerpro_pythonanywhere_com_wsgi.py`), delete everything, and paste:

```python
import os
import sys

path = '/home/Farmerpro/farmledger_pro'
if path not in sys.path:
    sys.path.insert(0, path)

os.environ['DJANGO_SETTINGS_MODULE'] = 'farmledger.settings'
os.environ['FARMLEDGER_USE_SQLITE'] = '1'
os.environ['DJANGO_SECRET_KEY'] = '<paste-the-generated-secret-key>'

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

Save the file.

---

## 10. Reload and test

Click the big green **Reload Farmerpro.pythonanywhere.com** button, then visit:

- `https://Farmerpro.pythonanywhere.com/` -> redirects to the admin login
  (the dashboard home requires login), then shows the dashboard
- `https://Farmerpro.pythonanywhere.com/admin/` -> Django admin
- `https://Farmerpro.pythonanywhere.com/dashboard/crops/crop/` -> Crops list
- `https://Farmerpro.pythonanywhere.com/reports/` -> Reports

---

## 11. Updating the deployed site after code changes

```bash
workon farmledger
cd /home/Farmerpro/farmledger_pro
git pull
pip install -r requirements.txt      # only if requirements changed
python manage.py migrate             # only if models changed
python manage.py collectstatic --noinput
```

Then press **Reload** on the Web tab. Nothing takes effect until you reload.

---

## 12. Troubleshooting

- **Error log / Server log**: Web tab -> Log files. The error log holds the
  Python traceback for any 500.
- **DisallowedHost**: add `Farmerpro.pythonanywhere.com` to `ALLOWED_HOSTS`.
- **CSRF verification failed**: add the `https://` domain to
  `CSRF_TRUSTED_ORIGINS`.
- **Unstyled admin / 404 on `/static/...`**: re-run `collectstatic` and check the
  static file mapping paths match `STATIC_ROOT`.
- **"This virtualenv seems to have the wrong Python version"** on the Web tab:
  the web app's Python version and the virtualenv's Python do not match. Set the
  web app to 3.13, or recreate the virtualenv with
  `mkvirtualenv --python=/usr/bin/python3.13 farmledger`, then **Reload**.
- **`ModuleNotFoundError: farmledger`**: the `path` in the WSGI file is wrong, or
  the Source code / Working directory fields do not point at
  `/home/Farmerpro/farmledger_pro`.
- **`no such table` errors**: `python manage.py migrate` was not run in the
  virtualenv, or the app is reading a different database file.
- **`attempt to write a readonly database`**: the SQLite file or its directory is
  not writable; confirm the file lives under `/home/Farmerpro/farmledger_pro`.
- **Free tier outbound internet is proxied** — anything in `integrations/` that
  calls an external API may be blocked.
