import re
from functools import lru_cache

import spacy

MAX_CHARACTERS = 12000
MAX_SENTENCES = 90


@lru_cache(maxsize=1)
def _pipeline():
    nlp = spacy.blank('xx')
    nlp.add_pipe('sentencizer', config={'punct_chars': ['.', '!', '?', '…']})
    return nlp


def split_sentences(text: str) -> list[str]:
    clean = text.strip().replace('\r\n', '\n')
    if not clean:
        raise ValueError('Введи текст для аналізу.')
    if len(clean) > MAX_CHARACTERS:
        raise ValueError(f'Максимальна довжина тексту — {MAX_CHARACTERS} символів.')
    doc = _pipeline()(clean)
    sentences = []
    for sentence in doc.sents:
        s = re.sub(r'\s+', ' ', sentence.text).strip()
        if s:
            sentences.append(s)
    if not sentences:
        raise ValueError('Не вдалося виділити речення.')
    if len(sentences) > MAX_SENTENCES:
        raise ValueError(f'Максимальна кількість речень — {MAX_SENTENCES}.')
    return sentences


def is_exact_quote(quote: str, sentence: str) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r'\s+', ' ', value).strip().casefold()

    return bool(quote.strip()) and normalize(quote) in normalize(sentence)
