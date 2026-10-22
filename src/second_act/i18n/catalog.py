"""Phrase catalogue for every template sentence, in two audiences and two languages.

``render(key, params, audience, language)`` fills a phrase. Numeric parameters are
wrapped in ``Num`` and formatted per language (Spanish uses a decimal comma);
``Chan`` parameters are channel names resolved per language and grammatical slot.
English analyst text is the canonical form the tactician and verifier work with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Audience = Literal["analyst", "casual"]
Language = Literal["en", "es"]
AUDIENCES: tuple[Audience, ...] = ("analyst", "casual")
LANGUAGES: tuple[Language, ...] = ("en", "es")


@dataclass(frozen=True)
class Num:
    """A number to format per language."""

    value: float


@dataclass(frozen=True)
class Chan:
    """A channel (left, centre, right) to name per language."""

    channel: str


@dataclass(frozen=True)
class Metric:
    """A metric name (with optional channel) to translate per variant."""

    metric: str
    channel: str | None = None


@dataclass(frozen=True)
class Word:
    """A small word from ``WORDS`` (rose, fell, rise, drop) to translate per variant."""

    key: str


@dataclass(frozen=True)
class Unit:
    """A unit suffix (per five minutes, %, m) to translate per language."""

    unit: str


def fmt_number(value: float, language: Language) -> str:
    """``1.5`` -> ``1.5`` (en) or ``1,5`` (es); whole numbers without decimals."""
    text = f"{value:g}"
    return text.replace(".", ",") if language == "es" else text


CHANNEL_FORMS: dict[Language, dict[str, dict[str, str]]] = {
    "en": {
        "via": {"left": "down the left", "centre": "through the middle", "right": "down the right"},
        "the": {"left": "the left", "centre": "the middle", "right": "the right"},
        "adj": {"left": "left", "centre": "centre", "right": "right"},
        "flank": {"left": "left", "centre": "central", "right": "right"},
    },
    "es": {
        "via": {"left": "por la izquierda", "centre": "por el centro", "right": "por la derecha"},
        "the": {"left": "la banda izquierda", "centre": "el centro", "right": "la banda derecha"},
        "adj": {"left": "izquierda", "centre": "central", "right": "derecha"},
        "flank": {"left": "izquierda", "centre": "central", "right": "derecha"},
    },
}

# Metric names for watcher/recap sentences: (en analyst, en casual, es analyst, es casual).
METRIC_NAMES: dict[str, tuple[str, str, str, str]] = {
    "channel_entries": (
        "{adj}-channel entries",
        "attacks {via}",
        "llegadas {via}",
        "ataques {via}",
    ),
    "final_third_entries": (
        "final-third entries",
        "attacks into the final third",
        "llegadas al último tercio",
        "ataques",
    ),
    "possession": ("possession", "share of the ball", "posesión", "posesión"),
    "field_tilt": ("field tilt", "territory", "dominio territorial", "dominio del terreno"),
    "ppda": ("PPDA", "pressing (PPDA)", "PPDA", "presión (PPDA)"),
    "turnover_height": (
        "turnover height",
        "where they win the ball",
        "altura de recuperación",
        "zona de recuperación",
    ),
    "high_regains": (
        "ball wins in the final third",
        "ball wins high up the pitch",
        "recuperaciones en el último tercio",
        "recuperaciones arriba",
    ),
    "progressive_passes": (
        "progressive passes",
        "forward passes",
        "pases progresivos",
        "pases hacia delante",
    ),
    "shots": ("shots", "shots", "tiros", "tiros"),
    "xg": ("xG", "chance quality (xG)", "xG", "calidad de ocasiones (xG)"),
    "pass_completion": (
        "pass completion",
        "passing accuracy",
        "acierto en el pase",
        "acierto en el pase",
    ),
    "pass_difficulty": (
        "pass difficulty",
        "pass difficulty",
        "dificultad de pase",
        "dificultad de pase",
    ),
    "sequence_length": (
        "possession length",
        "spells on the ball",
        "duración de las posesiones",
        "posesiones",
    ),
    "turnovers_per_min": ("turnover rate", "balls lost", "pérdidas por minuto", "balones perdidos"),
    "control_index": (
        "control index",
        "control of the game",
        "índice de control",
        "control del partido",
    ),
    "def_line_height": (
        "defensive line height",
        "defensive line",
        "altura de la línea defensiva",
        "línea defensiva",
    ),
    "team_width": ("team width", "width", "amplitud", "amplitud"),
}

UNIT_SUFFIX: dict[Language, dict[str, str]] = {
    "en": {"per_window": " per five minutes", "xg": " per five minutes", "pct": "%", "m": " m"},
    "es": {
        "per_window": " cada cinco minutos",
        "xg": " cada cinco minutos",
        "pct": " %",
        "m": " m",
    },
}

# key -> (en analyst, en casual, es analyst, es casual)
PHRASES: dict[str, tuple[str, str, str, str]] = {
    # --- half-time briefing: what is working ---
    "w_press": (
        "{tn}'s press is forcing mistakes: {hr} ball wins in the final third and a PPDA of "
        "{ppda}, against {ppda_o} for {on}.",
        "{tn}'s pressing is causing havoc: they have won the ball {hr} times in {on}'s third.",
        "La presión de {tn} fuerza errores: {hr} recuperaciones en el último tercio y un PPDA "
        "de {ppda}, frente a {ppda_o} de {on}.",
        "La presión de {tn} está haciendo daño: ya ha recuperado el balón {hr} veces en campo "
        "de {on}.",
    ),
    "w_channel": (
        "{tn} are attacking {via}: {n} of their {total} final-third entries ({share}%) come "
        "through that channel.",
        "{tn} keep attacking {via}: {share}% of their attacks go that way.",
        "{tn} ataca {via}: {n} de sus {total} llegadas al último tercio ({share} %) entran por "
        "ahí.",
        "{tn} insiste {via}: el {share} % de sus ataques va por ahí.",
    ),
    "w_tilt": (
        "{tn} have pinned {on} back, playing {tilt}% of the final-third passes.",
        "{tn} have {on} pinned in their own half.",
        "{tn} ha encerrado a {on}: juega el {tilt} % de los pases en el último tercio.",
        "{tn} tiene a {on} encerrado en su campo.",
    ),
    "w_xg": (
        "{tn} have created {xg} xG to {on}'s {xg_o}.",
        "{tn} are creating the better chances: {xg} xG to {xg_o}.",
        "{tn} ha generado {xg} xG por {xg_o} de {on}.",
        "{tn} está generando las mejores ocasiones: {xg} xG frente a {xg_o}.",
    ),
    "w_quiet": (
        "{tn} have kept {on} quiet, allowing {shots} shots worth {xg_o} xG.",
        "{tn} have kept {on} quiet: just {shots} shots.",
        "{tn} ha mantenido a raya a {on}: {shots} tiros por un valor de {xg_o} xG.",
        "{tn} ha mantenido a raya a {on}: solo {shots} tiros.",
    ),
    "w_possession": (
        "{tn} have controlled the ball with {poss}% possession.",
        "{tn} have had most of the ball ({poss}%).",
        "{tn} ha controlado el balón con un {poss} % de posesión.",
        "{tn} ha tenido casi siempre el balón ({poss} %).",
    ),
    "w_completion": (
        "{tn} have completed {pc}% of their passes.",
        "{tn} have looked after the ball: {pc}% of passes completed.",
        "{tn} ha completado el {pc} % de sus pases.",
        "{tn} ha cuidado el balón: {pc} % de pases completados.",
    ),
    "w_clinical_1": (
        "{tn} have made their chance count: a goal from {xg} xG.",
        "{tn} have been clinical: a goal from chances worth {xg} xG.",
        "{tn} ha aprovechado su ocasión: un gol con {xg} xG.",
        "{tn} ha sido letal: un gol con ocasiones de {xg} xG.",
    ),
    "w_clinical": (
        "{tn} have made their chances count: {g} goals from {xg} xG.",
        "{tn} have been clinical: {g} goals from chances worth {xg} xG.",
        "{tn} ha aprovechado sus ocasiones: {g} goles con {xg} xG.",
        "{tn} ha sido letal: {g} goles con ocasiones de {xg} xG.",
    ),
    # --- half-time briefing: what is not working ---
    "n_red": (
        "{tn} are down to {n} men after a red card.",
        "{tn} have had a player sent off and are down to {n}.",
        "{tn} se ha quedado con {n} jugadores tras una expulsión.",
        "{tn} se ha quedado con {n} por una expulsión.",
    ),
    "n_own_third": (
        "{tn} keep losing the ball in their own third: {on} have won it there {hr_o} times.",
        "{tn} keep giving the ball away near their own goal: {hr_o} times so far.",
        "{tn} no deja de perder el balón en su propio tercio: {on} lo ha recuperado ahí "
        "{hr_o} veces.",
        "{tn} pierde el balón cerca de su área una y otra vez: {hr_o} veces ya.",
    ),
    "n_deep": (
        "{tn} only win the ball back {th} m from their own goal on average, against {th_o} m "
        "for {on}.",
        "{tn} only win the ball back deep in their own half.",
        "{tn} solo recupera el balón a {th} m de su portería de media, frente a {th_o} m de {on}.",
        "{tn} solo recupera el balón muy cerca de su portería.",
    ),
    "n_little": (
        "{tn} have created little: {shots} shots worth {xg} xG.",
        "{tn} have barely threatened: {shots} shots.",
        "{tn} apenas ha generado peligro: {shots} tiros por un valor de {xg} xG.",
        "{tn} apenas ha inquietado: {shots} tiros.",
    ),
    "n_finishing": (
        "{tn} have scored {g} from {xg} xG.",
        "{tn} have not made their chances count: {g} goals from {xg} xG.",
        "{tn} ha marcado {g} con {xg} xG.",
        "{tn} no está aprovechando sus ocasiones: {g} goles con {xg} xG.",
    ),
    "n_unused": (
        "{tn} have barely used {the}: {share}% of their final-third entries.",
        "{tn} have hardly attacked {via}.",
        "{tn} apenas ha usado {the}: el {share} % de sus llegadas al último tercio.",
        "{tn} casi no ha atacado {via}.",
    ),
    "n_turnovers": (
        "{tn} are giving the ball away in open play {tpm} times a minute, more often than {on}.",
        "{tn} keep giving the ball away.",
        "{tn} pierde el balón en juego {tpm} veces por minuto, más que {on}.",
        "{tn} pierde demasiados balones.",
    ),
    "n_control": (
        "{tn}'s control index is {ci}, against {ci_o} for {on}.",
        "{on} have had more control of the game.",
        "El índice de control de {tn} es {ci}, frente a {ci_o} de {on}.",
        "{on} ha controlado más el partido.",
    ),
    # --- half-time briefing: predictions ---
    "p_block": (
        "{tn} reinforce their {adjflank} side to cut {on}'s {adj}-channel entries",
        "{tn} shut down {on}'s attacks {via}",
        "{tn} refuerza su banda {adjflank} para frenar las llegadas de {on} {via}",
        "{tn} cierra los ataques de {on} {via}",
    ),
    "p_block_centre": (
        "{tn} crowd the middle to cut {on}'s central entries",
        "{tn} close the middle to {on}",
        "{tn} puebla el centro para frenar las llegadas de {on} por dentro",
        "{tn} cierra el centro a {on}",
    ),
    "p_pivot": (
        "{tn} drop a midfielder deeper to beat the press, cutting how often {on} win the ball "
        "in {tn}'s third",
        "{tn} drop a midfielder deeper so they stop losing the ball near their own goal",
        "{tn} retrasa a un centrocampista para superar la presión y reducir las recuperaciones "
        "de {on} en su tercio",
        "{tn} retrasa a un centrocampista para dejar de perder balones cerca de su área",
    ),
    "p_switch": (
        "{tn} switch play away from {the} as {on} adjust, sending more attacks {via_alt}",
        "{tn} change the point of attack and go {via_alt} more",
        "{tn} cambia el juego lejos de {the} cuando {on} se ajusta y ataca más {via_alt}",
        "{tn} cambia de banda y ataca más {via_alt}",
    ),
    "p_fade": (
        "{tn}'s press fades as legs tire, so their PPDA rises",
        "{tn} cannot keep up their pressing intensity",
        "La presión de {tn} decae por el cansancio y su PPDA sube",
        "{tn} no puede mantener la intensidad de su presión",
    ),
    "p_possession": (
        "{tn} push more players forward and see more of the ball",
        "{tn} push forward and get more of the ball",
        "{tn} adelanta líneas y tiene más el balón",
        "{tn} da un paso adelante y tiene más el balón",
    ),
    "p_chase": (
        "{tn} chase more of the game and get into the final third more often",
        "{tn} go for it and attack more",
        "{tn} va a por el partido y llega más al último tercio",
        "{tn} se lanza al ataque",
    ),
    "p_direct": (
        "{tn} move the ball forward more directly after the break",
        "{tn} play more directly after the break",
        "{tn} juega más directo tras el descanso",
        "{tn} juega más directo tras el descanso",
    ),
    # --- half-time briefing: headline ---
    "h_lead": (
        "{lead} lead {gl}-{gt} at the break with {tilt}% of the final-third passing",
        "{lead} lead {gl}-{gt} at half-time and have dominated territory",
        "{lead} gana {gl}-{gt} al descanso con el {tilt} % de los pases en el último tercio",
        "{lead} gana {gl}-{gt} al descanso y domina el territorio",
    ),
    "h_lead_against": (
        "{lead} lead {gl}-{gt} at the break against the run of territory",
        "{lead} lead {gl}-{gt} at half-time despite having less of the ball in attack",
        "{lead} gana {gl}-{gt} al descanso pese a ceder el territorio",
        "{lead} gana {gl}-{gt} al descanso aunque ataca menos",
    ),
    "h_level": (
        "Level at {g1}-{g2}, with {better} edging the chances {xg_b} xG to {xg_w}",
        "Level at {g1}-{g2}, with {better} creating the better chances",
        "Empate {g1}-{g2}, con {better} por delante en ocasiones: {xg_b} xG frente a {xg_w}",
        "Empate {g1}-{g2}, con {better} generando más peligro",
    ),
    # --- watcher explanations ---
    "x_confirmed": (
        "{team}'s {label} {went} from {b} to {a}{unit} after the break, a {size}% {wanted}, so "
        "the prediction holds.",
        "It's working: {team}'s {label} {went} by {size}% after the break.",
        "{team}, {label}: de {b} a {a}{unit} tras el descanso ({size} % de {wanted}). La "
        "predicción se cumple.",
        "Funciona: {label} de {team}, un {size} % de {wanted} tras el descanso.",
    ),
    "x_opposite": (
        "{team}'s {label} {went} from {b} to {a}{unit} after the break: the opposite of the "
        "predicted {wanted}.",
        "Not happening: {team}'s {label} went the other way.",
        "{team}, {label}: de {b} a {a}{unit} tras el descanso, justo lo contrario de lo previsto.",
        "No está pasando: {label} de {team}, en sentido contrario.",
    ),
    "x_short": (
        "{team}'s {label} only {went} from {b} to {a}{unit} after the break, a {size}% change, "
        "well short of the {thr}% {wanted} predicted.",
        "Not happening: little change in {team}'s {label}.",
        "{team}, {label}: de {b} a {a}{unit} tras el descanso, un cambio del {size} %, lejos "
        "del {thr} % previsto.",
        "No está pasando: apenas hay cambios en {label} de {team}.",
    ),
    "x_head_confirmed": (
        "Prediction confirmed",
        "It's working",
        "Predicción confirmada",
        "Funciona",
    ),
    "x_head_contradicted": (
        "Prediction contradicted",
        "Not happening",
        "Predicción fallida",
        "No está pasando",
    ),
    # --- live triggers ---
    "t_channel.h": (
        "{tn} overload {via}",
        "{tn} keep coming {via}",
        "{tn} carga {via}",
        "{tn} no para de atacar {via}",
    ),
    "t_channel.b": (
        "{n} final-third entries {via} in five minutes, against an average of {avg}.",
        "{n} attacks {via} in the last five minutes.",
        "{n} llegadas al último tercio {via} en cinco minutos, frente a una media de {avg}.",
        "{n} ataques {via} en los últimos cinco minutos.",
    ),
    "t_press.h": (
        "{tn}'s press is biting",
        "{tn} are hunting the ball",
        "La presión de {tn} muerde",
        "{tn} va a por el balón",
    ),
    "t_press.b": (
        "{n} ball wins in {on}'s third in five minutes, against an average of {avg}.",
        "They have won the ball {n} times near {on}'s goal in five minutes.",
        "{n} recuperaciones en el tercio de {on} en cinco minutos, frente a una media de {avg}.",
        "Ha recuperado el balón {n} veces cerca del área de {on} en cinco minutos.",
    ),
    "t_height.h": (
        "{tn} winning the ball higher",
        "{tn} pushing up",
        "{tn} recupera más arriba",
        "{tn} da un paso adelante",
    ),
    "t_height.b": (
        "Average ball-win height of {h} m in the last five minutes, against {avg} m.",
        "They are winning the ball back much further up the pitch.",
        "Altura media de recuperación de {h} m en los últimos cinco minutos, frente a {avg} m.",
        "Está recuperando el balón mucho más arriba.",
    ),
    "t_tilt.h": (
        "{tn} camped in {on}'s half",
        "{tn} on top",
        "{tn} instalado en campo de {on}",
        "{tn} manda",
    ),
    "t_tilt.b": (
        "{tilt}% of the final-third passing in the last five minutes.",
        "Almost all the play is in {on}'s half.",
        "El {tilt} % de los pases en el último tercio en los últimos cinco minutos.",
        "Casi todo el juego se desarrolla en campo de {on}.",
    ),
    "t_chances.h": (
        "{tn} creating chances",
        "{tn} getting closer",
        "{tn} genera ocasiones",
        "{tn} se acerca al gol",
    ),
    "t_chances.b": (
        "{xg} xG in the last five minutes.",
        "Some big chances in the last five minutes.",
        "{xg} xG en los últimos cinco minutos.",
        "Ocasiones claras en los últimos cinco minutos.",
    ),
    # --- player tags ---
    "g_shot.h": (
        "{player}: {kmh} km/h strike",
        "Thunderbolt from {player}",
        "{player}: disparo a {kmh} km/h",
        "Cañonazo de {player}",
    ),
    "g_shot.b": (
        "{player}'s fastest shot of the half so far: {kmh} km/h.",
        "That shot from {player} flew at {kmh} km/h.",
        "El disparo más potente de {player} en esta parte: {kmh} km/h.",
        "El disparo de {player} salió a {kmh} km/h.",
    ),
    "g_speed.h": (
        "{player} hits {kmh} km/h",
        "{player} at full speed",
        "{player} alcanza {kmh} km/h",
        "{player} a toda velocidad",
    ),
    "g_speed.b": (
        "Top speed of the half so far, with {sprints} sprints above 25 km/h.",
        "Flat out: {kmh} km/h.",
        "Velocidad punta de esta parte, con {sprints} sprints por encima de 25 km/h.",
        "A tope: {kmh} km/h.",
    ),
    "g_passing.h": (
        "{player} pulling the strings",
        "{player} running the show",
        "{player} mueve los hilos",
        "{player} lleva la batuta",
    ),
    "g_passing.b": (
        "{n} completed passes rated difficult this half, average difficulty {avg}.",
        "{n} tough passes completed this half.",
        "{n} pases difíciles completados en esta parte, con una dificultad media de {avg}.",
        "{n} pases difíciles completados en esta parte.",
    ),
    # --- recap ---
    "r_head_draw": (
        "{hn} and {an} draw {g1}-{g2}",
        "{hn} and {an} share the points: {g1}-{g2}",
        "{hn} y {an} empatan {g1}-{g2}",
        "Reparto de puntos: {hn} {g1}-{g2} {an}",
    ),
    "r_head_win": (
        "{w} beat {l} {gw}-{gl}",
        "{w} win {gw}-{gl} against {l}",
        "{w} gana a {l} por {gw}-{gl}",
        "Victoria de {w} ante {l}: {gw}-{gl}",
    ),
    "r_change": (
        "{team}'s {label} {went} from {b} to {a}{unit} after the break.",
        "After the break, {team}'s {label} {went} from {b} to {a}{unit}.",
        "{team}, {label}: de {b} a {a}{unit} tras el descanso.",
        "Tras el descanso, {label} de {team}: de {b} a {a}{unit}.",
    ),
    "t_red.h": (
        "{tn} down to {n} men",
        "{tn} down to {n}!",
        "{tn}, con {n} jugadores",
        "¡{tn} se queda con {n}!",
    ),
    "t_red.b": (
        "A red card in the last five minutes changes the balance of the game.",
        "A player has been sent off.",
        "Una expulsión en los últimos cinco minutos cambia el partido.",
        "Han expulsado a un jugador.",
    ),
    "r_scored": (
        "{team} scored {g} after the break from {xg} xG.",
        "{team} scored {g} after the break.",
        "{team} marcó {g} tras el descanso con {xg} xG.",
        "{team} marcó {g} tras el descanso.",
    ),
    "r_final": (
        "It finished {hn} {g1}, {an} {g2}.",
        "Final score: {hn} {g1}, {an} {g2}.",
        "Final: {hn} {g1}, {an} {g2}.",
        "Resultado final: {hn} {g1}, {an} {g2}.",
    ),
    "r_red": (
        "{tn} finished a man down after a red card.",
        "{tn} had a player sent off.",
        "{tn} terminó con uno menos tras una expulsión.",
        "{tn} se quedó con uno menos.",
    ),
    "r_possession": (
        "{tn} had {poss}% of the ball after the break.",
        "{tn} had {poss}% of the ball after the break.",
        "{tn} tuvo el {poss} % del balón tras el descanso.",
        "{tn} tuvo el {poss} % del balón tras el descanso.",
    ),
    # --- club framing ---
    "f_good": (
        "Good sign for {club}: ",
        "Good news for {club} fans: ",
        "Buena señal para {club}: ",
        "Buenas noticias para {club}: ",
    ),
    "f_bad": (
        "Warning for {club}: ",
        "Worry for {club}: ",
        "Aviso para {club}: ",
        "Alarma en {club}: ",
    ),
    "f_view": ("For {club}: ", "For {club} fans: ", "Para {club}: ", "Para la afición de {club}: "),
}

WORDS: dict[str, tuple[str, str, str, str]] = {
    "rose": ("rose", "rose", "subida", "subida"),
    "fell": ("fell", "fell", "bajada", "bajada"),
    "stayed level": ("stayed level", "stayed level", "sin cambio", "sin cambio"),
    "rise": ("rise", "rise", "subida", "subida"),
    "drop": ("drop", "drop", "bajada", "bajada"),
}

_INDEX = {("analyst", "en"): 0, ("casual", "en"): 1, ("analyst", "es"): 2, ("casual", "es"): 3}


def variant_index(audience: Audience, language: Language) -> int:
    """Position of a variant in the catalogue tuples."""
    return _INDEX[(audience, language)]


def word(key: str, audience: Audience, language: Language) -> str:
    """A small translated word (rose/fell/rise/drop)."""
    return WORDS[key][variant_index(audience, language)]


def metric_name(metric: str, channel: str | None, audience: Audience, language: Language) -> str:
    """Display name of a metric, with its channel filled in."""
    template = METRIC_NAMES[metric][variant_index(audience, language)]
    forms = CHANNEL_FORMS[language]
    ch = channel or "centre"
    return template.format(via=forms["via"][ch], adj=forms["adj"][ch])


def unit_suffix(unit: str, language: Language) -> str:
    """Suffix for a value of the given unit (``per_window`` -> per five minutes)."""
    return UNIT_SUFFIX[language].get(unit, "")


def render(
    key: str, params: dict, audience: Audience = "analyst", language: Language = "en"
) -> str:
    """Fill phrase ``key`` for one variant."""
    template = PHRASES[key][variant_index(audience, language)]
    forms = CHANNEL_FORMS[language]
    values: dict[str, str] = {}
    for name, value in params.items():
        if isinstance(value, Num):
            values[name] = fmt_number(value.value, language)
        elif isinstance(value, Metric):
            values[name] = metric_name(value.metric, value.channel, audience, language)
        elif isinstance(value, Word):
            values[name] = word(value.key, audience, language)
        elif isinstance(value, Unit):
            values[name] = unit_suffix(value.unit, language)
        elif isinstance(value, Chan):
            for form, names in forms.items():
                suffix = "" if name == "ch" else name.removeprefix("ch")
                values[f"{form}{suffix}"] = names[value.channel]
        else:
            values[name] = str(value)
    return template.format(**values)
