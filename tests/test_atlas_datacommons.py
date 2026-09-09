import json
import unittest

import httpx

import extensions
import runtime
from plugins import atlas_datacommons as dc
from query_context import QueryContext


class HTTP:
    def __init__(self):self.calls=[]
    async def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs));path=url.rsplit('/',1)[-1];query=kwargs.get('params') or {};body=kwargs.get('json') or {}
        if path=='resolve' and query.get('resolver')=='indicator':value={'entities':[]}
        elif path=='resolve':value={'entities':[{'candidates':[{'dcid':'geoId/0644000'}]}]}
        elif path=='node' and body.get('property')=='->name':value={'data':{x:{'arcs':{'name':{'nodes':[{'value':'Miami'}]}}} for x in body['nodes']}}
        elif path=='node':value={'data':{body['nodes'][0]:{'arcs':{'x':{'nodes':[{'dcid':'geoId/1','name':'One'},{'dcid':'geoId/2','name':'Two'}]}}}}}
        elif path=='observation' and body.get('select')==['entity','variable']:
            var=body['variable']['dcids'][0];value={'byVariable':{var:{'byEntity':{x:{} for x in body['entity']['dcids']}}}}
        else:
            var=body['variable']['dcids'][0];value={'facets':{'1':{'importName':'Census','unit':'USD'}},'byVariable':{var:{'byEntity':{
                x:{'orderedFacets':[{'facetId':'1','observations':[{'date':'2023','value':i+1}]}]}
                for i,x in enumerate(body['entity']['dcids'])}}}}
        return httpx.Response(200,content=json.dumps(value).encode(),request=httpx.Request(method,url))


class DCTests(unittest.IsolatedAsyncioTestCase):
    def context(self):return QueryContext(http_client=HTTP())
    async def test_place_resolves_ids_variable_and_one_facet(self):
        with unittest.mock.patch.object(dc,'config',return_value={'api_key':'key'}):
            result=await dc.place(extensions.Read({},'dc',parameters={'params':{
                'place':'Miami','indicator':'median_household_income','year':2023}}),context=self.context())
        self.assertEqual(result.data[0]['place_dcid'],'geoId/0644000')
        self.assertEqual(result.data[0]['source'],'Census')
        self.assertEqual(result.provenance['resolved']['variable_dcid'],'Median_Income_Household')
        self.assertEqual(result.provenance['payload']['rows'],result.data)
    async def test_children_returns_full_map_and_requested_top(self):
        with unittest.mock.patch.object(dc,'config',return_value={'api_key':'key'}):
            result=await dc.children(extensions.Read({},'dc',parameters={'params':{
                'parent_place':'Miami','child_type':'county','indicator':'population','top_n':1}}),context=self.context())
        self.assertEqual(len(result.data),1);self.assertEqual(len(result.provenance['payload']['map_rows']),2)
        self.assertEqual(result.data[0]['rank'],1)
    async def test_key_and_caps_fail_honestly(self):
        with unittest.mock.patch.object(dc,'config',return_value={}):
            with self.assertRaises(runtime.AccessDenied):dc.Client(self.context())
        with unittest.mock.patch.object(dc,'config',return_value={'api_key':'key','max_entities':1}):
            client=dc.Client(self.context())
            with self.assertRaises(runtime.Refused):await client.observations('v',[{'dcid':'1','name':'1'},{'dcid':'2','name':'2'}])

if __name__=='__main__':unittest.main()
