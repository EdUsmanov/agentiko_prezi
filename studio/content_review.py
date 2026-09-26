"""Bounded review of actual exported text, shared by both generation engines."""
import asyncio
import json
from .models import ContextualAudit


async def review(slides, plans, gateway, stage, timeout, context=None):
    from .pipeline import grounded_review
    unique=[];aliases={};keys={}
    for slide in slides:
        # Geometry is reviewed separately. Only byte-identical semantic evidence
        # can share this check; variants with any changed text get their own row.
        key=json.dumps({k:slide[k] for k in ('title','actual_text','facts')},sort_keys=True,ensure_ascii=False)
        if key not in keys:
            keys[key]=(slide['variant'],slide['slide'])
            unique.append(slide)
        aliases.setdefault(keys[key],[]).append((slide['variant'],slide['slide']))
    async def check(batch):
        try:
            from . import review_cache
            payload={'slides':batch,**(context or {})}
            def validate(raw):
                parsed=grounded_review(raw,plans)
                allowed={(s['variant'],s['slide']) for s in batch}
                if any((f['variant'],f['slide']) not in allowed for f in parsed['findings']):
                    raise ValueError('Finding refers to a slide outside the supplied batch')
                return parsed
            key=review_cache.identity(gateway,stage,payload)
            parsed=review_cache.read(gateway,key,validate)
            if parsed is None:
                raw=await gateway.json_request(stage,payload,timeout=timeout,schema=ContextualAudit.model_json_schema())
                parsed=validate(raw)
                review_cache.write(gateway,key,parsed)
            return parsed['findings'],None
        except Exception as error:
            return [],type(error).__name__
    batches=await asyncio.gather(*(check(unique[i:i+8]) for i in range(0,len(unique),8)))
    findings=[{**f,'variant':variant,'slide':index} for found,_ in batches for f in found
        for variant,index in aliases[(f['variant'],f['slide'])]]
    failed=[error for _,error in batches if error]
    return {'status':'failed' if failed else 'completed','type':'exported_pptx_text_review',
        'findings':findings,'checked':len(slides) if not failed else None,
        'unique_slides':len(unique),'batches':len(batches),
        **({'reason':','.join(sorted(set(failed)))} if failed else {})}
