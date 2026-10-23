"""Content languages (round 9, decision 19.24): what a summary, a translation, chapters or an answer is written in.

Hungarian keeps its own, proven Hungarian instructions (summarize.py, translate.py …); every other language gets one
English instruction with the target language as a parameter. This table gives the English name of the language
(for that instruction), the word of the speaker labels ("Szereplő 1", "Speaker 1", "Sprecher 1" …) and the summary's
section headings.
"""
from __future__ import annotations

SOURCE = "hu"

# code: (English name, speaker word, (summary, key points, speakers))
TABLE: dict[str, tuple[str, str, tuple[str, str, str]]] = {
    "hu": ("Hungarian", "Szereplő", ("Összefoglaló", "Fő pontok", "Beszélők")),
    "en": ("English", "Speaker", ("Summary", "Key points", "Speakers")),
    "de": ("German", "Sprecher", ("Zusammenfassung", "Hauptpunkte", "Sprecher")),
    "it": ("Italian", "Interlocutore", ("Riepilogo", "Punti principali", "Interlocutori")),
    "es": ("Spanish", "Hablante", ("Resumen", "Puntos principales", "Hablantes")),
    "fr": ("French", "Intervenant", ("Résumé", "Points clés", "Intervenants")),
    "nl": ("Dutch", "Spreker", ("Samenvatting", "Belangrijkste punten", "Sprekers")),
    "pt": ("Portuguese", "Orador", ("Resumo", "Pontos principais", "Oradores")),
    "sv": ("Swedish", "Talare", ("Sammanfattning", "Huvudpunkter", "Talare")),
    "da": ("Danish", "Taler", ("Resumé", "Hovedpunkter", "Talere")),
    "fi": ("Finnish", "Puhuja", ("Tiivistelmä", "Pääkohdat", "Puhujat")),
    "ro": ("Romanian", "Vorbitor", ("Rezumat", "Idei principale", "Vorbitori")),
    "pl": ("Polish", "Mówca", ("Podsumowanie", "Najważniejsze punkty", "Mówcy")),
    "cs": ("Czech", "Mluvčí", ("Shrnutí", "Hlavní body", "Mluvčí")),
    "lt": ("Lithuanian", "Kalbėtojas", ("Santrauka", "Pagrindiniai punktai", "Kalbėtojai")),
    "sk": ("Slovak", "Rečník", ("Zhrnutie", "Hlavné body", "Rečníci")),
    "sl": ("Slovenian", "Govorec", ("Povzetek", "Glavne točke", "Govorci")),
    "hr": ("Croatian", "Govornik", ("Sažetak", "Glavne točke", "Govornici")),
    "bg": ("Bulgarian", "Говорител", ("Резюме", "Основни моменти", "Говорители")),
    "lv": ("Latvian", "Runātājs", ("Kopsavilkums", "Galvenie punkti", "Runātāji")),
}
# source languages a transcript may be in, beyond the 20 (English names for the instructions)
OTHER_NAMES = {"ru": "Russian", "uk": "Ukrainian", "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
               "tr": "Turkish", "el": "Greek", "sr": "Serbian", "no": "Norwegian", "ar": "Arabic", "he": "Hebrew"}


def code(value: str | None, default: str = SOURCE) -> str:
    """'de-AT' -> 'de'; an unknown or empty value -> default."""
    short = (value or "").split("-")[0].split("_")[0].lower()
    return short if short in TABLE else default


def is_source(value: str | None) -> bool:
    """Hungarian (also when not given: older centrals send no language)."""
    return code(value) == SOURCE


def english_name(value: str | None) -> str:
    short = (value or "").split("-")[0].lower()
    if short in TABLE:
        return TABLE[short][0]
    return OTHER_NAMES.get(short, value or "unknown")


def speaker_word(value: str | None) -> str:
    """The word of the speaker labels in a language; English for one outside the table."""
    short = (value or "").split("-")[0].lower()
    return TABLE[short][1] if short in TABLE else "Speaker"


def headings(value: str | None) -> tuple[str, str, str]:
    return TABLE[code(value, "en")][2]
