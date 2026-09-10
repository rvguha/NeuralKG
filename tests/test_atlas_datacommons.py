import json
import unittest
import unittest.mock

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
    def test_established_attribute_coordinate_maps_to_indicator(self):
        read=extensions.Read({},'dc',parameters={'entity':'Texas','attribute':'population'})
        self.assertEqual(dc.params(read)['place'],'Texas')
        self.assertEqual(dc.params(read)['indicator'],'population')
        self.assertEqual(dc.requested_year({'period':'2022'}),2022)
        self.assertIsNone(dc.requested_year({'period':'latest'}))
    def test_percent_statvar_supplies_semantic_unit_when_facet_omits_it(self):
        indicator={'dcid':'Percent_Person_WithDiabetes','name':'Percentage of Adults With Diabetes'}
        self.assertEqual(dc.reported_unit(indicator,{'raw':{}}),'percent')
        self.assertEqual(dc.reported_unit(indicator,{'raw':{'unit':'custom'}}),'custom')
        self.assertEqual(dc.reported_unit({'dcid':'Count_Person','name':'Population'},{'raw':{}}),'count')
    async def test_indicator_receipt_is_json_serializable_not_self_referential(self):
        with unittest.mock.patch.object(dc,'config',return_value={'api_key':'key'}):
            result=await dc.Client(self.context()).resolve_indicator('population',['geoId/48'])
        json.dumps(result)
        self.assertNotIn('considered',result['considered'][0])
    async def test_place_resolves_ids_variable_and_one_facet(self):
        with unittest.mock.patch.object(dc,'config',return_value={'api_key':'key'}):
            result=await dc.place(extensions.Read({},'dc',parameters={'params':{
                'place':'Miami','indicator':'median_household_income','year':2023}}),context=self.context())
        self.assertEqual(result.data[0]['place_dcid'],'geoId/0644000')
        self.assertEqual(result.data[0]['source'],'Census')
        self.assertEqual(result.provenance['resolved']['variable_dcid'],'Median_Income_Household')
        self.assertEqual(result.provenance['payload']['rows'],result.data)
        self.assertEqual(result.grain,'city')
        self.assertEqual(dc.place_grain([{'dcid':'country/USA'},{'dcid':'country/JPN'}]),'country')
    async def test_mapped_series_returns_one_relation_per_place(self):
        read=extensions.Read({},'dc',node={'operator':'MapReadSeries'},parameters={'params':{
            'places':['Miami','Miami'],'indicator':'median_household_income','year_from':2020}})
        with unittest.mock.patch.object(dc,'config',return_value={'api_key':'key'}):
            result=await dc.place(read,context=self.context())
        self.assertEqual(len(result.data),2)
        self.assertTrue(all(isinstance(relation,list) for relation in result.data))
        self.assertEqual(result.data[0][0]['place_dcid'],'geoId/0644000')
    async def test_children_returns_full_map_and_requested_top(self):
        with unittest.mock.patch.object(dc,'config',return_value={'api_key':'key'}):
            result=await dc.children(extensions.Read({},'dc',parameters={'params':{
                'parent_place':'Miami','child_type':'county','indicator':'population','top_n':1}}),context=self.context())
        self.assertEqual(len(result.data),1);self.assertEqual(len(result.provenance['payload']['map_rows']),2)
        self.assertEqual(result.data[0]['rank'],1)
    async def test_key_and_caps_fail_honestly(self):
        # api_key_env is pointed at a name that cannot be set, because Client falls back to
        # os.getenv() and this assertion otherwise passes or fails on whether the developer
        # happens to have DC_API_KEY exported.
        with unittest.mock.patch.object(dc,'config',return_value={'api_key_env':'DC_API_KEY_ABSENT_FOR_TEST'}):
            with self.assertRaises(runtime.AccessDenied):dc.Client(self.context())
        with unittest.mock.patch.object(dc,'config',return_value={'api_key':'key','max_entities':1}):
            client=dc.Client(self.context())
            with self.assertRaises(runtime.Refused):await client.observations('v',[{'dcid':'1','name':'1'},{'dcid':'2','name':'2'}])

    async def test_dag_children_does_not_truncate_before_ranking(self):
        with unittest.mock.patch.object(dc,'config',return_value={'api_key':'key'}):
            result=await dc.children(extensions.Read({},'dc',node={'operator':'ReadRows'},parameters={'params':{
                'parent_place':'Miami','child_type':'county','indicator':'population','top_n':1}}),context=self.context())
        self.assertEqual(len(result.data),2)
        self.assertEqual(result.provenance['payload']['coverage']['observed_places'],2)

if __name__=='__main__':unittest.main()
