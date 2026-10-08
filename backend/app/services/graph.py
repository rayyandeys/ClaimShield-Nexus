from collections import defaultdict, Counter
from datetime import date
import networkx as nx
from app.core import clean
from app.services.detection import make_finding

def build_graph(data, cutoff=None):
    graph=nx.MultiGraph()
    for table,typ,label in [('providers','provider','provider_name'),('facilities','facility','facility_name'),('members','member','member_id'),('claims','claim','claim_id')]:
        pk={'providers':'provider_id','facilities':'facility_id','members':'member_id','claims':'claim_id'}[table]
        for r in data[table]:
            if table=='claims' and cutoff and r['service_date']>cutoff:continue
            graph.add_node(r[pk],id=r[pk],label=r[label],type=typ,source_table=table)
    for f in data['facilities']:graph.add_node(f['owner_id'],id=f['owner_id'],label=f'Synthetic owner {f["owner_id"]}',type='owner',source_table='facilities')
    def edge(source,target,eid,typ,table,record,when=None,until=None,verification='source_record'):
        if source not in graph or target not in graph:return
        if cutoff and when and when>cutoff:return
        if cutoff and until and until<cutoff:return
        graph.add_edge(source,target,key=eid,id=eid,source=source,target=target,type=typ,source_table=table,source_record_id=record,effective_from=str(when) if when else None,effective_to=str(until) if until else None,verification_status=verification)
    for r in data['relationships']:edge(r['source_id'],r['target_id'],r['relationship_id'],r['relationship_type'],'relationships',r['relationship_id'],r['effective_from'],r['effective_to'],r['verification_status'])
    for r in data['referrals']:edge(r['source_provider_id'],r['destination_provider_id'],r['referral_id'],'referral','referrals',r['referral_id'],r['referral_date'])
    for r in data['claims']:
        for field,typ in [('provider_id','billed_by'),('member_id','service_for'),('facility_id','service_at')]:edge(r['claim_id'],r[field],r['claim_id']+'-'+typ,typ,'claims',r['claim_id'],r['service_date'])
    # Flagged Phoenix links are algorithmic similarity edges, labeled as such; they are not recorded relationships.
    for r in data.get('successor_links',[]):
        edge(r['predecessor_id'],r['successor_id'],r['link_id'],'possible_successor','provider_successors',r['link_id'],r['details'].get('successor_start') and date.fromisoformat(r['details']['successor_start']),verification='algorithmic_similarity')
        if graph.has_edge(r['predecessor_id'],r['successor_id'],r['link_id']):graph.edges[r['predecessor_id'],r['successor_id'],r['link_id']].update(score=r['score'],components=r['components'],finding_id=r['finding_id'])
    return graph

def referral_findings(data,independent,providers=None):
    """Corroborated referral concentration. providers: only evaluate referral sources that are, or refer to, these providers."""
    flagged=defaultdict(set);output=[]
    for f,_ in independent:
        for pid in f.related_provider_ids:flagged[pid].update(f.related_claim_ids)
    refs=defaultdict(list)
    for r in data['referrals']:refs[r['source_provider_id']].append(r)
    claims={c['claim_id']:c for c in data['claims']}
    for pid,rs in refs.items():
        counts=Counter(r['destination_provider_id'] for r in rs);destination,n=counts.most_common(1)[0];share=n/len(rs)
        if providers is not None and pid not in providers and destination not in providers:continue
        if len(rs)>=8 and share>=.6 and flagged[pid] and flagged[destination]:
            cs=[claims[cid] for cid in sorted(flagged[pid])[:8]]
            sources=[('referrals',r['referral_id'],r) for r in rs if r['destination_provider_id']==destination][:20]
            output.append(make_finding('corroborated_referral_concentration',cs,sources,f'{share:.0%} of {len(rs)} source referrals go to {destination}; both endpoints have independent screening findings.',engine='graph',entity=pid,context={'share':share,'destination':destination,'independent_source_claims':len(flagged[pid]),'independent_destination_claims':len(flagged[destination])},limitations=['Specialty pathways can concentrate referrals. A relationship does not establish collusion.','Graph corroboration is conditional on independent indicators, not propagated guilt.']))
    return output

def neighborhood(data,provider_id):
    """Recorded relationships around one provider, counted from source rows without rebuilding the whole graph."""
    claims=[c for c in data['claims'] if c['provider_id']==provider_id]
    return {'claims_billed':len(claims),'members':len({c['member_id'] for c in claims}),'facilities':sorted({c['facility_id'] for c in claims}),'referrals_out':sum(r['source_provider_id']==provider_id for r in data['referrals']),'referrals_in':sum(r['destination_provider_id']==provider_id for r in data['referrals']),'documented_relationships':sum(provider_id in (r['source_id'],r['target_id']) for r in data['relationships']),'possible_successor_links':sum(provider_id in (r['predecessor_id'],r['successor_id']) for r in data.get('successor_links',[]))}

def graph_analysis(data,independent):
    graph=build_graph(data);output=referral_findings(data,independent)
    # Shared ownership is factual context, not a standalone allegation.
    owners=defaultdict(list)
    for f in data['facilities']:owners[f['owner_id']].append(f['facility_id'])
    return output,{'nodes':graph.number_of_nodes(),'edges':graph.number_of_edges(),'connected_components':nx.number_connected_components(graph),'largest_component':max((len(c) for c in nx.connected_components(graph)),default=0),'shared_ownership':{k:v for k,v in owners.items() if len(v)>1},'degree_statistics':{'maximum':max(dict(graph.degree()).values(),default=0)},'limitation':'Connected components describe relationships only; they do not establish misconduct.'}

_cache={'key':None,'graph':None,'watermark':None}
def cached_graph(key,load,increment=None,watermark=None):
    """Full (no as-of cutoff) graph reused until the data version key changes; as-of views are always rebuilt.
    Live-stream claims are added in place: increment(previous_watermark) returns the stream rows ingested since then, and
    only those nodes and edges are added instead of rebuilding the graph."""
    if _cache['key']!=key:_cache.update(key=key,graph=build_graph(load()),watermark=watermark)
    elif increment is not None and watermark is not None and watermark!=_cache['watermark']:
        add_records(_cache['graph'],increment(_cache['watermark']));_cache['watermark']=watermark
    return _cache['graph']

def add_records(graph,rows):
    """Adds new provider/member/claim nodes and their claim edges (billed_by, service_for, service_at) to a built graph."""
    for table,typ,label,pk in [('providers','provider','provider_name','provider_id'),('members','member','member_id','member_id'),('claims','claim','claim_id','claim_id')]:
        for r in rows.get(table,[]):graph.add_node(r[pk],id=r[pk],label=r[label],type=typ,source_table=table)
    for r in rows.get('relationships',[]):
        if r['source_id'] in graph and r['target_id'] in graph:graph.add_edge(r['source_id'],r['target_id'],key=r['relationship_id'],id=r['relationship_id'],source=r['source_id'],target=r['target_id'],type=r['relationship_type'],source_table='relationships',source_record_id=r['relationship_id'],effective_from=str(r['effective_from']),effective_to=str(r['effective_to']) if r['effective_to'] else None,verification_status=r['verification_status'])
    for r in rows.get('claims',[]):
        for field,typ in [('provider_id','billed_by'),('member_id','service_for'),('facility_id','service_at')]:
            if r[field] in graph:graph.add_edge(r['claim_id'],r[field],key=r['claim_id']+'-'+typ,id=r['claim_id']+'-'+typ,source=r['claim_id'],target=r[field],type=typ,source_table='claims',source_record_id=r['claim_id'],effective_from=str(r['service_date']),effective_to=None,verification_status='source_record')

def bounded_network(data,entity_id,limit=80,node_types=None,relationship_types=None,cutoff=None,graph=None):
    graph=graph if graph is not None and cutoff is None else build_graph(data,cutoff)
    if entity_id not in graph:return None
    # Prioritize the provider/facility/ownership layer before claims and members.
    selected={entity_id};frontier=[entity_id]
    for depth in range(2):
        candidates=[]
        for node in frontier:
            for neighbor in graph.neighbors(node):
                if node_types and graph.nodes[neighbor]['type'] not in node_types:continue
                if relationship_types and not any(d['type'] in relationship_types for d in graph.get_edge_data(node,neighbor).values()):continue
                candidates.append(neighbor)
        candidates=sorted(set(candidates)-selected,key=lambda n:(graph.nodes[n]['type'] in {'claim','member'},n))[:max(0,limit-len(selected))]
        selected.update(candidates);frontier=candidates
    sub=graph.subgraph(selected)
    edges=[{'data':clean(d)} for _,_,d in sub.edges(data=True) if not relationship_types or d['type'] in relationship_types][:300]
    return {'nodes':[{'data':dict(d,degree=graph.degree(n))} for n,d in sub.nodes(data=True)],'edges':edges,'total_neighbors':graph.degree(entity_id),'truncated':len(selected)>=limit or sub.number_of_edges()>300,'limitation':'Connections show recorded relationships, not proven misconduct.'}

