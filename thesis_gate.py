"""
Porte d'achat : pas d'ACHAT sans analyse FORTE, vérifiée par le code.

Le 01/10/2026 le bot a voulu acheter KBC.BR sur une « thèse » réduite à
« KBC Group NV (KBC.BR) — Financial Services ». La recherche web n'avait
ramené que des pages de navigation, aucune news, aucun sentiment ; l'IA a dit
ACHAT quand même, parce que le prompt lui demandait de le faire faute de
défaut trouvé. Ce matin-là 8 candidats sur 8 étaient passés. Depuis août, la
plupart des scans validaient tout : l'étage IA, censé porter la stratégie, ne
filtrait plus rien.

Désormais la charge de la preuve est sur l'ACHAT. L'IA doit écrire une thèse
structurée, et ce module la REFUSE si :
  - le verdict n'est pas écrit explicitement (« VERDICT : ACHAT ») — une
    réponse sans verdict n'est plus un achat par défaut ;
  - la thèse, le « pourquoi maintenant » ou le risque sont absents ou creux ;
  - il y a moins de deux preuves, ou aucune ne s'appuie sur les DONNÉES de
    recherche fournies (news, web, catalyseurs, analystes) — une preuve que le
    code ne retrouve pas dans ces données peut être inventée ;
  - la conviction déclarée est sous MIN_CONVICTION.

Module feuille, sans réseau ni IA : testable, et le même jugement pour le
scan, le briefing et le contrôle pré-achat.
"""
import os
import re
import unicodedata

MIN_CONVICTION = int(os.getenv("MIN_CONVICTION", "4"))
MIN_PREUVES = 2

# Champs attendus, dans l'ordre du format demandé à l'IA.
_CHAMPS = {
    "these":       r"th[eè]se",
    "maintenant":  r"pourquoi maintenant",
    "risque_txt":  r"risque principal",
    "invalidation": r"invalidation",
    "conviction":  r"conviction",
}

# Mots trop génériques pour prouver qu'une preuve vient des données.
_VIDES = set("""
action actions titre titres cours société societe groupe group bourse marché
marche secteur prix niveau hausse baisse tendance momentum analyste analystes
objectif résultats resultats trimestre semaine semaines mois année annee depuis
avec dans pour selon cette entre après apres avant plus moins comme encore
toujours aussi leurs notre votre their about which there would could should
stock stocks share shares price market sector analyst analysts results quarter
""".split())


def _norm(txt: str) -> str:
    txt = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    return txt.lower()


def _mots(txt: str) -> set[str]:
    return {m for m in re.findall(r"[a-z]{5,}", _norm(txt)) if m not in _VIDES}


def _nombres(txt: str) -> set[str]:
    """Nombres d'au moins deux chiffres (montants, %, dates), normalisés."""
    return {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", txt or "")
            if len(re.sub(r"\D", "", n)) >= 2}


def parse(val: str) -> dict:
    """Lit la réponse structurée de l'IA. Champs absents → '' (ou None)."""
    out = {"verdict": None, "preuves": []}
    for line in (val or "").splitlines():
        s = line.strip().lstrip("-•* ").strip()
        low = _norm(s)
        m = re.match(r"verdict\s*:?\s*(achat|exclus|exclu)", low)
        if m and out["verdict"] is None:
            out["verdict"] = "ACHAT" if m.group(1) == "achat" else "EXCLUS"
            continue
        if re.match(r"preuve\s*\d*\s*:", low):
            out["preuves"].append(s.split(":", 1)[1].strip())
            continue
        for key, pat in _CHAMPS.items():
            if key not in out and re.match(_norm(pat) + r"\s*:", low):
                out[key] = s.split(":", 1)[1].strip()
                break
    conv = re.search(r"\d", out.get("conviction", "") or "")
    out["conviction"] = int(conv.group()) if conv else None
    for key in ("these", "maintenant", "risque_txt", "invalidation"):
        out.setdefault(key, "")
    return out


def _creux(txt: str, min_mots: int, nom_societe: str = "") -> bool:
    """Trop court, ou ne disant rien d'autre que le nom / le secteur."""
    mots = re.findall(r"\w+", txt or "")
    if len(mots) < min_mots:
        return True
    reste = _mots(txt) - _mots(nom_societe)
    return len(reste) < 3


def preuve_ancree(preuve: str, donnees: str, nom_societe: str = "") -> bool:
    """La preuve s'appuie-t-elle sur les données de recherche fournies ?

    Ancrée si elle partage avec elles un nombre (montant, %, date) ou au moins
    deux mots significatifs — hors nom de la société, qui serait partout.
    """
    if not preuve or not donnees:
        return False
    if _nombres(preuve) & _nombres(donnees):
        return True
    communs = (_mots(preuve) & _mots(donnees)) - _mots(nom_societe)
    return len(communs) >= 2


def check(val: str, donnees: str, nom_societe: str = "") -> tuple[bool, str, dict]:
    """(accepté, motif du refus, champs lus). `donnees` = news + web +
    catalyseurs + analystes : ce que l'IA a reçu SUR LA SOCIÉTÉ, hors
    indicateurs techniques (ceux-là, le filtre quantitatif les a déjà jugés)."""
    f = parse(val)
    if f["verdict"] != "ACHAT":
        return False, "pas de verdict ACHAT explicite", f
    if _creux(f["these"], 10, nom_societe):
        return False, "thèse absente ou creuse", f
    if _creux(f["maintenant"], 6, nom_societe):
        return False, "« pourquoi maintenant » absent", f
    if _creux(f["risque_txt"], 6, nom_societe):
        return False, "risque principal non identifié", f
    if len(f["preuves"]) < MIN_PREUVES:
        return False, f"moins de {MIN_PREUVES} preuves concrètes", f
    if not any(preuve_ancree(p, donnees, nom_societe) for p in f["preuves"]):
        return False, "aucune preuve retrouvée dans les données de recherche", f
    if f["conviction"] is None or f["conviction"] < MIN_CONVICTION:
        return False, (f"conviction {f['conviction'] or '?'}/5 < {MIN_CONVICTION}"), f
    return True, "", f


def resume(f: dict) -> str:
    """Thèse mémorisée : la vraie, pas l'en-tête société (bug du 01/10/2026,
    où `val.splitlines()[0]` stockait « KBC Group NV — Financial Services »)."""
    return (f.get("these") or "").strip()


# Seuil de veto : à ≤ 0.2, Jev dit clairement « non » — on agit sur ce non.
# Entre 0.2 et 0.8, avis incertain : il ne bloque pas (la porte du code a déjà
# exigé thèse + preuves).
JEV_VETO = float(os.getenv("JEV_THESIS_VETO", "0.2"))


def jev_review(f: dict, donnees: str, societe: str) -> dict | None:
    """Contre-avis Jev sur une thèse ACHAT déjà passée par `check`.

    Deux questions étroites, sur du texte (le terrain où Jev est fiable) :
      - les faits cités figurent-ils vraiment dans les données de recherche ?
      - la thèse est-elle une raison propre à la société, pas du générique ?
    Renvoie {"ancrage": p, "specifique": p, "veto": motif|None}, ou None si
    Jev n'a pas répondu.
    """
    import jev
    state = {
        "company": societe,
        "research": (donnees or "")[:6000],
        "analysis": {"thesis": f.get("these", ""), "why_now": f.get("maintenant", ""),
                     "evidence": f.get("preuves", [])},
    }
    ans = jev.ask(state, {
        "ancrage": {"type": "noul", "instructions":
            "Are the facts cited in `analysis.evidence` actually stated in `research` "
            "(news, web snippets, analyst data), rather than absent from it?"},
        "specifique": {"type": "noul", "instructions":
            "Does `analysis.thesis` give a reason specific to `company` for its share "
            "price to rise, rather than generic momentum, sector or market commentary?"},
    }, label="thesis")
    p_anc, p_spec = jev.noul(ans, "ancrage"), jev.noul(ans, "specifique")
    if p_anc is None and p_spec is None:
        return None
    veto = None
    if p_anc is not None and p_anc <= JEV_VETO:
        veto = f"preuves absentes des données (p={p_anc:.2f})"
    elif p_spec is not None and p_spec <= JEV_VETO:
        veto = f"thèse générique, rien de propre à la société (p={p_spec:.2f})"
    return {"ancrage": p_anc, "specifique": p_spec, "veto": veto}
