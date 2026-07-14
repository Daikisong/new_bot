from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from collections import Counter
from pathlib import Path

SOURCE = Path(os.environ.get('NSLAB_REVIEW_SOURCE', 'source/temp/nslab_review_20220824_20260715t024535/review_news.py'))
spec = importlib.util.spec_from_file_location('nslab_review_source_current', SOURCE)
if spec is None or spec.loader is None:
    raise RuntimeError(f'review source missing: {SOURCE}')
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def run_range(args: argparse.Namespace) -> None:
    prepared = Path(args.prepared)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    metadata = mod.json.loads((prepared / 'metadata.json').read_text(encoding='utf-8'))
    rows = mod.read_csv(prepared / 'inputs' / metadata['news_file'])
    start = args.start
    end = args.end
    if not (1 <= start <= end <= len(rows)):
        raise RuntimeError(f'invalid range {start}-{end} for {len(rows)} rows')
    selected = rows[start-1:end]
    model_inputs = []
    for index, row in enumerate(selected, start):
        model_inputs.append({
            'id': f'SRC-NEWS-{index:06d}',
            'i': index,
            'ts': f"{row['date']}T{row['time']}+09:00",
            'title': row.get('title', ''),
            'body': row.get('body', ''),
        })
    token = os.environ.get('GITHUB_TOKEN', '')
    if not token:
        raise RuntimeError('GITHUB_TOKEN missing')
    primary = os.environ.get('NSLAB_MICRO_PRIMARY', 'openai/gpt-4o-mini')
    preferred = [
        primary,
        'openai/gpt-4o-mini',
        'mistral-ai/mistral-medium-2505',
        'meta/llama-3.3-70b-instruct',
        'mistral-ai/mistral-small-2503',
        'openai/gpt-4.1-nano',
        'openai/gpt-4.1-mini',
        'cohere/cohere-command-a',
        'microsoft/phi-4-mini-instruct',
    ]
    mod.MODELS = list(dict.fromkeys(preferred))
    mod.FALLBACK_MODELS = ['openai/gpt-4.1', 'microsoft/phi-4', 'deepseek/deepseek-v3-0324']
    client = mod.Client(token, primary, output / f'model_calls_{start:04d}_{end:04d}.jsonl')
    reviews: dict[str, dict] = {}
    model_counts: Counter[str] = Counter()

    def process(batch: list[dict], label: str) -> None:
        try:
            parsed, model = client.call(mod.SYSTEM, mod.user_prompt(batch), label)
            records = parsed.get('r') if isinstance(parsed, dict) else None
            if not isinstance(records, list) or len(records) != len(batch):
                raise ValueError('record count mismatch')
            by_id = {str(record.get('id')): record for record in records if isinstance(record, dict)}
            expected = {row['id'] for row in batch}
            if set(by_id) != expected:
                raise ValueError('record id coverage mismatch')
            for row in batch:
                normalized = mod.normalize(by_id[row['id']], row, model, -1)
                normalized['micro_range_start'] = start
                normalized['micro_range_end'] = end
                normalized['semantic_review_protocol'] = 'FULL_TITLE_BODY_E1_E2_E3_ADJUDICATION_NO_KEYWORD_SHORTCUT_MICRO_RANGE'
                reviews[row['id']] = normalized
                model_counts[model] += 1
        except Exception:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                process(batch[:midpoint], label + 'A')
                process(batch[midpoint:], label + 'B')
                return
            raise

    for batch_index, batch in enumerate(mod.batches(model_inputs, max_items=10, max_chars=85000), 1):
        process(batch, f'MICRO_FULL_ROW_{start:04d}_{end:04d}_B{batch_index:02d}')
        print(f'range {start}-{end}: {len(reviews)}/{len(model_inputs)}', flush=True)
    expected_ids = [f'SRC-NEWS-{index:06d}' for index in range(start, end + 1)]
    if set(reviews) != set(expected_ids):
        raise RuntimeError('micro range source coverage mismatch')
    ordered = [reviews[source_id] for source_id in expected_ids]
    if not all(row.get('full_title_body_reviewed') is True and row.get('quote_found_in_source_row') is True for row in ordered):
        raise RuntimeError('micro full text or quote validation failed')
    review_path = output / f'reviews_range_{start:04d}_{end:04d}.jsonl'
    mod.write_jsonl(review_path, ordered)
    mod.write_json(output / f'receipt_range_{start:04d}_{end:04d}.json', {
        'schema_version': 'nslab.semantic_review_micro_range_receipt.v1',
        'run_id': mod.RUN_ID,
        'global_start_row': start,
        'global_end_row': end,
        'assigned_row_count': len(ordered),
        'reviewed_row_count': len(ordered),
        'first_source_id': expected_ids[0],
        'last_source_id': expected_ids[-1],
        'model_counts': dict(model_counts),
        'reviews_sha256': mod.sha256_bytes(review_path.read_bytes()),
        'full_title_body_reviewed_count': len(ordered),
        'quote_found_count': len(ordered),
        'status': 'FULL_ROW_SEMANTIC_REVIEW_MICRO_RANGE_COMPLETE',
        'outcome_content_access_count': 0,
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepared', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--start', type=int, required=True)
    parser.add_argument('--end', type=int, required=True)
    args = parser.parse_args()
    run_range(args)


if __name__ == '__main__':
    main()
