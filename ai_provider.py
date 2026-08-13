import base64
import os
from abc import ABC, abstractmethod
from config import AI_PROVIDER, AI_MODEL, ANTHROPIC_API_KEY, OPENAI_API_KEY, MISTRAL_API_KEY, GROQ_API_KEY, GEMINI_API_KEY


def _env_key(name: str):
    """Clé API lue en priorité dans os.environ : permet la configuration À CHAUD
    (/fallback sur Telegram écrit os.environ + .env) sans redémarrage."""
    return os.environ.get(name) or globals().get(name)

VISION_PROMPT = """Tu analyses une capture d'écran du portefeuille Bourse Direct.
Extrait chaque ligne de titre visible. Format strict, une ligne par titre :
NOM | TICKER_YAHOO | QTE | PRU | COURS_ACTUEL | VALEUR_TOTALE

Règles :
- TICKER_YAHOO : ajoute .PA pour Euronext Paris (ex: GNFT.PA, LBIRD.PA, VU.PA), sans suffixe pour US (ILMN, NVDA)
- Si le ticker n'est pas visible, utilise le code ISIN ou le NOM en majuscules
- Séparateur décimal : point (ex: 8.51 et non 8,51)
- Ignore les lignes récapitulatives (ESPÈCES, TITRES, total)
- Si une valeur n'est pas visible : N/A
Réponds UNIQUEMENT avec les lignes de données, sans explication."""


class AIProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 800) -> str:
        pass

    def complete_cheap(self, prompt: str, max_tokens: int = 100) -> str:
        """Complétion sur le modèle le moins cher du provider (micro-tâches :
        scoring, classification). Par défaut : même modèle que complete()."""
        return self.complete(prompt, max_tokens=max_tokens)

    def complete_with_image(self, prompt: str, image_bytes: bytes) -> str:
        """Vision par défaut : renvoie une erreur explicite si non surchargé."""
        raise NotImplementedError(
            f"{self.__class__.__name__} ne supporte pas la vision. "
            "Utilisez anthropic, openai, groq ou gemini."
        )

    def complete_cheap_with_image(self, prompt: str, image_bytes: bytes) -> str:
        """Vision sur le modèle le moins cher (micro-tâches descriptives :
        lecture de graphique). Par défaut : même modèle que complete_with_image()."""
        return self.complete_with_image(prompt, image_bytes)


class AnthropicProvider(AIProvider):
    DEFAULT_MODEL = "claude-sonnet-4-6"
    CHEAP_MODEL   = "claude-haiku-4-5-20251001"

    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=_env_key("ANTHROPIC_API_KEY"))
        self.model = AI_MODEL or self.DEFAULT_MODEL

    @staticmethod
    def _track(model: str, msg):
        """Enregistre les tokens réels de l'appel (bilan honnête — /stats,
        dashboard). Best-effort : jamais bloquant.

        On facture le modèle RÉELLEMENT servi (`msg.model`) et non celui
        demandé : un alias peut résoudre vers autre chose que ce qu'on croit.
        """
        try:
            import api_costs
            u = msg.usage
            # `input_tokens` d'Anthropic exclut déjà le cache : rien à retrancher.
            api_costs.record(
                getattr(msg, "model", None) or model,
                u.input_tokens, u.output_tokens,
                getattr(u, "cache_creation_input_tokens", 0) or 0,
                getattr(u, "cache_read_input_tokens", 0) or 0,
            )
        except Exception as e:
            # Surtout PAS un `pass` : c'est lui qui a caché neuf jours de
            # TypeError et laissé le suivi des coûts mort en silence.
            print(f"[api costs] suivi anthropic impossible : {e}")

    def complete(self, prompt: str, max_tokens: int = 800) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        self._track(self.model, msg)
        return msg.content[0].text

    def complete_cheap(self, prompt: str, max_tokens: int = 100) -> str:
        msg = self.client.messages.create(
            model=self.CHEAP_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        self._track(self.CHEAP_MODEL, msg)
        return msg.content[0].text

    def complete_with_image(self, prompt: str, image_bytes: bytes) -> str:
        return self._vision(self.model, prompt, image_bytes)

    def complete_cheap_with_image(self, prompt: str, image_bytes: bytes) -> str:
        # Lecture de graphique = tâche descriptive structurée → Haiku suffit
        # (~12× moins cher que Sonnet en input). Le verdict ACHAT/EXCLUS reste
        # sur le modèle principal.
        return self._vision(self.CHEAP_MODEL, prompt, image_bytes)

    def _vision(self, model: str, prompt: str, image_bytes: bytes) -> str:
        b64 = base64.standard_b64encode(image_bytes).decode()
        msg = self.client.messages.create(
            model=model,
            max_tokens=1000,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        self._track(model, msg)
        return msg.content[0].text


class OpenAIProvider(AIProvider):
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=_env_key("OPENAI_API_KEY"))
        self.model = AI_MODEL or self.DEFAULT_MODEL

    def _chat(self, model: str, messages: list, max_tokens: int) -> str:
        """Les modèles OpenAI récents (gpt-5, o-series) exigent
        max_completion_tokens ; les anciens n'acceptent que max_tokens.
        On tente le nouveau paramètre d'abord, fallback sur l'ancien."""
        try:
            r = self.client.chat.completions.create(
                model=model, messages=messages,
                max_completion_tokens=max_tokens,
            )
        except Exception as e:
            if "max_completion_tokens" not in str(e):
                raise
            r = self.client.chat.completions.create(
                model=model, messages=messages,
                max_tokens=max_tokens,
            )
        return r.choices[0].message.content

    def complete(self, prompt: str, max_tokens: int = 800) -> str:
        return self._chat(self.model, [{"role": "user", "content": prompt}], max_tokens)

    def complete_with_image(self, prompt: str, image_bytes: bytes) -> str:
        b64 = base64.standard_b64encode(image_bytes).decode()
        return self._chat("gpt-4o-mini", [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}], 1000)


class MistralProvider(AIProvider):
    DEFAULT_MODEL = "mistral-small-latest"
    VISION_MODEL = "pixtral-12b-2409"

    def __init__(self):
        from mistralai import Mistral
        self.client = Mistral(api_key=_env_key("MISTRAL_API_KEY"))
        self.model = AI_MODEL or self.DEFAULT_MODEL

    def complete(self, prompt: str, max_tokens: int = 800) -> str:
        r = self.client.chat.complete(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return r.choices[0].message.content

    def complete_with_image(self, prompt: str, image_bytes: bytes) -> str:
        b64 = base64.standard_b64encode(image_bytes).decode()
        r = self.client.chat.complete(
            model=self.VISION_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": f"data:image/jpeg;base64,{b64}"},
            ]}],
        )
        return r.choices[0].message.content


class GroqProvider(AIProvider):
    DEFAULT_MODEL = "llama-3.3-70b-versatile"
    VISION_MODEL = "llama-3.2-11b-vision-preview"

    def __init__(self):
        from groq import Groq
        self.client = Groq(api_key=_env_key("GROQ_API_KEY"))
        self.model = AI_MODEL or self.DEFAULT_MODEL

    def complete(self, prompt: str, max_tokens: int = 800) -> str:
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return r.choices[0].message.content

    def complete_with_image(self, prompt: str, image_bytes: bytes) -> str:
        b64 = base64.standard_b64encode(image_bytes).decode()
        r = self.client.chat.completions.create(
            model=self.VISION_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
        )
        return r.choices[0].message.content


class GeminiProvider(AIProvider):
    DEFAULT_MODEL = "gemini-flash-latest"   # alias evergreen (fallback si découverte KO)
    # Ordre de préférence — un flash récent, stable de préférence.
    _PREF = ["gemini-flash-latest", "gemini-3-flash", "gemini-2.5-flash", "gemini-2.0-flash"]

    def __init__(self):
        import google.generativeai as genai
        genai.configure(api_key=_env_key("GEMINI_API_KEY"))
        self._genai = genai
        # AI_MODEL ne s'applique QUE si Gemini est le provider principal ; en
        # fallback, il hériterait d'un nom Claude/GPT → 404. Sinon on découvre
        # dynamiquement le meilleur flash accessible à CETTE clé (les modèles
        # bloqués pour le compte ne sont pas renvoyés — plus de "no longer
        # available", cause de l'échec gemini-2.5-flash du 19/07).
        forced = AI_MODEL if AI_PROVIDER == "gemini" else ""
        self.model = genai.GenerativeModel(forced or self._discover(genai))

    def _discover(self, genai) -> str:
        try:
            avail = [m.name.replace("models/", "") for m in genai.list_models()
                     if "generateContent" in getattr(m, "supported_generation_methods", [])]
        except Exception as e:
            print(f"[gemini] list_models KO ({e}) — défaut {self.DEFAULT_MODEL}")
            return self.DEFAULT_MODEL
        for pref in self._PREF:
            if pref in avail:
                return pref
        # sinon : un modèle 'flash' quelconque, en évitant preview/exp/thinking
        flash = sorted((n for n in avail if "flash" in n),
                       key=lambda n: ("preview" in n, "exp" in n, "thinking" in n, len(n)))
        chosen = flash[0] if flash else (avail[0] if avail else self.DEFAULT_MODEL)
        print(f"[gemini] modèle auto-sélectionné : {chosen}")
        return chosen

    # Les modèles Gemini 3 « pensent » (thinking) : la réflexion consomme des
    # tokens de sortie AVANT le texte. Un budget trop court → tout part en
    # réflexion, zéro texte, et r.text lève (cause de l'échec du 19/07 avec le
    # test à 10 tokens). On garantit une marge minimale au-dessus du besoin réel.
    _MIN_OUTPUT = 4096

    @staticmethod
    def _track(model_name: str, r):
        """Coûts API (bilan honnête) — usage_metadata Gemini, best-effort.

        `gemini-flash-latest` est un ALIAS : le tarif dépend de ce vers quoi il
        résout, pas de son nom. On facture donc `model_version`, renvoyé par
        l'API, quand il est disponible.
        """
        try:
            import api_costs
            u = r.usage_metadata
            # Gemini INCLUT les jetons de cache dans prompt_token_count ; on les
            # retranche pour ne pas les facturer deux fois (Anthropic, lui, les
            # compte à part — d'où le contrat « entrée hors cache » de record).
            cache_read = getattr(u, "cached_content_token_count", 0) or 0
            api_costs.record(
                getattr(r, "model_version", None) or model_name,
                max(0, u.prompt_token_count - cache_read),
                u.candidates_token_count,
                0, cache_read,
            )
        except Exception as e:
            # Surtout PAS un `pass` : c'est un `except: pass` ici qui a caché
            # neuf jours de TypeError et laissé le suivi des coûts mort sans
            # que rien ne le signale (04→13/08/2026).
            print(f"[api costs] suivi gemini impossible : {e}")

    @staticmethod
    def _extract(r) -> str:
        """Lit le texte SANS l'accesseur rapide r.text (qui lève si aucune Part).
        Message clair avec finish_reason si la réponse est vide (thinking a tout
        consommé, SAFETY, RECITATION…)."""
        cands = getattr(r, "candidates", None) or []
        if cands:
            parts = getattr(getattr(cands[0], "content", None), "parts", None) or []
            txt = "".join(getattr(p, "text", "") or "" for p in parts).strip()
            if txt:
                return txt
        fr = getattr(cands[0], "finish_reason", "?") if cands else "aucun candidat"
        raise RuntimeError(f"réponse Gemini vide (finish_reason={fr}) — "
                           f"réflexion trop longue ou contenu filtré")

    def complete(self, prompt: str, max_tokens: int = 800) -> str:
        r = self.model.generate_content(
            prompt,
            generation_config={"max_output_tokens": max(max_tokens, self._MIN_OUTPUT)},
        )
        self._track(self.model.model_name, r)
        return self._extract(r)

    def complete_with_image(self, prompt: str, image_bytes: bytes) -> str:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        r = self.model.generate_content(
            [prompt, img],
            generation_config={"max_output_tokens": max(1000, self._MIN_OUTPUT)},
        )
        self._track(self.model.model_name, r)
        return self._extract(r)


_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "mistral": MistralProvider,
    "groq": GroqProvider,
    "gemini": GeminiProvider,
}

PROVIDER_INFO = {
    "anthropic": {"free": False, "vision": True,  "default_model": "claude-sonnet-4-6"},
    "openai":    {"free": False, "vision": True,  "default_model": "gpt-4o-mini"},
    "mistral":   {"free": False, "vision": True,  "default_model": "mistral-small-latest"},
    "groq":      {"free": True,  "vision": True,  "default_model": "llama-3.3-70b-versatile"},
    "gemini":    {"free": True,  "vision": True,  "default_model": "auto (flash récent)"},
}


class FallbackProvider(AIProvider):
    """Chaîne de providers : le principal d'abord, puis chaque fallback dans
    l'ordre si l'appel échoue (crédits épuisés, panne, rate limit). Le bot
    reste opérationnel au lieu de devenir aveugle (incident du 17/07/2026 :
    clé Anthropic à sec → tous les scans/validations en échec).

    Les fallbacks sont instanciés PARESSEUSEMENT (au premier échec du
    principal) : une clé configurée à chaud via /fallback est prise en compte
    sans redémarrage. Notification Telegram throttlée (1×/6h max)."""

    _last_notify = 0.0

    def __init__(self, chain: list[str]):
        self.chain = chain            # noms, ex ["anthropic", "gemini"]
        self._instances: dict = {}

    def _get(self, name: str) -> AIProvider:
        if name not in self._instances:
            self._instances[name] = _PROVIDERS[name]()
        return self._instances[name]

    def _notify_switch(self, failed: str, used: str, err: Exception):
        import time as _time
        now = _time.time()
        if now - FallbackProvider._last_notify < 6 * 3600:
            return
        FallbackProvider._last_notify = now
        try:
            # tg (transport seul), pas telegram_bot : importer les handlers
            # depuis un provider IA créait un cycle, contourné par cet import
            # local. Le module feuille supprime le besoin du contournement.
            import tg
            tg.send(
                f"🔀 FALLBACK IA ACTIF\n"
                f"{failed} en échec ({str(err)[:120]})\n"
                f"→ bascule sur {used}. Vérifie tes crédits {failed}."
            )
        except Exception:
            pass

    def _run(self, method: str, *args, **kwargs):
        last_err = None
        for i, name in enumerate(self.chain):
            try:
                result = getattr(self._get(name), method)(*args, **kwargs)
                if i > 0:
                    print(f"[AI fallback] {method} servi par {name} "
                          f"(échec de {self.chain[0]})")
                    self._notify_switch(self.chain[0], name, last_err or Exception("?"))
                return result
            except NotImplementedError as e:
                last_err = e            # provider sans vision → suivant
            except Exception as e:
                last_err = e
                print(f"[AI fallback] {name}.{method} : {e}")
        raise last_err if last_err else RuntimeError("aucun provider disponible")

    def complete(self, prompt: str, max_tokens: int = 800) -> str:
        return self._run("complete", prompt, max_tokens=max_tokens)

    def complete_cheap(self, prompt: str, max_tokens: int = 100) -> str:
        return self._run("complete_cheap", prompt, max_tokens=max_tokens)

    def complete_with_image(self, prompt: str, image_bytes: bytes) -> str:
        return self._run("complete_with_image", prompt, image_bytes)

    def complete_cheap_with_image(self, prompt: str, image_bytes: bytes) -> str:
        return self._run("complete_cheap_with_image", prompt, image_bytes)


def get_fallback_chain() -> list[str]:
    """Fallbacks configurés (AI_FALLBACK_PROVIDERS, ex "gemini,groq"),
    lus À CHAQUE appel (configurables à chaud via /fallback), filtrés sur les
    providers connus et différents du principal."""
    raw = os.environ.get("AI_FALLBACK_PROVIDERS", "")
    return [p.strip().lower() for p in raw.split(",")
            if p.strip() and p.strip().lower() in _PROVIDERS
            and p.strip().lower() != AI_PROVIDER]


def get_provider() -> AIProvider:
    cls = _PROVIDERS.get(AI_PROVIDER)
    if not cls:
        raise ValueError(
            f"AI_PROVIDER='{AI_PROVIDER}' inconnu. Valeurs valides: {list(_PROVIDERS)}"
        )
    fallbacks = get_fallback_chain()
    if fallbacks:
        return FallbackProvider([AI_PROVIDER] + fallbacks)
    return cls()
