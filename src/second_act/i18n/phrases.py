"""A catalogue phrase with its parameters, renderable in any audience and language."""

from __future__ import annotations

from dataclasses import dataclass, field

from second_act.i18n.catalog import Audience, Language, render


@dataclass(frozen=True)
class Phrase:
    """Catalogue key plus parameters."""

    key: str
    params: dict = field(default_factory=dict)

    def render(self, audience: Audience = "analyst", language: Language = "en") -> str:
        return render(self.key, self.params, audience, language)
