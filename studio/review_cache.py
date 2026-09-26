"""Exact, private review checkpoints. No approximate matching or cached failures."""
import hashlib
import json
from pathlib import Path
from .cache_version import stage_version,atomic_json


def identity(gateway,stage,value):
    if not hasattr(gateway,'_review_versions'):gateway._review_versions={}
    if stage not in gateway._review_versions:gateway._review_versions[stage]=stage_version(stage)
    names=('model_id','base_url','thinking','thinking_token_budget','structured_output','openrouter_providers','openrouter_allow_fallbacks')
    data=[gateway._review_versions[stage],{k:getattr(getattr(gateway,'settings',None),k,None) for k in names},value]
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def read(gateway,key,validate):
    if not hasattr(gateway,'_reviews'):gateway._reviews={}
    try:
        raw=gateway._reviews.get(key)
        root=getattr(getattr(gateway,'settings',None),'data_dir',None)
        if raw is None and root:raw=json.loads((Path(root)/'review-cache'/(key+'.json')).read_text())
        if raw is not None:return validate(raw)
    except (OSError,ValueError,KeyError,TypeError):pass
    return None


def write(gateway,key,value):
    if not hasattr(gateway,'_reviews'):gateway._reviews={}
    gateway._reviews[key]=value
    root=getattr(getattr(gateway,'settings',None),'data_dir',None)
    if root:
        folder=Path(root)/'review-cache';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        target=folder/(key+'.json');atomic_json(target,value);target.chmod(0o600)
