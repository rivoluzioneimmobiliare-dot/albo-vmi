import datetime
import json
import re
import shutil
import unicodedata
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from PIL import Image

FOTO_WEBP_QUALITY = 85

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATE_DIR = BASE_DIR / "template"
SITE_DIR = BASE_DIR / "site"


def load_agenti():
    with open(DATA_DIR / "agenti.json", encoding="utf-8") as f:
        return json.load(f)


def load_lista_nazionale():
    path = DATA_DIR / "lista_nazionale.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _normalizza_nome(nome):
    nome = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z]", "", nome.lower())


def collega_profili(lista, agenti):
    """Per i nominativi senza un sito proprio, se coincidono con un agente con
    pagina propria, collega la riga alla pagina profilo invece che a nulla."""
    per_nome = {_normalizza_nome(a["nome"]): a for a in agenti}
    collegati = 0
    for voce in lista:
        if voce.get("sito"):
            continue
        chiave = _normalizza_nome(voce["nome"])
        agente = per_nome.get(chiave)
        if not agente:
            # gestisce le foto a due persone: "Juri Ceccon" nella lista deve
            # trovare "Juri Ceccon e Daniele Soligo" nella pagina profilo
            for chiave_agente, candidato in per_nome.items():
                if chiave_agente.startswith(chiave) or chiave.startswith(chiave_agente):
                    agente = candidato
                    break
        if agente:
            voce["profilo_url"] = f"agenti/{agente['id']}.html"
            collegati += 1
    if collegati:
        print(f"Lista nazionale: {collegati} nominativi senza sito collegati alla loro pagina profilo")
    return lista


def raggruppa_per_regione(lista):
    regioni = {}
    for voce in lista:
        regioni.setdefault(voce["regione"], []).append(voce)
    return sorted(regioni.items())


def raggruppa_caroselli(agenti):
    gruppi = {1: [], 2: [], 3: []}
    for a in agenti:
        c = a.get("carosello")
        if c in gruppi:
            gruppi[c].append(a)
    for c in gruppi:
        gruppi[c].sort(key=lambda a: (a.get("ordine") or 0, a.get("nome", "")))
    return [(c, gruppi[c]) for c in (1, 2, 3) if gruppi[c]]


def pulisci_site_dir():
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    SITE_DIR.mkdir(parents=True)


FOTO_CACHE_DIR = DATA_DIR / ".foto_webp_cache"


def converti_foto_webp():
    """Converte ogni foto sorgente (PNG, trasparente) in WebP compresso per il sito pubblico.

    Le conversioni vengono tenute in una cache persistente (fuori da site/, che
    viene ripulita a ogni generazione) cosi le foto invariate vengono solo
    copiate invece di essere ricompresse da zero: molto piu veloce quando si
    rigenera il sito dopo aver cambiato solo pochi agenti.
    """
    foto_dst = SITE_DIR / "foto"
    foto_dst.mkdir(exist_ok=True)
    FOTO_CACHE_DIR.mkdir(exist_ok=True)
    prima = dopo = 0
    convertite = copiate_da_cache = 0
    for f in sorted((DATA_DIR / "foto").glob("*.png")):
        dst = foto_dst / f"{f.stem}.webp"
        cache = FOTO_CACHE_DIR / f"{f.stem}.webp"
        if cache.exists() and cache.stat().st_mtime >= f.stat().st_mtime:
            shutil.copy2(cache, dst)
            copiate_da_cache += 1
        else:
            img = Image.open(f).convert("RGBA")
            img.save(cache, "WEBP", quality=FOTO_WEBP_QUALITY, method=4)
            shutil.copy2(cache, dst)
            convertite += 1
        prima += f.stat().st_size
        dopo += dst.stat().st_size
    if prima:
        print(
            f"Foto ottimizzate: {prima/1_000_000:.1f} MB -> {dopo/1_000_000:.1f} MB "
            f"({convertite} ricompresse, {copiate_da_cache} dalla cache)"
        )


def copia_assets():
    converti_foto_webp()

    video_dst = SITE_DIR / "video"
    video_src = DATA_DIR / "video"
    if video_src.exists():
        video_dst.mkdir(exist_ok=True)
        for f in video_src.glob("*.mp4"):
            shutil.copy2(f, video_dst / f.name)

    shutil.copy2(TEMPLATE_DIR / "style.css", SITE_DIR / "style.css")

    vendor_dst = SITE_DIR / "vendor"
    vendor_src = TEMPLATE_DIR / "vendor"
    if vendor_src.exists():
        shutil.copytree(vendor_src, vendor_dst, dirs_exist_ok=True)


def genera_home(env, agenti):
    template = env.get_template("home.html")
    html = template.render(
        caroselli=raggruppa_caroselli(agenti),
        anno=datetime.date.today().year,
    )
    (SITE_DIR / "index.html").write_text(html, encoding="utf-8")


def genera_profili(env, agenti):
    template = env.get_template("profilo.html")
    agenti_dst = SITE_DIR / "agenti"
    agenti_dst.mkdir(exist_ok=True)
    for a in agenti:
        html = template.render(a=a)
        (agenti_dst / f"{a['id']}.html").write_text(html, encoding="utf-8")


def genera_lista_nazionale(env, anno, agenti):
    lista = load_lista_nazionale()
    if not lista:
        return
    lista = collega_profili(lista, agenti)
    template = env.get_template("tutti-gli-agenti.html")
    html = template.render(regioni=raggruppa_per_regione(lista), totale=len(lista), anno=anno)
    (SITE_DIR / "tutti-gli-agenti.html").write_text(html, encoding="utf-8")


def genera_pagine_statiche(env, anno):
    for nome in ("chi-siamo.html", "contatti.html", "privacy.html"):
        template = env.get_template(nome)
        html = template.render(anno=anno)
        (SITE_DIR / nome).write_text(html, encoding="utf-8")


def main():
    agenti = load_agenti()
    anno = datetime.date.today().year
    pulisci_site_dir()
    copia_assets()
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    genera_home(env, agenti)
    genera_profili(env, agenti)
    genera_lista_nazionale(env, anno, agenti)
    genera_pagine_statiche(env, anno)
    print(f"Sito generato in {SITE_DIR} ({len(agenti)} agenti, {len(agenti)} pagine profilo)")


if __name__ == "__main__":
    main()
