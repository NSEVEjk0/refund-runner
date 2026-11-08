"""Fixed decoders, invoked only in the worker image."""
import json
import sys
sys.path.insert(0, '/worker')
try:
    if sys.argv[1] == 'documents':
        from documents import run
    elif sys.argv[1] == 'audio':
        from media import run
    else:
        raise ValueError('Unsupported fixed worker operation.')
    print(json.dumps(run(), ensure_ascii=True, sort_keys=True, allow_nan=False))
except Exception as error:
    print(json.dumps({'error': type(error).__name__, 'message': str(error)[:500]}), file=sys.stderr)
    raise SystemExit(2)
