import base64
from abc import ABC, abstractmethod
from config import AI_PROVIDER, AI_MODEL, ANTHROPIC_API_KEY, OPENAI_API_KEY, MISTRAL_API_KEY, GROQ_API_KEY, GEMINI_API_KEY

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
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = AI_MODEL or self.DEFAULT_MODEL

    @staticmethod
    def _track(model: str, msg):
        """Enregistre les tokens réels de l'appel (bilan honnête — /stats,
        dashboard). Best-effort : jamais bloquant."""
        try:
            import api_costs
            api_costs.record(model, msg.usage.input_tokens, msg.usage.output_tokens)
        except Exception:
            pass

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
        self.client = OpenAI(api_key=OPENAI_API_KEY)
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
        self.client = Mistral(api_key=MISTRAL_API_KEY)
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
        self.client = Groq(api_key=GROQ_API_KEY)
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
    DEFAULT_MODEL = "gemini-2.0-flash"

    def __init__(self):
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        self._genai = genai
        model_name = AI_MODEL or self.DEFAULT_MODEL
        self.model = genai.GenerativeModel(model_name)

    def complete(self, prompt: str, max_tokens: int = 800) -> str:
        r = self.model.generate_content(
            prompt,
            generation_config={"max_output_tokens": max_tokens},
        )
        return r.text

    def complete_with_image(self, prompt: str, image_bytes: bytes) -> str:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        r = self.model.generate_content([prompt, img])
        return r.text


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
    "gemini":    {"free": True,  "vision": True,  "default_model": "gemini-2.0-flash"},
}


def get_provider() -> AIProvider:
    cls = _PROVIDERS.get(AI_PROVIDER)
    if not cls:
        raise ValueError(
            f"AI_PROVIDER='{AI_PROVIDER}' inconnu. Valeurs valides: {list(_PROVIDERS)}"
        )
    return cls()
