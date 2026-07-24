import datetime
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, redirect, render_template, request, send_from_directory, url_for

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
AGENTI_JSON = DATA_DIR / "agenti.json"
NAZIONALE_JSON = DATA_DIR / "lista_nazionale.json"
FOTO_DIR = DATA_DIR / "foto"
FOTO_RAW_DIR = DATA_DIR / "foto_raw"
FOTO_TMP_DIR = DATA_DIR / "foto_tmp"
SITE_DIR = BASE_DIR / "site"
GENERA_SITO_SCRIPT = BASE_DIR / "build" / "genera_sito.py"

for d in (FOTO_DIR, FOTO_RAW_DIR, FOTO_TMP_DIR):
    d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR / "build"))
import scontorno  # noqa: E402  (loads the background-removal model once at startup)

app = Flask(__name__)

SOCIAL_FIELDS = ["facebook", "instagram", "youtube", "linkedin", "tiktok"]


def load_agenti():
    if not AGENTI_JSON.exists():
        return []
    with open(AGENTI_JSON, encoding="utf-8") as f:
        return json.load(f)


def save_agenti(agenti):
    with open(AGENTI_JSON, "w", encoding="utf-8") as f:
        json.dump(agenti, f, ensure_ascii=False, indent=2)
        f.write("\n")


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "agente"


def unique_id(nome, agenti, exclude_id=None):
    base = slugify(nome)
    existing = {a["id"] for a in agenti if a["id"] != exclude_id}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def load_nazionale():
    if not NAZIONALE_JSON.exists():
        return []
    with open(NAZIONALE_JSON, encoding="utf-8") as f:
        return json.load(f)


def save_nazionale(lista):
    with open(NAZIONALE_JSON, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)
        f.write("\n")


@app.route("/")
def lista():
    agenti = load_agenti()
    agenti_ordinati = sorted(
        agenti, key=lambda a: (a.get("carosello") or 99, a.get("ordine") or 0, a.get("nome", ""))
    )
    return render_template("lista.html", agenti=agenti_ordinati, totale=len(agenti))


@app.route("/nuovo", methods=["GET", "POST"])
def nuovo():
    return _form(agente=None)


@app.route("/modifica/<agente_id>", methods=["GET", "POST"])
def modifica(agente_id):
    agenti = load_agenti()
    agente = next((a for a in agenti if a["id"] == agente_id), None)
    if agente is None:
        return redirect(url_for("lista"))
    return _form(agente=agente)


def _form(agente):
    agenti = load_agenti()
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            return render_template("form.html", agente=agente or {}, errore="Il nome è obbligatorio.")

        if agente is None:
            agente_id = unique_id(nome, agenti)
            nuovo_agente = {}
        else:
            agente_id = agente["id"]
            nuovo_agente = dict(agente)

        nuovo_agente["id"] = agente_id
        nuovo_agente["nome"] = nome
        nuovo_agente["agenzia"] = request.form.get("agenzia", "").strip()
        nuovo_agente["citta"] = request.form.get("citta", "").strip()
        nuovo_agente["fondatore"] = request.form.get("fondatore") == "on"
        carosello = request.form.get("carosello", "").strip()
        nuovo_agente["carosello"] = int(carosello) if carosello else None
        ordine = request.form.get("ordine", "").strip()
        nuovo_agente["ordine"] = int(ordine) if ordine else 0
        nuovo_agente["bio"] = request.form.get("bio", "").strip()
        nuovo_agente["telefono"] = request.form.get("telefono", "").strip()
        nuovo_agente["email"] = request.form.get("email", "").strip()
        nuovo_agente["sito"] = request.form.get("sito", "").strip()
        nuovo_agente["social"] = {
            campo: request.form.get(f"social_{campo}", "").strip() for campo in SOCIAL_FIELDS
        }

        foto_token = request.form.get("foto_token", "").strip()
        if foto_token:
            tmp_path = FOTO_TMP_DIR / f"{foto_token}.png"
            if tmp_path.exists():
                dest = FOTO_DIR / f"{agente_id}.png"
                shutil.move(str(tmp_path), str(dest))
                nuovo_agente["foto"] = f"{agente_id}.png"
                raw_tmp = next(FOTO_TMP_DIR.glob(f"{foto_token}_raw.*"), None)
                if raw_tmp:
                    raw_dest = FOTO_RAW_DIR / f"{agente_id}{raw_tmp.suffix}"
                    shutil.move(str(raw_tmp), str(raw_dest))
        elif "foto" not in nuovo_agente:
            nuovo_agente["foto"] = ""

        if agente is None:
            agenti.append(nuovo_agente)
        else:
            agenti = [nuovo_agente if a["id"] == agente["id"] else a for a in agenti]

        save_agenti(agenti)
        return redirect(url_for("lista"))

    return render_template("form.html", agente=agente or {}, errore=None)


@app.route("/elimina/<agente_id>", methods=["POST"])
def elimina(agente_id):
    agenti = load_agenti()
    agente = next((a for a in agenti if a["id"] == agente_id), None)
    if agente:
        nome_foto = agente.get("foto")
        if nome_foto:
            foto_path = FOTO_DIR / nome_foto
            if foto_path.is_file():
                foto_path.unlink()
        agenti = [a for a in agenti if a["id"] != agente_id]
        save_agenti(agenti)
    return redirect(url_for("lista"))


@app.route("/nazionale")
def nazionale_lista():
    lista = load_nazionale()
    lista_ordinata = sorted(lista, key=lambda v: (v.get("regione", ""), v.get("nome", "")))
    regioni = sorted(set(v.get("regione", "") for v in lista))
    return render_template(
        "nazionale_lista.html", lista=lista_ordinata, totale=len(lista), regioni=regioni
    )


@app.route("/nazionale/nuovo", methods=["GET", "POST"])
def nazionale_nuovo():
    return _nazionale_form(voce=None)


@app.route("/nazionale/modifica/<voce_id>", methods=["GET", "POST"])
def nazionale_modifica(voce_id):
    lista = load_nazionale()
    voce = next((v for v in lista if v["id"] == voce_id), None)
    if voce is None:
        return redirect(url_for("nazionale_lista"))
    return _nazionale_form(voce=voce)


def _nazionale_form(voce):
    lista = load_nazionale()
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            return render_template("nazionale_form.html", voce=voce or {}, errore="Il nome è obbligatorio.")

        if voce is None:
            voce_id = unique_id(nome, lista)
            nuova_voce = {}
        else:
            voce_id = voce["id"]
            nuova_voce = dict(voce)

        nuova_voce["id"] = voce_id
        nuova_voce["nome"] = nome
        nuova_voce["agenzia"] = request.form.get("agenzia", "").strip()
        nuova_voce["citta"] = request.form.get("citta", "").strip()
        nuova_voce["regione"] = request.form.get("regione", "").strip()
        nuova_voce["sito"] = request.form.get("sito", "").strip()

        if voce is None:
            lista.append(nuova_voce)
        else:
            lista = [nuova_voce if v["id"] == voce["id"] else v for v in lista]

        save_nazionale(lista)
        return redirect(url_for("nazionale_lista"))

    return render_template("nazionale_form.html", voce=voce or {}, errore=None)


@app.route("/nazionale/elimina/<voce_id>", methods=["POST"])
def nazionale_elimina(voce_id):
    lista = load_nazionale()
    lista = [v for v in lista if v["id"] != voce_id]
    save_nazionale(lista)
    return redirect(url_for("nazionale_lista"))


@app.route("/api/scontorno", methods=["POST"])
def api_scontorno():
    file = request.files.get("foto")
    if not file or not file.filename:
        return {"errore": "Nessun file ricevuto"}, 400

    token = uuid.uuid4().hex
    suffix = Path(file.filename).suffix.lower() or ".jpg"
    raw_path = FOTO_TMP_DIR / f"{token}_raw{suffix}"
    file.save(raw_path)

    dst_path = FOTO_TMP_DIR / f"{token}.png"
    try:
        scontorno.process(raw_path, dst_path)
    except Exception as exc:  # noqa: BLE001
        return {"errore": f"Scontorno fallito: {exc}"}, 500

    return {"token": token, "preview_url": url_for("foto_tmp", filename=f"{token}.png")}


@app.route("/foto/<path:filename>")
def foto(filename):
    return send_from_directory(FOTO_DIR, filename)


@app.route("/foto_tmp/<path:filename>")
def foto_tmp(filename):
    return send_from_directory(FOTO_TMP_DIR, filename)


@app.route("/genera-sito", methods=["POST"])
def genera_sito():
    risultato = subprocess.run(
        [sys.executable, str(GENERA_SITO_SCRIPT)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
    )
    torna_a = request.form.get("torna_a") or url_for("lista")
    if risultato.returncode == 0:
        return redirect(f"{torna_a}?msg=genera-sito-ok")
    print(risultato.stdout)
    print(risultato.stderr)
    return redirect(f"{torna_a}?msg=genera-sito-errore")


@app.route("/anteprima/")
@app.route("/anteprima/<path:filename>")
def anteprima(filename="index.html"):
    if not (SITE_DIR / filename).exists() and "." not in filename:
        filename = filename.rstrip("/") + ".html"
    return send_from_directory(SITE_DIR, filename)


@app.route("/pubblica", methods=["POST"])
def pubblica():
    torna_a = request.form.get("torna_a") or url_for("lista")

    stato = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(BASE_DIR), capture_output=True, text=True
    )
    if not stato.stdout.strip():
        return redirect(f"{torna_a}?msg=pubblica-nulla")

    subprocess.run(["git", "add", "-A"], cwd=str(BASE_DIR), capture_output=True, text=True)

    commit = subprocess.run(
        [
            "git", "commit", "-m",
            f"Aggiornamento dati e sito dalla dashboard — {datetime.date.today().isoformat()}",
        ],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        print(commit.stdout)
        print(commit.stderr)
        return redirect(f"{torna_a}?msg=pubblica-errore")

    push = subprocess.run(["git", "push"], cwd=str(BASE_DIR), capture_output=True, text=True)
    if push.returncode != 0:
        print(push.stdout)
        print(push.stderr)
        return redirect(f"{torna_a}?msg=pubblica-errore")

    return redirect(f"{torna_a}?msg=pubblica-ok")


def _apri_browser():
    time.sleep(1)
    webbrowser.open("http://127.0.0.1:5050")


if __name__ == "__main__":
    threading.Thread(target=_apri_browser, daemon=True).start()
    app.run(port=5050, debug=False)
