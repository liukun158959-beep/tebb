"""A complete JSON answer, separate from JSON examples inside prose."""
import json


def json_answer(text):
    if not isinstance(text,str):raise TypeError('answer must be text')
    text=text.strip();lines=text.splitlines()
    if len(lines)>=3 and lines[0].strip()in{'```','```json'}and lines[-1].strip()=='```':
        text='\n'.join(lines[1:-1])
    def pairs(items):
        out={}
        for key,value in items:
            if key in out:raise ValueError('duplicate JSON key')
            out[key]=value
        return out
    def constant(value):raise ValueError('nonfinite JSON value')
    return json.loads(text,object_pairs_hook=pairs,parse_constant=constant)


CODE_FIELDS={'code','command','commands','script','scripts','example','examples'}


def map_strings(value,transform,*,code=False):
    """Keep JSON keys/types intact; explicit code fields stay examples."""
    if isinstance(value,dict):return {key:map_strings(item,transform,code=code or key.lower()in CODE_FIELDS)for key,item in value.items()}
    if isinstance(value,list):return [map_strings(item,transform,code=code)for item in value]
    return transform(value)if isinstance(value,str)and not code else value


def text_values(text):
    out=[]
    map_strings(json_answer(text),lambda value:out.append(value)or value)
    return out
