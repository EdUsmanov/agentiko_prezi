"""Recompose an immutable run with the current analyzer, without new model copy.

Writes a private regression folder; never changes the original run/package.
"""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from studio.config import Settings,ROOT
from studio.store import Store
from studio.pipeline import load_package
from studio.models import ContentModel,Plans
from studio.template import analyze_template
from studio.native_template import compile_backgrounds
from studio.planner import assign_compositions
from studio.composer import compose_variant
from studio.audit import repair_scenes,audit_scenes
from studio.render import render_variant
from studio.visual import review_visuals
from studio.gateway import ModelGateway


def main():
    parser=argparse.ArgumentParser();parser.add_argument('run');parser.add_argument('--vision',action='store_true');parser.add_argument('--sections',action='store_true')
    parser.add_argument('--reuse-sections',action='store_true',help='Reuse a previously verified grouping locally; no inference')
    args=parser.parse_args();settings=Settings.from_env();store=Store(settings.data_dir)
    run=store.get(args.run);package=load_package(store,run['package_id'])
    source=store.directory(package.id)/'input.pptx';old=store.directory(args.run)
    out=ROOT/'test-results'/('design-regression-'+args.run);out.mkdir(parents=True,exist_ok=True)
    package.template=analyze_template(source,out,allow_download=False)
    compile_backgrounds(package.template,source,out)
    package.content=ContentModel.model_validate_json((old/'generation-content.json').read_text())
    plans=assign_compositions(Plans.model_validate_json((old/'plans.json').read_text()),package)
    if args.sections or args.reuse_sections:
        from studio.sections import prepare_sections,add_dividers
        from studio.planner import validate_plans
        if args.reuse_sections:
            package.analysis['section_groups']=json.loads((out/'regression.json').read_text())['section_groups']
        else:
            asyncio.run(prepare_sections(package,ModelGateway(settings)))
        plans=validate_plans(add_dividers(plans,package),package)
    (out/'profile.json').write_text(package.template.model_dump_json(indent=2))
    (out/'plans.json').write_text(plans.model_dump_json(indent=2))
    from studio.diversity import ensure_diversity
    decks={v.key:compose_variant(v,package) for v in plans.variants}
    for scenes in decks.values():repair_scenes(scenes,package)
    diversity=ensure_diversity(decks,package)
    results=[]
    for variant in plans.variants:
        scenes=decks[variant.key];repairs=repair_scenes(scenes,package)
        findings=audit_scenes(scenes,package)
        rendering=render_variant(scenes,package.template,source,out/variant.key)
        results.append({'key':variant.key,'slides':len(scenes),'rendering':rendering,
            'errors':[f.model_dump() for f in findings if f.severity=='error'],
            'repairs':[f.model_dump() for f in repairs]})
    report={'source_run':args.run,'results':results,'section_groups':package.analysis.get('section_groups'),
        'section_dividers':package.analysis.get('section_dividers'),'diversity':diversity}
    if args.vision:
        report['visual']=asyncio.run(review_visuals(results,out,ModelGateway(settings),180))
    (out/'regression.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__': main()
