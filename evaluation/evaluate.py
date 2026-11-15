"""Run live evaluation against an LLM: python -m evaluation.evaluate [--limit N]."""
import argparse
import json
from pathlib import Path

from app.config import settings
from app.llm import LLMClient
from app.pipeline import analyze

ROOT = Path(__file__).resolve().parent


def metrics(predicted: set, expected: set) -> dict:
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    return {'tp': tp, 'fp': fp, 'fn': fn}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=12)
    args = parser.parse_args()
    cfg = settings()
    if not cfg.api_key:
        parser.error('Встав API_KEY у local_config.py перед запуском live evaluation.')
    cases = json.loads((ROOT / 'cases.json').read_text(encoding='utf-8'))[:args.limit]
    totals = {'characters': {'tp': 0, 'fp': 0, 'fn': 0},
              'relations': {'tp': 0, 'fp': 0, 'fn': 0}}
    results = []
    for case in cases:
        try:
            result = analyze(case['text'], LLMClient(cfg))
            by_id = {c.id: c.name.casefold() for c in result.characters}
            actual_chars = set(by_id.values())
            actual_rel = {(by_id[r.source_id], by_id[r.target_id], r.kind) for r in result.relations}
            gold_chars = {x.casefold() for x in case['characters']}
            gold_rel = {(a.casefold(), b.casefold(), kind) for a, b, kind in case['relations']}
            scored = {'characters': metrics(actual_chars, gold_chars),
                      'relations': metrics(actual_rel, gold_rel)}
            for k, counts in scored.items():
                for key in counts:
                    totals[k][key] += counts[key]
            results.append({'id': case['id'], 'counts': scored, 'unresolved_mentions': result.metadata['unresolved']})
            print(f"{case['id']}: character {scored['characters']} | relation {scored['relations']}")
        except Exception as exc:
            results.append({'id': case['id'], 'error': f'{type(exc).__name__}: {exc}'})
            print(f"{case['id']}: ERROR {type(exc).__name__}: {exc}")

    scores = {}
    for name, counts in totals.items():
        p = counts['tp'] / max(1, counts['tp'] + counts['fp'])
        r = counts['tp'] / max(1, counts['tp'] + counts['fn'])
        f1 = 2 * p * r / (p + r) if p + r else 0
        scores[name] = {'precision': round(p, 3), 'recall': round(r, 3), 'f1': round(f1, 3)}
    output = {'model': cfg.model, 'cases_run': len(results), 'cases_succeeded': sum('error' not in r for r in results),
              'scores': scores, 'cases': results}
    (ROOT / 'results.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'cases_succeeded': output['cases_succeeded'], 'scores': scores}, ensure_ascii=False, indent=2))
    print('Результати збережено в evaluation/results.json')


if __name__ == '__main__':
    main()
