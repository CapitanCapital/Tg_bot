"""OpenRouter adapter restricted to zero-priced endpoints and :free model IDs."""
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

DEFAULT_MODEL = 'nvidia/nemotron-3-super-120b-a12b:free'


def check_schema(value, schema):
    kind = schema['type']
    valid = {'object':dict, 'array':list, 'string':str, 'integer':int, 'boolean':bool}
    if type(value) is not valid[kind]:
        raise ValueError('Model response has incorrect JSON type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Model response has invalid enum')
    if kind == 'object':
        if set(value) != set(schema['properties']):
            raise ValueError('Model response has missing or extra fields')
        for key, subschema in schema['properties'].items():
            check_schema(value[key], subschema)
    elif kind == 'array':
        if len(value) > schema.get('maxItems', len(value)):
            raise ValueError('Model response exceeds array limit')
        for element in value:
            check_schema(element, schema['items'])


class OpenRouterAPI:
    def __init__(self, key, model, directory):
        if not model.endswith(':free'):
            raise ValueError('Only explicit :free models are allowed')
        self.key, self.model, self.directory = key, model, directory
        self.usage = []

    def build_request(self, stage, instructions, payload, schema, max_tokens):
        content = json.dumps(payload, ensure_ascii=False)
        if len(content) > 650_000:
            raise ValueError('Input exceeds configured size bound')
        return {'model':self.model, 'messages':[{'role':'system','content':instructions},
                {'role':'user','content':content}], 'max_tokens':max_tokens,
                'reasoning':{'enabled':False},
                'provider':{'require_parameters':True,'max_price':{'prompt':0,'completion':0,'request':0}},
                'response_format':{'type':'json_schema','json_schema':{'name':stage,'strict':True,'schema':schema}}}

    def ask(self, stage, instructions, payload, schema, max_tokens):
        body = self.build_request(stage,instructions,payload,schema,max_tokens)
        req = Request('https://openrouter.ai/api/v1/chat/completions',data=json.dumps(body).encode(),
                      headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'})
        print('OpenRouter stage: '+stage,flush=True)
        try:
            with urlopen(req,timeout=180) as response:
                data=json.load(response)
        except HTTPError as exc:
            # Only surface numeric status, never echo provider input or credentials.
            raise RuntimeError('OpenRouter HTTP '+str(exc.code)) from None
        if data.get('error'):
            raise RuntimeError('OpenRouter returned an upstream error')
        usage=data.get('usage',{})
        self.usage.append({'stage':stage,'actual_model':data.get('model'),**usage})
        usage_path = self.directory/'usage.json'
        recorded = json.loads(usage_path.read_text()) if usage_path.exists() else []
        recorded.append(self.usage[-1])
        usage_path.write_text(json.dumps(recorded,indent=2))
        if float(usage.get('cost') or 0) != 0:
            raise RuntimeError('Unexpected nonzero cost; stopping')
        choices=data.get('choices',[])
        if not choices or choices[0].get('finish_reason') != 'stop':
            raise RuntimeError('OpenRouter response incomplete: '+str(choices[0].get('finish_reason') if choices else 'no_choices'))
        message=choices[0]['message']
        if message.get('refusal'):
            raise RuntimeError('Model refused the request')
        text=message.get('content')
        if not isinstance(text,str):
            raise ValueError('No JSON response content')
        value=json.loads(text)
        check_schema(value,schema)
        (self.directory/(stage+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2))
        return value
