"""Bounded, validated preparation requests with private resumable checkpoints."""
import json
from pathlib import Path
import httpx
from .security import digest
from .gateway import ModelResponseTruncated


class InductionFailure(ValueError):
    def __init__(self, message, *, provider_failure=False, fatal=False):
        super().__init__(message)
        self.provider_failure = provider_failure
        self.fatal = fatal


def provider_failure(error):
    return isinstance(error, (TimeoutError, httpx.TransportError)) or (
        isinstance(error, httpx.HTTPStatusError)
        and (error.response.status_code == 429 or error.response.status_code >= 500))


async def validated_request(gateway, stage, payload, schema, validate, *, timeout,
                            images=None, progress=None, split_group=False):
    cache = None
    root = getattr(gateway.settings, 'data_dir', None)
    if root:
        from .cache_version import stage_version
        if not hasattr(gateway, '_stage_versions'):gateway._stage_versions={}
        if stage not in gateway._stage_versions:gateway._stage_versions[stage]=stage_version(stage)
        identity = {'version': gateway._stage_versions[stage], 'stage': stage,
            'model': getattr(gateway.settings, 'model_id', ''),
            'endpoint': getattr(gateway.settings, 'base_url', ''),
            'thinking': getattr(gateway.settings, 'thinking', None),
            'thinking_token_budget': getattr(gateway.settings, 'thinking_token_budget', 0),
            'structured_output': getattr(gateway.settings, 'structured_output', False),
            'openrouter_providers':getattr(gateway.settings,'openrouter_providers',None),
            'openrouter_allow_fallbacks':getattr(gateway.settings,'openrouter_allow_fallbacks',None),
            'payload': payload, 'schema': schema,
            'images': [digest(data) for data in (images or [])]}
        key = digest(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode())
        cache = Path(root) / 'analysis-cache' / (key + '.json')
        try:
            raw = json.loads(cache.read_text())
            result = validate(raw)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        else:
            from .diagnostics import event
            event("model.cache_hit",stage=stage)
            if hasattr(gateway, 'calls'):
                gateway.calls.append({'stage': stage, 'status': 'completed', 'cache_hit': True})
            return result
        if split_group and cache.with_suffix('.split.json').is_file():
            raise InductionFailure('SplitCheckpoint')
    errors = []
    last_error = None
    fatal = False
    for attempt in range(2):
        try:
            raw = await gateway.json_request(stage, payload, timeout=timeout, schema=schema,
                                            **({'images': images} if images else {}))
            result = validate(raw)
        except Exception as exc:
            last_error = exc
            fatal = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (400, 401, 403, 404, 422)
            # Never persist provider response bodies, credentials or document text.
            error = {'stage': stage, 'attempt': attempt + 1, 'error_type': type(exc).__name__}
            if isinstance(exc, httpx.HTTPStatusError):
                error['http_status'] = exc.response.status_code
            errors.append(error)
            if not hasattr(gateway, 'induction_errors'):
                gateway.induction_errors = []
            gateway.induction_errors.append(error)
            if fatal or (split_group and (provider_failure(exc) or isinstance(exc, ModelResponseTruncated))):
                # Don't repeat a large timed-out or truncated batch before splitting.
                # A service/configuration failure must not fan out into N retries.
                break
            if not attempt and progress:
                progress('Повторяем незавершённый блок анализа: ' + error['error_type'])
            feedback='Match the schema and preserve required ID coverage. No extra fields.'
            if stage in ('editorial','editorial_repair','editorial_review','template_analyst') and isinstance(exc,ValueError):
                from .diagnostics import redact
                feedback+=' Validation issue: '+redact(str(exc))[:1400]
            payload = {**payload, 'validation_feedback':feedback}
            continue
        if cache:
            from .cache_version import atomic_json
            # Store only schema-validated typed data; no arbitrary model prose.
            atomic_json(cache, result)
            cache.chmod(0o600)
        return result
    if cache and split_group and not fatal:
        from .cache_version import atomic_json
        atomic_json(cache.with_suffix('.split.json'), {'split': True})
    failure=InductionFailure(errors[-1]['error_type'],
                           provider_failure=provider_failure(last_error), fatal=fatal)
    failure.stage=stage
    failure.attempt_errors=errors
    raise failure from last_error
