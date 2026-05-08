"""Shared buddy companion model, rendering, and transient runtime state."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from textwrap import wrap
from typing import Literal

from prompt_toolkit.formatted_text import FormattedText
from rich.align import Align
from rich.console import RenderResult
from rich.table import Table
from rich.text import Text

from koder_agent.harness.config.schema import HarnessCompanionConfig, RuntimeConfig

COMPANION_ASSISTANT_GUIDANCE = """Companion note:
A small companion may sit beside the user's input box and occasionally comment in a speech bubble.
You are not the companion.
If the user addresses the companion directly, keep your response to one line or answer only the part meant for you.
Do not narrate what the companion says; the bubble handles that."""

RARITIES = ("common", "uncommon", "rare", "epic", "legendary")
RARITY_WEIGHTS = {
    "common": 60,
    "uncommon": 25,
    "rare": 10,
    "epic": 4,
    "legendary": 1,
}
SPECIES = (
    "duck",
    "goose",
    "blob",
    "cat",
    "dragon",
    "octopus",
    "owl",
    "penguin",
    "turtle",
    "snail",
    "ghost",
    "axolotl",
    "capybara",
    "cactus",
    "robot",
    "rabbit",
    "mushroom",
    "chonk",
)
EYES = ("·", "✦", "×", "◉", "@", "°")
HATS = ("none", "crown", "tophat", "propeller", "halo", "wizard", "beanie", "tinyduck")
STAT_NAMES = ("DEBUGGING", "PATIENCE", "CHAOS", "WISDOM", "SNARK")
RARITY_FLOOR = {
    "common": 5,
    "uncommon": 15,
    "rare": 25,
    "epic": 35,
    "legendary": 50,
}
SOUL_NAMES = (
    "Pip",
    "Mochi",
    "Byte",
    "Nori",
    "Pebble",
    "Nova",
    "Orbit",
    "Fig",
    "Pixel",
    "Comet",
    "Echo",
    "Tango",
)
PERSONALITY_BY_STAT = {
    "DEBUGGING": "quietly forensic",
    "PATIENCE": "calm under pressure",
    "CHAOS": "mischievously chaotic",
    "WISDOM": "oddly wise",
    "SNARK": "dryly funny",
}
PET_REACTIONS = (
    "perks up immediately.",
    "leans into the attention.",
    "does a tiny celebratory wiggle.",
    "looks pleased with itself.",
)
SALT = "friend-2026-401"
TICK_MS = 500
PET_BURST_MS = 2500
REACTION_BUBBLE_SECONDS = 4.0
MIN_COLS_FOR_FULL_SPRITE = 100
IDLE_SEQUENCE = (0, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 2, 0, 0, 0)
HEART = "♥"
PET_HEARTS = (
    f"   {HEART}    {HEART}   ",
    f"  {HEART}  {HEART}   {HEART}  ",
    f" {HEART}   {HEART}  {HEART}   ",
    f"{HEART}  {HEART}      {HEART} ",
    ".    .   .  ",
)
PROMPT_RARITY_STYLES = {
    "common": "ansibrightblack",
    "uncommon": "ansigreen",
    "rare": "ansicyan",
    "epic": "ansimagenta",
    "legendary": "ansiyellow",
}
RICH_RARITY_STYLES = {
    "common": "grey62",
    "uncommon": "green",
    "rare": "cyan",
    "epic": "magenta",
    "legendary": "yellow",
}
_TASK_REACTIONS = {
    "run_shell": "sniffing the shell smoke",
    "read_file": "reading the room",
    "edit_file": "nudging the diff",
    "glob_search": "combing the tree",
    "grep_search": "tracking a scent",
    "web_search": "scanning the horizon",
    "task_delegate": "rounding up a helper",
}
_DIRECT_ADDRESS_REACTIONS = (
    "tilts its head and chirps a tiny yes.",
    "scoots closer to the diff and squints.",
    "looks up like it definitely has an opinion.",
)
BODIES: dict[str, tuple[tuple[str, ...], ...]] = {
    "duck": (
        (
            "            ",
            "    __      ",
            "  <({E} )___  ",
            "   (  ._>   ",
            "    `--´    ",
        ),
        (
            "            ",
            "    __      ",
            "  <({E} )___  ",
            "   (  ._>   ",
            "    `--´~   ",
        ),
        (
            "            ",
            "    __      ",
            "  <({E} )___  ",
            "   (  .__>  ",
            "    `--´    ",
        ),
    ),
    "goose": (
        (
            "            ",
            "     ({E}>    ",
            "     ||     ",
            "   _(__)_   ",
            "    ^^^^    ",
        ),
        (
            "            ",
            "    ({E}>     ",
            "     ||     ",
            "   _(__)_   ",
            "    ^^^^    ",
        ),
        (
            "            ",
            "     ({E}>>   ",
            "     ||     ",
            "   _(__)_   ",
            "    ^^^^    ",
        ),
    ),
    "blob": (
        (
            "            ",
            "   .----.   ",
            "  ( {E}  {E} )  ",
            "  (      )  ",
            "   `----´   ",
        ),
        (
            "            ",
            "  .------.  ",
            " (  {E}  {E}  ) ",
            " (        ) ",
            "  `------´  ",
        ),
        (
            "            ",
            "    .--.    ",
            "   ({E}  {E})   ",
            "   (    )   ",
            "    `--´    ",
        ),
    ),
    "cat": (
        (
            "            ",
            "   /\\_/\\    ",
            "  ( {E}   {E})  ",
            "  (  ω  )   ",
            '  (")_(")   ',
        ),
        (
            "            ",
            "   /\\_/\\    ",
            "  ( {E}   {E})  ",
            "  (  ω  )   ",
            '  (")_(")~  ',
        ),
        (
            "            ",
            "   /\\-/\\    ",
            "  ( {E}   {E})  ",
            "  (  ω  )   ",
            '  (")_(")   ',
        ),
    ),
    "dragon": (
        (
            "            ",
            "  /^\\  /^\\  ",
            " <  {E}  {E}  > ",
            " (   ~~   ) ",
            "  `-vvvv-´  ",
        ),
        (
            "            ",
            "  /^\\  /^\\  ",
            " <  {E}  {E}  > ",
            " (        ) ",
            "  `-vvvv-´  ",
        ),
        (
            "   ~    ~   ",
            "  /^\\  /^\\  ",
            " <  {E}  {E}  > ",
            " (   ~~   ) ",
            "  `-vvvv-´  ",
        ),
    ),
    "octopus": (
        (
            "            ",
            "   .----.   ",
            "  ( {E}  {E} )  ",
            "  (______)  ",
            "  /\\/\\/\\/\\  ",
        ),
        (
            "            ",
            "   .----.   ",
            "  ( {E}  {E} )  ",
            "  (______)  ",
            "  \\/\\/\\/\\/  ",
        ),
        (
            "     o      ",
            "   .----.   ",
            "  ( {E}  {E} )  ",
            "  (______)  ",
            "  /\\/\\/\\/\\  ",
        ),
    ),
    "owl": (
        (
            "            ",
            "   /\\  /\\   ",
            "  (({E})({E}))  ",
            "  (  ><  )  ",
            "   `----´   ",
        ),
        (
            "            ",
            "   /\\  /\\   ",
            "  (({E})({E}))  ",
            "  (  ><  )  ",
            "   .----.   ",
        ),
        (
            "            ",
            "   /\\  /\\   ",
            "  (({E})(-))  ",
            "  (  ><  )  ",
            "   `----´   ",
        ),
    ),
    "penguin": (
        (
            "            ",
            "  .---.     ",
            "  ({E}>{E})     ",
            " /(   )\\    ",
            "  `---´     ",
        ),
        (
            "            ",
            "  .---.     ",
            "  ({E}>{E})     ",
            " |(   )|    ",
            "  `---´     ",
        ),
        (
            "  .---.     ",
            "  ({E}>{E})     ",
            " /(   )\\    ",
            "  `---´     ",
            "   ~ ~      ",
        ),
    ),
    "turtle": (
        (
            "            ",
            "   _,--._   ",
            "  ( {E}  {E} )  ",
            " /[______]\\ ",
            "  ``    ``  ",
        ),
        (
            "            ",
            "   _,--._   ",
            "  ( {E}  {E} )  ",
            " /[______]\\ ",
            "   ``  ``   ",
        ),
        (
            "            ",
            "   _,--._   ",
            "  ( {E}  {E} )  ",
            " /[======]\\ ",
            "  ``    ``  ",
        ),
    ),
    "snail": (
        (
            "            ",
            " {E}    .--.  ",
            "  \\  ( @ )  ",
            "   \\_`--´   ",
            "  ~~~~~~~   ",
        ),
        (
            "            ",
            "  {E}   .--.  ",
            "  |  ( @ )  ",
            "   \\_`--´   ",
            "  ~~~~~~~   ",
        ),
        (
            "            ",
            " {E}    .--.  ",
            "  \\  ( @  ) ",
            "   \\_`--´   ",
            "   ~~~~~~   ",
        ),
    ),
    "ghost": (
        (
            "            ",
            "   .----.   ",
            "  / {E}  {E} \\  ",
            "  |      |  ",
            "  ~`~``~`~  ",
        ),
        (
            "            ",
            "   .----.   ",
            "  / {E}  {E} \\  ",
            "  |      |  ",
            "  `~`~~`~`  ",
        ),
        (
            "    ~  ~    ",
            "   .----.   ",
            "  / {E}  {E} \\  ",
            "  |      |  ",
            "  ~~`~~`~~  ",
        ),
    ),
    "axolotl": (
        (
            "            ",
            "}~(______)~{",
            "}~({E} .. {E})~{",
            "  ( .--. )  ",
            "  (_/  \\_)  ",
        ),
        (
            "            ",
            "~}(______){~",
            "~}({E} .. {E}){~",
            "  ( .--. )  ",
            "  (_/  \\_)  ",
        ),
        (
            "            ",
            "}~(______)~{",
            "}~({E} .. {E})~{",
            "  (  --  )  ",
            "  ~_/  \\_~  ",
        ),
    ),
    "capybara": (
        (
            "            ",
            "  n______n  ",
            " ( {E}    {E} ) ",
            " (   oo   ) ",
            "  `------´  ",
        ),
        (
            "            ",
            "  n______n  ",
            " ( {E}    {E} ) ",
            " (   Oo   ) ",
            "  `------´  ",
        ),
        (
            "    ~  ~    ",
            "  u______n  ",
            " ( {E}    {E} ) ",
            " (   oo   ) ",
            "  `------´  ",
        ),
    ),
    "cactus": (
        (
            "            ",
            " n  ____  n ",
            " | |{E}  {E}| | ",
            " |_|    |_| ",
            "   |    |   ",
        ),
        (
            "            ",
            "    ____    ",
            " n |{E}  {E}| n ",
            " |_|    |_| ",
            "   |    |   ",
        ),
        (
            " n        n ",
            " |  ____  | ",
            " | |{E}  {E}| | ",
            " |_|    |_| ",
            "   |    |   ",
        ),
    ),
    "robot": (
        (
            "            ",
            "   .[||].   ",
            "  [ {E}  {E} ]  ",
            "  [ ==== ]  ",
            "  `------´  ",
        ),
        (
            "            ",
            "   .[||].   ",
            "  [ {E}  {E} ]  ",
            "  [ -==- ]  ",
            "  `------´  ",
        ),
        (
            "     *      ",
            "   .[||].   ",
            "  [ {E}  {E} ]  ",
            "  [ ==== ]  ",
            "  `------´  ",
        ),
    ),
    "rabbit": (
        (
            "            ",
            "   (\\__/)   ",
            "  ( {E}  {E} )  ",
            " =(  ..  )= ",
            '  (")__(")  ',
        ),
        (
            "            ",
            "   (|__/)   ",
            "  ( {E}  {E} )  ",
            " =(  ..  )= ",
            '  (")__(")  ',
        ),
        (
            "            ",
            "   (\\__/)   ",
            "  ( {E}  {E} )  ",
            " =( .  . )= ",
            '  (")__(")  ',
        ),
    ),
    "mushroom": (
        (
            "            ",
            " .-o-OO-o-. ",
            "(__________)",
            "   |{E}  {E}|   ",
            "   |____|   ",
        ),
        (
            "            ",
            " .-O-oo-O-. ",
            "(__________)",
            "   |{E}  {E}|   ",
            "   |____|   ",
        ),
        (
            "   . o  .   ",
            " .-o-OO-o-. ",
            "(__________)",
            "   |{E}  {E}|   ",
            "   |____|   ",
        ),
    ),
    "chonk": (
        (
            "            ",
            "  /\\    /\\  ",
            " ( {E}    {E} ) ",
            " (   ..   ) ",
            "  `------´  ",
        ),
        (
            "            ",
            "  /\\    /|  ",
            " ( {E}    {E} ) ",
            " (   ..   ) ",
            "  `------´  ",
        ),
        (
            "            ",
            "  /\\    /\\  ",
            " ( {E}    {E} ) ",
            " (   ..   ) ",
            "  `------´~ ",
        ),
    ),
}
HAT_LINES = {
    "none": "",
    "crown": "   \\^^^/    ",
    "tophat": "   [___]    ",
    "propeller": "    -+-     ",
    "halo": "   (   )    ",
    "wizard": "    /^\\     ",
    "beanie": "   (___)    ",
    "tinyduck": "    ,>      ",
}


@dataclass(frozen=True)
class CompanionBones:
    rarity: str
    species: str
    eye: str
    hat: str
    shiny: bool
    stats: dict[str, int]


@dataclass(frozen=True)
class Companion:
    name: str
    personality: str
    hatched_at: int
    rarity: str
    species: str
    eye: str
    hat: str
    shiny: bool
    stats: dict[str, int]


@dataclass(frozen=True)
class BuddyDisplayState:
    busy: bool = False
    reaction: str | None = None
    pet_at: float | None = None


@dataclass(frozen=True)
class CompanionRender:
    lines: tuple[str, ...]
    line_roles: tuple[Literal["bubble", "heart", "sprite", "name"], ...]
    width: int


class BuddyRuntime:
    """Tracks transient companion UI state for the current process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._busy = False
            self._reaction: str | None = None
            self._reaction_until = 0.0
            self._reaction_source: str | None = None
            self._pet_at: float | None = None

    def snapshot(self, *, now: float | None = None) -> BuddyDisplayState:
        current = time.time() if now is None else now
        with self._lock:
            reaction = self._reaction
            if reaction is not None and current >= self._reaction_until:
                self._reaction = None
                self._reaction_until = 0.0
                self._reaction_source = None
                reaction = None
            return BuddyDisplayState(
                busy=self._busy,
                reaction=reaction,
                pet_at=self._pet_at,
            )

    def mark_hatched(self) -> None:
        with self._lock:
            self._busy = False
            self._reaction = None
            self._reaction_until = 0.0
            self._reaction_source = None

    def mark_pet(self, reaction: str) -> None:
        now = time.time()
        with self._lock:
            self._pet_at = now
            self._reaction = reaction
            self._reaction_until = now + REACTION_BUBBLE_SECONDS
            self._reaction_source = "pet"

    def mark_observer(self, reaction: str) -> None:
        now = time.time()
        with self._lock:
            self._reaction = reaction
            self._reaction_until = now + REACTION_BUBBLE_SECONDS
            self._reaction_source = "observer"

    def mark_task_start(self) -> None:
        with self._lock:
            self._busy = True

    def mark_tool_call(self, tool_name: str) -> None:
        now = time.time()
        reaction = _TASK_REACTIONS.get(tool_name) or f"tracking {tool_name}..."
        with self._lock:
            self._busy = True
            self._reaction = reaction
            self._reaction_until = now + REACTION_BUBBLE_SECONDS
            self._reaction_source = "task"

    def mark_task_complete(self) -> None:
        with self._lock:
            self._busy = False
            if self._reaction_source == "task":
                self._reaction = None
                self._reaction_until = 0.0
                self._reaction_source = None


buddy_runtime = BuddyRuntime()


def _mulberry32(seed: int):
    state = seed & 0xFFFFFFFF

    def _next() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        value = state
        value = (((value ^ (value >> 15)) * (1 | value)) & 0xFFFFFFFF) ^ value
        value = (
            value + ((((value ^ (value >> 7)) * (61 | value)) & 0xFFFFFFFF) ^ value)
        ) & 0xFFFFFFFF
        return ((value ^ (value >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return _next


def _hash_string(text: str) -> int:
    result = 2166136261
    for char in text:
        result ^= ord(char)
        result = (result * 16777619) & 0xFFFFFFFF
    return result


def _pick(rng, values: tuple[str, ...]) -> str:
    return values[int(rng() * len(values))]


def _roll_rarity(rng) -> str:
    total = sum(RARITY_WEIGHTS.values())
    roll = rng() * total
    for rarity in RARITIES:
        roll -= RARITY_WEIGHTS[rarity]
        if roll < 0:
            return rarity
    return "common"


def _roll_stats(rng, rarity: str) -> dict[str, int]:
    floor = RARITY_FLOOR[rarity]
    peak = _pick(rng, STAT_NAMES)
    dump = _pick(rng, STAT_NAMES)
    while dump == peak:
        dump = _pick(rng, STAT_NAMES)
    stats: dict[str, int] = {}
    for name in STAT_NAMES:
        if name == peak:
            stats[name] = min(100, floor + 50 + int(rng() * 30))
        elif name == dump:
            stats[name] = max(1, floor - 10 + int(rng() * 15))
        else:
            stats[name] = floor + int(rng() * 40)
    return stats


@lru_cache(maxsize=128)
def _roll_bones(seed: str) -> CompanionBones:
    rng = _mulberry32(_hash_string(seed + SALT))
    rarity = _roll_rarity(rng)
    return CompanionBones(
        rarity=rarity,
        species=_pick(rng, SPECIES),
        eye=_pick(rng, EYES),
        hat="none" if rarity == "common" else _pick(rng, HATS),
        shiny=rng() < 0.01,
        stats=_roll_stats(rng, rarity),
    )


def _top_stat(stats: dict[str, int]) -> str:
    return max(stats.items(), key=lambda item: item[1])[0]


def companion_seed() -> str:
    return (
        os.environ.get("KODER_BUDDY_SEED")
        or os.environ.get("SAFEUSER")
        or os.environ.get("USER")
        or "anon"
    )


def hatch_companion(*, seed: str | None = None, now: float | None = None) -> Companion:
    actual_seed = seed or companion_seed()
    bones = _roll_bones(actual_seed)
    rng = _mulberry32(_hash_string(actual_seed + ":soul"))
    name = _pick(rng, SOUL_NAMES)
    top_stat = _top_stat(bones.stats)
    personality = PERSONALITY_BY_STAT[top_stat]
    hatched_at = int((time.time() if now is None else now))
    return Companion(
        name=name,
        personality=personality,
        hatched_at=hatched_at,
        rarity=bones.rarity,
        species=bones.species,
        eye=bones.eye,
        hat=bones.hat,
        shiny=bones.shiny,
        stats=bones.stats,
    )


def to_stored_companion(companion: Companion) -> HarnessCompanionConfig:
    return HarnessCompanionConfig(
        name=companion.name,
        personality=companion.personality,
        hatched_at=companion.hatched_at,
    )


def get_companion(config: RuntimeConfig, *, seed: str | None = None) -> Companion | None:
    stored = config.harness.companion
    if stored is None:
        return None
    bones = _roll_bones(seed or companion_seed())
    return Companion(
        name=stored.name,
        personality=stored.personality,
        hatched_at=stored.hatched_at,
        rarity=bones.rarity,
        species=bones.species,
        eye=bones.eye,
        hat=bones.hat,
        shiny=bones.shiny,
        stats=bones.stats,
    )


def render_profile(prefix: str, companion: Companion) -> str:
    lines = [
        prefix,
        f"name: {companion.name}",
        f"species: {companion.species}",
        f"rarity: {companion.rarity}",
        f"personality: {companion.personality}",
    ]
    if companion.shiny:
        lines.append("shiny: true")
    return "\n".join(lines)


def reaction_for_name(name: str) -> str:
    rng = _mulberry32(_hash_string(name + ":pet"))
    return PET_REACTIONS[int(rng() * len(PET_REACTIONS))]


def _mentions_name(text: str, name: str) -> bool:
    lowered = text.lower()
    target = name.lower()
    if target not in lowered:
        return False
    for separator in (" ", ",", ":", "!", "?", ".", "\n", "\t", "'", '"'):
        lowered = lowered.replace(separator, " ")
    tokens = [token for token in lowered.split(" ") if token]
    return target in tokens


def observe_turn(
    *,
    companion: Companion,
    user_input: str,
    assistant_output: str,
) -> str | None:
    """Generate a short companion quip from the latest turn."""
    source = f"{user_input}\n{assistant_output}".lower()

    if _mentions_name(user_input, companion.name):
        rng = _mulberry32(_hash_string(companion.name + ":" + user_input))
        reply = _DIRECT_ADDRESS_REACTIONS[int(rng() * len(_DIRECT_ADDRESS_REACTIONS))]
        return f"{companion.name} {reply}"

    if "pass" in source and "test" in source:
        return "does a tiny victory lap."
    if any(term in source for term in ("error", "failed", "exception", "traceback")):
        return "winces at the stack trace."
    if any(term in source for term in ("todo", "watch out", "follow-up", "follow up")):
        return "keeps one eye on the loose ends."
    if any(term in source for term in ("diff", "patch", "edit", "refactor", "rename")):
        return "noses at the fresh diff."
    return None


def render_face(companion: Companion) -> str:
    eye = companion.eye
    if companion.species in {"duck", "goose"}:
        return f"({eye}>"
    if companion.species == "blob":
        return f"({eye}{eye})"
    if companion.species == "cat":
        return f"={eye}ω{eye}="
    if companion.species == "dragon":
        return f"<{eye}~{eye}>"
    if companion.species == "octopus":
        return f"~({eye}{eye})~"
    if companion.species == "owl":
        return f"({eye})({eye})"
    if companion.species == "penguin":
        return f"({eye}>)"
    if companion.species == "turtle":
        return f"[{eye}_{eye}]"
    if companion.species == "snail":
        return f"{eye}(@)"
    if companion.species == "ghost":
        return f"/{eye}{eye}\\"
    if companion.species == "axolotl":
        return f"}}{eye}.{eye}{{"
    if companion.species == "capybara":
        return f"({eye}oo{eye})"
    if companion.species in {"cactus", "mushroom"}:
        return f"|{eye}  {eye}|"
    if companion.species == "robot":
        return f"[{eye}{eye}]"
    if companion.species == "rabbit":
        return f"({eye}..{eye})"
    return f"({eye}.{eye})"


def sprite_frame_count(species: str) -> int:
    return len(BODIES[species])


def render_sprite(companion: Companion, frame: int = 0) -> list[str]:
    frames = BODIES[companion.species]
    body = [line.replace("{E}", companion.eye) for line in frames[frame % len(frames)]]
    lines = list(body)
    if companion.hat != "none" and not lines[0].strip():
        lines[0] = HAT_LINES[companion.hat]
    if not lines[0].strip() and all(not frame_lines[0].strip() for frame_lines in frames):
        lines.pop(0)
    return lines


def _bubble_lines(text: str, *, width: int = 26) -> list[str]:
    wrapped = wrap(text, max(8, width - 4)) or [text]
    inner_width = max(len(line) for line in wrapped)
    top = "." + "-" * (inner_width + 2) + "."
    middle = [f"| {line.ljust(inner_width)} |" for line in wrapped]
    bottom = "'" + "-" * (inner_width + 2) + "'"
    tail_indent = max(1, inner_width // 2)
    return [top, *middle, bottom, f"{' ' * tail_indent}\\"]


def render_companion(
    companion: Companion,
    *,
    state: BuddyDisplayState,
    now: float | None = None,
    columns: int = 120,
    show_reaction: bool = False,
) -> CompanionRender:
    current = time.time() if now is None else now
    tick = int((current * 1000) // TICK_MS)
    petting = state.pet_at is not None and (current - state.pet_at) * 1000 < PET_BURST_MS
    reaction = state.reaction if show_reaction else None

    if columns < MIN_COLS_FOR_FULL_SPRITE:
        label = (
            f'"{reaction[:24]}{"…" if reaction and len(reaction) > 24 else ""}"'
            if reaction
            else companion.name
        )
        prefix = f"{HEART} " if petting else ""
        line = f"{prefix}{render_face(companion)} {label}"
        return CompanionRender(lines=(line,), line_roles=("name",), width=len(line))

    frame_count = sprite_frame_count(companion.species)
    if state.busy or reaction or petting:
        sprite_frame = tick % frame_count
        blink = False
    else:
        step = IDLE_SEQUENCE[tick % len(IDLE_SEQUENCE)]
        blink = step == -1
        sprite_frame = 0 if blink else step % frame_count

    sprite_lines = render_sprite(companion, sprite_frame)
    if blink:
        sprite_lines = [line.replace(companion.eye, "-") for line in sprite_lines]

    lines: list[str] = []
    roles: list[Literal["bubble", "heart", "sprite", "name"]] = []
    if reaction:
        bubble = _bubble_lines(reaction)
        lines.extend(bubble)
        roles.extend(["bubble"] * len(bubble))
    if petting:
        lines.append(PET_HEARTS[tick % len(PET_HEARTS)])
        roles.append("heart")
    lines.extend(sprite_lines)
    roles.extend(["sprite"] * len(sprite_lines))
    lines.append(companion.name)
    roles.append("name")
    width = max(len(line) for line in lines) if lines else 0
    return CompanionRender(lines=tuple(lines), line_roles=tuple(roles), width=width)


def render_companion_lines(
    companion: Companion,
    *,
    state: BuddyDisplayState,
    now: float | None = None,
    columns: int = 120,
    show_reaction: bool = False,
) -> tuple[str, ...]:
    return render_companion(
        companion,
        state=state,
        now=now,
        columns=columns,
        show_reaction=show_reaction,
    ).lines


def companion_reserved_columns(config: RuntimeConfig, *, columns: int) -> int:
    companion = get_companion(config)
    if companion is None or config.harness.companion_muted:
        return 0
    render = render_companion(companion, state=buddy_runtime.snapshot(), columns=columns)
    return render.width


def _prompt_style_for(role: str, rarity: str) -> str:
    if role == "bubble":
        return "ansibrightblack"
    if role == "heart":
        return "ansigreen"
    rarity_style = PROMPT_RARITY_STYLES[rarity]
    if role == "name":
        return f"bold {rarity_style}"
    return rarity_style


def _rich_style_for(role: str, rarity: str) -> str:
    if role == "bubble":
        return "grey62"
    if role == "heart":
        return "green"
    rarity_style = RICH_RARITY_STYLES[rarity]
    if role == "name":
        return f"bold {rarity_style}"
    return rarity_style


def build_prompt_toolkit_text(
    config: RuntimeConfig,
    *,
    now: float | None = None,
    columns: int = 120,
    show_reaction: bool = False,
) -> FormattedText:
    companion = get_companion(config)
    if companion is None or config.harness.companion_muted:
        return FormattedText([])
    state = buddy_runtime.snapshot(now=now)
    render = render_companion(
        companion,
        state=state,
        now=now,
        columns=columns,
        show_reaction=show_reaction,
    )
    fragments: list[tuple[str, str]] = []
    for index, line in enumerate(render.lines):
        fragments.append((_prompt_style_for(render.line_roles[index], companion.rarity), line))
        if index < len(render.lines) - 1:
            fragments.append(("", "\n"))
    return FormattedText(fragments)


def build_rich_text(
    config: RuntimeConfig,
    *,
    now: float | None = None,
    columns: int = 120,
    show_reaction: bool = False,
) -> Text | None:
    companion = get_companion(config)
    if companion is None or config.harness.companion_muted:
        return None
    state = buddy_runtime.snapshot(now=now)
    render = render_companion(
        companion,
        state=state,
        now=now,
        columns=columns,
        show_reaction=show_reaction,
    )
    text = Text()
    for index, line in enumerate(render.lines):
        text.append(line, style=_rich_style_for(render.line_roles[index], companion.rarity))
        if index < len(render.lines) - 1:
            text.append("\n")
    return text


class BuddyLiveLayout:
    """Rich renderable that keeps the companion visible beside streaming output."""

    def __init__(self, *, body_getter, config_getter, show_reaction: bool = True):
        self._body_getter = body_getter
        self._config_getter = config_getter
        self._show_reaction = show_reaction

    def __rich_console__(self, console, options) -> RenderResult:
        body = self._body_getter()
        companion = build_rich_text(
            self._config_getter(),
            now=time.time(),
            columns=options.max_width,
            show_reaction=self._show_reaction,
        )
        if companion is None:
            yield body
            return

        companion_width = max((len(line) for line in companion.plain.splitlines()), default=0)
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(ratio=1)
        grid.add_column(width=max(14, companion_width))
        grid.add_row(body or Text(""), Align.left(companion))
        yield grid
