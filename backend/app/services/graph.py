from collections import defaultdict, Counter
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
    return graph

def graph_analysis(data,independent):
    graph=build_graph(data); flagged=defaultdict(set);output=[]
    for f,_ in independent:
        for pid in f.related_provider_ids:flagged[pid].update(f.related_claim_ids)
    refs=defaultdict(list)
    for r in data['referrals']:refs[r['source_provider_id']].append(r)
    claims={c['claim_id']:c for c in data['claims']}
    for pid,rs in refs.items():
        counts=Counter(r['destination_provider_id'] for r in rs);destination,n=counts.most_common(1)[0];share=n/len(rs)
        if len(rs)>=8 and share>=.6 and flagged[pid] and flagged[destination]:
            cs=[claims[cid] for cid in sorted(flagged[pid])[:8]]
            sources=[('referrals',r['referral_id'],r) for r in rs if r['destination_provider_id']==destination][:20]
            output.append(make_finding('corroborated_referral_concentration',cs,sources,f'{share:.0%} of {len(rs)} source referrals go to {destination}; both endpoints have independent screening findings.',engine='graph',entity=pid,context={'share':share,'destination':destination,'independent_source_claims':len(flagged[pid]),'independent_destination_claims':len(flagged[destination])},limitations=['Specialty pathways can concentrate referrals. A relationship does not establish collusion.','Graph corroboration is conditional on independent indicators, not propagated guilt.']))
    # Shared ownership is factual context, not a standalone allegation.
    owners=defaultdict(list)
    for f in data['facilities']:owners[f['owner_id']].append(f['facility_id'])
    return output,{'nodes':graph.number_of_nodes(),'edges':graph.number_of_edges(),'connected_components':nx.number_connected_components(graph),'largest_component':max((len(c) for c in nx.connected_components(graph)),default=0),'shared_ownership':{k:v for k,v in owners.items() if len(v)>1},'degree_statistics':{'maximum':max(dict(graph.degree()).values(),default=0)},'limitation':'Connected components describe relationships only; they do not establish misconduct.'}

def bounded_network(data,entity_id,limit=80,node_types=None,relationship_types=None,cutoff=None):
    graph=build_graph(data,cutoff)
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

